"""Generate data/world_collectibles.json from the game's own map assets plus the saves.

    uv run --extra gen python tools/gen_world_collectibles.py <saves dir>

Power slugs, somersloops, Mercer spheres, crashed drop pods and the loot caches strewn round
the crash sites are placed by the map, not by the player, so a save cannot record them by
listing what exists -- it records the **negative**: which map-placed actors are gone. "250
slugs collected" has no denominator in a save. The denominator comes out of the cooked
world instead -- 4,521 packages under ``Map/GameLevel01``, one per world-partition cell plus
the persistent level -- and the saves are asked only for status.

An actor is any export whose Outer is the package's ``/Script/Engine.Level`` export. That
structural rule is what reaches the natively classed actors: a native class is not a path
but an ``FPackageObjectIndex`` script import, a 62-bit hash of the object path resolved
through ``global.utoc``'s ScriptObjects chunk, so no ``/Game/`` test can match one.

**Three states, because two would lie.**

* ``collected`` -- the newest save's destroyed-actor list names this (cell, instance).
* ``present``   -- the newest save has a live actor header at this key whose position agrees
  with the map's to within ``POSITION_TOLERANCE_CM``.
* ``unknown``   -- neither. The game has never had that actor loaded while saving, so
  nothing on disk says whether it is still standing. All three are printed per category, so
  no two of them can be added up into the third.

The newest save alone decides, and that it can is measured rather than assumed: the union
over every save of the session resolves no row it cannot, reported as
``rows_only_older_saves_could_state``. The older saves are read as evidence for
``_meta.respawn``, which re-tests the premise underneath the whole file -- that a collected
thing stays gone -- by asking whether a key ever leaves a destroyed list, and whether a save
calls a key destroyed while a later one holds a live actor at it.

**The key is (cell, instance)**, unique over every map-placed actor in ``GameLevel01``; the
bare instance name is not. Between saveVersion 52 and 60 the level was edited -- actors
renamed, moved between cells, nudged by centimetres -- and the game migrates a cell's saved
records only when that cell is next streamed, so a stale record's key can now belong to a
different map actor. A live record is therefore accepted only if its position agrees with
the map's, and the rejects are counted in ``_meta.status_evidence.live_records_displaced``.

**Two things a naive read of the assets gets wrong**, both of which move a shrine 73 cm:

* A shrine's root ``SceneComponent`` is attached to the SPHERE's root, across an actor
  boundary, so its ``RelativeLocation`` is not a world position and the chain has to be
  composed -- rotator to quaternion, parent scale applied.
* A component's transform is serialised on the placed instance only where it differs from
  its class template, and ``BP_WAT2``'s root carries ``RelativeScale3D = 2.7`` in the class,
  so the template has to be read out of the class ``.uasset``'s ``<Name>_GEN_VARIABLE``
  export. ``_meta.position_agreement`` re-measures the result against the save's own live
  actors every run.

**Hazard context is inference, and it is kept apart from the placements.** Where the game
states a radius -- a spore flower's damage sphere, a spawner's ``mSpawnRadius`` -- the
containment test uses that number and is a fact; where it does not, the row carries the
distance and no verdict, because a threshold would be this generator's opinion dressed as
data. "Is it in a cave" and "can you reach it without a jetpack" are properties of no actor
and are not derived at all (``_meta.not_derived``).

Nothing above is asserted where it can be counted: ``_meta`` carries the class census, the
respawn probe that decides which harvestable plants earn rows, the identity checks, and the
accounting that puts every map-placed class in exactly one of a category, ``_meta.excluded``
or ``_meta.not_classified``. The coordinates are facts about Coffee Stain's map read from
the installed game, with no third-party world table involved in any form.
"""

from __future__ import annotations

import collections
import itertools
import json
import math
import re
import struct
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pioneersav import (
    FIRST_MODERN_BODY,
    ActorHeader,
    ParseError,
    decompress_body,
    read_body,
    read_info_bytes,
    read_object,
)
from satisfactory_mcp.core.gameassets.iostore import IoStore, oodle_decompress
from satisfactory_mcp.core.gameassets.levels import level_paths, walk_levels
from satisfactory_mcp.core.gameassets.packages import (
    AssetIndex,
    ClassFacts,
    PackageView,
    ScriptObjects,
    class_name_of,
    read_float,
    read_vector_array,
    root_component,
    world_transform,
)
from satisfactory_mcp.core.gameassets.provenance import installed_build_from_exe

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools._common import base_parser, require_gen

#: Cooked map packages live under this prefix inside the container. That this prefix is the
#: whole world is measured rather than asserted: every other ``.umap`` in the container is
#: read with the same walk and reported, with its actor count and how many actors of an
#: emitted class it places, in ``_meta.source.placements.other_levels_in_the_container``.
MAP_PREFIX = "Map/GameLevel01"

#: The save's actor instanceName is this plus the map export's name, byte for byte, which
#: is what makes ``join_key`` exact rather than approximate.
INSTANCE_PREFIX = "Persistent_Level:PersistentLevel."

#: Map actor class -> the category this table reports. Every one is a one-shot pickup or the
#: pedestal of one: taking it removes the actor for good, so ``collected`` is a durable fact
#: about it, which ``_meta.respawn`` re-tests every run. Map-placed classes that are not here
#: are in ``EXCLUDED`` or, with their counts, in ``_meta.not_classified``.
CATEGORIES = {
    "BP_Crystal_C": "power_slug_blue",
    "BP_Crystal_mk2_C": "power_slug_yellow",
    "BP_Crystal_mk3_C": "power_slug_purple",
    "BP_WAT1_C": "somersloop",
    "BP_WAT2_C": "mercer_sphere",
    "BP_MercerShrine_C": "mercer_shrine",
    "BP_SomerSloopShrine_C": "somersloop_shrine",
    "BP_DropPod_C": "crashed_drop_pod",
    "FGItemPickup_Spawnable": "loot_cache",
    "BP_UnlockPickup_Customization_C": "customization_unlock_pickup",
    "BP_TapePickup_C": "tape_pickup",
    "BP_Shroom_01_C": "mushroom",
}

#: The three harvestable plants, probed every run for the respawn machinery -- whether the
#: class carries ``mNumRespawns`` and ``mUpdatedOnDayNr`` at all, and whether any instance's
#: counter moves. That probe is what decides which of them earn rows, rather than a sentence
#: asserting it: two regrow and are in ``EXCLUDED``, the third is a ``CATEGORIES`` row.
RESPAWN_PROBE = {"BP_BerryBush_C", "BP_NutBush_C", "BP_Shroom_01_C"}

#: The properties the probe looks for. ``mSavedNumItems`` is in the list because it is the
#: field most easily misread as a remaining count: it is the plant's fixed yield -- every nut
#: bush writes 5 -- and never a countdown.
RESPAWN_PROPERTIES = ("mNumRespawns", "mUpdatedOnDayNr", "mSavedNumItems")

#: Per-category notes worth carrying into the artifact, keyed by category.
CATEGORY_NOTES = {
    "mercer_shrine": (
        "the pedestal a Mercer sphere stands on, attached to the sphere's own root -- see "
        "attached_to. One per sphere, which totals.pedestals.mercer_shrine.one_to_one "
        "checks every run, so it is a second row about one find and not a second collectible."
    ),
    "somersloop_shrine": (
        "the pedestal under a somersloop, attached to the somersloop's root, and there are "
        "fewer of them than there are somersloops -- compare the two 'placed' counts. It is "
        "not save-serialised at all: no live header for "
        "this class appears in any save on disk and none of the destroyed-actor lists "
        "names one -- rows_any_save_mentions is 0 -- so every row is state 'unknown' by "
        "construction, and this class can be located but never state-tracked."
    ),
    "crashed_drop_pod": (
        "stays in the world after it is looted, so present-vs-collected is the wrong "
        "question for it: 'looted' is the one that matters and is emitted alongside "
        "'state'. Only a dismantled pod is destroyed. unlock_cost is the pod's own "
        "mUnlockCost -- what it wants before it gives up its hard drive."
    ),
    "loot_cache": (
        "a loose pile of manufactured parts, the FGItemPickup_Spawnable the map places at "
        "crash sites and abandoned camps. 'contents' is the actor's own mPickupItems. This "
        "is the class an earlier revision of this file missed entirely, because a native "
        "class cannot appear in a /Game/ walk. A crate the PLAYER drops is the same class "
        "and has no map row, so it is never a row here; how many such records the saves "
        "hold is counted under status_evidence.live_records_with_no_map_row rather than "
        "asserted. Some of these caches carry no _UAID_ placement id and are map placements "
        "all the same, which is why with_the_map_s_own_placement_id is reported below "
        "instead of the suffix being used as a filter."
    ),
    "tape_pickup": (
        "class path is under /Game/FactoryGame/Testing/BoomBox/. Three are placed, the "
        "saves do serialise it, and this file locates them; it does not claim to know "
        "what picking one up grants."
    ),
    "customization_unlock_pickup": (
        "a single placement. No save on disk mentions it, live or gone -- see "
        "rows_any_save_mentions -- so whether the class is save-serialised at all or the "
        "player has simply never been there is not settled here. Either way: unknown."
    ),
    "mushroom": (
        "the harvestable mushroom, and the ONE of the three harvestable plants that does "
        "not regrow -- which is why it is a row while the berry and nut bushes are in "
        "_meta.excluded. It is not asserted: _meta.respawn.flora shows BP_Shroom_01_C "
        "carrying neither mNumRespawns nor mUpdatedOnDayNr on any save record on disk, "
        "while both bushes carry both, and _meta.respawn.durability shows no mushroom key "
        "ever leaving a destroyed list or turning up live after one. So 'collected' is "
        "durable for it and the three states mean the same thing here as they do for a power "
        "slug. What harvesting one grants is NOT claimed: contents_read counts the placements "
        "whose own mPickupItems the map writes, it comes back 0, and a yield taken off the "
        "class default instead would be a different kind of fact under the same key. Two "
        "mushrooms can stand less than a metre apart, which is measured rather than explained "
        "away -- see _meta.identity.coincident_note."
    ),
}

#: Map-placed classes left out, and the measurement behind each. The map's own placement
#: count is filled in per run, so "not a collectible" cannot be confused with "we missed
#: it", and an entry whose count comes back 0 is printed as a stale exclusion. Classes not
#: named here are still counted, in ``_meta.not_classified``.
EXCLUDED = {
    "BP_Ship_C": (
        "crash-site scenery. Its only saved property is mDismantleRefundsIndex -- there "
        "is nothing in it to collect; the hard drive is in the BP_DropPod_C beside it and "
        "the parts are in the FGItemPickup_Spawnable caches, both of which ARE rows here."
    ),
    "BP_CrashSiteDebris_C": "crash-site scenery, same as BP_Ship_C: mDismantleRefundsIndex only.",
    "BP_DebrisActor_01_C": "crash-site scenery, as BP_CrashSiteDebris_C.",
    "BP_DebrisActor_02_C": "crash-site scenery, as BP_CrashSiteDebris_C.",
    "BP_DebrisActor_03_C": "crash-site scenery, as BP_CrashSiteDebris_C.",
    "BP_BerryBush_C": (
        "regrows, so 'collected' is not a durable fact about it and none of the three "
        "states could mean what it means for the rest of this file. The measurement is in "
        "_meta.respawn.flora: this class carries mNumRespawns and mUpdatedOnDayNr, the "
        "counter rises and never falls, and it is not capped at the value this world "
        "happens to have reached. The map's placements are still the denominator for it -- "
        "they are simply not emitted, because a location whose state can never be stated is "
        "better left out than shipped with a state-shaped field. NOTE for anyone tempted to "
        "put it back: mSavedNumItems is the bush's FIXED YIELD and not a remaining count."
    ),
    "BP_NutBush_C": (
        "regrows, as BP_BerryBush_C -- mNumRespawns and mUpdatedOnDayNr, measured in "
        "_meta.respawn.flora. Its mSavedNumItems is 5 on every record on disk, which is the "
        "clearest demonstration that the field is a yield and not a countdown."
    ),
    "BP_ResourceNode_C": "has its own table with resource and purity: data/resource_nodes.json.",
    "BP_ResourceNodeGeyser_C": "in data/resource_nodes.json.",
    "BP_FrackingSatellite_C": "in data/resource_nodes.json.",
    "BP_FrackingCore_C": "in data/resource_nodes.json.",
    "BP_ResourceDeposit_C": (
        "hand-mineable only; no extractor can be placed on it. Read here for one thing "
        "only: a deposit's mOverrideResourceClass is where the map's uranium is, which is "
        "the whole of the world's static radiation -- see hazard.nearest_uranium_cm."
    ),
    "BP_DestructibleLargeRock_C": "destructible scenery, not a pickup.",
    "BP_DestructibleSmallRock_C": "destructible scenery, not a pickup.",
    "BP_DestructibleFlatRock_C": "destructible scenery, not a pickup.",
    "BP_DestructibleFoliage_C": "destructible scenery, not a pickup.",
    "BP_CreatureSpawner_C": (
        "spawns creatures; there is nothing to collect at the spawner itself. Read here "
        "for hazard context: mCreatureClass says what it spawns, mSpawnData how many, and "
        "mSpawnRadius how far -- see hazard.hostiles_nearby."
    ),
    "Char_CrabHatcher_C": (
        "a hostile creature placed directly rather than through a spawner, so it is a "
        "hazard and not a pickup. Its class declares mDetectionRadius, which is the "
        "containment test hazard.spawns_here uses for it."
    ),
    "Char_BigCrabHatcher_C": "as Char_CrabHatcher_C, the elite variant.",
    "BP_SporeFlower_C": (
        "destructible scenery, not a pickup. Read here for hazard context: its class "
        "carries a DamageSphere whose SphereRadius is a stated fact, which is what makes "
        "hazard.inside_spore_flower_damage_sphere a fact rather than a threshold."
    ),
    "BP_VolumeGas_01_C": (
        "the fog perimeter of a gas field, and a volume rather than a damage source. Its "
        "position feeds hazard.nearest_gas_cm; no radius is derived from it, because the "
        "extent it does carry (mSize_X/mSize_Y) scales a box whose base size is not in "
        "the cooked data."
    ),
    "FGDamageOverTimeVolume": (
        "the one class that looks like it should BE the gas channel and is not. Every "
        "placement's mDotClass is resolved and counted in "
        "_meta.hazard_context.sources.damage_over_time_volume_classes; they come back as the "
        "world-boundary kill box rather than as gas, which is why the gas channel reads spore "
        "flowers and gas pillars instead of this."
    ),
    "FGAmbientVolume": (
        "sound-design zones, of which the ones naming a cave ambient setting are the "
        "closest the assets come to marking a cave. Not used: they are placed for audio, "
        "so they neither cover every cave nor stop at its mouth -- see _meta.not_derived."
    ),
    "FGWorldSettings": (
        "one per cell, and the reason a bare instance name is not unique over the widened "
        "walk: the map places one per cell under a name that repeats across them. Never a "
        "collectible, so the row set is unaffected -- see _meta.identity."
    ),
    "Model": "the cell's BSP model, one per cell and named as repetitively as FGWorldSettings.",
    "StaticMeshActor": "plain scenery meshes. No interaction, nothing to collect.",
    "InstancedFoliageActor": (
        "one per cell, holding that cell's foliage as instance transforms in bulk rather "
        "than as actors. The harvestable plants ARE separate actors and are excluded above "
        "on their own merits."
    ),
    "FGCliffActor": "cliff-face scenery meshes.",
    "WorldPartitionHLOD": "the low-detail stand-in a cell shows before it has streamed in.",
}

#: Gas pillars ship as five numbered blueprints; matched rather than listed so a sixth
#: would be picked up instead of silently ignored.
GAS_PILLAR = re.compile(r"^BP_GasPillar_\d+_C$")

#: Classes read for hazard context only. None of them is ever a row.
HAZARD_CLASSES = {
    "FGDamageOverTimeVolume",
    "BP_CreatureSpawner_C",
    "Char_CrabHatcher_C",
    "Char_BigCrabHatcher_C",
    "BP_SporeFlower_C",
    "BP_VolumeGas_01_C",
    "BP_ResourceNode_C",
    "BP_ResourceDeposit_C",
}

#: How far a save's live position may sit from the map's before the record is treated as
#: describing a different actor. 1 m falls in the gap between two populations rather than
#: inside either: accepted records agree to a fraction of a centimetre, or to tens of them
#: where the game has not re-migrated the cell, while the rejects run from just over a metre
#: to kilometres. Both ends are re-measured into ``_meta.position_agreement`` and
#: ``_meta.status_evidence.displaced_gap_cm``.
POSITION_TOLERANCE_CM = 100.0

#: How far the hazard block looks, for all three of its channels. THIS FILE'S reporting
#: horizon rather than anything the game declares, which is why what it gates is a distance
#: or a species list and never a verdict. 50 m is the order of magnitude the map works at: a
#: gas field's ``mProximityPillarWorldLocations`` names its own pillars out to about that
#: far, re-measured into ``_meta.hazard_context.sources.gas_field_own_span_cm``.
HAZARD_RADIUS_CM = 5000.0

