"""Power Shards and Somersloops: what is held, what is slotted, what is free."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar

from ...core.gamedata.model import GameData

if TYPE_CHECKING:
    from ..world.inventory import Inventory

__all__ = ["OverclockBudget"]


@dataclass
class OverclockBudget:
    """The two spendable pools that change a machine's rate.

    Takes the build records rather than reaching for them: both budgets read
    ``InventoryPotential`` off every machine, extractor and generator alike, and
    which records those are is the census's business.
    """

    projection: dict
    game: GameData
    inventory: Inventory
    records: list[dict] = field(default_factory=list)

    #: The Somersloop and Mercer Sphere item classes. Class ids rather than display names,
    #: which would silently match nothing in a localised dump.
    SLOOP_ITEM: ClassVar[str] = "Desc_WAT1_C"
    MERCER_ITEM: ClassVar[str] = "Desc_WAT2_C"

    def shard_budget(self) -> dict:
        """Power Shards held, committed and free.

        Committed shards are READ off each buildable's ``InventoryPotential`` component and
        never derived from its clock: a shard raises the maximum clock rather than setting
        it, so a building may hold more shards than its clock needs. ``free`` excludes
        machine inventories and the ground crates (see ``stock``), because slotted shards
        live inside machines and counting the raw machine total as shards on hand
        overstates the spendable pool.
        """
        from ...core.gamedata.constants import POTENTIAL_SHARD_SLOTS, shards_for_clock

        shard_items = self.game.clock_shards()
        per_shard = max(shard_items.values()) if shard_items else 0.0
        stock = self.inventory.stock()
        free = sum(stock.get(item, 0.0) for item in shard_items)

        # Uncrafted slugs are latent shards and are counted separately: on the reference
        # save the Depot alone holds slugs worth 404 shards against 22 already crafted.
        # ``by_place`` splits where they physically are, since ``free`` pools carried,
        # stored and Depot together and so cannot answer "is that in the Depot?".
        wanted = set(shard_items) | set(self.game.slug_yields())
        by_place: dict[str, dict[str, float]] = {}
        for place, source in (
            ("carried", self.inventory.sources.get("player", {})),
            #: The storage-container bucket, which is what ``stock`` spends. NOT the
            #: ``crate`` bucket -- the crates on the ground are excluded from both.
            ("storage", self.inventory.sources.get("storage", {})),
            ("depot", self.projection.get("depot", {})),
        ):
            held = {self.game.item_name(k): v for k, v in source.items() if k in wanted and v}
            if held:
                by_place[place] = held

        slugs = []
        craftable = 0.0
        for item, yield_each in sorted(self.game.slug_yields().items(), key=lambda kv: -kv[1]):
            held = stock.get(item, 0.0)
            if not held:
                continue
            slugs.append(
                {
                    "item": item,
                    "name": self.game.item_name(item),
                    "held": held,
                    "each": yield_each,
                    "shards": held * yield_each,
                }
            )
            craftable += held * yield_each

        committed = 0
        holders: list[dict] = []
        for record in self.records:
            slotted = sum(
                n
                for item, n in (record.get("potential_slots") or {}).items()
                if item in shard_items
            )
            clock = float(record.get("clock") or 1.0)
            needed = shards_for_clock(clock, per_shard)
            if not slotted and not needed:
                continue
            committed += slotted
            holders.append(
                {
                    "instance": record["instance"].rsplit(".", 1)[-1],
                    "cls": record.get("cls", "?"),
                    "clock": clock,
                    "slotted": slotted,
                    "needed": needed,
                    #: A slot filled but not being used by the current clock.
                    "idle": max(0, slotted - needed),
                }
            )
        holders.sort(key=lambda h: (-h["slotted"], h["cls"]))
        return {
            "shard_items": shard_items,
            "slugs": slugs,
            "by_place": by_place,
            #: Shards these slugs would yield once crafted. NOT free: crafting is a
            #: manual step, so this is potential, never availability.
            "craftable": craftable,
            "potential": free + craftable,
            "free": free,
            "committed": committed,
            "owned": free + committed,
            "holders": holders,
            "slots_per_building": POTENTIAL_SHARD_SLOTS,
            #: False on a projection too old to carry InventoryPotential, where ``committed``
            #: is unknown rather than zero.
            "measured": any("potential_slots" in r for r in self.records),
        }

    def sloop_budget(self) -> dict:
        """Somersloops on hand and in machines.

        Free ones come from ``stock`` -- carried, storage containers and the Dimensional
        Depot -- which is exactly the set that can be spent. Committed ones are read from
        ``InventoryPotential``, the same component that holds Power Shards, the two being
        distinguished only by item class; the count is the slot contents and never derived
        from ``mPendingProductionBoost``, since inverting that multiplier needs the
        building's base and step and rounds. Mercer Spheres are counted separately and never
        added in: they share the WAT prefix and do nothing for production.
        """
        stock = self.inventory.stock()
        free = float(stock.get(self.SLOOP_ITEM, 0.0))
        by_place: dict[str, float] = {}
        for place, source in (
            ("carried", self.inventory.sources.get("player", {})),
            ("storage", self.inventory.sources.get("storage", {})),
            ("depot", self.projection.get("depot", {})),
        ):
            held = float(source.get(self.SLOOP_ITEM, 0.0))
            if held:
                by_place[place] = held

        committed = 0.0
        holders: list[dict] = []
        for record in self.records:
            slotted = float((record.get("potential_slots") or {}).get(self.SLOOP_ITEM, 0.0))
            if not slotted:
                continue
            committed += slotted
            building = self.game.buildings.get(record.get("cls", ""))
            holders.append(
                {
                    "instance": record["instance"].rsplit(".", 1)[-1],
                    "cls": record.get("cls", "?"),
                    "name": building.name if building else record.get("cls", "?"),
                    "sloops": slotted,
                    #: What the plan model says that many slots are worth, so a caller can
                    #: check it against the boost the save reports.
                    "boost": building.boost_for(int(slotted)) if building else None,
                    "boost_in_save": record.get("production_boost"),
                }
            )
        holders.sort(key=lambda h: (-h["sloops"], h["cls"]))
        return {
            "item": self.SLOOP_ITEM,
            "free": free,
            "by_place": by_place,
            "committed": committed,
            "owned": free + committed,
            "holders": holders,
            "mercer_spheres": float(stock.get(self.MERCER_ITEM, 0.0)),
            #: As the shard budget's ``measured``: false means unknown, not zero.
            "committed_measured": any("potential_slots" in r for r in self.records),
        }
