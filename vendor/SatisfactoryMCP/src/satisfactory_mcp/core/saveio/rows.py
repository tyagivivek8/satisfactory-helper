"""The interned tables of the projection, decoded in one place.

``extract`` emits five tables as bare positional rows rather than as records, because a
record per piece would be megabytes -- so every reader has to decode one, and this is the
only module that does. A row comes out as the numbers it holds: raw centimetres, no
rounding, no unit conversion, no display names, no game concepts, and the one lookup done
here is the interned class index against the table's own ``classes`` list, which has no
meaning outside the table it was written with. Two shapes to trip on: trailing columns are
additive, so a row shorter than its ``*_ROW_WIDTH`` means "the projection does not carry
that column" rather than a tear, and a row that will not decode leaves a HOLE -- ``index``
is the row's position in the raw list, because ``pipe_flow`` and ``/api/pipes`` join by
position and a consumer sizes its array with ``*_count``.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, NamedTuple

__all__ = [
    "BELT_ROW_WIDTH",
    "PIPE_ROW_WIDTH",
    "POWER_POLE_ROW_WIDTH",
    "STRUCTURE_ROW_WIDTH",
    "WIRE_ROW_WIDTH",
    "BeltSegment",
    "PipeSegment",
    "PowerPole",
    "Structure",
    "Wire",
    "belt_segment_count",
    "iter_belt_segments",
    "iter_pipe_segments",
    "iter_power_poles",
    "iter_structures",
    "iter_wires",
    "pipe_segment_count",
    "wire_count",
]

# ``test_saveio_rows`` holds these five widths against the widest row the committed
# projection contains, so a new column fails a test here until a reader has decided it wants
# it.

#: Columns of ``structures["instances"]``: ``[classIndex, x, y, z, yaw]``. Yaw arrived in
#: schema 12.
STRUCTURE_ROW_WIDTH = 5

#: Columns of ``belts["segments"]``: ``[chainIndex, classIndex, points, actorIndex, spans]``.
#: The actor join arrived in schema 20 and the spans in 15, the latter emitted only for a run
#: that actually bends, so a four-column row is the ordinary case rather than an old projection.
BELT_ROW_WIDTH = 5

#: Columns of ``pipes["segments"]``: ``[networkIndex, classIndex, points, actorIndex, spans]``.
#: The actor join arrived in schema 14 and the spans in 15, the latter again only where the
#: pipe bends. The same five columns as a belt, in the same order, since schema 20.
PIPE_ROW_WIDTH = 5

#: Columns of ``power["poles"]["instances"]``: ``[classIndex, x, y, z, yaw, actorIndex]``. All
#: six arrived together in schema 17, so there is no short form of this row yet.
POWER_POLE_ROW_WIDTH = 6

#: Columns of ``power["wires"]``: ``[x0, y0, z0, x1, y1, z1]``, both ends of one span, in the
#: order of the ``graph["power"]`` edge the row sits opposite. Schema 17.
WIRE_ROW_WIDTH = 6


class Structure(NamedTuple):
    """One lightweight buildable: where it stands, which way it faces, and what it is.

    ``yaw`` is ``None`` for both ways a facing can be missing -- a projection older than
    schema 12, and schema 16's null for a rotation the parser could not read -- and both
    mean "draw it axis-aligned and do not claim a bearing".
    """

    class_index: int
    cls: str | None
    x: float
    y: float
    z: float
    yaw: float | None


class BeltSegment(NamedTuple):
    """One conveyor piece: its chain, its class, its polyline, its actor and its curve.

    ``points`` are the spline's control points in world centimetres, in travel order, and
    there is at least one. ``actor_index`` points into ``graph["actors"]`` on a
    ``PipeSegment``'s terms and is ``-1`` for a piece that has none -- a projection older than
    schema 20, or a belt the graph does not name. ``spans`` is schema 15's tangent column
    exactly as stored -- one entry per span, ``0`` for a straight one -- or ``None`` where the
    row carries no such column; its only reader is ``/api/belts``, so it is left undecoded.
    """

    index: int
    chain: int
    class_index: int
    cls: str | None
    points: list[list[float]]
    actor_index: int
    spans: Any | None


class PipeSegment(NamedTuple):
    """One fluid pipe: its network, its class, its polyline, its actor and its curve.

    ``actor_index`` points into ``graph["actors"]`` and is ``-1`` for a pipe that has none --
    a projection older than schema 14, or a pipe the graph does not name. ``network_index``
    points into ``pipes["networks"]`` and is likewise ``-1`` where no network claims the pipe.
    """

    index: int
    network_index: int
    class_index: int
    cls: str | None
    points: list[list[float]]
    actor_index: int
    spans: Any | None


class PowerPole(NamedTuple):
    """One power pole, wall outlet or tower: where it stands, and what it is joined to.

    ``yaw`` is ``None`` on a ``Structure``'s terms, and ``actor_index`` is ``-1`` for a pole
    no wire names, such as an unstrung tower platform. That index is the join a caller counts
    a pole's wires with: this table carries no degree of its own, because ``graph["power"]``
    already is the connectivity and a second copy could disagree with it.
    """

    class_index: int
    cls: str | None
    x: float
    y: float
    z: float
    yaw: float | None
    actor_index: int


class Wire(NamedTuple):
    """One power wire's drawn span: its two endpoints in world centimetres.

    ``index`` is the row's position in ``power["wires"]``, which is also its position in
    ``graph["power"]`` -- the two lists are written in one pass for exactly that reason -- so
    ``wire.index`` is how a caller reaches the pair of actors this span joins. ``a`` is the
    end at ``graph["power"][index][0]`` and ``b`` the end at ``[1]``, an order ``extract._power``
    establishes by measurement because the save's own agrees with the edge's about half the time.
    """

    index: int
    a: list[float]
    b: list[float]


def _table(projection: dict, key: str) -> dict:
    """One interned table, as a dict, whatever the projection carries in its place.

    ``{}`` for a missing key AND for a key holding something that is not a dict, so that a
    projection too old for ``pipes`` and one whose ``pipes`` is a stray list both read as
    "this table has nothing in it".
    """
    payload = projection.get(key) if isinstance(projection, dict) else None
    return payload if isinstance(payload, dict) else {}


def _classes(table: dict) -> list:
    raw = table.get("classes")
    return raw if isinstance(raw, list) else []


def _rows(table: dict, key: str) -> list:
    raw = table.get(key)
    return raw if isinstance(raw, list) else []


def _class_at(classes: list, index: int) -> str | None:
    if 0 <= index < len(classes):
        name = classes[index]
        return name if isinstance(name, str) else None
    return None


def _points(raw: Any) -> list[list[float]]:
    """A segment's control points as ``[[x, y, z], ...]`` centimetres, guarded per point.

    A point that will not decode costs that point and not the segment: a belt whose third
    corner is unreadable is still a belt between the corners that read.
    """
    out: list[list[float]] = []
    for point in raw if isinstance(raw, (list, tuple)) else ():
        if not isinstance(point, (list, tuple)) or len(point) < 3:
            continue
        try:
            out.append([float(point[0]), float(point[1]), float(point[2])])
        except (TypeError, ValueError):
            continue
    return out


def _column(row: Any, index: int) -> Any | None:
    """A trailing, additive column, or ``None`` where the row predates it."""
    return row[index] if len(row) > index else None


def iter_structures(projection: dict) -> Iterator[Structure]:
    """Every lightweight buildable in ``structures``, decoded, in the table's own order.

    A piece whose class index points past the ``classes`` list still comes through: it is a
    place with a real position and an unknown class, not a place that is not there.
    """
    table = _table(projection, "structures")
    classes = _classes(table)
    for row in _rows(table, "instances"):
        if not isinstance(row, (list, tuple)) or len(row) < 4:
            continue
        try:
            class_index = int(row[0])
            x, y, z = float(row[1]), float(row[2]), float(row[3])
        except (TypeError, ValueError):
            continue
        raw_yaw = _column(row, 4)
        try:
            yaw = None if raw_yaw is None else float(raw_yaw)
        except (TypeError, ValueError):
            yaw = None
        yield Structure(
            class_index=class_index,
            cls=_class_at(classes, class_index),
            x=x,
            y=y,
            z=z,
            yaw=yaw,
        )


def belt_segment_count(projection: dict) -> int:
    """How many rows ``belts["segments"]`` holds, decodable or not."""
    return len(_rows(_table(projection, "belts"), "segments"))


def iter_belt_segments(projection: dict) -> Iterator[BeltSegment]:
    """Every conveyor piece in ``belts``, decoded, in the table's own order.

    A row whose points do not decode at all is dropped: a piece with no geometry is not a
    piece. ``actor_index`` normalises to ``-1`` rather than dropping the row, on a pipe
    segment's terms: a belt the graph does not name is still a belt, and is still drawn.
    """
    table = _table(projection, "belts")
    classes = _classes(table)
    for index, row in enumerate(_rows(table, "segments")):
        if not isinstance(row, (list, tuple)) or len(row) < 3:
            continue
        try:
            chain = int(row[0])
            class_index = int(row[1])
        except (TypeError, ValueError):
            continue
        points = _points(row[2])
        if not points:
            continue
        actor = _column(row, 3)
        yield BeltSegment(
            index=index,
            chain=chain,
            class_index=class_index,
            cls=_class_at(classes, class_index),
            points=points,
            actor_index=actor if isinstance(actor, int) and actor >= 0 else -1,
            spans=_column(row, 4),
        )


def pipe_segment_count(projection: dict) -> int:
    """How many rows ``pipes["segments"]`` holds, decodable or not.

    What a positional array over the segments has to be sized with: ``pipe_flow`` promises
    ``/api/pipes`` one answer per ROW rather than one per readable row.
    """
    return len(_rows(_table(projection, "pipes"), "segments"))


def iter_pipe_segments(projection: dict) -> Iterator[PipeSegment]:
    """Every fluid pipe in ``pipes``, decoded, in the table's own order.

    Dropped on a belt segment's terms. ``actor_index`` and ``network_index`` normalise to
    ``-1`` rather than dropping the row: a pipe no network claims and a pipe the graph does
    not name are both ordinary, and both are drawn.
    """
    table = _table(projection, "pipes")
    classes = _classes(table)
    for index, row in enumerate(_rows(table, "segments")):
        if not isinstance(row, (list, tuple)) or len(row) < 3:
            continue
        try:
            network_index = int(row[0])
            class_index = int(row[1])
        except (TypeError, ValueError):
            continue
        points = _points(row[2])
        if not points:
            continue
        actor = _column(row, 3)
        yield PipeSegment(
            index=index,
            network_index=network_index,
            class_index=class_index,
            cls=_class_at(classes, class_index),
            points=points,
            # ``isinstance``, not ``int()``: an actor index is a position in a list this
            # projection also carries, so a float or a string here is a torn row rather than
            # a number in the wrong type.
            actor_index=actor if isinstance(actor, int) and actor >= 0 else -1,
            spans=_column(row, 4),
        )


def iter_power_poles(projection: dict) -> Iterator[PowerPole]:
    """Every power pole in ``power``, decoded, in the table's own order.

    Dropped on a ``Structure``'s terms, because a pole IS a placement. A projection cut
    before schema 17 has no ``power`` key at all and yields nothing.
    """
    table = _table(_table(projection, "power"), "poles")
    classes = _classes(table)
    for row in _rows(table, "instances"):
        if not isinstance(row, (list, tuple)) or len(row) < 4:
            continue
        try:
            class_index = int(row[0])
            x, y, z = float(row[1]), float(row[2]), float(row[3])
        except (TypeError, ValueError):
            continue
        raw_yaw = _column(row, 4)
        try:
            yaw = None if raw_yaw is None else float(raw_yaw)
        except (TypeError, ValueError):
            yaw = None
        actor = _column(row, 5)
        yield PowerPole(
            class_index=class_index,
            cls=_class_at(classes, class_index),
            x=x,
            y=y,
            z=z,
            yaw=yaw,
            actor_index=actor if isinstance(actor, int) and actor >= 0 else -1,
        )


def wire_count(projection: dict) -> int:
    """How many rows ``power["wires"]`` holds, decodable or not.

    The number a caller checks against ``len(graph["power"])`` before joining the two: the
    extractor promises they are equal on every save, so a projection where they are not is
    one to refuse rather than to index into.
    """
    payload = _table(projection, "power").get("wires")
    return len(payload) if isinstance(payload, list) else 0


def iter_wires(projection: dict) -> Iterator[Wire]:
    """Every power wire's span in ``power``, decoded, in the table's own order.

    ``null`` is what the writer emits for a wire that published no geometry, so a save older
    than the property yields no wires at all while ``graph["power"]`` still carries every
    edge: the connections are known and where they run is not.
    """
    for index, row in enumerate(_rows(_table(projection, "power"), "wires")):
        if not isinstance(row, (list, tuple)) or len(row) < WIRE_ROW_WIDTH:
            continue
        try:
            ends = [float(v) for v in row[:WIRE_ROW_WIDTH]]
        except (TypeError, ValueError):
            continue
        yield Wire(index=index, a=ends[:3], b=ends[3:])
