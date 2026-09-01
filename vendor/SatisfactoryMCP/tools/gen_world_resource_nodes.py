"""Generate data/world_resource_nodes.json from the installed game's own map package.

    uv run --extra gen python tools/gen_world_resource_nodes.py

Every resource-node actor the world level places -- plain nodes, well satellites, geysers
and fracking cores -- with its class, resource, purity, world position, and for a
satellite the core it draws from. This is the authoritative node SET the rest of the tree
projects from: ``tools/gen_resource_nodes.py`` cuts the served table out of it, the region
layer's land mask and the map sheet's calibration project its positions, and the
heightmap's validation gate reads its ``z``.

Only ``Persistent_Level.umap`` places these four classes in this build -- a fact about the
build, not about the format -- so every run sweeps the whole ``GameLevel01`` package list
and refuses to write if a placement turns up in a streamed cell. The emitted ``_meta``
carries the rest: how each field is read, the deposit exclusion, the licence, and the
record of the retired third-party table under ``retired_mit_table``.
"""

from __future__ import annotations

import collections
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from satisfactory_mcp.core.gameassets.iostore import IoStore, oodle_decompress
from satisfactory_mcp.core.gameassets.levels import LEVEL_SUFFIX, level_paths, walk_levels
from satisfactory_mcp.core.gameassets.packages import (
    AssetIndex,
    ClassFacts,
    PackageView,
    ScriptObjects,
    class_name_of,
    root_component,
    world_transform,
)
from satisfactory_mcp.core.gameassets.provenance import InstallNotFound, installed_build

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools._common import base_parser, require_gen

WORLD_PREFIX = "Map/GameLevel01"
PERSISTENT_LEAF = "Persistent_Level.umap"

#: A deposit is counted every run but never emitted, so its exclusion stays a decision
#: rather than a blind spot.
EMITTED = (
    "BP_ResourceNode_C",
    "BP_FrackingSatellite_C",
    "BP_ResourceNodeGeyser_C",
    "BP_FrackingCore_C",
)
DEPOSIT = "BP_ResourceDeposit_C"

#: ``mPurity`` FName -> the vocabulary every consumer speaks. UE omits a property equal to
#: its class default and the default is ``RP_Normal``, so absence decodes to ``normal``
#: and ``RP_Normal`` itself never appears on a placed instance.
PURITY = {None: "normal", "RP_Normal": "normal", "RP_Inpure": "impure", "RP_Pure": "pure"}

#: Decimal places of a centimetre. The composed float32 transforms are not meaningful past
#: this, and a fixed rounding keeps regeneration diffs readable.
ROUND = 4

#: The retirement record for ``data/world_resource_nodes.mit.json``, deleted in the same
#: commit that first generated this file. Transcribed, not recomputed -- the file it was
#: measured against is gone -- so no run gates on it. ``tests/test_nodes_provenance.py``
#: pins these figures.
RETIRED_MIT_TABLE = {
    "what": (
        "data/world_resource_nodes.mit.json: 626 resource-node rows vendored from "
        "rockfactory/satisfactory-logistics (MIT, Copyright (c) 2024 Leonardo Ascione), "
        "itself an FModel dump of this same Persistent_Level.umap, cut from a 2024 build. "
        "Deleted in the same commit as this file's first generation; every consumer reads "
        "this first-party extraction instead, so no third-party data and no attribution "
        "obligation remains."
    ),
    "parity_measured": "2026-07-30, this extraction against the MIT rows, on the build below",
    "rows": {"mit": 626, "this_extraction_plus_the_deposit": 626, "shared_ids": 625},
    "composition": (
        "identical on both sides: 459 BP_ResourceNode_C, 118 BP_FrackingSatellite_C, "
        "31 BP_ResourceNodeGeyser_C, 17 BP_FrackingCore_C, and the persistent level's one "
        "BP_ResourceDeposit_C (a row there, out of scope here)"
    ),
    "purity": "equal on all 625 shared ids",
    "resource": (
        "equal on all 594 comparable ids; the 31 geysers are not comparable because the "
        "asset carries no mResourceClass and the MIT rows labelled them with the synthetic "
        "Desc_GeothermalEnergy_C"
    ),
    "positions": {
        "within_the_mit_files_whole_centimetre_rounding": 600,
        "max_delta_inside_that_floor_cm": 0.83,
        "moved_since_the_mit_extraction": (
            "25 rows, 9.54-80.38 cm apart, every one vertical -- the horizontal component "
            "is at most 0.64 cm, i.e. rounding -- because the game moved these nodes in a "
            "map update after the MIT set was cut. The same 25 rows and the same 80.38 cm "
            "maximum had already been measured against saveVersion 60 save actors while "
            "the MIT table was current, so the disagreement is dated game movement, and "
            "this extraction is authoritative."
        ),
        "moved_rows_dz_cm": {
            "BP_ResourceNode143_1543": -80.38,
            "BP_ResourceNode124_5785": -50.31,
            "BP_ResourceNode137_2248": 40.25,
            "BP_ResourceNode586": 40.14,
            "BP_ResourceNode566": -40.0,
            "BP_ResourceNode553": 30.43,
            "BP_ResourceNode556": 30.32,
            "BP_ResourceNode40": -30.25,
            "BP_ResourceNode469": -30.23,
            "BP_ResourceNode12_91": -30.11,
            "BP_ResourceNode466": 30.06,
            "BP_ResourceNode229": 30.02,
            "BP_ResourceNode545": 29.88,
            "BP_ResourceNode550": 29.82,
            "BP_ResourceNode464_UAID_40B076DF2F7914E201_2026233335": 29.68,
            "BP_ResourceNode486": 29.52,
            "BP_ResourceNode53_510": -20.32,
            "BP_ResourceNode442": 20.14,
            "BP_ResourceNode85": 20.07,
            "BP_ResourceNode144_1644": 19.98,
            "BP_ResourceNode620": -19.57,
            "BP_ResourceNode573_UAID_40B076DF2F7983E001_1840982787": -19.54,
            "BP_ResourceNode464_UAID_40B076DF2F790EE201_1850696287": -10.12,
            "BP_ResourceNode441": 10.03,
            "BP_ResourceNode554": 9.54,
        },
    },
    "renamed": {
        "mit": "BP_ResourceNode11",
        "now": "BP_ResourceNode20_UAID_04D9F5D42711A7C902_1245462149",
        "apart_cm": 150.12,
        "what": (
            "a pure Limestone node the game renamed and moved between the MIT set's build "
            "and this one -- the same rename every saveVersion 52 vs 60 save pair on this "
            "machine shows"
        ),
    },
}


