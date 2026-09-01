"""Where "near" points: a coordinate, the player, a factory, a conduit run, a platform.

Lives with the map code rather than with any one tool group because the map tools and
the node tools both ask the same question.
"""

from __future__ import annotations

from . import geo

#: The conduit-run spelling this project settles on, everywhere: ``chain:<n>`` for a belt
#: chain and ``pipe:<n>`` for a pipeline piece, which is the ident ``search_conduits``
#: prints in its own id and connects columns. The web map's "pipe #12" is a caption.
RUN_PREFIXES = ("chain", "pipe")

#: Foundation platforms, by the index ``factory_map show=slabs`` prints.
SLAB_PREFIX = "slab"


def player_xy(st) -> tuple[float, float] | None:
    """Player XY for the near:me selector, or None if the save has no pawn."""
    here = st.player_position() if st else None
    return (here[0], here[1]) if here else None


def _run_origin(st, text: str) -> tuple[tuple[float, float], str]:
    """Centre on a belt chain or pipe piece, by the ident ``search_conduits`` prints.

    Its MIDPOINT, so a radius around it reaches both ways along the run; the answer names
    the run's own length, because a radius smaller than that only sees part of it.
    """
    if st is None:
        raise ValueError(f"{text!r} names a conduit run, which needs a readable save")
    want = text.casefold()
    for run in st.conduit_runs:
        if run.ident.casefold() == want:
            return run.midpoint(), f"{run.ident} (midpoint of a {run.length_m:.0f}m {run.label})"
    raise ValueError(f"no conduit run called {text!r}; search_conduits lists the ids it takes")


def _slab_origin(st, text: str) -> tuple[tuple[float, float], str]:
    """Centre on a foundation platform, by the index ``factory_map show=slabs`` prints.

    The tile MEAN the slab carries, not its bbox centre: an L-shaped platform's bbox
    centre is a spot with no floor on it. A platform with no machines on it resolves like
    any other -- a bare slab is exactly the thing this answers questions about.
    """
    if st is None:
        raise ValueError(f"{text!r} names a platform, which needs a readable save")
    try:
        index = int(text.partition(":")[2])
    except ValueError:
        raise ValueError(
            f"{text!r} needs an integer index, the one factory_map show=slabs prints"
        ) from None
    slabs = st.structures.slabs
    if not 0 <= index < len(slabs):
        raise ValueError(f"slab:{index} out of range (0..{len(slabs) - 1})")
    slab = slabs[index]
    width, depth = slab.extent
    return (slab.centre[0], slab.centre[1]), (
        f"slab:{index} ({slab.tiles} tiles, {width / 100:.0f}x{depth / 100:.0f}m, "
        f"{slab.storeys} storey(s))"
    )


def resolve_origin(st, near: str) -> tuple[tuple[float, float], str]:
    """Resolve a location: "x,y" in metres, "me", a factory, a conduit run, or a slab.

    A factory name is the useful one now that factories exist -- "nearest coal to the
    coal powerplant" is the question actually being asked, and hand-copying a centroid
    out of another tool's output is how the wrong coordinate gets used. A run ident --
    ``chain:7``, ``pipe:333`` -- and a platform index -- ``slab:12`` -- close the same
    loop for two more sets of ids that other tools print and nothing would take back.
    """
    text = near.strip()
    head = text.partition(":")[0].casefold()
    if ":" in text and head in RUN_PREFIXES:
        return _run_origin(st, text)
    if ":" in text and head == SLAB_PREFIX:
        return _slab_origin(st, text)
    if "," in text:
        try:
            x_m, y_m = (float(v) for v in text.split(",", 1))
        except ValueError as exc:
            raise ValueError(f"{near!r} is not an x,y pair in metres") from exc
        return (x_m * 100.0, y_m * 100.0), f"{int(x_m)},{int(y_m)}"

    if text.casefold() in ("me", "player", "here"):
        here = player_xy(st)
        if here is None:
            raise ValueError("this save has no player pawn, so 'me' cannot be resolved")
        return here, "you"

    label = st.labels.find(text) if st else None
    if label is None:
        known = ", ".join(x.name for x in st.labels.labels) if st else ""
        raise ValueError(
            f"{near!r} is neither an x,y pair, 'me', nor a named factory"
            + (f". Named: {known}" if known else "")
        )
    pos = {}
    for key in ("machines", "extractors", "generators"):
        for record in st.projection.get(key, ()):
            if record.get("pos"):
                pos[record["instance"].rsplit(".", 1)[-1]] = record["pos"]
    points = [pos[m][:2] for m in label.anchors if m in pos]
    if not points:
        raise ValueError(f"{label.name!r} has no machines left to centre on")
    return geo.centroid(points), label.name