#: Whether a drop pod has been looted. Kept as a truthiness test rather than an equality
#: one because the byte the game writes for true is not stable: 1 on saveVersion 52 bodies
#: and both 1 and 16 on version 60 ones.
LOOTED_PROPERTY = "mHasBeenLooted"

#: .NET ticks are 100 ns intervals since 0001-01-01, which is what the save header stores.
_TICK_EPOCH = datetime(1, 1, 1, tzinfo=UTC)

#: Trailing decoration a placement counter leaves on an instance name. Used ONLY to measure
#: how wrong a name-based class rule would be -- never to assign a class.
_NAME_TAIL = re.compile(r"(_UAID_[0-9A-Fa-f]+)?(_\d+)?$")

#: The ``artifact_unsplit`` population: a name whose digit is glued to the stem, so no
#: split can tell BP_WAT1 (somersloop) from BP_WAT2 (Mercer sphere).
_GLUED_WAT = re.compile(r"^BP_WAT\d")

#: The map's own placement id. Present on the names the world-partition cooker issues and
#: absent on names inherited from an older hand-placed actor, so it is a sufficient mark of
#: a map placement and NOT a necessary one -- measured in ``_meta.identity``.
_UAID = "_UAID_"


# --------------------------------------------------------------------------------------
# Reading the map.
# --------------------------------------------------------------------------------------


@dataclass
class Placement:
    """One map-placed collectible: what it is, where it is, and what it hangs off."""

    instance: str
    cell: str
    cls: str
    class_path: str
    position: tuple[float, float, float]
    attached_to: str | None
    #: Class-specific facts read off this very actor: a cache's contents, a pod's cost.
    detail: dict = field(default_factory=dict)


@dataclass
class Hazard:
    """One map actor read for context rather than as a row."""

    kind: str
    cls: str
    position: tuple[float, float, float]
    #: What it is, in game terms: a creature descriptor, or a resource class.
    label: str | None = None
    #: How many creatures the spawner holds, where it says.
    count: int | None = None
    #: A radius the actor or its class declares, in cm. None where none is declared.
    radius: float | None = None
    #: How far this actor's own data reaches out into the world -- a gas volume naming its
    #: pillars. Not a containment radius: it is what sizes this file's reporting horizon.
    span: float | None = None


@dataclass
class MapWorld:
    placements: list[Placement]
    hazards: list[Hazard]
    #: class name -> how many the map places, over EVERY actor class in GameLevel01.
    class_counts: collections.Counter
    #: the same, restricted to blueprint (``/Game/``) classes, which is what a prefix-matching
    #: walk can see.
    game_class_counts: collections.Counter
    #: (cell, instance) -> class, over every map-placed actor and not only the emitted ones.
    #: What makes the destroyed entries that are not rows identifiable rather than a mystery.
    class_by_key: dict[tuple[str, str], str]
    #: instance names carrying the map's own _UAID_ placement id, and how many are distinct.
    uaid_names: int
    uaid_names_distinct: int
    #: class -> how many of its actors reuse an instance name another actor already has, so
    #: that "the bare name is not unique" names its own culprits.
    name_repeats_by_class: collections.Counter
    actor_count: int
    distinct_keys: int
    distinct_names: int
    names_with_two_classes: int
    packages_read: int
    packages_without_a_level: int
    unresolved_roots: collections.Counter
    unresolved_script_classes: int
    seconds: float


def _pickup_contents(view: PackageView, actor: int) -> dict | None:
    """``mPickupItems`` -> what the cache holds.

    ``FInventoryStack`` is ``{ FInventoryItem Item; int32 NumItems; }`` and ``FInventoryItem``
    has no tagged members at all here: its payload is an ``int32 ItemClass``, which is an
    ``FPackageIndex`` into the import map, followed by an ``int32 ItemState``. So the item
    class only becomes readable once the import map is (see ``PackageView.import_path``).
    """
    payload = view.props(actor).get("mPickupItems")
    if payload is None:
        return None
    fields = view.decode_struct(payload)
    item = fields.get("Item")
    path = None
    if isinstance(item, dict) and "_raw" in item:
        raw = bytes.fromhex(item["_raw"])
        if len(raw) >= 4:
            path = view.import_path(raw[0:4])
    count = fields.get("NumItems")
    if path is None and count is None:
        return None
    return {
        "item": class_name_of(path) if path else None,
        "item_path": path,
        "count": count,
    }


def _pod_unlock_cost(view: PackageView, actor: int) -> dict | None:
    """``mUnlockCost`` -> ``FGDropPodUnlockCost {CostType, ItemCost, PowerConsumption}``.

    ``cost_type`` is null where the pod does not serialise it. UE writes a property only
    where it differs from the class default, and both ``Item`` and ``Power`` are written
    explicitly by other pods, so a third never-written value must exist -- and its name is
    nowhere in the cooked assets.
    """
    payload = view.props(actor).get("mUnlockCost")
    if payload is None:
        return None
    fields = view.decode_struct(payload)
    item_cost = fields.get("ItemCost")
    out: dict = {"cost_type": fields.get("CostType")}
    if isinstance(item_cost, dict) and "_raw" not in item_cost:
        path = item_cost.get("ItemClass")
        out["item"] = class_name_of(path) if isinstance(path, str) else None
        out["amount"] = item_cost.get("Amount")
    power = fields.get("PowerConsumption")
    if power:
        out["power_mw"] = round(power, 3)
    return out


def read_map(store: IoStore, scripts: ScriptObjects, progress: bool = True) -> MapWorld:
    """Every actor the cooked ``GameLevel01`` packages place, with a transform.

    Transforms are composed only for the classes this table emits and the ones it reads for
    hazard context: the export table alone settles the class histogram and the identity
    claims, and property-tag parsing is most of the cost.
    """
    index = AssetIndex(store)
    classes = ClassFacts(store, index)
    wanted = set(CATEGORIES)

    placements: list[Placement] = []
    hazards: list[Hazard] = []
    class_counts: collections.Counter = collections.Counter()
    game_class_counts: collections.Counter = collections.Counter()
    keys: dict[tuple[str, str], str] = {}
    classes_by_name: dict[str, set[str]] = collections.defaultdict(set)
    unresolved: collections.Counter = collections.Counter()
    name_repeats: collections.Counter = collections.Counter()
    uaid_names: list[str] = []
    unresolved_scripts = 0
    no_level = 0
    started = time.time()

    def unreadable(_path: str, exc: Exception) -> None:
        # A package we cannot parse must not lose the other 4,520. Bucketed by exception
        # TYPE, which is what says whether the container format moved or one asset is bad.
        unresolved[f"package failed to parse: {type(exc).__name__}"] += 1

    packages = level_paths(store, contains=MAP_PREFIX)
    for number, total, path, view in walk_levels(
        store, scripts, paths=packages, on_unreadable=unreadable
    ):
        leaf = path.rsplit("/", 1)[-1]
        cell = leaf[: -len(".umap")]
        if not view.level_slots:
            no_level += 1
            continue
        for export in view.exports:
            slot = export["slot"]
            if view.outer_of.get(slot) not in view.level_slots:
                continue
            class_path = view.class_of.get(slot)
            cls = class_name_of(class_path)
            instance = export["name"]
            class_counts[cls] += 1
            if class_path and class_path.startswith("/Game/"):
                game_class_counts[cls] += 1
            elif class_path and "<unresolved:" in class_path:
                unresolved_scripts += 1
            keys[(cell, instance)] = cls
            if instance in classes_by_name:
                name_repeats[cls] += 1
            classes_by_name[instance].add(cls)
            if _UAID in instance:
                uaid_names.append(instance)

            row = cls in wanted
            hazard = cls in HAZARD_CLASSES or bool(GAS_PILLAR.match(cls))
            if not (row or hazard):
                continue
            root = root_component(view, slot)
            if root is None:
                unresolved[f"no root component: {cls}"] += 1
                continue
            transform, parent = world_transform(view, root, classes)
            if transform is None:
                unresolved[f"unresolvable attach chain: {cls}"] += 1
                continue
            if row:
                detail: dict = {}
                if cls == "FGItemPickup_Spawnable":
                    contents = _pickup_contents(view, slot)
                    if contents is None:
                        unresolved["pickup with no readable mPickupItems"] += 1
                    else:
                        detail["contents"] = contents
                if cls == "BP_Shroom_01_C":
                    # Same field, no warning if it is absent: a mushroom's yield lives on its
                    # class rather than on the placement. Asked anyway, so "the map does not
                    # say" is a count in the artifact.
                    contents = _pickup_contents(view, slot)
                    if contents is not None:
                        detail["contents"] = contents
                if cls == "BP_DropPod_C":
                    cost = _pod_unlock_cost(view, slot)
                    if cost is not None:
                        detail["unlock_cost"] = cost
                placements.append(
                    Placement(
                        instance=instance,
                        cell=cell,
                        cls=cls,
                        class_path=f"{class_path}.{cls}"
                        if class_path and class_path.startswith("/Game/")
                        else str(class_path),
                        position=transform[0],
                        attached_to=view.exports[parent]["name"] if parent is not None else None,
                        detail=detail,
                    )
                )
            if hazard:
                got = _read_hazard(view, slot, cls, transform[0], classes)
                if got is not None:
                    hazards.append(got)
        if progress and number % 1000 == 0:
            print(
                f"  {number:>5}/{total} packages  {time.time() - started:>5.1f}s"
                f"  {len(placements)} collectibles  {len(hazards)} hazard actors",
                flush=True,
            )

    return MapWorld(
        placements=placements,
        hazards=hazards,
        class_counts=class_counts,
        game_class_counts=game_class_counts,
        class_by_key=keys,
        uaid_names=len(uaid_names),
        uaid_names_distinct=len(set(uaid_names)),
        name_repeats_by_class=name_repeats,
        actor_count=sum(class_counts.values()),
        distinct_keys=len(keys),
        distinct_names=len(classes_by_name),
        names_with_two_classes=sum(1 for v in classes_by_name.values() if len(v) > 1),
        packages_read=len(packages),
        packages_without_a_level=no_level,
        unresolved_roots=unresolved,
        unresolved_script_classes=unresolved_scripts,
        seconds=time.time() - started,
    )


def read_other_levels(store: IoStore, scripts: ScriptObjects) -> list[dict]:
    """Every ``.umap`` in the container that is NOT part of ``GameLevel01``, walked the same way.

    The row set rests on "GameLevel01 is the world", and a collectible placed by another
    level would be missing from this file with nothing in it to show. So the other levels are
    read with the identical actor rule and reported with their class histogram and -- the
    point of the exercise -- how many actors of an emitted class each one places.
    """
    out: list[dict] = []
    for path in sorted(p for p in store.by_path if p.endswith(".umap") and MAP_PREFIX not in p):
        entry: dict = {"package": path.rsplit("/", 1)[-1]}
        try:
            view = PackageView(store.read_path(path), scripts)
            actors = [
                e["slot"] for e in view.exports if view.outer_of.get(e["slot"]) in view.level_slots
            ]
            counts = collections.Counter(class_name_of(view.class_of[slot]) for slot in actors)
        except Exception as exc:
            # A side level that will not parse is a fact worth reporting, not a crash.
            entry["read"] = f"failed: {type(exc).__name__}"
            out.append(entry)
            continue
        entry["actors"] = len(actors)
        entry["actor_classes"] = len(counts)
        entry["actors_of_an_emitted_class"] = {
            cls: n for cls, n in sorted(counts.items()) if cls in CATEGORIES
        }
        entry["largest_classes"] = {cls: n for cls, n in counts.most_common(5)}
        out.append(entry)
    return out


def _read_hazard(
    view: PackageView,
    slot: int,
    cls: str,
    position: tuple[float, float, float],
    classes: ClassFacts,
) -> Hazard | None:
    """One hazard actor's kind, what it holds and the radius its own class declares."""
    props = view.props(slot)
    class_package = view.class_of.get(slot) or ""
    if cls == "BP_CreatureSpawner_C":
        creature = view.import_path(props.get("mCreatureClass", b""))
        spawn = props.get("mSpawnData")
        radius = read_float(props.get("mSpawnRadius", b"")) or read_float(
            classes.defaults(class_package).get("mSpawnRadius", b"")
        )
        return Hazard(
            kind="creature_spawner",
            cls=cls,
            position=position,
            label=creature,
            count=struct.unpack_from("<I", spawn, 0)[0] if spawn and len(spawn) >= 4 else None,
            radius=radius,
        )
    if cls in ("Char_CrabHatcher_C", "Char_BigCrabHatcher_C"):
        return Hazard(
            kind="hatcher",
            cls=cls,
            position=position,
            label=class_package,
            count=1,
            radius=read_float(classes.defaults(class_package).get("mDetectionRadius", b"")),
        )
    if cls == "BP_SporeFlower_C":
        return Hazard(
            kind="gas_cloud",
            cls=cls,
            position=position,
            radius=classes.component_float(class_package, "DamageSphere", "SphereRadius"),
        )
    if cls == "BP_VolumeGas_01_C":
        # The volume names its own field's pillars in world space, which is the only
        # statement the assets make about how far a gas field reaches.
        pillars = read_vector_array(props.get("mProximityPillarWorldLocations"))
        return Hazard(
            kind="gas_field",
            cls=cls,
            position=position,
            span=max((math.dist(position, p) for p in pillars), default=None),
        )
    if GAS_PILLAR.match(cls):
        return Hazard(kind="gas_field", cls=cls, position=position)
    if cls in ("BP_ResourceNode_C", "BP_ResourceDeposit_C"):
        payload = props.get("mResourceClass") or props.get("mOverrideResourceClass")
        resource = view.import_path(payload) if payload else None
        return Hazard(kind="resource", cls=cls, position=position, label=resource)
    if cls == "FGDamageOverTimeVolume":
        # Read because this class looks exactly like a gas source and is not one. Which
        # damage it deals is ``mDotClass``, on a child component rather than on the volume.
        dot = None
        for child in view.children.get(slot, []):
            payload = view.props(child).get("mDotClass")
            if payload is not None:
                dot = view.import_path(payload) or dot
        return Hazard(kind="damage_volume", cls=cls, position=position, label=dot)
    return None


class CreatureNames:
    """``Char_*`` class -> the ``Desc_*`` descriptor the game labels it with, and passivity.

    Both are read from the assets rather than listed here. The descriptor mapping is the
    inverse of every ``CreatureDescriptors/Desc_*.mCreatureClass``, the game's own Char ->
    Desc edge. Passivity is ``mIsPassiveCreature`` on the creature's class default object --
    a bool, so its value is the byte in the tag rather than a payload -- and it matters
    because a lizard doll and a hog are both creature spawns.
    """

    FOLDER = "/CreatureDescriptors/"

    def __init__(self, store: IoStore, index: AssetIndex, classes: ClassFacts) -> None:
        self.classes = classes
        self.by_char: dict[str, str] = {}
        self.descriptors = 0
        self.unreadable_descriptors = 0
        for path in store.by_path:
            leaf = path.rsplit("/", 1)[-1]
            if self.FOLDER not in path or not path.endswith(".uasset"):
                continue
            if not leaf.startswith("Desc_"):
                continue
            self.descriptors += 1
            try:
                view = PackageView(store.read_path(path))
            except Exception as exc:
                print(f"  WARNING: creature descriptor {leaf}: {type(exc).__name__} {exc}")
                self.unreadable_descriptors += 1
                continue
            for export in view.exports:
                if not export["name"].startswith("Default__"):
                    continue
                creature = view.import_path(view.props(export["slot"]).get("mCreatureClass", b""))
                if creature:
                    self.by_char[creature] = leaf[: -len(".uasset")] + "_C"
                break
        self._passive: dict[str, bool | None] = {}
        self.index = index

    def label(self, creature_package: str | None) -> str:
        if not creature_package:
            return "<unresolved creature>"
        return self.by_char.get(creature_package) or class_name_of(creature_package)

    def is_passive(self, creature_package: str | None) -> bool | None:
        """True, False, or None where the class asset could not be read at all."""
        if not creature_package:
            return None
        if creature_package not in self._passive:
            if self.index.path_for(creature_package) is None:
                self._passive[creature_package] = None
            else:
                flag = self.classes.flag(creature_package, "mIsPassiveCreature")
                # Absent means "same as the class default", and the default is hostile:
                # the property is serialised on the four passive creatures and on none of
                # the hostile ones.
                self._passive[creature_package] = bool(flag)
        return self._passive[creature_package]


class Radioactivity:
    """Which resource classes are radioactive, over the ones the ground actually holds.

    ``mRadioactiveDecay`` on an item descriptor is the whole model, and the set is closed by
    construction: anything radioactive in the WORLD has to be a resource some node or deposit
    carries, so checking every resource class the map references checks all of them. The
    manufactured radioactive parts do not exist until a player makes them.
    """

    def __init__(self, classes: ClassFacts, index: AssetIndex, resources: set[str]) -> None:
        self.decay: dict[str, float] = {}
        self.checked = 0
        self.unreadable = 0
        for package in sorted(resources):
            if index.path_for(package) is None:
                self.unreadable += 1
                continue
            self.checked += 1
            value = read_float(classes.defaults(package).get("mRadioactiveDecay", b""))
            if value and value > 0:
                self.decay[package] = value

    def is_radioactive(self, package: str | None) -> bool:
        return bool(package) and package in self.decay