def sweep(store: IoStore, scripts: ScriptObjects) -> tuple[dict, dict, PackageView, str]:
    """Count the emitted classes over every world package; hand back the persistent level.

    Returns ``(per-package counts for packages placing any counted class, the world-wide
    class census, the persistent level's view, its container path)``. The view comes out
    of the same walk that proves it is the only package that matters, so the proof and the
    read cannot diverge.
    """
    counted = set(EMITTED) | {DEPOSIT}
    per_package: dict[str, collections.Counter] = {}
    census: collections.Counter = collections.Counter()
    persistent: PackageView | None = None
    persistent_path: str | None = None
    failures: collections.Counter = collections.Counter()

    def unreadable(_path: str, exc: Exception) -> None:
        failures[type(exc).__name__] += 1

    paths = level_paths(store, contains=WORLD_PREFIX)
    started = time.time()
    for number, total, path, view in walk_levels(
        store, scripts, paths=paths, on_unreadable=unreadable
    ):
        here: collections.Counter = collections.Counter()
        for export in view.exports:
            slot = export["slot"]
            if view.outer_of.get(slot) not in view.level_slots:
                continue
            cls = class_name_of(view.class_of.get(slot))
            if cls in counted:
                here[cls] += 1
        if here:
            per_package[path.rsplit("/", 1)[-1]] = here
            census.update(here)
        if path.endswith("/" + PERSISTENT_LEAF):
            persistent = view
            persistent_path = path
        if number and number % 1000 == 0:
            print(f"  {number:>5}/{total} packages  {time.time() - started:>5.1f}s", flush=True)
    if failures:
        raise SystemExit(f"unreadable world packages, so the census is not a proof: {failures}")
    if persistent is None or persistent_path is None:
        raise SystemExit(f"no {PERSISTENT_LEAF} under {WORLD_PREFIX} -- container layout moved")

    outside = {
        leaf: {cls: n for cls, n in here.items() if cls in EMITTED}
        for leaf, here in per_package.items()
        if leaf != PERSISTENT_LEAF and any(cls in EMITTED for cls in here)
    }
    if outside:
        raise SystemExit(
            "emitted classes are placed outside the persistent level, so a one-package "
            f"read is incomplete from this build on: {outside}"
        )
    return per_package, dict(census), persistent, persistent_path


