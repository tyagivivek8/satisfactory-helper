"""Foundation slabs: what the player physically built as one thing.

The sharpest of the factory signals, because a player builds a platform and then fills
it: belts and wires cross between platforms freely, but a foundation is only ever placed
against another foundation deliberately. Adjacency is geometric, since snapping is a
build-time UI concept that is not serialized -- two 8 m foundations that touch have
centres 800 cm apart, and a multi-storey factory is one structure rather than several
sharing a footprint. Ramps, stairs and walls join the union as nodes of their own, so a
RUN of them bridges a gap no single piece could span; asking only whether one piece
touches two slabs finds nothing, because the interesting case is slab -> wall -> wall ->
slab.

**Catwalks are excluded, and that is the whole trick.** They are the long walkways a
player runs BETWEEN distant platforms, so chaining them scores the highest purity of any
bridging rule and still welds two genuinely separate factories together. An over-segmented
slab can be merged by naming; an over-merged one cannot be split.

Not every factory sits on foundations -- some are built straight on the ground and have no
slab at all -- so slabs are another candidate signal, never the arbiter.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

from ...core.saveio import rows as saverows

__all__ = ["LINK_XY", "LINK_Z", "STAND_ON", "Slab", "Structures", "build_structures"]

#: Face-adjacency cutoff in cm. Between a shared face (800) and a shared corner (1131).
LINK_XY = 830.0

#: Vertical reach in cm for stacked floors. The knee: purity climbs to 1600 and then
#: stops, while only the risk of merging two structures grows past it. Four storeys
#: sounds generous until you notice a refinery deck is not built at wall height.
LINK_Z = 1600.0

#: How far under a machine to look for the tile it stands on, in cm.
STAND_ON = 600.0

#: This module works in CENTIMETRES throughout -- the save's own units -- and is the one
#: place that does not route distance through ``geo.distance_m``. Nothing here is reported
#: to a caller in metres, so raw ``math.dist`` against the cm thresholds above is correct.

#: Connective tissue. Catwalks are POINTEDLY absent -- see the module docstring.
_BRIDGE = ("Ramp", "Stair", "Wall")
_FOUNDATION = ("Foundation", "Platform")

_CELL = 800.0


@dataclass
class Slab:
    """One connected platform.

    ``bbox`` is the tiles' axis-aligned XY bounding box, ``(min_x, min_y, max_x, max_y)``
    in cm. It cannot be derived from ``centre``/``extent``: ``centre`` is the tile MEAN,
    which sits wherever the tiles are dense, so ``centre +- extent/2`` invents corners an
    L-shaped platform does not have.
    """

    index: int
    tiles: int
    centre: tuple[float, float, float]
    extent: tuple[float, float]
    z_span: tuple[float, float]
    bbox: tuple[float, float, float, float]

    @property
    def storeys(self) -> int:
        return max(1, round((self.z_span[1] - self.z_span[0]) / 400.0) + 1)


@dataclass
class Structures:
    """Slabs, and which slab each machine stands on."""

    slabs: list[Slab] = field(default_factory=list)
    slab_of: dict[str, int] = field(default_factory=dict)

    def machines_on(self, index: int) -> list[str]:
        return sorted(m for m, s in self.slab_of.items() if s == index)

    def groups(self) -> list[list[str]]:
        """Machine sets by slab, largest first. Machines on no slab are omitted --
        they are ground-built and this signal has nothing to say about them."""
        by_slab: dict[int, list[str]] = defaultdict(list)
        for machine, index in self.slab_of.items():
            by_slab[index].append(machine)
        return sorted((sorted(v) for v in by_slab.values()), key=len, reverse=True)

    def summary(self) -> dict:
        return {
            "slabs": len(self.slabs),
            "tiles": sum(s.tiles for s in self.slabs),
            "machines_on_slabs": len(self.slab_of),
        }


class _Union:
    def __init__(self, n: int) -> None:
        self.par = list(range(n))

    def find(self, a: int) -> int:
        par = self.par
        while par[a] != a:
            par[a] = par[par[a]]
            a = par[a]
        return a

    def join(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.par[rb] = ra


def _grid(points: list[tuple[float, float, float]]) -> dict[tuple[int, int], list[int]]:
    cells: dict[tuple[int, int], list[int]] = defaultdict(list)
    for i, p in enumerate(points):
        cells[(int(p[0] // _CELL), int(p[1] // _CELL))].append(i)
    return cells


def _neighbourhood(cells: dict, cx: int, cy: int) -> list[int]:
    out: list[int] = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            out += cells.get((cx + dx, cy + dy), ())
    return out


def build_structures(
    projection: dict,
    link_xy: float = LINK_XY,
    link_z: float = LINK_Z,
) -> Structures:
    """Group foundations into slabs and assign each machine to the one beneath it."""
    tiles: list[tuple[float, float, float]] = []
    walkways: list[tuple[float, float, float]] = []
    for piece in saverows.iter_structures(projection):
        # ``""`` for a class index the table cannot resolve: the two tests below are
        # substring tests, and a piece with no name is neither a foundation nor a bridge.
        cls = piece.cls or ""
        point = (piece.x, piece.y, piece.z)
        if any(k in cls for k in _FOUNDATION):
            tiles.append(point)
        elif any(k in cls for k in _BRIDGE):
            walkways.append(point)

    if not tiles:
        return Structures()

    # Bridge pieces join the union as nodes of their own rather than as a post-pass, so a
    # CHAIN of them spans a gap no single piece could.
    nodes = tiles + walkways
    cells = _grid(nodes)
    union = _Union(len(nodes))
    for (cx, cy), members in cells.items():
        near = _neighbourhood(cells, cx, cy)
        for i in members:
            a = nodes[i]
            for j in near:
                if j <= i:
                    continue
                b = nodes[j]
                if math.dist(a[:2], b[:2]) > link_xy:
                    continue
                # Same level (touching faces) or one directly above the other.
                if abs(a[2] - b[2]) <= link_z:
                    union.join(i, j)

    grouped: dict[int, list[int]] = defaultdict(list)
    for i in range(len(tiles)):
        grouped[union.find(i)].append(i)

    slabs: list[Slab] = []
    tile_slab: dict[int, int] = {}
    for members in sorted(grouped.values(), key=len, reverse=True):
        xs = [tiles[i][0] for i in members]
        ys = [tiles[i][1] for i in members]
        zs = [tiles[i][2] for i in members]
        index = len(slabs)
        slabs.append(
            Slab(
                index=index,
                tiles=len(members),
                centre=(sum(xs) / len(xs), sum(ys) / len(ys), sum(zs) / len(zs)),
                extent=(max(xs) - min(xs), max(ys) - min(ys)),
                z_span=(min(zs), max(zs)),
                bbox=(min(xs), min(ys), max(xs), max(ys)),
            )
        )
        for i in members:
            tile_slab[i] = index

    tile_cells = _grid(tiles)
    slab_of: dict[str, int] = {}
    for key in ("machines", "extractors", "generators"):
        for record in projection.get(key, ()):
            pos = record.get("pos")
            if not pos:
                continue
            name = record["instance"].rsplit(".", 1)[-1]
            cx, cy = int(pos[0] // _CELL), int(pos[1] // _CELL)
            best, best_d = None, STAND_ON
            for i in _neighbourhood(tile_cells, cx, cy):
                t = tiles[i]
                # The tile must be UNDER the machine: a machine on an upper floor
                # would otherwise claim the ground-level slab it happens to sit above.
                if not (-STAND_ON <= pos[2] - t[2] <= 1200.0):
                    continue
                d = math.dist(t[:2], pos[:2])
                if d < best_d:
                    best, best_d = i, d
            if best is not None:
                slab_of[name] = tile_slab[best]

    return Structures(slabs=slabs, slab_of=slab_of)