# --------------------------------------------------------------------------------------
# Hazard context: geometry between a collectible and the map actors around it.
# --------------------------------------------------------------------------------------


class SpatialIndex:
    """A uniform grid over hazard sources, so the context pass is linear rather than N*M.

    ``near`` looks at the 27 cells around a point, so it finds everything within one cell
    edge and nothing is guaranteed beyond that: the cell must be at least the largest radius
    any query uses. That is wider than this file's reporting horizon, because a spawner's own
    ``mSpawnRadius`` goes further.
    """

    def __init__(self, cell_cm: float) -> None:
        self.cell = cell_cm
        self.buckets: dict[tuple[int, int, int], list] = collections.defaultdict(list)

    def add(self, position: tuple[float, float, float], value) -> None:
        self.buckets[self._key(position)].append((position, value))

    def _key(self, position: tuple[float, float, float]) -> tuple[int, int, int]:
        return (
            int(position[0] // self.cell),
            int(position[1] // self.cell),
            int(position[2] // self.cell),
        )

    def near(self, position: tuple[float, float, float]):
        cx, cy, cz = self._key(position)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for other, value in self.buckets.get((cx + dx, cy + dy, cz + dz), ()):
                        yield math.dist(position, other), value


@dataclass
class HazardWorld:
    """The hazard sources, resolved and indexed, plus the counts that describe them."""

    index: SpatialIndex
    spawn_radius_declared: int
    spawn_radius_missing: int
    hostile_placements: int
    passive_placements: int
    unknown_passivity: int
    species: collections.Counter
    gas_clouds: int
    gas_fields: int
    #: class -> every distinct radius that class's placements declare, with how many
    #: placements there are. Per class rather than per hazard kind, because ``spawns_here``
    #: tests each source against its OWN radius -- and because a kind can be two classes:
    #: ``Char_CrabHatcher_C`` declares a detection radius and ``Char_BigCrabHatcher_C`` does
    #: not.
    class_declared_radius_cm: dict[str, dict]
    #: The spread of the per-placement mSpawnRadius: 1,137 spawners "declaring a radius" says
    #: nothing about how far those radii reach.
    spawner_radius_cm: dict[str, float | int | None]
    #: The widest radius any source declares, which is what the lookup grid is sized to.
    widest_declared_radius_cm: float
    #: What every FGDamageOverTimeVolume in the map actually does damage with. The evidence
    #: that this class is the world boundary and not the gas channel.
    damage_volume_classes: collections.Counter
    #: The distribution of how far a gas volume's own pillar list reaches, which is what the
    #: reporting horizon is sized against rather than fitted to.
    gas_field_span_cm: dict[str, float | int | None]
    uranium_sources: int
    resource_classes_checked: int
    radioactive_classes: dict[str, float]
    deposits_without_a_resource: int


def build_hazards(world: MapWorld, creatures: CreatureNames, decay: Radioactivity) -> HazardWorld:
    """Resolve every hazard actor into an indexed source, and count what was resolved."""
    widest = max([HAZARD_RADIUS_CM, *(h.radius for h in world.hazards if h.radius)])
    grid = SpatialIndex(widest)
    species: collections.Counter = collections.Counter()
    hostile = passive = unknown = declared = missing = 0
    clouds = fields = uranium = no_resource = 0
    damage_volumes: collections.Counter = collections.Counter()
    radii: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    spawner_radii: list[float] = []
    spans: list[float] = []
    for source in world.hazards:
        if source.kind == "creature_spawner":
            state = creatures.is_passive(source.label)
            if state is None:
                unknown += 1
            if state:
                passive += 1
                continue
            hostile += 1
            if source.radius:
                declared += 1
                spawner_radii.append(source.radius)
            else:
                missing += 1
            label = creatures.label(source.label)
            species[label] += 1
            grid.add(
                source.position,
                ("hostile", label, source.count or 1, source.radius, source.label),
            )
        elif source.kind == "hatcher":
            hostile += 1
            radii[source.cls][source.radius] += 1
            species[source.cls] += 1
            grid.add(source.position, ("hostile", source.cls, 1, source.radius, source.label))
        elif source.kind == "gas_cloud":
            clouds += 1
            radii[source.cls][source.radius] += 1
            grid.add(source.position, ("gas_cloud", source.cls, 1, source.radius, None))
        elif source.kind == "gas_field":
            fields += 1
            if source.span is not None:
                spans.append(source.span)
            grid.add(source.position, ("gas_field", source.cls, 1, None, None))
        elif source.kind == "damage_volume":
            damage_volumes[class_name_of(source.label) if source.label else "no mDotClass"] += 1
        elif source.kind == "resource":
            if source.label is None:
                no_resource += 1
            elif decay.is_radioactive(source.label):
                uranium += 1
                grid.add(
                    source.position, ("radioactive", class_name_of(source.label), 1, None, None)
                )
    return HazardWorld(
        index=grid,
        spawn_radius_declared=declared,
        spawn_radius_missing=missing,
        hostile_placements=hostile,
        passive_placements=passive,
        unknown_passivity=unknown,
        species=species,
        gas_clouds=clouds,
        gas_fields=fields,
        class_declared_radius_cm={
            cls: {
                "placements": sum(seen.values()),
                "distinct_radii": len(seen),
                "radius_cm": [None if r is None else round(r, 3) for r in sorted(seen, key=str)],
                "placements_declaring_no_radius": seen.get(None, 0),
            }
            for cls, seen in sorted(radii.items())
        },
        spawner_radius_cm=_spread(spawner_radii),
        widest_declared_radius_cm=widest,
        damage_volume_classes=damage_volumes,
        gas_field_span_cm=_spread(spans),
        uranium_sources=uranium,
        resource_classes_checked=decay.checked,
        radioactive_classes={class_name_of(k): round(v, 3) for k, v in decay.decay.items()},
        deposits_without_a_resource=no_resource,
    )


#: The nuclear hog. Not radioactive itself -- no mRadioactiveDecay anywhere on it -- so it
#: is reported as its own distinct reason and never folded into the uranium one.
NUCLEAR_HOG = "Desc_HogNuclear_C"


def hazard_context(position: tuple[float, float, float], hazards: HazardWorld) -> dict:
    """The hazard block for one collectible. Distances are facts; verdicts are not made."""
    hostiles: collections.Counter = collections.Counter()
    spawns_here: set[str] = set()
    nearest_hostile: tuple[float, str] | None = None
    gas: tuple[float, str] | None = None
    inside_cloud = False
    nearest_uranium: float | None = None
    nearest_hog: float | None = None

    for distance, (kind, label, count, radius, _package) in hazards.index.near(position):
        if kind == "hostile":
            if distance <= HAZARD_RADIUS_CM:
                hostiles[label] += count
                if nearest_hostile is None or distance < nearest_hostile[0]:
                    nearest_hostile = (distance, label)
            if radius is not None and distance <= radius:
                spawns_here.add(label)
            if label == NUCLEAR_HOG and distance <= HAZARD_RADIUS_CM:
                nearest_hog = distance if nearest_hog is None else min(nearest_hog, distance)
        elif kind in ("gas_cloud", "gas_field"):
            if kind == "gas_cloud" and radius is not None and distance <= radius:
                inside_cloud = True
            if distance <= HAZARD_RADIUS_CM and (gas is None or distance < gas[0]):
                gas = (distance, label)
        elif kind == "radioactive" and distance <= HAZARD_RADIUS_CM:
            nearest_uranium = (
                distance if nearest_uranium is None else min(nearest_uranium, distance)
            )

    out: dict = {}
    if hostiles:
        out["hostiles_nearby"] = dict(sorted(hostiles.items()))
    if spawns_here:
        out["spawns_here"] = sorted(spawns_here)
    if nearest_hostile is not None:
        out["nearest_hostile_cm"] = round(nearest_hostile[0], 1)
    if gas is not None:
        out["nearest_gas_cm"] = round(gas[0], 1)
        out["nearest_gas_class"] = gas[1]
    if inside_cloud:
        out["inside_spore_flower_damage_sphere"] = True
    if nearest_uranium is not None:
        out["nearest_uranium_cm"] = round(nearest_uranium, 1)
    if nearest_hog is not None:
        out["nearest_nuclear_hog_spawner_cm"] = round(nearest_hog, 1)
    return out


# --------------------------------------------------------------------------------------
# Reading the saves.
# --------------------------------------------------------------------------------------


@dataclass
class SaveFacts:
    """One save's contribution, without keeping its 44 MB body alive."""

    path: Path
    name: str
    save_version: int
    build_version: int
    session: str
    play_seconds: int
    ticks: int
    #: (cell, instance leaf) -> (class, position, looted-or-None) for the emitted classes.
    live: dict[tuple[str, str], tuple[str, tuple[float, float, float], bool | None]]
    #: (cell, path leaf) for every map actor this save records as gone.
    destroyed: set[tuple[str, str]]
    #: Cells the save has a level record for, and cells the grid table declares.
    recorded_cells: set[str] = field(default_factory=set)
    declared_cells: set[str] = field(default_factory=set)
    #: (cell, instance leaf) of every live actor of ANY map-placed class. Kept for the
    #: newest save only: it is what shows a partition cell streams in pieces.
    live_any_class: set[tuple[str, str]] = field(default_factory=set)
    #: flora class -> how many live records of it this save holds.
    flora_records: collections.Counter = field(default_factory=collections.Counter)
    #: (flora class, property) -> how many of those records carry that property at all.
    #: Presence, not value: a class that does not carry mNumRespawns has no respawn machinery.
    flora_property_records: collections.Counter = field(default_factory=collections.Counter)
    #: (flora class, property, value) -> count, for the three properties in
    #: RESPAWN_PROPERTIES. Small: the values are single digits and day numbers.
    flora_values: collections.Counter = field(default_factory=collections.Counter)
    #: (cell, instance leaf) -> (class, mNumRespawns), where the record carries it: the series
    #: whose monotonicity is the test. The class travels with the value because a
    #: pre-partition key is one the current map has no entry for.
    flora_counter: dict[tuple[str, str], tuple[str, int]] = field(default_factory=dict)
    #: How many flora records' property block could not be decoded, so a missing property is
    #: never confused with an unreadable one.
    flora_unreadable: int = 0

    @property
    def when(self) -> datetime:
        return _TICK_EPOCH + timedelta(microseconds=self.ticks / 10)


def find_saves(root: Path) -> list[Path]:
    """Every ``.sav`` in *root* AND in the per-account subdirectory Steam uses.

    Both, not one-or-the-other: the game keeps the saves in the account directory and drops a
    105-byte ``ServerManager_V2.sav`` in the root beside it, so a rule that stops at the root
    as soon as it finds anything finds only that file. It is not a save game and the reader
    skips it like any other unreadable file.
    """
    return sorted(set(root.glob("*.sav")) | set(root.glob("*/*.sav")))


def read_save_facts(path: Path, map_classes: set[str], keep_all_classes: bool) -> SaveFacts | None:
    """Pull one save's collectible facts, or return None with a printed reason.

    Everything below the header is version-gated on the header's own ``save_version``: a
    pre-1.0 body has a 48-byte chunk preamble and a flat level list, and reading it with the
    modern layout fails on the first chunk tag.

    Only the level walk is eager, which is 0.23 s against 1.99 s for a full parse because the
    property blocks are 86% of the cost. The blocks with a question attached are decoded by
    offset during the walk: ``mHasBeenLooted`` on at most 118 drop pods, and the respawn
    properties on the ``RESPAWN_PROBE`` plants -- thousands per save, and paid for because
    they are the evidence that ``collected`` means anything.
    """
    try:
        data = path.read_bytes()
        info = read_info_bytes(data)
        old = info.save_version < FIRST_MODERN_BODY
        inflated = decompress_body(data, info.body_offset, old=old)
        body = read_body(inflated, info.save_version)
    except (ParseError, OSError) as exc:
        print(f"  skip {path.name}: {exc}")
        return None

    live: dict[tuple[str, str], tuple[str, tuple[float, float, float], bool | None]] = {}
    any_class: set[tuple[str, str]] = set()
    flora_records: collections.Counter = collections.Counter()
    flora_property_records: collections.Counter = collections.Counter()
    flora_values: collections.Counter = collections.Counter()
    flora_counter: dict[tuple[str, str], int] = {}
    flora_unreadable = 0
    for level in body.levels:
        for header, slot in zip(level.headers, level.objects, strict=True):
            if not isinstance(header, ActorHeader):
                continue
            cls = header.type_path.rsplit(".", 1)[-1]
            leaf = header.instance_name.rsplit(".", 1)[-1]
            if keep_all_classes and cls in map_classes:
                any_class.add((level.name, leaf))
            probe = cls in RESPAWN_PROBE
            if cls not in CATEGORIES and not probe:
                continue
            properties: dict | None = None
            if probe or cls == "BP_DropPod_C":
                try:
                    properties = dict(
                        read_object(
                            inflated, slot, actor=True, save_version=info.save_version
                        ).properties
                    )
                except ParseError:
                    properties = None
            if probe:
                flora_records[cls] += 1
                if properties is None:
                    flora_unreadable += 1
                else:
                    for name in RESPAWN_PROPERTIES:
                        if name not in properties:
                            continue
                        flora_property_records[(cls, name)] += 1
                        value = properties[name]
                        if isinstance(value, int) and not isinstance(value, bool):
                            flora_values[(cls, name, value)] += 1
                    respawns = properties.get("mNumRespawns")
                    if isinstance(respawns, int) and not isinstance(respawns, bool):
                        flora_counter[(level.name, leaf)] = (cls, respawns)
            if cls not in CATEGORIES:
                continue
            looted = None
            if cls == "BP_DropPod_C" and properties is not None:
                looted = bool(properties.get(LOOTED_PROPERTY))
            live[(level.name, leaf)] = (cls, tuple(header.position), looted)

    return SaveFacts(
        path=path,
        name=path.name,
        save_version=info.save_version,
        build_version=info.build_version,
        session=info.session_name,
        play_seconds=info.play_duration_s,
        ticks=info.save_datetime_ticks,
        live=live,
        destroyed={(cell, name.rsplit(".", 1)[-1]) for cell, name in body.destroyed_actors},
        recorded_cells={level.name for level in body.levels},
        declared_cells={cell for grid in body.preamble.grids for cell in grid.cell_names},
        live_any_class=any_class,
        flora_records=flora_records,
        flora_property_records=flora_property_records,
        flora_values=flora_values,
        flora_counter=flora_counter,
        flora_unreadable=flora_unreadable,
    )


# --------------------------------------------------------------------------------------
# The merge: map rows plus save status, and every number in _meta as a by-product of it.
# --------------------------------------------------------------------------------------


def _name_stem_class(leaf: str, stems: dict[str, str]) -> str | None:
    """The class a longest-prefix rule over instance names WOULD pick. Diagnostic only."""
    base = _NAME_TAIL.sub("", leaf)
    base = re.sub(r"_C$", "", base)
    base = re.sub(r"_?\d+$", "", base)
    best: tuple[str, str] | None = None
    for stem, cls in stems.items():
        if (leaf.startswith(stem) or base.startswith(stem)) and (
            best is None or len(stem) > len(best[0])
        ):
            best = (stem, cls)
    return best[1] if best else None


def _spread(values: list[float]) -> dict[str, float | int | None]:
    """Matched count plus the distribution of a list of distances, for ``_meta``."""
    ordered = sorted(values)
    return {
        "matched": len(ordered),
        "median_cm": round(ordered[len(ordered) // 2], 6) if ordered else None,
        "p90_cm": round(ordered[int(0.9 * (len(ordered) - 1))], 6) if ordered else None,
        "max_cm": round(ordered[-1], 3) if ordered else None,
        "over_1cm": sum(1 for v in ordered if v > 1),
        "over_1m": sum(1 for v in ordered if v > 100),
    }


def _by_count(counter: collections.Counter) -> dict[str, int]:
    """A counter as a plain dict, biggest first, so ``_meta`` reads in a stable order."""
    return dict(sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])))


@dataclass
class BuildContext:
    """What ``build()``'s nine measurements share: the inputs, then their accumulators.

    The inputs come first and are set once, in ``build()``. Everything below them is
    measured, each field by exactly one ``_measure_*`` function, in the order the
    functions run. The measured fields are ``init=False`` with no default, so a section
    reading a number before the section that measures it has run is an AttributeError
    rather than a silently empty value.
    """

    world: MapWorld
    hazards: HazardWorld
    facts: list[SaveFacts]
    newest: SaveFacts
    store: IoStore
    scripts: ScriptObjects
    game_build: str | None
    pyooz_version: str
    on_disk: list[SaveFacts]
    files_found: int
    other_levels: list[dict]
    rows_in: list[Placement]
    by_key: dict[tuple[str, str], Placement]
    duplicate_keys: int
    duplicate_names: int

    # _measure_status
    present: dict[tuple[str, str], bool | None] = field(init=False)
    displaced: list[tuple[tuple[str, str], float]] = field(init=False)
    orphan_live: collections.Counter = field(init=False)
    agreeing: list[float] = field(init=False)
    collected: set[tuple[str, str]] = field(init=False)
    destroyed_others: collections.Counter = field(init=False)
    recoverable: int = field(init=False)
    rows: list[dict] = field(init=False)

    # _measure_pedestals
    pedestals: dict[str, dict] = field(init=False)

    # _measure_coincident_positions
    coincident_pairs: set[tuple[tuple[str, str], tuple[str, str]]] = field(init=False)
    coincident_by_category: collections.Counter = field(init=False)
    coincident_states_differ: int = field(init=False)
    coincident_both_have_a_placement_id: int = field(init=False)
    coincident: int = field(init=False)

    # _measure_older_save_staleness
    older_only: set[tuple[str, str]] = field(init=False)
    displaced_by_version: dict[int, set] = field(init=False)
    joined_by_save: dict[str, int] = field(init=False)
    orphan_all: collections.Counter = field(init=False)
    orphan_versions: dict[tuple[str, str], set[int]] = field(init=False)
    pre_partition_keys: set[tuple[str, str]] = field(init=False)
    pre_partition_cells: collections.Counter = field(init=False)

    # _measure_orphans
    orphan_old_layout: int = field(init=False)
    orphan_renamed: int = field(init=False)
    orphan_unexplained: int = field(init=False)

    # _measure_exploration
    collectible_cells: set[str] = field(init=False)
    cells_no_record: set[str] = field(init=False)
    unknown_rows: list[dict] = field(init=False)
    unknown_no_record: int = field(init=False)
    observed_map_actors: int = field(init=False)
    map_actors_in_recorded_cells: int = field(init=False)
    map_cells: set[str] = field(init=False)

    # _measure_naming
    wrong: int = field(init=False)
    silent: int = field(init=False)
    borrowed: collections.Counter = field(init=False)
    glued: list[Placement] = field(init=False)
    glued_by_category: collections.Counter = field(init=False)
    glued_destroyed: list[Placement] = field(init=False)
    glued_destroyed_unresolved: int = field(init=False)
    mentioned: set[tuple[str, str]] = field(init=False)
    coincident_both_recorded: int = field(init=False)

    # _measure_respawn
    pairs_compared: int = field(init=False)
    pairs_skipped: int = field(init=False)
    destroyed_observations: collections.Counter = field(init=False)
    left_the_list: collections.Counter = field(init=False)
    revived: collections.Counter = field(init=False)
    revived_displaced: collections.Counter = field(init=False)
    coexisting: collections.Counter = field(init=False)
    coexisting_by_version: collections.Counter = field(init=False)
    newest_coexisting: int = field(init=False)
    flora: dict[str, dict] = field(init=False)
    flora_unreadable: int = field(init=False)

    # _measure_per_category
    per_category: dict[str, dict] = field(init=False)
    excluded: dict[str, dict] = field(init=False)
    unclassified: dict[str, int] = field(init=False)
    census: dict[str, dict] = field(init=False)
    hostile_rows: int = field(init=False)
    spawns_here_rows: int = field(init=False)
    gas_rows: int = field(init=False)
    cloud_rows: int = field(init=False)
    uranium_rows: int = field(init=False)
    hog_rows: int = field(init=False)
    sessions: collections.Counter = field(init=False)
    pre_partition: list[SaveFacts] = field(init=False)


def _measure_status(ctx: BuildContext) -> None:
    """Status -- the newest save decides; the others are the evidence that it may.

    The rows themselves come out of the same pass: a row's state IS the merge, so the row
    list and the status evidence are one derivation rather than two that could disagree.
    """
    present: dict[tuple[str, str], bool | None] = {}
    displaced: list[tuple[tuple[str, str], float]] = []
    orphan_live: collections.Counter = collections.Counter()
    agreeing: list[float] = []
    for key, (cls, position, looted) in ctx.newest.live.items():
        placement = ctx.by_key.get(key)
        if placement is None:
            orphan_live[cls] += 1
            continue
        gap = math.dist(position, placement.position)
        if gap <= POSITION_TOLERANCE_CM:
            present[key] = looted
            agreeing.append(gap)
        else:
            displaced.append((key, gap))

    collected = {key for key in ctx.newest.destroyed if key in ctx.by_key}
    # What the rest of the destroyed list is, by the map's own class for that key, so that
    # "the remainder is scenery" is derived. A key the map does not place gets its own
    # bucket.
    destroyed_others: collections.Counter = collections.Counter()
    for key in ctx.newest.destroyed:
        if key not in ctx.by_key:
            destroyed_others[ctx.world.class_by_key.get(key, "not a map-placed actor at all")] += 1

    # Could a displaced record be re-attached by position alone? Reported, never applied:
    # state has one derivation, and a row labelled unknown beats a heuristic overriding it.
    by_class: dict[str, list[Placement]] = collections.defaultdict(list)
    for placement in ctx.rows_in:
        by_class[placement.cls].append(placement)
    recoverable = 0
    for key, _gap in displaced:
        cls, position, _looted = ctx.newest.live[key]
        ranked = sorted(math.dist(position, p.position) for p in by_class[cls])
        if (
            ranked
            and ranked[0] <= POSITION_TOLERANCE_CM
            and (len(ranked) == 1 or ranked[1] > 10 * max(ranked[0], 1.0))
        ):
            recoverable += 1

    rows: list[dict] = []
    for placement in ctx.rows_in:
        key = (placement.cell, placement.instance)
        if key in collected:
            state = "collected"
        elif key in present:
            state = "present"
        else:
            state = "unknown"
        row = {
            "instance": INSTANCE_PREFIX + placement.instance,
            "cell": placement.cell,
            "category": CATEGORIES[placement.cls],
            "class": placement.cls,
            "x": round(placement.position[0], 1),
            "y": round(placement.position[1], 1),
            "z": round(placement.position[2], 1),
            "state": state,
        }
        if placement.attached_to:
            row["attached_to"] = INSTANCE_PREFIX + placement.attached_to
        if placement.cls == "BP_DropPod_C":
            row["looted"] = present.get(key) if state == "present" else None
        row.update(placement.detail)
        context = hazard_context(placement.position, ctx.hazards)
        if context:
            row["hazard"] = context
        rows.append(row)
    rows.sort(key=lambda r: (r["category"], r["cell"], r["instance"]))

    ctx.present = present
    ctx.displaced = displaced
    ctx.orphan_live = orphan_live
    ctx.agreeing = agreeing
    ctx.collected = collected
    ctx.destroyed_others = destroyed_others
    ctx.recoverable = recoverable
    ctx.rows = rows


def _measure_pedestals(ctx: BuildContext) -> None:
    """A shrine is the base of the sphere or somersloop above it -- AttachParent says so.

    Checked rather than asserted: if the pairing were not 1:1, "298 shrines" would be a
    second collectible rather than a second row about one find, and a consumer adding the
    categories up would double-count every artifact.
    """
    pedestals: dict[str, dict] = {}
    row_category = {row["instance"]: row["category"] for row in ctx.rows}
    for category in ("mercer_shrine", "somersloop_shrine"):
        mine = [r for r in ctx.rows if r["category"] == category]
        parents = [r.get("attached_to") for r in mine]
        parent_categories = collections.Counter(
            row_category.get(p, "not a row here") for p in parents
        )
        pedestals[category] = {
            "rows": len(mine),
            "with_a_parent": sum(1 for p in parents if p),
            "distinct_parents": len({p for p in parents if p}),
            "parent_category": _by_count(parent_categories),
            "one_to_one": len({p for p in parents if p}) == len(mine),
        }

    ctx.pedestals = pedestals


def _measure_coincident_positions(ctx: BuildContext) -> None:
    """Two rows of one category within a metre would be one collectible counted twice.

    Checked over the emitted rows, per category, on a grid so it is linear.
    """
    coincident_pairs: set[tuple[tuple[str, str], tuple[str, str]]] = set()
    coincident_by_category: collections.Counter = collections.Counter()
    coincident_states_differ = 0
    coincident_both_have_a_placement_id = 0
    buckets: dict[tuple[str, int, int, int], list[dict]] = collections.defaultdict(list)
    for row in ctx.rows:
        cell = (row["category"], int(row["x"] // 100), int(row["y"] // 100), int(row["z"] // 100))
        buckets[cell].append(row)
    for row in ctx.rows:
        cx, cy, cz = int(row["x"] // 100), int(row["y"] // 100), int(row["z"] // 100)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for other in buckets.get((row["category"], cx + dx, cy + dy, cz + dz), ()):
                        if other is row:
                            continue
                        gap = math.dist(
                            (row["x"], row["y"], row["z"]), (other["x"], other["y"], other["z"])
                        )
                        if gap > POSITION_TOLERANCE_CM:
                            continue
                        first = (row["cell"], row["instance"].removeprefix(INSTANCE_PREFIX))
                        second = (other["cell"], other["instance"].removeprefix(INSTANCE_PREFIX))
                        pair = (first, second) if first < second else (second, first)
                        if pair in coincident_pairs:
                            continue
                        coincident_pairs.add(pair)
                        coincident_by_category[row["category"]] += 1
                        if row["state"] != other["state"]:
                            coincident_states_differ += 1
                        if _UAID in first[1] and _UAID in second[1]:
                            coincident_both_have_a_placement_id += 1
    coincident = len(coincident_pairs)

    ctx.coincident_pairs = coincident_pairs
    ctx.coincident_by_category = coincident_by_category
    ctx.coincident_states_differ = coincident_states_differ
    ctx.coincident_both_have_a_placement_id = coincident_both_have_a_placement_id
    ctx.coincident = coincident


def _measure_older_save_staleness(ctx: BuildContext) -> None:
    """What the older saves add, and how stale their keys are.

    This is the evidence for "the newest save is the authority": if the union resolved
    rows it cannot, it is not.
    """
    older_only = set()
    displaced_by_version: dict[int, set] = collections.defaultdict(set)
    joined_by_save: dict[str, int] = {}
    orphan_all: collections.Counter = collections.Counter()
    orphan_versions: dict[tuple[str, str], set[int]] = collections.defaultdict(set)
    pre_partition_keys: set[tuple[str, str]] = set()
    pre_partition_cells: collections.Counter = collections.Counter()
    for save in ctx.facts:
        old = save.save_version < FIRST_MODERN_BODY
        joined = 0
        for key, (cls, position, _looted) in save.live.items():
            placement = ctx.by_key.get(key)
            if placement is None:
                orphan_all[cls] += 1
                orphan_versions[key].add(save.save_version)
                continue
            joined += 1
            if old:
                pre_partition_keys.add(key)
                pre_partition_cells[key[0]] += 1
            if math.dist(position, placement.position) > POSITION_TOLERANCE_CM:
                displaced_by_version[save.save_version].add(key)
            elif key not in ctx.present and key not in ctx.collected:
                older_only.add(key)
        for key in save.destroyed:
            if key in ctx.by_key:
                joined += 1
                if old:
                    pre_partition_keys.add(key)
                    pre_partition_cells[key[0]] += 1
                if key not in ctx.collected and key not in ctx.present:
                    older_only.add(key)
        joined_by_save[save.name] = joined

    ctx.older_only = older_only
    ctx.displaced_by_version = displaced_by_version
    ctx.joined_by_save = joined_by_save
    ctx.orphan_all = orphan_all
    ctx.orphan_versions = orphan_versions
    ctx.pre_partition_keys = pre_partition_keys
    ctx.pre_partition_cells = pre_partition_cells


def _measure_orphans(ctx: BuildContext) -> None:
    """What the orphans are, which is the closest thing to a proof that no row is missing.

    An orphan is a save's live record of an emitted class that this table has no row for,
    and it can only be a record from the pre-partition world layout, a pre-patch record
    whose name the map now places in a DIFFERENT cell, or a collectible the map read failed
    to find. The third would be a bug, so it is counted rather than argued about.
    """
    row_names = {p.instance for p in ctx.rows_in}
    orphan_old_layout = orphan_renamed = orphan_unexplained = 0
    for key, versions in ctx.orphan_versions.items():
        if max(versions) < FIRST_MODERN_BODY:
            orphan_old_layout += 1
        elif key[1] in row_names:
            orphan_renamed += 1
        else:
            orphan_unexplained += 1

    ctx.orphan_old_layout = orphan_old_layout
    ctx.orphan_renamed = orphan_renamed
    ctx.orphan_unexplained = orphan_unexplained


def _measure_exploration(ctx: BuildContext) -> None:
    """Exploration: how much of the world the saves have observed, per collectible.

    A cell is not a unit of coverage: the newest save has a level record for cells it has
    only partly streamed, so the honest figure is per collectible.
    """
    collectible_cells = {p.cell for p in ctx.rows_in}
    recorded_union: set[str] = set()
    for save in ctx.facts:
        recorded_union |= save.recorded_cells
    cells_no_record = collectible_cells - recorded_union
    unknown_rows = [r for r in ctx.rows if r["state"] == "unknown"]
    unknown_no_record = sum(1 for r in unknown_rows if r["cell"] in cells_no_record)

    in_recorded_cells = [k for k in ctx.world.class_by_key if k[0] in ctx.newest.recorded_cells]
    observed_map_actors = sum(
        1 for k in in_recorded_cells if k in ctx.newest.live_any_class or k in ctx.newest.destroyed
    )
    map_actors_in_recorded_cells = len(in_recorded_cells)
    map_cells = {cell for cell, _instance in ctx.world.class_by_key}

    ctx.collectible_cells = collectible_cells
    ctx.cells_no_record = cells_no_record
    ctx.unknown_rows = unknown_rows
    ctx.unknown_no_record = unknown_no_record
    ctx.observed_map_actors = observed_map_actors
    ctx.map_actors_in_recorded_cells = map_actors_in_recorded_cells
    ctx.map_cells = map_cells


def _measure_naming(ctx: BuildContext) -> None:
    """Naming, scored over the map's own rows, where the truth is known."""
    stems = {cls.removesuffix("_C"): cls for cls in CATEGORIES}
    wrong = silent = 0
    for placement in ctx.rows_in:
        guess = _name_stem_class(placement.instance, stems)
        if guess is None:
            silent += 1
        elif guess != placement.cls:
            wrong += 1
    # The same question against EVERY map-placed class rather than only the emitted ones,
    # which is where it gets sharp: a row whose name is another class's stem is a row a
    # name-based rule would file under a class this file may not even emit.
    all_stems = {cls.removesuffix("_C"): cls for cls in ctx.world.class_counts}
    borrowed: collections.Counter = collections.Counter()
    for placement in ctx.rows_in:
        guess = _name_stem_class(placement.instance, all_stems)
        if guess is not None and guess != placement.cls:
            borrowed[f"{guess} -> {placement.cls}"] += 1
    glued = [p for p in ctx.rows_in if _GLUED_WAT.match(p.instance)]
    glued_by_category = collections.Counter(CATEGORIES[p.cls] for p in glued)
    glued_destroyed = [
        ctx.by_key[key]
        for key in ctx.newest.destroyed
        if _GLUED_WAT.match(key[1]) and key in ctx.by_key
    ]
    glued_destroyed_unresolved = sum(
        1 for key in ctx.newest.destroyed if _GLUED_WAT.match(key[1]) and key not in ctx.by_key
    )

    # Whether the game serialises a class at all: a row whose key ANY save mentions, live or
    # gone, however displaced. 0 means the class can only ever be located, never
    # state-tracked, which is what BP_SomerSloopShrine_C measures at.
    mentioned: set[tuple[str, str]] = set()
    for save in ctx.facts:
        mentioned |= save.live.keys() & ctx.by_key.keys()
        mentioned |= save.destroyed & ctx.by_key.keys()

    # Does the GAME hold two records where two rows sit within a metre of each other? If it
    # does, they are two actors and not one row emitted twice.
    coincident_both_recorded = sum(
        1 for first, second in ctx.coincident_pairs if first in mentioned and second in mentioned
    )

    ctx.wrong = wrong
    ctx.silent = silent
    ctx.borrowed = borrowed
    ctx.glued = glued
    ctx.glued_by_category = glued_by_category
    ctx.glued_destroyed = glued_destroyed
    ctx.glued_destroyed_unresolved = glued_destroyed_unresolved
    ctx.mentioned = mentioned
    ctx.coincident_both_recorded = coincident_both_recorded


def _measure_respawn(ctx: BuildContext) -> None:
    """The premise every state here rests on -- a taken collectible stays gone -- tested.

    Two ways it can fail:

    (1) A key leaves a save's destroyed list. Only consecutive saves of the SAME build are
        compared: across a build that re-issued instance names a key vanishing means the
        name changed, so those pairs are counted and skipped rather than reported.
    (2) A save names a key destroyed and a LATER save has a live record at that key. The
        position gate is not optional here: a player-dropped crate sharing a bare name with
        a map cache looks exactly like a resurrection until its position is checked. Both
        the accepted and the position-rejected counts are printed.
    """

    def _class_of(key: tuple[str, str]) -> str:
        return ctx.world.class_by_key.get(key, "not a map-placed actor at all")

    destroyed_observations: collections.Counter = collections.Counter()
    left_the_list: collections.Counter = collections.Counter()
    pairs_compared = pairs_skipped = 0
    for older, newer in itertools.pairwise(ctx.facts):
        if older.build_version != newer.build_version:
            pairs_skipped += 1
            continue
        pairs_compared += 1
        for key in older.destroyed - newer.destroyed:
            left_the_list[_class_of(key)] += 1
    for save in ctx.facts:
        for key in save.destroyed:
            destroyed_observations[_class_of(key)] += 1

    # A save can list one (cell, instance) as destroyed AND hold a live actor at it whose
    # position agrees with the map. That is the game's own migration, not a resurrection:
    # pre-partition the bare name BP_Crystal1 belonged to two slugs in two levels, and one
    # slug's destroyed record was keyed into the other's cell. So a "destroyed then live"
    # observation splits three ways and only the middle one would falsify the premise.
    first_destroyed: dict[tuple[str, str], int] = {}
    revived: collections.Counter = collections.Counter()
    revived_displaced: collections.Counter = collections.Counter()
    coexisting: collections.Counter = collections.Counter()
    coexisting_by_version: collections.Counter = collections.Counter()
    for index, save in enumerate(ctx.facts):
        for key in save.destroyed & save.live.keys():
            placement = ctx.by_key.get(key)
            _cls, position, _looted = save.live[key]
            if placement is not None and (
                math.dist(position, placement.position) <= POSITION_TOLERANCE_CM
            ):
                coexisting[_class_of(key)] += 1
                coexisting_by_version[save.save_version] += 1
        for key, (_cls, position, _looted) in save.live.items():
            first = first_destroyed.get(key)
            if first is None or first >= index or key in save.destroyed:
                continue
            placement = ctx.by_key.get(key)
            if placement is not None and (
                math.dist(position, placement.position) <= POSITION_TOLERANCE_CM
            ):
                revived[_class_of(key)] += 1
            else:
                revived_displaced[_class_of(key)] += 1
        for key in save.destroyed:
            first_destroyed.setdefault(key, index)
    newest_coexisting = sum(
        1
        for key in ctx.newest.destroyed & ctx.newest.live.keys()
        if (placement := ctx.by_key.get(key)) is not None
        and math.dist(ctx.newest.live[key][1], placement.position) <= POSITION_TOLERANCE_CM
    )

    # The flora probe: the property side of the same question. A class that carries no
    # mNumRespawns at all has no respawn machinery, which is a structural answer rather than
    # an inference from not having seen one regrow.
    flora_records: collections.Counter = collections.Counter()
    flora_props: collections.Counter = collections.Counter()
    flora_values: collections.Counter = collections.Counter()
    flora_unreadable = 0
    for save in ctx.facts:
        flora_records.update(save.flora_records)
        flora_props.update(save.flora_property_records)
        flora_values.update(save.flora_values)
        flora_unreadable += save.flora_unreadable
    counter_first: collections.Counter = collections.Counter()
    counter_rose: collections.Counter = collections.Counter()
    counter_fell: collections.Counter = collections.Counter()
    counter_incomparable: collections.Counter = collections.Counter()
    seen_counter: dict[tuple[str, str], tuple[int, int]] = {}
    for save in ctx.facts:
        for key, (cls, value) in save.flora_counter.items():
            previous = seen_counter.get(key)
            if previous is None:
                counter_first[cls] += 1
            elif previous[0] != save.build_version:
                counter_incomparable[cls] += 1
            elif value > previous[1]:
                counter_rose[cls] += 1
            elif value < previous[1]:
                counter_fell[cls] += 1
            seen_counter[key] = (save.build_version, value)

    flora: dict[str, dict] = {}
    for cls in sorted(RESPAWN_PROBE):
        values: dict[str, dict[str, int]] = {}
        for name in RESPAWN_PROPERTIES:
            mine = {v: n for (c, p, v), n in flora_values.items() if c == cls and p == name}
            values[name] = {str(v): mine[v] for v in sorted(mine)}
        respawns = [int(v) for v in values["mNumRespawns"]]
        flora[cls] = {
            "placed_by_the_map": ctx.world.class_counts.get(cls, 0),
            "live_records_over_the_saves_used": flora_records.get(cls, 0),
            "records_carrying": {
                name: flora_props.get((cls, name), 0) for name in RESPAWN_PROPERTIES
            },
            "values": values,
            "highest_mNumRespawns_seen": max(respawns) if respawns else None,
            "counter_first_appearances": counter_first.get(cls, 0),
            "counter_rose": counter_rose.get(cls, 0),
            "counter_fell": counter_fell.get(cls, 0),
            "counter_pairs_a_build_change_made_incomparable": counter_incomparable.get(cls, 0),
            "destroyed_observations": destroyed_observations.get(cls, 0),
            "keys_that_left_the_destroyed_list": left_the_list.get(cls, 0),
            "respawns": bool(flora_props.get((cls, "mNumRespawns"), 0)),
            "emitted_as": CATEGORIES.get(cls),
        }

    ctx.pairs_compared = pairs_compared
    ctx.pairs_skipped = pairs_skipped
    ctx.destroyed_observations = destroyed_observations
    ctx.left_the_list = left_the_list
    ctx.revived = revived
    ctx.revived_displaced = revived_displaced
    ctx.coexisting = coexisting
    ctx.coexisting_by_version = coexisting_by_version
    ctx.newest_coexisting = newest_coexisting
    ctx.flora = flora
    ctx.flora_unreadable = flora_unreadable


def _measure_per_category(ctx: BuildContext) -> None:
    """Per category: placed is exact; the other three are what the saves have observed.

    The whole-map bookkeeping the category split is checked against rides along, as it
    always sat in this section: the excluded and not_classified buckets, the pickup
    class_census, the hazard-touch counts and the session tally.
    """
    per_category: dict[str, dict] = {}
    for category in sorted(set(CATEGORIES.values())):
        mine = [r for r in ctx.rows if r["category"] == category]
        entry = {
            "placed": len(mine),
            "collected": sum(1 for r in mine if r["state"] == "collected"),
            "present": sum(1 for r in mine if r["state"] == "present"),
            "unknown": sum(1 for r in mine if r["state"] == "unknown"),
            "rows_any_save_mentions": sum(
                1
                for p in ctx.rows_in
                if CATEGORIES[p.cls] == category and (p.cell, p.instance) in ctx.mentioned
            ),
            "with_the_map_s_own_placement_id": sum(
                1 for p in ctx.rows_in if CATEGORIES[p.cls] == category and _UAID in p.instance
            ),
        }
        if category == "crashed_drop_pod":
            entry["present_and_looted"] = sum(1 for r in mine if r.get("looted"))
            entry["unlock_cost_serialised"] = sum(1 for r in mine if "unlock_cost" in r)
            entry["unlock_cost_with_an_item"] = sum(
                1 for r in mine if r.get("unlock_cost", {}).get("amount")
            )
            entry["unlock_cost_with_a_power_figure"] = sum(
                1 for r in mine if r.get("unlock_cost", {}).get("power_mw")
            )
            entry["unlock_cost_type_unserialised"] = sum(
                1 for r in mine if "unlock_cost" in r and r["unlock_cost"]["cost_type"] is None
            )
        if category == "loot_cache":
            items = collections.Counter()
            total = 0
            for row in mine:
                contents = row.get("contents") or {}
                if contents.get("item"):
                    items[contents["item"]] += contents.get("count") or 0
                    total += contents.get("count") or 0
            entry["contents_read"] = sum(1 for r in mine if r.get("contents"))
            entry["distinct_item_types"] = len(items)
            entry["items_in_total"] = total
            entry["items_by_type"] = _by_count(items)
        if category == "mushroom":
            entry["contents_read"] = sum(1 for r in mine if r.get("contents"))
            entry["respawns"] = ctx.flora["BP_Shroom_01_C"]["respawns"]
            entry["respawn_evidence"] = "_meta.respawn.flora.BP_Shroom_01_C"
        if category in CATEGORY_NOTES:
            entry["note"] = CATEGORY_NOTES[category]
        entry["class"] = next(c for c, cat in CATEGORIES.items() if cat == category)
        entry["class_path"] = next(
            p.class_path for p in ctx.rows_in if CATEGORIES[p.cls] == category
        )
        per_category[category] = entry

    excluded = {
        cls: {"placed_by_the_map": ctx.world.class_counts.get(cls, 0), "why": why}
        for cls, why in EXCLUDED.items()
    }
    # The gas pillars are excluded for one shared reason and there are five numbered
    # blueprints of them, so they are matched rather than listed -- which also means a sixth
    # would be excluded with its count instead of quietly appearing in not_classified.
    for cls, count in ctx.world.class_counts.items():
        if GAS_PILLAR.match(cls):
            excluded[cls] = {
                "placed_by_the_map": count,
                "why": (
                    "part of a gas field, and scenery rather than a pickup. Its position "
                    "feeds hazard.nearest_gas_cm; it declares no radius of its own, so no "
                    "containment test is derived from it."
                ),
            }
    # Built from the excluded dict rather than from EXCLUDED, because the gas pillars are
    # added to it by pattern above and a class must land in exactly one of the two buckets.
    unclassified = _by_count(
        collections.Counter(
            {
                cls: count
                for cls, count in ctx.world.class_counts.items()
                if cls not in CATEGORIES and cls not in excluded
            }
        )
    )

    # The regression guard for the bug this revision fixes. A pickup class that stops being
    # emitted, or a new one the map gains, shows up here as a number rather than as silence.
    census = {
        cls: {
            "placed_by_the_map": count,
            "native_class": cls not in ctx.world.game_class_counts,
            "emitted_as": CATEGORIES.get(cls),
        }
        for cls, count in sorted(ctx.world.class_counts.items())
        if "Pickup" in cls or "pickup" in cls
    }

    def _with(key: str) -> int:
        return sum(1 for r in ctx.rows if r.get("hazard", {}).get(key) is not None)

    hostile_rows = _with("hostiles_nearby")
    spawns_here_rows = _with("spawns_here")
    gas_rows = _with("nearest_gas_cm")
    cloud_rows = _with("inside_spore_flower_damage_sphere")
    uranium_rows = _with("nearest_uranium_cm")
    hog_rows = _with("nearest_nuclear_hog_spawner_cm")

    sessions = collections.Counter(f.session for f in ctx.on_disk)
    pre_partition = [f for f in ctx.facts if f.save_version < FIRST_MODERN_BODY]

    ctx.per_category = per_category
    ctx.excluded = excluded
    ctx.unclassified = unclassified
    ctx.census = census
    ctx.hostile_rows = hostile_rows
    ctx.spawns_here_rows = spawns_here_rows
    ctx.gas_rows = gas_rows
    ctx.cloud_rows = cloud_rows
    ctx.uranium_rows = uranium_rows
    ctx.hog_rows = hog_rows
    ctx.sessions = sessions
    ctx.pre_partition = pre_partition


def _assemble_meta(ctx: BuildContext) -> dict:
    """``_meta``, assembled from what the nine measurements left on ``ctx``.

    Nothing new is measured here -- only sums and reshaping of numbers a ``_measure_*``
    already derived, which is what keeps every total a by-product of the rows it claims
    to describe.
    """
    largest = ", ".join(f"{cls} ({count})" for cls, count in list(ctx.unclassified.items())[:5])
    persistent = sum(1 for r in ctx.rows if r["cell"] == "Persistent_Level")
    meta = {
        "description": (
            "Every one-shot collectible the classes in totals.by_category place in the "
            "world -- power slugs, somersloops, Mercer spheres and their shrines, crashed "
            "drop pods, the loot caches at crash sites, the mushrooms -- with its exact "
            "position and whether the newest save says it is still there. One-shot is a "
            "measured property and not a label: respawn re-tests every run that no emitted "
            "class ever comes back, which is why the two regrowing plants are excluded and "
            "the mushroom is not. The row set is the map's own placement list for those "
            "classes; whether the class list itself is complete is a separate question, and "
            "accounting plus source.placements.other_levels_in_the_container are what a "
            "missing class would show up in."
        ),
        "why": (
            "The world is not saved. A save lists the collectibles still standing in cells "
            "it has written, and separately the map actors it considers gone; nothing in it "
            "states how many the map placed. The denominator therefore has to come from the "
            "map itself, which is what the placements below are; the saves supply only state."
        ),
        "source": {
            "placements": {
                "kind": "the game's own cooked map assets, read from the installed game",
                "container": (
                    f"{ctx.store.cas_path.parent.name}/{ctx.store.cas_path.stem}.utoc + .ucas"
                ),
                "container_utoc_version": ctx.store.version,
                "container_flags": hex(ctx.store.flags),
                "container_flags_note": (
                    "0x0d is Compressed|Signed|Indexed. The Encrypted bit is unset and the "
                    "encryption key GUID is null: signed is not encrypted, and no DRM is "
                    "circumvented by reading it."
                ),
                "container_bytes": [ctx.store.toc_bytes, ctx.store.cas_bytes],
                "container_modified_utc": ctx.store.toc_mtime.isoformat(timespec="seconds"),
                "game_build": ctx.game_build,
                "packages_read": ctx.world.packages_read,
                "packages_with_no_level_export": ctx.world.packages_without_a_level,
                "map_actors_placed": ctx.world.actor_count,
                "actor_classes_placed": len(ctx.world.class_counts),
                "level_read": MAP_PREFIX,
                "other_levels_in_the_container": ctx.other_levels,
                "other_levels_note": (
                    "that the level above is the whole world was once a sentence in the "
                    "source; it is this list instead. Every other .umap the container holds "
                    "is walked with the identical actor rule and reported with its actor "
                    "count and, the point of it, how many actors of a class this file emits "
                    "it places -- actors_of_an_emitted_class. All of them empty is what "
                    "makes 'the row set is the map's placement list' a statement about the "
                    "game rather than about one directory. A non-empty one would mean rows "
                    "are missing. Note what these levels ARE: a HUB audio sub-level, the "
                    "dedicated-server entry, the two main-menu backdrops and a developer "
                    "test map -- the test map does place resource nodes, which is why the "
                    "check is run against emitted classes rather than eyeballed."
                ),
                "actors_read_but_given_no_transform": _by_count(ctx.world.unresolved_roots),
                "actors_read_but_given_no_transform_note": (
                    "an actor of a class this file reads whose root component or attach chain "
                    "could not be resolved, so it has no position and is NOT a row. Empty "
                    "means every one was placed. This is the one way a collectible could go "
                    "missing without the accounting noticing, since the class histogram counts "
                    "it either way -- hence a number here rather than nothing."
                ),
                "actor_rule": (
                    "an export whose Outer is the package's /Script/Engine.Level export. A "
                    "class-path prefix test cannot be used: a native class is a 62-bit hash "
                    "rather than a path, so a /Game/ test silently drops every one of them."
                ),
                "script_objects": {
                    "source": "global.utoc chunk type 5 (ScriptObjects)",
                    "chunk_bytes": ctx.scripts.chunk_bytes,
                    "objects": ctx.scripts.object_count,
                    "script_packages": ctx.scripts.package_count,
                    "unresolved_class_hashes_in_the_map": ctx.world.unresolved_script_classes,
                    "role": (
                        "turns a script-import hash back into /Script/FactoryGame.<Class>. "
                        "With 0 unresolved hashes above, every actor class in the map has a "
                        "name."
                    ),
                },
                "seconds": round(ctx.world.seconds, 1),
                "decompressor": {
                    "name": "pyooz",
                    "version": ctx.pyooz_version,
                    "import_name": "ooz",
                    "licence": "GPL-3.0",
                    "role": (
                        "Oodle block decompression, offline, at generation time only. An "
                        "OPTIONAL dependency: the `gen` extra in pyproject.toml, pinned "
                        "exactly because it decides these bytes, and asked for on the "
                        "command line -- `uv run --extra gen python "
                        "tools/gen_world_collectibles.py`. It is imported at module scope "
                        "nowhere, and lazily inside one function of "
                        "satisfactory_mcp.core.gameassets.iostore, so the server and the "
                        "test suite run with it absent. No part of it is present in this "
                        "file."
                    ),
                },
                "licence": (
                    "the coordinates are facts about Coffee Stain's map, read out of the "
                    "installed game. No third-party world table contributed to this file."
                ),
            },
            "status": {
                "kind": "the player's own save files",
                "role": "state only -- collected / present / unknown. Never a position.",
                "session": ctx.newest.session,
                "save_files_found": ctx.files_found,
                "saves_read": len(ctx.on_disk),
                "files_that_are_not_a_save": ctx.files_found - len(ctx.on_disk),
                "files_that_are_not_a_save_note": (
                    "the game drops a 105-byte ServerManager_V2.sav beside the real saves. "
                    "It is not a save game; any other count here would be a save this "
                    "project's parser cannot read, which is a bug worth chasing."
                ),
                "sessions_on_disk": _by_count(ctx.sessions),
                "saves_used": len(ctx.facts),
                "saves_in_another_session": len(ctx.on_disk) - len(ctx.facts),
                "sessions_note": (
                    "the union of two sessions is not a world: an instance name means "
                    "whatever the save that wrote it meant. Only the largest session's "
                    "saves are used, and the rest are counted here and dropped."
                ),
                "save_versions": sorted({f.save_version for f in ctx.facts}),
                "build_versions": sorted({f.build_version for f in ctx.facts}),
                "saves_predating_world_partition": len(ctx.pre_partition),
                "saves_that_join_no_row_here": sum(
                    1 for v in ctx.joined_by_save.values() if v == 0
                ),
                "rows_the_pre_partition_saves_can_key": len(ctx.pre_partition_keys),
                "cells_the_pre_partition_saves_key_through": _by_count(ctx.pre_partition_cells),
                "pre_partition_note": (
                    f"{len(ctx.pre_partition)} of the saves used are below saveVersion "
                    f"{FIRST_MODERN_BODY}, from before the world was partitioned. They parse -- "
                    "the body layout is version-gated on the header's own save_version -- and "
                    "they are read because they are evidence for the rename below. What they "
                    "can key is measured rather than assumed: their per-tile level records "
                    "carry a package path for a tile the current map has no cell for, which "
                    "leaves only the names both layouts share -- see "
                    "cells_the_pre_partition_saves_key_through for what those turn out to be. "
                    "They set no state either way: the newest save does that."
                ),
                "saved_between": [
                    ctx.facts[0].when.date().isoformat(),
                    ctx.newest.when.date().isoformat(),
                ],
                "play_duration_hours": [
                    round(ctx.facts[0].play_seconds / 3600, 1),
                    round(ctx.newest.play_seconds / 3600, 1),
                ],
                "newest_save": ctx.newest.name,
                "state_authority": (
                    "the newest save by the clock it was written at, not by file mtime -- an "
                    "autosave rewritten in place has a fresh mtime and an old clock. Its live "
                    "set and destroyed list are cumulative, and "
                    "rows_only_older_saves_could_state below measures whether the union of "
                    "all of them would say anything more."
                ),
            },
        },
        "derivation": {
            "placed": (
                "an actor export of one of these classes in a cooked GameLevel01 package. "
                "EXACT for the classes named: it is the map's own placement list and not a "
                "count of sightings. It says nothing about classes not named -- see "
                "class_census and not_classified."
            ),
            "collected": "the newest save's destroyed-actor list names this (cell, instance)",
            "present": (
                "the newest save has a live actor header at this (cell, instance) whose "
                f"position is within {POSITION_TOLERANCE_CM:.0f} cm of the map's"
            ),
            "unknown": (
                "neither. The game has never had that actor loaded while writing a save, so "
                "nothing on disk says whether it is still standing. NOT 'present'."
            ),
            "position": (
                "the actor's root SceneComponent transform, composed up the AttachParent "
                "chain with class component-template defaults filled in, in world-space cm"
            ),
            "class": "the export's class, read from the package. Never inferred from a name.",
            "looted": (
                "mHasBeenLooted off the live drop-pod body. null where the pod's state is "
                "not present, because an unobserved pod's loot flag is unobserved too."
            ),
            "contents": (
                "the loot cache's own mPickupItems: an FInventoryStack whose FInventoryItem "
                "carries no tagged members, so the item class is an FPackageIndex read "
                "through the package's import map."
            ),
            "unlock_cost": (
                "the drop pod's own mUnlockCost. cost_type is null where the pod does not "
                "serialise it, which means it holds the class default -- and since other "
                "pods explicitly write both Item and Power, the default is a third value "
                "whose name is nowhere in the cooked assets. Not guessed."
            ),
            "attached_to": (
                "the instance whose root component this actor's root is attached to, from "
                "the map's own AttachParent chain -- exact, not a distance guess"
            ),
            "hazard": (
                "INFERENCE, not placement. Geometry between this collectible and other map "
                "actors, plus the radii those actors' own classes declare. See "
                "hazard_context for what each key means and what it does not."
            ),
        },
        "respawn": {
            "what": (
                "the premise every 'state' in this file rests on, tested rather than "
                "asserted: that taking a collectible removes its actor for good, so "
                "'collected' is durable. If any emitted class came back, 'collected' would "
                "be a snapshot of one save's opinion and the table would have to say so."
            ),
            "schema_rule": (
                "a class whose contents regrow gets no row. It would need a state field "
                "that cannot be filled: a harvested berry bush is NOT destroyed -- it stays "
                "live with mUpdatedOnDayNr set and its fruit comes back -- so 'present' "
                "would mean 'a bush is here', which a consumer would read as 'there is "
                "fruit here'. Rather than ship a state-shaped field that cannot carry a "
                "state, the two regrowing classes are in _meta.excluded with their map "
                "counts, and nothing in this file offers a remaining-set for them. "
                "mSavedNumItems in the values below is the plant's fixed YIELD and is never "
                "a countdown: every nut bush on disk writes 5."
            ),
            "durability": {
                "method": (
                    "over the saves used, in save-clock order. Two failure modes: a key "
                    "leaving a destroyed list, and a key a save called destroyed turning up "
                    "live in a later one. The first is only meaningful between saves of the "
                    "SAME build, because a build that re-issues instance names makes a key "
                    "disappear without anything coming back -- those pairs are counted and "
                    "skipped. The second is gated on position, because an auto-numbered "
                    "instance name is not identity."
                ),
                "consecutive_same_build_pairs_compared": ctx.pairs_compared,
                "pairs_skipped_because_the_build_changed": ctx.pairs_skipped,
                "destroyed_observations_by_class": _by_count(ctx.destroyed_observations),
                "keys_that_left_the_destroyed_list_by_class": _by_count(ctx.left_the_list),
                "keys_that_left_the_destroyed_list_note": (
                    "by the class the MAP gives that key, so 'not a map-placed actor at all' "
                    "means one of the player's own actors -- a destroyed record for something "
                    "the player built, which the game is free to forget. No emitted class "
                    "appearing here is the result that matters."
                ),
                "rows_destroyed_then_live_again_by_class": _by_count(ctx.revived),
                "rows_destroyed_then_live_again_rejected_by_position_by_class": _by_count(
                    ctx.revived_displaced
                ),
                "rows_a_single_save_lists_as_both_destroyed_and_live_by_class": _by_count(
                    ctx.coexisting
                ),
                "rows_a_single_save_lists_as_both_by_save_version": {
                    str(version): count
                    for version, count in sorted(ctx.coexisting_by_version.items())
                },
                "in_the_newest_save": ctx.newest_coexisting,
                "both_note": (
                    "a save CAN name one (cell, instance) in its destroyed list and hold a "
                    "live actor at that same key whose position agrees with the map. Those "
                    "records contradict each other and neither is a respawn: before the world "
                    "was partitioned the bare name BP_Crystal1 belonged to two different "
                    "slugs in two different levels, and the migration keyed one of them into "
                    "the other's cell. That is the game's own migration hitting the same "
                    "identity problem this file refuses to ignore. The version "
                    "breakdown is the point: they are a saveVersion 52 phenomenon and "
                    "in_the_newest_save is 0, so no state in this file currently rests on the "
                    "tie. Should it ever be non-zero, the precedence is that the destroyed "
                    "list wins and the row reads collected -- stated here because it would "
                    "otherwise be an accident of the order two ifs are written in."
                ),
                "rejected_by_position_note": (
                    "these are what a name-only test would have called resurrections. A "
                    "player-dropped FGItemPickup_Spawnable can carry the same bare "
                    "auto-numbered name as a map cache and sit 100 m away, and the same "
                    "goes for a record the 52 -> 60 rename left pointing at the wrong "
                    "cell. Reported as its own number so the value of the position gate is "
                    "visible rather than argued: keys_that_left_the_destroyed_list and "
                    "rows_destroyed_then_live_again are what survives it."
                ),
            },
            "flora": ctx.flora,
            "flora_note": (
                "the three harvestable plants, probed on every live record in every save of the "
                "session used. "
                "records_carrying is presence, not value: a class carrying no mNumRespawns "
                "on any of tens of thousands of those records has no respawn machinery, which is "
                "structural evidence and not an inference from never having watched one "
                "regrow. 'respawns' is set from exactly that, and 'emitted_as' says what "
                "this file did about it -- null means the class is in _meta.excluded. "
                "counter_fell is the number that would contradict the model."
            ),
            "flora_records_whose_properties_would_not_decode": ctx.flora_unreadable,
        },
        "identity": {
            "key": "(cell, instance)",
            "key_is_unique": ctx.duplicate_keys == 0,
            "duplicate_keys": ctx.duplicate_keys,
            "duplicate_instance_names_among_rows": ctx.duplicate_names,
            "map_actors_in_gamelevel01": ctx.world.actor_count,
            "map_actors_with_a_blueprint_class": sum(ctx.world.game_class_counts.values()),
            "blueprint_actor_classes": len(ctx.world.game_class_counts),
            "widening_note": (
                "map_actors_with_a_blueprint_class is what a /Game/ prefix walk sees, and it "
                "is reported beside the full count so the widening to native classes can be "
                "checked as additive rather than taken on trust: the blueprint figure must "
                "not move."
            ),
            "distinct_keys": ctx.world.distinct_keys,
            "distinct_instance_names": ctx.world.distinct_names,
            "instance_names_carrying_two_classes": ctx.world.names_with_two_classes,
            "names_with_the_map_s_placement_id": ctx.world.uaid_names,
            "names_with_the_map_s_placement_id_distinct": ctx.world.uaid_names_distinct,
            "actors_reusing_an_instance_name_by_class": _by_count(ctx.world.name_repeats_by_class),
            "note": (
                "(cell, instance) is unique over every map-placed actor -- duplicate_keys is "
                "0. The bare instance name is NOT, and rather than say which classes are to "
                "blame, actors_reusing_an_instance_name_by_class counts them: widening the "
                "walk to native classes brought in per-cell housekeeping singletons that "
                "reuse one name across every cell, and they account for the whole of the "
                "difference between map_actors_in_gamelevel01 and distinct_instance_names. "
                "None of them is a collectible class. The _UAID_ names -- the map's own "
                "placement ids -- stay globally unique."
            ),
            "placement_id_note": (
                "the _UAID_ suffix is a sufficient mark of a map placement and not a "
                "necessary one: some map-placed actors carry names inherited from older "
                "hand-placed ones. with_the_map_s_own_placement_id is reported per category "
                "so a consumer filtering on the suffix can see what that filter would cost."
            ),
            "rows_within_1m_of_another_row_in_the_same_category": ctx.coincident,
            "rows_within_1m_of_another_row_by_category": _by_count(ctx.coincident_by_category),
            "coincident_pairs_the_saves_hold_a_record_for_both_of": ctx.coincident_both_recorded,
            "coincident_pairs_where_both_carry_a_placement_id": (
                ctx.coincident_both_have_a_placement_id
            ),
            "coincident_pairs_the_saves_give_different_states": ctx.coincident_states_differ,
            "coincident_note": (
                "pairs, not rows, and a question rather than an error: two rows of one "
                "category within a metre of each other COULD be one physical collectible "
                "emitted twice. For every artefact class the count is 0, which is the result "
                "worth having. It is not 0 for the mushroom, which is what a mushroom is -- "
                "they grow in clumps. What settles those pairs is identity, not distance, and "
                "the three numbers beside the count are exactly how far the evidence goes: "
                "the saves hold a SEPARATE record for both members of "
                "coincident_pairs_the_saves_hold_a_record_for_both_of of them, so the game "
                "itself tracks two actors there; both members carry the map's own globally "
                "unique _UAID_ placement id in "
                "coincident_pairs_where_both_carry_a_placement_id of them. "
                "coincident_pairs_the_saves_give_different_states would be the strongest "
                "evidence of all -- one harvested while the other still stands, which one "
                "actor cannot be -- and it is reported even though this player's saves happen "
                "to give it as 0: the pairs are all in the same state, so that particular "
                "proof is simply not available here."
            ),
        },
        "class_census": {
            "what": (
                "a narrow regression guard, and it is worth being exact about how narrow. "
                "The bug it exists for was a missing CLASS rather than a missing row: a "
                "native class cannot appear in a /Game/ walk, so every loot cache was absent "
                "with nothing in the file to show it. What this section catches is that "
                "specific shape of regression -- a class whose NAME CONTAINS 'pickup' that "
                "stops being emitted, or a new one the map gains. emitted_as null on one of "
                "them is either a deliberate exclusion or a bug."
            ),
            "net": (
                "a substring test on the class name, which most collectible classes fail: "
                "BP_Crystal_C, BP_WAT1_C, BP_WAT2_C and BP_Shroom_01_C are all rows in this "
                "file and none of them would ever appear below. This is NOT a list of the "
                "world's collectible classes and it is not a completeness proof for the row "
                "set. The wider guard is accounting, which forces every class the map places "
                "into a category, excluded or not_classified and checks the three sum to the "
                "map's own actor count; other_levels_in_the_container is the guard for "
                "collectibles placed outside the level this file reads."
            ),
            "classes": ctx.census,
        },
        "hazard_context": {
            "what": (
                "derived context, kept in each row's own 'hazard' object so it can never be "
                "mistaken for a placement. Nothing here is a fact about the collectible: it "
                "is geometry between it and other map actors."
            ),
            "hostiles_nearby": (
                "creature descriptor -> how many creatures the spawners within "
                f"reporting_radius_cm ({HAZARD_RADIUS_CM:.0f} cm) of this row hold, from each "
                "spawner's mCreatureClass and the length of its mSpawnData, plus crab "
                "hatchers placed directly. That radius is THIS FILE'S and nothing the game "
                "declares, which is why the row also carries nearest_hostile_cm and lets a "
                "consumer draw its own line."
            ),
            "spawns_here": (
                "the subset whose own declared radius contains this row -- a spawner's "
                "mSpawnRadius, a hatcher's mDetectionRadius. This one IS a map-declared "
                "fact rather than a chosen threshold."
            ),
            "passive_creatures_excluded": (
                "hostile means: mIsPassiveCreature is NOT set on the creature's own class "
                "default object. That is the game's own flag rather than a list kept here, "
                "but the split is definitional and not cross-validated -- nothing else in "
                "the cooked assets was checked against it, so a hostile creature that "
                "happens to carry the flag would be dropped silently. What IS measured is "
                "how often the flag could not be read at all: "
                "creature_classes_whose_passivity_is_unknown, which is the number that would "
                "make the split unsafe."
            ),
            "nearest_gas_cm": (
                "distance to the nearest gas actor of any of the three kinds -- spore "
                f"flower, gas pillar, gas perimeter volume -- within {HAZARD_RADIUS_CM:.0f} cm. "
                "A distance, not a verdict: a gas field's own extent scales a box whose base "
                "size is not in the cooked data, so no radius can honestly be derived for it. "
                "gas_field_own_span_cm under sources is the one thing the field does say "
                "about its own reach, and it is what sizes the reporting horizon."
            ),
            "inside_spore_flower_damage_sphere": (
                "the one gas containment test that IS a fact: BP_SporeFlower_C's class "
                "carries a DamageSphere whose SphereRadius the game itself states. The value "
                "is not repeated here -- see class_declared_radius_cm under sources, which "
                "lists it per class with the number of placements behind it, because each "
                "source is tested against its own radius and a single figure quoted in prose "
                "cannot say that."
            ),
            "nearest_uranium_cm": (
                "distance to the nearest resource node or deposit whose resource class "
                "carries mRadioactiveDecay. Scope, exactly: the resource classes the map's "
                "own nodes and deposits name were read and checked -- "
                "resource_classes_checked and radioactive_resource_classes under sources say "
                "how many and which. It is NOT a scan of every item descriptor in the game, "
                "so this is 'the radiation the map places in the ground', not 'all radiation "
                "in Satisfactory'. Radioactive manufactured parts are outside it by "
                "construction: they do not exist until a player makes them, and no map actor "
                "holds one."
            ),
            "nearest_nuclear_hog_spawner_cm": (
                "a SEPARATE reason, never folded into the uranium one. Char_NuclearHog "
                "carries no mRadioactiveDecay of its own, so this is an empirical correlate "
                "-- the designers put nuclear hogs on uranium -- and not a modelled fact."
            ),
            "sources": {
                "hostile_placements": ctx.hazards.hostile_placements,
                "passive_placements_excluded": ctx.hazards.passive_placements,
                "creature_classes_whose_passivity_is_unknown": ctx.hazards.unknown_passivity,
                "hostile_species": _by_count(ctx.hazards.species),
                "hostile_species_note": (
                    "placements, not creatures, and two kinds of key: a Desc_* is the "
                    "creature descriptor a BP_CreatureSpawner_C names, while a Char_* is a "
                    "creature the map places directly with no spawner around it. Both are "
                    "hostiles_nearby sources; only the first has an mSpawnData to say how "
                    "many creatures one placement holds."
                ),
                "creature_spawners_declaring_their_own_radius": ctx.hazards.spawn_radius_declared,
                "creature_spawners_with_no_radius": ctx.hazards.spawn_radius_missing,
                "creature_spawner_radius_cm": ctx.hazards.spawner_radius_cm,
                "creature_spawner_radius_note": (
                    "the two counts above are BP_CreatureSpawner_C only -- the directly "
                    "placed Char_* hatchers are in class_declared_radius_cm below and are "
                    "not spawners. mSpawnRadius varies per placement, so the spread is here "
                    "rather than one number: 'N spawners declare a radius' says nothing "
                    "about how far those radii reach, and it is the radius that decides "
                    "spawns_here."
                ),
                "spore_flowers": ctx.hazards.gas_clouds,
                "class_declared_radius_cm": ctx.hazards.class_declared_radius_cm,
                "class_declared_radius_note": (
                    "per class, every distinct radius its placements declare and how many "
                    "placements there are. Per class because a single number was wrong in "
                    "both directions: the hatchers are TWO classes with different placement "
                    "counts, and one figure taken from whichever placement happened to be "
                    "read first spoke for both of them -- and it spoke wrongly, because "
                    "Char_BigCrabHatcher_C's class declares NO mDetectionRadius at all, so "
                    "its 151 placements can never contribute to spawns_here however close a "
                    "collectible sits. A radius_cm of [null] means exactly that: the class is "
                    "a hostiles_nearby source and not a containment test. distinct_radii > 1 "
                    "would mean the class does not have one radius; spawns_here would still "
                    "be right, since it tests every source against its own, while any single "
                    "number quoted for the class would be wrong."
                ),
                "gas_field_actors": ctx.hazards.gas_fields,
                "gas_field_own_span_cm": ctx.hazards.gas_field_span_cm,
                "gas_field_own_span_note": (
                    "how far the furthest pillar a BP_VolumeGas_01_C names in its own "
                    "mProximityPillarWorldLocations sits from the volume, over the volumes "
                    f"that populate it. The {HAZARD_RADIUS_CM:.0f} cm reporting horizon is "
                    "sized against this median rather than fitted to anything."
                ),
                "widest_declared_radius_cm": ctx.hazards.widest_declared_radius_cm,
                "damage_over_time_volume_classes": _by_count(ctx.hazards.damage_volume_classes),
                "damage_over_time_volume_note": (
                    "FGDamageOverTimeVolume is a native map actor carrying an mDotClass, so "
                    "it is the obvious candidate for the gas channel. It is not one: these "
                    "are what its placements actually deal damage with, resolved per run, "
                    "and they are the box that kills a player who leaves the map."
                ),
                "widest_declared_radius_note": (
                    "the widest radius any source declares, and what the lookup grid is "
                    "sized to. It exceeds the reporting radius, so spawns_here can and does "
                    "fire further out than hostiles_nearby."
                ),
                "radioactive_sources_in_the_ground": ctx.hazards.uranium_sources,
                "resource_classes_checked_for_radioactivity": ctx.hazards.resource_classes_checked,
                "radioactive_resource_classes": ctx.hazards.radioactive_classes,
                "deposits_with_no_resource_class": ctx.hazards.deposits_without_a_resource,
                "deposits_note": (
                    "a deposit that does not serialise mOverrideResourceClass holds its "
                    "class default, which is null, so its resource is unknown rather than "
                    "assumed. Those deposits contribute no radiation."
                ),
            },
            "reporting_radius_cm": HAZARD_RADIUS_CM,
            "rows_touched": {
                "rows": len(ctx.rows),
                "with_a_hostile_in_reporting_radius": ctx.hostile_rows,
                "with_a_hostile_whose_own_radius_contains_them": ctx.spawns_here_rows,
                "inside_a_spore_flower_damage_sphere": ctx.cloud_rows,
                "with_gas_in_reporting_radius": ctx.gas_rows,
                "with_uranium_in_reporting_radius": ctx.uranium_rows,
                "with_a_nuclear_hog_spawner_in_reporting_radius": ctx.hog_rows,
                "with_no_hazard_context_at_all": sum(1 for r in ctx.rows if not r.get("hazard")),
            },
        },
        "not_derived": {
            "what": (
                "context a consumer may want that this file does NOT produce, with the "
                "reason. Listed so an absence reads as a decision rather than an oversight."
            ),
            "cave": (
                "not derivable. The nearest thing in the assets is the FGAmbientVolume sound "
                "zones, of which the ones naming a cave ambient setting are placed for audio "
                "and so neither cover every cave nor stop at its mouth. A cave is a shape a "
                "human recognises; nothing in the cooked packages draws it."
            ),
            "reachability": (
                "not derivable. There is no navmesh in the cooked packages and no jetpack, "
                "hover-pack or hazmat gate anywhere in them. Height above local ground looks "
                "promising and then inverts -- the hardest-to-reach collectibles sit LOWER "
                "above their local ground than the easy ones, because what the measure "
                "actually picks up is biome altitude. z is in every row; any verdict built "
                "on it would be this file's guess wearing data's clothes."
            ),
            "rotation": (
                "not emitted. It was extracted and validated against the saves' own "
                "quaternions, but nothing downstream asks which way a slug faces, so it is "
                "left out rather than carried as 4 more numbers on every row."
            ),
        },
        "status_evidence": {
            "live_records_accepted": len(ctx.present),
            "live_records_displaced": len(ctx.displaced),
            "live_records_displaced_note": (
                "a live header whose (cell, instance) hits a map row but whose position does "
                "not. The saveVersion 52 -> 60 patch re-issued names and cells, and the game "
                "migrates a cell's saved records only when that cell is next streamed, so a "
                "cell not revisited since the patch still holds pre-patch records that key "
                "the wrong map row. These set no state; the rows they touch stay unknown."
            ),
            "displaced_gap_cm": (
                [
                    round(min(g for _k, g in ctx.displaced), 1),
                    round(max(g for _k, g in ctx.displaced), 1),
                ]
                if ctx.displaced
                else None
            ),
            "displaced_gap_note": (
                f"the {POSITION_TOLERANCE_CM:.0f} cm line sits in a gap in the data rather "
                "than inside a cluster: the worst ACCEPTED record is at "
                "position_agreement.max_cm and the nearest REJECT is the first number in "
                "displaced_gap_cm, with nothing between them. Read the two together -- the "
                "margin is not enormous, and if a future game version narrowed it this is "
                "where that would be visible."
            ),
            "displaced_records_a_position_match_could_re_attach": ctx.recoverable,
            "displaced_records_re_attached": 0,
            "displaced_note": (
                "so at most this many rows called unknown here are in fact standing. Measured "
                "and reported rather than applied: state has one derivation, and a distance "
                "heuristic that silently overrides it is worse than an honest unknown."
            ),
            "live_records_with_no_map_row": sum(ctx.orphan_live.values()),
            "live_records_with_no_map_row_by_class": _by_count(ctx.orphan_live),
            "live_records_with_no_map_row_in_any_save_used": sum(ctx.orphan_all.values()),
            "live_records_with_no_map_row_in_any_save_used_by_class": _by_count(ctx.orphan_all),
            "orphan_keys_distinct": len(ctx.orphan_versions),
            "orphan_keys_only_a_pre_partition_save_names": ctx.orphan_old_layout,
            "orphan_keys_whose_name_the_map_places_in_another_cell": ctx.orphan_renamed,
            "orphan_keys_unexplained": ctx.orphan_unexplained,
            "orphan_keys_unexplained_note": (
                "THIS is the number that would say a collectible is missing from the table. "
                "An orphan is a save's live record of an emitted class with no row here, and "
                "it can only be three things: a record written under the pre-partition world "
                "layout, whose level names no current cell matches; a pre-patch record whose "
                "instance name the map now places in a different cell, which is the 52 -> 60 "
                "rename again; or a collectible the map read failed to find. The first two are "
                "counted above and this is the remainder. Scope: LIVE records of the emitted "
                "classes. 0 means no save on disk holds a live record of one of those classes "
                "that this table cannot account for -- it is not a statement about the "
                "destroyed lists, which are accounted for separately in "
                "destroyed_entries_that_are_not_rows_here_by_class, nor about classes this "
                "file does not emit."
            ),
            "live_records_with_no_map_row_note": (
                "a live header of an emitted class whose (cell, instance) is not a row here. "
                "Two things land in it and both are excluded from the table by the same "
                "mechanism -- having no row: a crate the player dropped, which shares "
                "FGItemPickup_Spawnable with the map's loot caches, and an actor from a cell "
                "layout the current map no longer has, which is why the figure over every "
                "save used is large while the newest save's is what it is. The map's own "
                "caches are told apart by having a row at all, never by their name."
            ),
            "destroyed_entries_in_newest_save": len(ctx.newest.destroyed),
            "destroyed_entries_that_are_rows_here": len(ctx.collected),
            "destroyed_entries_that_are_not_rows_here_by_class": _by_count(ctx.destroyed_others),
            "destroyed_entries_note": (
                "what the remainder is, by the class the MAP gives that (cell, instance) -- "
                "not a guess about it. A key the map does not place at all -- one of the "
                "player's own actors, or a pre-patch key the game has not migrated -- would "
                "appear under its own label rather than be folded into a class."
            ),
            "rows_only_older_saves_could_state": len(ctx.older_only),
            "rows_only_older_saves_could_state_note": (
                "0 means the newest save is sufficient and the other saves are corroboration."
            ),
            "distinct_displaced_rows_by_save_version": {
                str(version): len(keys)
                for version, keys in sorted(ctx.displaced_by_version.items())
            },
            "displaced_by_version_note": (
                "distinct rows some save of that version displaces. The pre-patch 52 saves "
                "displace more, as they must, but the count does not fall to zero at 60 -- "
                "which is the whole point: the game has migrated the cells this player has "
                "revisited since the patch and not the others."
            ),
        },
        "position_agreement": {
            "what": (
                "the accepted positions re-measured against the newest save's own live actor "
                "headers, which is the game's word on where its actors are. The displaced "
                "records are excluded and counted under status_evidence instead."
            ),
            **_spread(ctx.agreeing),
            "over_1cm_note": (
                "a level-design edit between game versions nudged some actors by whole "
                "centimetres and the game rewrites the saved transform when the cell is next "
                "streamed, so a handful of cells still carry the pre-edit value. The map is "
                "the current one."
            ),
            "guard": (
                "a component's transform is serialised only where it differs from its class "
                "template, and BP_WAT2's root template is scaled 2.7. Composing with a "
                "default of 1.0 instead puts every shrine 73.1 cm out, which would show up "
                "here as a median in the tens of centimetres rather than a small fraction of "
                "one."
            ),
        },
        "totals": {
            "denominator": (
                "'placed' is EXACT for the classes this file names -- it is the map's own "
                "count. 'collected', 'present' and 'unknown' are OBSERVED, and only through "
                "this player's saves: they are what the saves know, and they shift as the "
                "player explores. placed = collected + present + unknown, always."
            ),
            "rows_any_save_mentions_note": (
                "rows some save on disk names at all, live or gone, however displaced. It is "
                "at least collected + present, and where it is 0 the class is not "
                "save-serialised: the game keeps no record of it, so it can be located and "
                "never state-tracked."
            ),
            "by_category": ctx.per_category,
            "rows": len(ctx.rows),
            "collected": sum(1 for r in ctx.rows if r["state"] == "collected"),
            "present": sum(1 for r in ctx.rows if r["state"] == "present"),
            "unknown": sum(1 for r in ctx.rows if r["state"] == "unknown"),
            "pedestals": ctx.pedestals,
            "pedestals_note": (
                "a shrine is the base the artifact above it stands on, named exactly by the "
                "map's own AttachParent -- see attached_to on each shrine row. The pairing is "
                "1:1, so a shrine is a second row about one find and NOT a second collectible: "
                "summing every category over-counts artifacts by the number of shrines."
            ),
        },
        "exploration": {
            "what": (
                "how much of the map the SAVES have observed. This is about the player: the "
                "row set does not depend on it, because the rows come from the map."
            ),
            "collectibles_observed": len(ctx.rows) - len(ctx.unknown_rows),
            "collectibles_observed_pct": round(
                100 * (len(ctx.rows) - len(ctx.unknown_rows)) / len(ctx.rows), 1
            ),
            "collectibles_unknown": len(ctx.unknown_rows),
            "unknown_in_a_cell_with_no_level_record_in_any_save": ctx.unknown_no_record,
            "unknown_in_a_cell_the_saves_have_partly_streamed": len(ctx.unknown_rows)
            - ctx.unknown_no_record,
            "cells_holding_a_collectible": len(ctx.collectible_cells),
            "cells_holding_a_collectible_with_no_level_record": len(ctx.cells_no_record),
            "cells_the_newest_save_has_a_level_record_for": len(ctx.newest.recorded_cells),
            "cells_the_newest_save_s_grid_table_declares": len(ctx.newest.declared_cells),
            "cells_holding_a_collectible_the_grid_table_does_not_declare": len(
                ctx.collectible_cells - ctx.newest.declared_cells
            ),
            "there_is_deliberately_no_cells_the_map_places_an_actor_in_figure": (
                f"it would be {len(ctx.map_cells)}, which is exactly source.placements."
                "packages_read, because every cooked cell holds the per-cell housekeeping "
                "singletons (FGWorldSettings, Model) whatever else is in it. It used to be "
                "printed above and its only use was to be divided into the two save-side cell "
                "counts, which measures nothing at all. The cell population that is about "
                "collectibles is cells_holding_a_collectible."
            ),
            "cell_counts_are_not_denominators": (
                "the cell counts above are still three different populations and none divides "
                "another. The map places collectibles in one set of cells, the save's grid "
                "table declares another set of runtime cells, and the save has level records "
                "for a third -- including cells the grid table does not declare, some of which "
                "hold a collectible, which is what "
                "cells_holding_a_collectible_the_grid_table_does_not_declare counts. Read "
                "them side by side, not as a fraction."
            ),
            "map_actors_in_cells_the_newest_save_records": ctx.map_actors_in_recorded_cells,
            "of_those_the_newest_save_has_a_record_of": ctx.observed_map_actors,
            "cell_is_not_a_unit_of_coverage": (
                "a partition cell streams in pieces. Over all "
                f"{len(ctx.world.class_counts)} map-placed classes, the newest save has a level "
                f"record for cells holding {ctx.map_actors_in_recorded_cells} map actors and a "
                f"record of only {ctx.observed_map_actors} of them "
                f"({100 * ctx.observed_map_actors / max(ctx.map_actors_in_recorded_cells, 1):.0f}"
                "%), so "
                "'the cell has been visited' does not mean 'its contents are known'. That is "
                "why unknown is a per-collectible state here and not a per-cell one, and why "
                "the cell counts above are context rather than the coverage figure."
            ),
        },
        "naming": {
            "what": (
                "a naming aid, kept apart from the placements. The placements are map facts; "
                "this section is about what a consumer may and may not infer from a name."
            ),
            "rule": (
                "An instance name never decides a class. The digits are a placement counter, "
                "not a class index -- a BP_WAT1_C is named BP_WAT133 -- and the map's actors "
                "kept the names of the actors they were copied from, so BP_Crystal_mk2_C "
                "instances named BP_Crystal_C_15 exist. The class comes from the map."
            ),
            "resolves_from": (
                "the (cell, instance) of the placements below. A save's destroyed-actor entry "
                "is a bare path with no class; look it up here and the class is exact."
            ),
            "prefix_rule_wrong": ctx.wrong,
            "prefix_rule_silent": ctx.silent,
            "prefix_rule_checked": len(ctx.rows_in),
            "prefix_rule_note": (
                "a longest-prefix rule over class stems, scored against the map's own "
                "answer for the very rows in this file. 'silent' is a name no stem matched -- "
                "and most of the silence is the mushroom: 1,363 of its placements are named "
                "BP_Shroom_<counter>, with the digits glued to a stem that is BP_Shroom_01, "
                "so no split recovers the class. The same failure as BP_WAT1 vs BP_WAT2."
            ),
            "rows_whose_name_stem_is_a_different_map_class": _by_count(ctx.borrowed),
            "borrowed_name_note": (
                "read as 'a name-based rule would say the first and the map says the second'. "
                "Scored against every class the map places, not only the emitted ones, which "
                "is what makes it worth its space: 154 mushrooms carry names beginning "
                "BP_BerryBush, so a name rule would file them under a class this file "
                "deliberately does NOT emit because it regrows -- the answer would not merely "
                "be the wrong category, it would be a row that should not exist keyed to a "
                "plant that does. Empty would mean no row's name belongs to another class."
            ),
            "glued_index_names": len(ctx.glued),
            "glued_index_note": (
                "names matching ^BP_WAT[0-9], where the placement counter is glued to the "
                "stem so no split can tell BP_WAT1 (somersloop) from BP_WAT2 (Mercer "
                "sphere). The map resolves all of them."
            ),
            "glued_index_by_category": _by_count(ctx.glued_by_category),
            "glued_index_destroyed_in_newest_save": len(ctx.glued_destroyed),
            "glued_index_destroyed_resolved": _by_count(
                collections.Counter(CATEGORIES[p.cls] for p in ctx.glued_destroyed)
            ),
            "glued_index_destroyed_unresolved": ctx.glued_destroyed_unresolved,
        },
        "accounting": {
            "what": (
                "every actor the map places, split three ways. There is no fourth bucket and "
                "no overlap, so adds_up being true is the check that a class cannot go "
                "missing the way the loot caches did -- silently, with nothing in the file to "
                "show for it."
            ),
            "map_actors_in_gamelevel01": ctx.world.actor_count,
            "emitted_as_rows": len(ctx.rows),
            "excluded_on_purpose": sum(e["placed_by_the_map"] for e in ctx.excluded.values()),
            "not_classified": sum(ctx.unclassified.values()),
            "adds_up": len(ctx.rows)
            + sum(e["placed_by_the_map"] for e in ctx.excluded.values())
            + sum(ctx.unclassified.values())
            == ctx.world.actor_count,
        },
        "excluded": ctx.excluded,
        "excluded_note": (
            "map-placed classes deliberately not emitted as rows, with the map's own count "
            "of each so 'not a collectible' can never be read as 'we missed it'. This list "
            "is a set of decisions, NOT a proof of completeness: a class absent from both "
            "this list and the rows is in not_classified with its count, and class_census is "
            "the check that specifically covers pickups."
        ),
        "not_classified": ctx.unclassified,
        "not_classified_note": (
            "every remaining actor class the packages under source.placements.level_read "
            "place, native and blueprint alike, with counts. What that buys is bounded and "
            "worth stating: every actor the walk FOUND lands in a category, in excluded or "
            "here, and accounting.adds_up checks the three against the map's own actor "
            "count -- so a class cannot go missing the way the loot caches did. It is not "
            "evidence that the walk found every actor, which is what "
            "source.placements.actors_read_but_given_no_transform and "
            "packages_with_no_level_export are for, nor that this level is the only one -- "
            "see other_levels_in_the_container. "
            f"The largest are {largest}. "
            "One entry may read 'export:N': that is an actor whose class is an export of its "
            "own package rather than an import, reported as it is found rather than binned."
        ),
        "join_key": "instance (matches save actor instanceName exactly)",
        "cell_note": (
            "'cell' is the cooked package the map places the actor in, and it is byte-"
            "identical to the save's own level record name -- which is what makes the join "
            f"exact. It is a world-partition cell for all but {persistent} "
            "rows, which the map puts in Persistent_Level itself. It is not a spatial box: "
            "one cell's actors can be far apart."
        ),
        "units": "centimetres; north is -Y, east is +X, up is +Z",
    }
    return meta


def build(
    world: MapWorld,
    hazards: HazardWorld,
    facts: list[SaveFacts],
    store: IoStore,
    scripts: ScriptObjects,
    game_build: str | None,
    pyooz_version: str,
    on_disk: list[SaveFacts],
    files_found: int,
    other_levels: list[dict],
) -> tuple[list[dict], dict]:
    """Turn map placements plus save facts into rows and ``_meta``.

    Rows and ``_meta`` are built together because every number in ``_meta`` is a by-product
    of one merge, and a total computed separately can drift from the rows it describes. The
    merge is nine measurements over one shared ``BuildContext``, in a fixed order because
    each may read what the earlier ones measured; ``_assemble_meta`` only reshapes.
    """
    facts = sorted(facts, key=lambda f: (f.ticks, f.play_seconds))
    rows_in = [p for p in world.placements if p.cls in CATEGORIES]
    by_key = {(p.cell, p.instance): p for p in rows_in}
    by_name: dict[str, list[Placement]] = collections.defaultdict(list)
    for placement in rows_in:
        by_name[placement.instance].append(placement)
    ctx = BuildContext(
        world=world,
        hazards=hazards,
        facts=facts,
        newest=facts[-1],
        store=store,
        scripts=scripts,
        game_build=game_build,
        pyooz_version=pyooz_version,
        on_disk=on_disk,
        files_found=files_found,
        other_levels=other_levels,
        rows_in=rows_in,
        by_key=by_key,
        duplicate_keys=len(rows_in) - len(by_key),
        duplicate_names=sum(len(v) - 1 for v in by_name.values()),
    )
    for measure in (
        _measure_status,
        _measure_pedestals,
        _measure_coincident_positions,
        _measure_older_save_staleness,
        _measure_orphans,
        _measure_exploration,
        _measure_naming,
        _measure_respawn,
        _measure_per_category,
    ):
        measure(ctx)
    return ctx.rows, _assemble_meta(ctx)


# --------------------------------------------------------------------------------------


def main() -> int:
    parser = base_parser(__doc__.splitlines()[0])
    parser.add_argument(
        "saves",
        nargs="?",
        default=Path.home() / "AppData/Local/FactoryGame/Saved/SaveGames",
        type=Path,
        help="directory of .sav files; a per-account subdirectory is searched too",
    )
    parser.add_argument(
        "-o",
        "--out",
        type=Path,
        default=ROOT / "data" / "world_collectibles.json",
        help="destination JSON",
    )
    args = parser.parse_args()

    pyooz_version = require_gen("ooz")["pyooz"]

    paks = args.game / "FactoryGame" / "Content" / "Paks"
    if not (paks / "FactoryGame-Windows.utoc").exists():
        print(f"no FactoryGame-Windows.utoc under {paks}")
        return 1
    print(f"reading the map from {paks} with pyooz {pyooz_version}")
    scripts = ScriptObjects(paks, oodle_decompress)
    print(
        f"  global.utoc ScriptObjects: {scripts.object_count} objects in "
        f"{scripts.package_count} script packages, {scripts.chunk_bytes / 1e6:.1f} MB"
    )
    store = IoStore(paks, "FactoryGame-Windows", oodle_decompress)
    print(
        f"  .utoc v{store.version} flags {hex(store.flags)}, {store.entry_count} entries, "
        f"{store.block_count} blocks, {store.block_size // 1024} KiB blocks, "
        f"methods {store.methods}"
    )
    world = read_map(store, scripts)
    print(
        f"  {world.packages_read} packages, {world.actor_count} map-placed actors in "
        f"{len(world.class_counts)} classes "
        f"({sum(world.game_class_counts.values())} of them blueprint-classed, which is what "
        f"a /Game/ walk would see), {len(world.placements)} collectibles, "
        f"{len(world.hazards)} hazard actors, {store.blocks_read} blocks -> "
        f"{store.bytes_out / 1e6:.0f} MB, {world.seconds:.1f}s"
    )
    for reason, count in world.unresolved_roots.most_common(5):
        print(f"  WARNING: {count} x {reason}")
    stale = [cls for cls in EXCLUDED if not world.class_counts.get(cls)]
    if stale:
        print(f"  WARNING: EXCLUDED names {len(stale)} class(es) the map does not place: {stale}")

    index = AssetIndex(store)
    classes = ClassFacts(store, index)
    creatures = CreatureNames(store, index, classes)
    resources = {h.label for h in world.hazards if h.kind == "resource" and h.label}
    decay = Radioactivity(classes, index, resources)
    hazards = build_hazards(world, creatures, decay)
    if NUCLEAR_HOG not in hazards.species:
        print(
            f"  WARNING: no {NUCLEAR_HOG} among the hostile species, so the second radiation "
            f"reason can never fire. Species found: {sorted(hazards.species)}"
        )
    print(
        f"  hazards: {hazards.hostile_placements} hostile placements over "
        f"{len(hazards.species)} species ({hazards.passive_placements} passive ones dropped "
        f"by mIsPassiveCreature), {hazards.gas_clouds} spore flowers, "
        f"{hazards.gas_fields} gas-field actors, {hazards.uranium_sources} radioactive "
        f"sources over {hazards.resource_classes_checked} resource classes checked "
        f"({list(hazards.radioactive_classes)})"
    )
    for cls, declared in hazards.class_declared_radius_cm.items():
        print(
            f"  declared radius: {cls:24} {declared['placements']:>5} placements  "
            f"{declared['distinct_radii']} distinct {declared['radius_cm']}"
        )
    other_levels = read_other_levels(store, scripts)
    stray = [e for e in other_levels if e.get("actors_of_an_emitted_class")]
    print(
        f"  {len(other_levels)} other .umap in the container "
        f"({sum(e.get('actors', 0) for e in other_levels)} actors); "
        f"{len(stray)} of them place an actor of an emitted class"
    )
    if stray:
        print(f"  WARNING: rows are missing -- another level places collectibles: {stray}")

    paths = find_saves(args.saves)
    if not paths:
        print(f"no .sav files under {args.saves}")
        return 1
    print(f"\nreading {len(paths)} save(s) from {args.saves} for state")
    map_classes = set(world.class_counts)
    on_disk = [
        got
        for got in (read_save_facts(path, map_classes, keep_all_classes=False) for path in paths)
        if got is not None
    ]
    if not on_disk:
        print("no readable saves")
        return 1

    # The session filter runs BEFORE the newest save is chosen, and the newest save is
    # chosen by the clock it recorded rather than by mtime. Both orderings matter: picking
    # first would let a save from another session decide state, and trusting mtime would let
    # an autosave rewritten in place outrank a later real save.
    sessions = collections.Counter(f.session for f in on_disk)
    facts = on_disk
    if len(sessions) > 1:
        keep = sessions.most_common(1)[0][0]
        print(f"  {len(sessions)} sessions present: {dict(sessions)}")
        print(f"  the union of two worlds is not a world; keeping {keep!r} only")
        facts = [f for f in on_disk if f.session == keep]

    newest = max(facts, key=lambda f: (f.ticks, f.play_seconds))
    print(f"  newest by save clock: {newest.name} ({newest.when.date()}); re-reading it in full")
    again = read_save_facts(newest.path, map_classes, keep_all_classes=True)
    if again is None:
        print(f"  {newest.name} became unreadable on the second pass")
        return 1
    facts[facts.index(newest)] = again

    rows, meta = build(
        world,
        hazards,
        facts,
        store,
        scripts,
        installed_build_from_exe(args.game),
        pyooz_version,
        on_disk,
        len(paths),
        other_levels,
    )
    args.out.write_text(json.dumps({"_meta": meta, "collectibles": rows}, indent=1), "utf-8")

    totals, evidence, ident = meta["totals"], meta["status_evidence"], meta["identity"]
    explore, agree, naming = meta["exploration"], meta["position_agreement"], meta["naming"]
    shown = args.out.relative_to(ROOT) if args.out.is_relative_to(ROOT) else args.out
    print(f"\nwrote {shown}  {len(rows)} placements  {args.out.stat().st_size} B")
    print(f"{'category':30}{'placed':>7}{'collected':>10}{'present':>9}{'unknown':>9}")
    for category, entry in totals["by_category"].items():
        extra = []
        if entry.get("present_and_looted") is not None:
            extra.append(f"{entry['present_and_looted']} of those present already looted")
        if entry.get("distinct_item_types") is not None:
            extra.append(f"{entry['items_in_total']} items of {entry['distinct_item_types']} types")
        tail = f"   ({'; '.join(extra)})" if extra else ""
        print(
            f"{category:30}{entry['placed']:>7}{entry['collected']:>10}"
            f"{entry['present']:>9}{entry['unknown']:>9}{tail}"
        )
    print(
        f"{'TOTAL':30}{totals['rows']:>7}{totals['collected']:>10}"
        f"{totals['present']:>9}{totals['unknown']:>9}"
    )
    print(
        f"\nidentity: key {ident['key']}, {ident['duplicate_keys']} duplicate keys over "
        f"{totals['rows']} rows; {ident['map_actors_with_a_blueprint_class']} of "
        f"{ident['map_actors_in_gamelevel01']} map actors are blueprint-classed; "
        f"{ident['distinct_instance_names']} distinct names, "
        f"{ident['instance_names_carrying_two_classes']} carrying two classes, "
        f"{ident['names_with_the_map_s_placement_id_distinct']} of "
        f"{ident['names_with_the_map_s_placement_id']} placement-id names distinct; "
        f"{ident['rows_within_1m_of_another_row_in_the_same_category']} rows within 1 m of "
        "another in the same category"
    )
    for cls, entry in meta["class_census"]["classes"].items():
        print(
            f"  pickup class {cls:34} {entry['placed_by_the_map']:>5} placed  "
            f"{'native' if entry['native_class'] else 'blueprint':<9} -> "
            f"{entry['emitted_as'] or 'NOT EMITTED'}"
        )
    tally = meta["accounting"]
    print(
        f"accounting: {tally['emitted_as_rows']} rows + {tally['excluded_on_purpose']} "
        f"excluded on purpose + {tally['not_classified']} not classified = "
        f"{tally['map_actors_in_gamelevel01']} map actors"
        f"{'' if tally['adds_up'] else '  -- DOES NOT ADD UP, a class is in two buckets'}"
    )
    respawn = meta["respawn"]
    durable = respawn["durability"]
    emitted = set(CATEGORIES)
    left = durable["keys_that_left_the_destroyed_list_by_class"]
    print(
        f"respawn: over {durable['consecutive_same_build_pairs_compared']} same-build save "
        f"pairs ({durable['pairs_skipped_because_the_build_changed']} skipped at a build "
        f"change), {sum(left.values())} keys left a destroyed list "
        f"({sum(n for c, n in left.items() if c in emitted)} of a class emitted here) and "
        f"{sum(durable['rows_destroyed_then_live_again_by_class'].values())} rows went "
        "destroyed -> live again; "
        f"{sum(durable['rows_a_single_save_lists_as_both_destroyed_and_live_by_class'].values())}"
        " destroyed/live coexistences, "
        f"{durable['in_the_newest_save']} of them in the newest save"
    )
    for cls, entry in respawn["flora"].items():
        print(
            f"  {cls:20} {entry['live_records_over_the_saves_used']:>7} records, "
            f"mNumRespawns on {entry['records_carrying']['mNumRespawns']}, "
            f"mUpdatedOnDayNr on {entry['records_carrying']['mUpdatedOnDayNr']}, "
            f"counter +{entry['counter_rose']}/-{entry['counter_fell']}, "
            f"max {entry['highest_mNumRespawns_seen']} -> "
            f"{'REGROWS, excluded' if entry['respawns'] else 'one-shot'}"
            f"{'' if entry['respawns'] else ', emitted as ' + str(entry['emitted_as'])}"
        )
    print(
        f"positions: {agree['matched']} accepted records agree with the map to a median of "
        f"{agree['median_cm']} cm, p90 {agree['p90_cm']} cm, worst {agree['max_cm']} cm "
        f"({agree['over_1cm']} over 1 cm, all of them cells the game has not re-migrated)"
    )
    for category, check in totals["pedestals"].items():
        print(
            f"pedestals: {check['rows']} {category} rows attach to "
            f"{check['distinct_parents']} distinct parents, {check['parent_category']}"
            f"{'' if check['one_to_one'] else '  -- NOT 1:1, see _meta'}"
        )
    print(
        f"status: {evidence['live_records_accepted']} live records accepted, "
        f"{evidence['live_records_displaced']} displaced by the 52->60 rename and ignored "
        f"({evidence['displaced_records_a_position_match_could_re_attach']} of those could be "
        "re-attached by position, reported not applied); "
        f"{evidence['destroyed_entries_that_are_rows_here']} of "
        f"{evidence['destroyed_entries_in_newest_save']} destroyed entries are rows here; "
        f"{evidence['live_records_with_no_map_row']} live records of an emitted class have no "
        f"map row ({evidence['live_records_with_no_map_row_by_class']}); "
        f"{evidence['rows_only_older_saves_could_state']} rows only an older save could state"
    )
    print(
        f"completeness: over every save used, {evidence['orphan_keys_distinct']} distinct "
        f"(cell, instance) of an emitted class have no row here -- "
        f"{evidence['orphan_keys_only_a_pre_partition_save_names']} only a pre-partition save "
        f"names, {evidence['orphan_keys_whose_name_the_map_places_in_another_cell']} are the "
        f"52->60 rename, {evidence['orphan_keys_unexplained']} unexplained"
        f"{'' if not evidence['orphan_keys_unexplained'] else '  -- A ROW MAY BE MISSING'}"
    )
    print(
        f"exploration: the saves have observed {explore['collectibles_observed']} of "
        f"{totals['rows']} collectibles ({explore['collectibles_observed_pct']}%); of the "
        f"{explore['collectibles_unknown']} unknown, "
        f"{explore['unknown_in_a_cell_with_no_level_record_in_any_save']} are in a cell no "
        "save has a record of and the rest in cells streamed only in part"
    )
    context = meta["hazard_context"]["rows_touched"]
    print(
        f"hazard context (reporting radius {HAZARD_RADIUS_CM / 100:.0f} m, inference not "
        f"placement): {context['with_a_hostile_in_reporting_radius']} rows have a hostile "
        f"near, {context['with_a_hostile_whose_own_radius_contains_them']} sit inside a "
        f"spawner's or hatcher's OWN declared radius, "
        f"{context['inside_a_spore_flower_damage_sphere']} inside a spore flower's declared "
        f"damage sphere, {context['with_gas_in_reporting_radius']} have gas near, "
        f"{context['with_uranium_in_reporting_radius']} uranium, "
        f"{context['with_a_nuclear_hog_spawner_in_reporting_radius']} a nuclear hog; "
        f"{context['with_no_hazard_context_at_all']} rows have none of it"
    )
    print(
        f"naming: a longest-prefix rule over instance names would get "
        f"{naming['prefix_rule_wrong']} of {naming['prefix_rule_checked']} classes wrong and "
        f"{naming['prefix_rule_silent']} not at all; the {naming['glued_index_names']} "
        f"^BP_WAT[0-9] names resolve to {naming['glued_index_by_category']}, and the "
        f"{naming['glued_index_destroyed_in_newest_save']} of them the newest save has "
        f"destroyed resolve to {naming['glued_index_destroyed_resolved']} with "
        f"{naming['glued_index_destroyed_unresolved']} unresolved"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
