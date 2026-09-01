"""What the map placed, what this save took, and which rows answer the question asked.

Validating the mode, un-retiring a group, refusing the modes the map table is required for,
resolving an origin, picking and sorting the rows, dropping the pedestals and tallying the
observed states are all the decision of *which* placements answer the question, which is a
domain decision -- so it happens here and hands the presenter a finished view. Every refusal
is a string on the view rather than an early return of formatted text, because the caller
that renders is not always the caller that decides.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..spatial.origin import resolve_origin
from ..world.state import WorldState
from .table import CollectiblesUnreadable, CollectibleTable, load_collectibles

__all__ = ["GENERATOR_COMMAND", "RETIRED_GROUPS", "CollectiblesView", "collect_view"]

#: What to run when the table is not there. Said in full, because "regenerate it" is not a
#: command and the reader is an assistant relaying it to somebody at a prompt.
GENERATOR_COMMAND = "uv run python tools/gen_world_collectibles.py"

#: Group names this tool used to accept, and what to say instead. Every one of them was a
#: name-prefix bucket; the map's placement table resolves the classes those buckets guessed
#: at, so the answer is a rename in some cases and "that is not a collectible" in others.
RETIRED_GROUPS: dict[str, str] = {
    "slug_blue": "power_slug_blue",
    "slug_yellow": "power_slug_yellow",
    "slug_purple": "power_slug_purple",
    "artifact_unsplit": (
        "the map resolves every glued BP_WAT name, so nothing is unsplit any more: "
        "ask for 'somersloop' or 'mercer_sphere'"
    ),
    "flora": (
        "'flora' mixed four classes. The mushroom is 'mushroom'; berry and nut bushes "
        "REGROW and are deliberately untracked; a spore flower is a hazard, not a pickup"
    ),
    "debris": "crash-site scenery is not a collectible -- see the unresolved rows in the census",
    "crash_site": (
        "the pod itself is 'crashed_drop_pod'; the ship and debris beside it are scenery, "
        "and the parts scattered around it are 'loot_cache'"
    ),
    "dropped_pickup": (
        "a map-placed cache is 'loot_cache'; a pickup the PLAYER dropped is not map-placed "
        "at all and appears among the unresolved rows"
    ),
}


@dataclass
class CollectiblesView:
    """Which placements answer the question, and everything needed to say why."""

    #: The validated mode: census, collected, remaining or nearest.
    mode: str
    #: The category asked for, already folded and un-retired. ``None`` means all of them.
    group: str | None = None
    #: A refusal, already carrying its leading ``!``. Nothing else on the view is populated.
    error: str | None = None
    removed: dict = field(default_factory=dict)
    #: ``None`` when ``data/world_collectibles.json`` has never been generated.
    table: CollectibleTable | None = None
    #: Listing rows, sorted for the mode and distance-annotated when mode=nearest. ``None``
    #: for the census, which counts off ``removed`` instead.
    rows: list[dict] | None = None
    #: Pedestal rows dropped from an unfiltered listing.
    hidden: int = 0
    #: Every category that is the base another one stands on.
    pedestals: list[str] = field(default_factory=list)
    #: Observed state -> how many listing rows are in it.
    counts: dict[str, int] = field(default_factory=dict)
    #: Where distances are measured from, in centimetres, and what to call it.
    origin: tuple[float, float] | None = None
    where: str = ""
    #: True when there is no map table and only the save's own degraded census is possible.
    save_only: bool = False


def collect_view(
    st: WorldState, group: str | None, mode: str, near: str | None
) -> CollectiblesView:
    """Pick the placements that answer one collectibles question.

    The order of the checks is the order of the refusals, and it is load-bearing: an
    unknown mode is refused before a retired group, and both before the save is asked for
    its destroyed list, so a caller who got two things wrong is told about the first one.
    """
    wanted = (mode or "census").strip().casefold()
    if wanted not in ("census", "collected", "remaining", "nearest"):
        return CollectiblesView(
            mode=wanted,
            error=f"! unknown mode {mode!r}. Choose from: census, collected, remaining, nearest",
        )

    table = st.collectibles
    if table is None:
        # ``st.collectibles`` degrades silently for both, and the two need different
        # answers: a fresh clone has never generated the file and a half-written one has
        # to be deleted first. Asking again strictly is what separates them.
        try:
            load_collectibles(strict=True)
        except CollectiblesUnreadable as exc:
            return CollectiblesView(
                mode=wanted,
                group=group,
                error=(
                    f"! the map's placement table is CORRUPT, not missing: {exc}. Delete it "
                    f"and run {GENERATOR_COMMAND} -- until then nothing here knows how many "
                    "collectibles exist or where they are"
                ),
            )
    if group:
        # Category names are lowercase snake_case, so folding the argument is a
        # normalisation and not a guess. A retired bucket is renamed where the map has the
        # same thing under a new name, and refused where it does not.
        group = group.strip().casefold()
        hint = RETIRED_GROUPS.get(group)
        if hint and table is not None and hint in table.by_category:
            group = hint
        elif hint and table is not None:
            return CollectiblesView(
                mode=wanted,
                group=group,
                error=f"! '{group}' is no longer a category: {hint}",
            )

    if table is None and wanted in ("remaining", "nearest"):
        # An explicit refusal, not the census: answering a narrower question than the one
        # asked would teach the caller that the argument worked.
        return CollectiblesView(
            mode=wanted,
            group=group,
            error=(
                f"! mode={wanted!r} needs the map's own placement table and "
                "data/world_collectibles.json has never been generated, so nothing here "
                "knows how many collectibles exist or where they are. Only mode=census and "
                f"mode=collected work from a save alone. Generate it with {GENERATOR_COMMAND}"
            ),
        )

    removed = st.removed_actors(group)
    if "error" in removed:
        return CollectiblesView(mode=wanted, group=group, error="! " + removed["error"])

    view = CollectiblesView(
        mode=wanted,
        group=group,
        removed=removed,
        table=table,
        save_only=table is None,
    )
    if table is None or wanted == "census":
        return view

    origin = None
    where = ""
    if wanted == "nearest":
        if near:
            try:
                origin, where = resolve_origin(st, near)
            except ValueError as exc:
                return CollectiblesView(mode=wanted, group=group, error=f"! {exc}")
        else:
            here = st.player_position()
            if here is None:
                return CollectiblesView(
                    mode=wanted,
                    group=group,
                    error=(
                        "! mode='nearest' needs an origin and this save has no player pawn: "
                        "pass near='x,y' in metres or a named factory"
                    ),
                )
            origin, where = (here[0], here[1]), "you"
        rows = st.nearest_placements(origin, group)
    elif wanted == "remaining":
        rows = st.placements(group, remaining_only=True)
        rows.sort(key=lambda r: (r["category"], r["name"]))
    else:
        rows = [p for p in st.placements(group) if p["collected"]]
        rows.sort(key=lambda r: (r["category"], r["name"]))

    # A shrine is the base its artifact stands on, so an unfiltered listing would show it as
    # a second row a metre from the sphere -- the double-count the census warns about, in
    # listing form. Dropped only when no group was asked for: group='mercer_shrine' means the
    # caller wants the pedestals.
    pedestals = sorted({c for c in table.by_category if table.pedestal_of(c)})
    hidden = 0
    if group is None:
        before = len(rows)
        rows = [r for r in rows if r["category"] not in pedestals]
        hidden = before - len(rows)

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["observed"] or "collected"] = counts.get(r["observed"] or "collected", 0) + 1

    view.rows = rows
    view.hidden = hidden
    view.pedestals = pedestals
    view.counts = counts
    view.origin = origin
    view.where = where
    return view
