"""One walk over the world's cooked level packages, for the generators that need it.

Two generators sweep the same 4,521 ``.umap`` packages of ``GameLevel01`` for two entirely
different things -- ``gen_world_collectibles`` wants the actors, ``gen_world_heightmap``
wants the landscape components and the static meshes -- and both used to open with their
own copy of the same six lines: filter the container's paths, sort them, read each one,
turn it into a ``PackageView``, count the ones that will not parse, carry on.

The copies had already diverged in ways that were invisible from either side. One selected
on ``"Map/GameLevel01"`` over the path KEYS, the other on ``"/GameLevel01/"`` over the path
VALUES -- which happen to give the identical sorted list of 4,521 today, measured, and are
two different questions that would stop agreeing the moment the container gained a second
mount or a duplicate entry. One recorded the exception type of a package it could not read,
the other only that there had been one.

So the walk lives here and the interpretation stays in the generators. This module decides
which packages exist and hands them over parsed; it has no opinion about what is in them,
which is the same division ``packages`` itself keeps.

**Byte-identity matters here more than anywhere else in the tree.** These two generators
write pinned artifacts, and their output is checked by regenerating and hashing. The order
this yields in is therefore part of the contract and not an implementation detail: sorted
by container path, ascending, exactly as both copies did.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

from .iostore import IoStore
from .packages import PackageView, ScriptObjects

__all__ = ["LEVEL_SUFFIX", "WORLD_LEVEL_DIR", "level_paths", "walk_levels"]

#: What a level package is called. The world's geometry, its actors and its landscape all
#: arrive in these; ``.uasset`` is everything else and is read by class, not by sweep.
LEVEL_SUFFIX = ".umap"

#: The one world. A substring rather than a prefix because the container path carries a
#: mount point in front of it that is not this module's business.
WORLD_LEVEL_DIR = "Map/GameLevel01"


def level_paths(
    store: IoStore, *, contains: str = WORLD_LEVEL_DIR, suffix: str = LEVEL_SUFFIX
) -> list[str]:
    """Every level package of one world, sorted -- the sweep order both generators bank on.

    Over ``by_path``, which is keyed by path and therefore names each package once. The
    other spelling, ``paths.values()``, yields one entry per container ENTRY, so two
    entries pointing at one path would sweep it twice and count it twice. Measured on the
    installed build the two agree exactly (4,521 either way, no duplicates), which is what
    makes this a simplification rather than a change.
    """
    return sorted(p for p in store.by_path if p.endswith(suffix) and contains in p)


def walk_levels(
    store: IoStore,
    scripts: ScriptObjects,
    *,
    paths: list[str] | None = None,
    contains: str = WORLD_LEVEL_DIR,
    suffix: str = LEVEL_SUFFIX,
    on_unreadable: Callable[[str, Exception], None] | None = None,
) -> Iterator[tuple[int, int, str, PackageView]]:
    """Yield ``(index, total, path, view)`` for every readable level package, in order.

    ``index`` and ``total`` are handed out rather than left to the caller to count because
    both callers print progress off them and neither wants to hold the path list as well.
    ``index`` counts packages VISITED, including the unreadable ones, so a progress line
    stays a fraction of the whole sweep.

    A package that will not parse is skipped and reported to ``on_unreadable``, never
    raised: one unreadable package out of 4,521 must not cost the other 4,520, and it is
    the caller that knows whether to count it, name its exception type, or refuse the run.
    Passing nothing means the caller has decided a failure is not worth recording, which
    is a decision and not the default -- there is no silent path through here that does not
    go through an argument somebody wrote.

    ``paths`` is for the caller that needs the total BEFORE the sweep -- one of them
    records "packages read" in its artifact, and deriving that from what the sweep yielded
    would report 4,520 for a world where one package failed, which is a different claim.
    Passing it skips the listing rather than repeating it.
    """
    if paths is None:
        paths = level_paths(store, contains=contains, suffix=suffix)
    total = len(paths)
    for index, path in enumerate(paths):
        try:
            view = PackageView(store.read_path(path), scripts)
        except Exception as exc:
            if on_unreadable is not None:
                on_unreadable(path, exc)
            continue
        yield index, total, path, view