def other_levels(store: IoStore, scripts: ScriptObjects) -> list[dict]:
    """The container's non-world levels, swept with the identical rule.

    The developer test map places resource nodes too, so a count from outside GameLevel01
    is recorded rather than treated as a contradiction.
    """
    counted = set(EMITTED) | {DEPOSIT}
    out: list[dict] = []
    for path in sorted(
        p for p in store.by_path if p.endswith(LEVEL_SUFFIX) and WORLD_PREFIX not in p
    ):
        entry: dict = {"package": path.rsplit("/", 1)[-1]}
        try:
            view = PackageView(store.read_path(path), scripts)
            counts = collections.Counter(
                class_name_of(view.class_of.get(export["slot"]))
                for export in view.exports
                if view.outer_of.get(export["slot"]) in view.level_slots
            )
        except Exception as exc:
            entry["read"] = f"failed: {type(exc).__name__}"
            out.append(entry)
            continue
        entry["actors"] = sum(counts.values())
        entry["of_a_counted_class"] = {
            cls: n for cls, n in sorted(counts.items()) if cls in counted
        }
        out.append(entry)
    return out


def read_rows(view: PackageView, classes: ClassFacts) -> list[dict]:
    """Every emitted actor of the persistent level as a finished row, or die saying why."""
    rows: list[dict] = []
    core_names: dict[int, str] = {}
    resource_of_core: dict[str, str | None] = {}
    problems: list[str] = []

    actors = [
        (export, class_name_of(view.class_of.get(export["slot"])))
        for export in view.exports
        if view.outer_of.get(export["slot"]) in view.level_slots
    ]
    for export, cls in actors:
        if cls == "BP_FrackingCore_C":
            core_names[export["slot"]] = export["name"]

    for export, cls in actors:
        if cls not in EMITTED:
            continue
        slot = export["slot"]
        name = export["name"]
        props = view.props(slot)

        raw = props.get("mPurity")
        purity_name = view._fname(raw) if raw is not None else None
        if purity_name not in PURITY:
            problems.append(f"{name}: unknown mPurity value {purity_name!r}")
            continue

        resource_path = (
            view.import_path(props["mResourceClass"]) if "mResourceClass" in props else None
        )
        resource = class_name_of(resource_path) if resource_path else None
        if cls == "BP_ResourceNodeGeyser_C":
            if resource is not None:
                problems.append(f"{name}: a geyser carrying mResourceClass ({resource}) is new")
        elif resource is None:
            problems.append(f"{name}: no readable mResourceClass")

        core = None
        if cls == "BP_FrackingSatellite_C":
            ref = view.export_ref(props.get("mCore", b""))
            core = core_names.get(ref) if ref is not None else None
            if core is None:
                problems.append(f"{name}: mCore resolves to no BP_FrackingCore_C export")

        root = root_component(view, slot)
        transform = world_transform(view, root, classes)[0] if root is not None else None
        if transform is None:
            problems.append(f"{name}: no composable root-component transform")
            continue

        row = {
            "id": name,
            "class": cls,
            "resource": resource,
            "purity": PURITY[purity_name],
            "x": round(transform[0][0], ROUND),
            "y": round(transform[0][1], ROUND),
            "z": round(transform[0][2], ROUND),
        }
        if cls == "BP_FrackingCore_C":
            resource_of_core[name] = resource
        if core is not None:
            row["core"] = core
        rows.append(row)

    # A core and its satellites tap one deposit, so a resource disagreement across the
    # link is a misread rather than a curiosity.
    referenced = {row["core"] for row in rows if "core" in row}
    for row in rows:
        if row["class"] == "BP_FrackingCore_C" and row["id"] not in referenced:
            problems.append(f"{row['id']}: a fracking core no satellite references")
        if "core" in row and row["resource"] != resource_of_core.get(row["core"]):
            problems.append(
                f"{row['id']}: satellite says {row['resource']}, its core "
                f"{row['core']} says {resource_of_core.get(row['core'])}"
            )

    if problems:
        for p in problems:
            print("  PROBLEM:", p)
        raise SystemExit(f"{len(problems)} problem(s); refusing to write a partial table")
    rows.sort(key=lambda r: r["id"])
    return rows


def main() -> int:
    parser = base_parser("Generate data/world_resource_nodes.json from the installed game.")
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "data" / "world_resource_nodes.json",
        help="where to write the table",
    )
    args = parser.parse_args()
    versions = require_gen("ooz")

    try:
        build_pin, _raw = installed_build(args.game)
    except InstallNotFound as exc:
        print(f"{exc}\nPass --game if the install is somewhere else.")
        return 1
    paks = args.game / "FactoryGame" / "Content" / "Paks"
    if not (paks / "FactoryGame-Windows.utoc").exists():
        print(f"no FactoryGame-Windows.utoc under {paks}")
        return 1
    print(f"installed build: {build_pin}")
    print(f"reading the game's own assets from {paks} with pyooz {versions['pyooz']}")

    store = IoStore(paks, "FactoryGame-Windows", oodle_decompress)
    scripts = ScriptObjects(paks, oodle_decompress)
    classes = ClassFacts(store, AssetIndex(store))

    per_package, census, persistent, persistent_path = sweep(store, scripts)
    rows = read_rows(persistent, classes)
    side_levels = other_levels(store, scripts)

    by_class = collections.Counter(r["class"] for r in rows)
    by_purity = collections.Counter(r["purity"] for r in rows)
    satellites = [r for r in rows if r["class"] == "BP_FrackingSatellite_C"]
    cores = {r["id"] for r in rows if r["class"] == "BP_FrackingCore_C"}
    deposits_in_cells = census.get(DEPOSIT, 0) - per_package.get(PERSISTENT_LEAF, {}).get(
        DEPOSIT, 0
    )

    out = {
        "_meta": {
            "description": (
                "Every resource-node actor the game's world level places: class, resource, "
                "purity, world position, and the satellite -> fracking-core link. The "
                "authoritative first-party node set that data/resource_nodes.json, the "
                "region layer's land mask, the map sheet's calibration and the heightmap's "
                "validation gate all project from."
            ),
            "licence": (
                "First-party. Identifiers, classes, purities and coordinates are facts "
                "about Coffee Stain's map, read from the reader's own installed copy of "
                "the game by tools/gen_world_resource_nodes.py. No third-party table "
                "contributed to this file, in any form; no external licence and no "
                "attribution obligation attaches. The MIT-licensed table this replaced is "
                "retired and deleted -- see retired_mit_table."
            ),
            "source": {
                "container": "FactoryGame-Windows.utoc/.ucas",
                "package": persistent_path.lstrip("./"),
                "read_by": "satisfactory_mcp.core.gameassets (iostore, packages, levels)",
                "method": (
                    "exports whose Outer is the package's /Script/Engine.Level export; "
                    "resource from the mResourceClass ObjectProperty via the import map; "
                    "purity from the mPurity ByteProperty FName (absent means the class "
                    "default, normal); the well link from the satellite's mCore "
                    "ObjectProperty, an export reference in the same package; positions "
                    "from the composed root-component world transform"
                ),
                "decoder": versions,
            },
            "game_version_pinned": build_pin,
            "generated": datetime.now(UTC).date().isoformat(),
            "count": len(rows),
            "by_class": dict(sorted(by_class.items())),
            "by_purity": dict(sorted(by_purity.items())),
            "coverage": {
                "world_level": WORLD_PREFIX,
                "packages_swept": len(level_paths(store, contains=WORLD_PREFIX)),
                "emitted_classes_outside_the_persistent_level": 0,
                "note": (
                    "all four emitted classes are placed exclusively by the persistent "
                    "level; the streamed cell packages place none -- measured this run "
                    "over every world package, and the run refuses to write when that "
                    "stops holding, so the one-package read cannot go quietly incomplete"
                ),
                "other_levels_in_the_container": side_levels,
            },
            "deposits": {
                "why_no_rows": (
                    "BP_ResourceDeposit_C is hand-mineable only -- no extractor can be "
                    "placed on one -- so a deposit row would advertise capacity that "
                    "cannot be built"
                ),
                "placed_by_the_persistent_level": per_package.get(PERSISTENT_LEAF, {}).get(
                    DEPOSIT, 0
                ),
                "placed_by_the_streamed_cells": deposits_in_cells,
            },
            "geysers": (
                "carry no mResourceClass -- a geyser is a placement target for the "
                "Geothermal Generator, not an item -- so resource is null here and "
                "consumers label it synthetically"
            ),
            "well_links": {
                "satellites": len(satellites),
                "cores": len(cores),
                "statement": (
                    "total on both sides and resource-consistent: every satellite carries "
                    "mCore, every core is referenced, and every satellite names its "
                    "core's resource -- re-checked every run, the run dies otherwise"
                ),
            },
            "not_carried": (
                "the retired MIT rows also carried a yaw rotation and a display name; no "
                "consumer ever read either, so neither is a field here"
            ),
            "join_key": (
                "id; a save actor's instanceName is 'Persistent_Level:PersistentLevel.' "
                "plus this, byte for byte"
            ),
            "units": "centimetres; north is -Y, east is +X, up is +Z",
            "retired_mit_table": RETIRED_MIT_TABLE,
        },
        "nodes": rows,
    }

    args.out.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {args.out.relative_to(ROOT)}  {len(rows)} rows  {args.out.stat().st_size} B")
    print("by class:", dict(sorted(by_class.items())))
    print("by purity:", dict(sorted(by_purity.items())))
    print(
        f"deposits: {out['_meta']['deposits']['placed_by_the_persistent_level']} in the "
        f"persistent level, {deposits_in_cells} in the streamed cells, 0 emitted"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
