from __future__ import annotations

from types import SimpleNamespace

from satisfactory_helper import site_profile
from satisfactory_mcp.domain.factories.floors import Band, Platform


def test_site_profile_keeps_storage_base_and_excludes_extractor_from_storeys(monkeypatch) -> None:
    bands = [
        Band(ordinal=13, top_cm=1200, low_cm=1200, high_cm=1200, pieces=9, cells=9),
        Band(ordinal=23, top_cm=3000, low_cm=3000, high_cm=3000, pieces=9, cells=9),
    ]
    platform = Platform(
        index=0,
        cells=9,
        pieces=18,
        centre_cm=(0, 0),
        extent_cm=(2400, 2400),
        cell_set={(x, y) for x in range(-1, 2) for y in range(-1, 2)},
        bands=bands,
    )
    projection = {
        "machines": [
            {
                "instance": "Persistent_Level.Machine_A",
                "cls": "Build_Foundry_C",
                "recipe": "Recipe_Steel_C",
                "pos": [0, 0, 3000],
            }
        ],
        "extractors": [
            {
                "instance": "Persistent_Level.Miner_A",
                "cls": "Build_Miner_C",
                "pos": [0, 0, 800],
            }
        ],
        "generators": [],
    }
    holding = SimpleNamespace(
        source="storage",
        pos=(0.0, 0.0, 1200.0),
        instance="Storage_A",
        kind="solid",
        cls="Build_Storage_C",
        items=(("Desc_SteelPipe_C", 4800.0),),
        total=4800.0,
    )
    game = SimpleNamespace(
        recipes={"Recipe_Steel_C": SimpleNamespace(name="Solid Steel Ingot")},
        building_name=lambda cls: {
            "Build_Foundry_C": "Foundry",
            "Build_Miner_C": "Miner Mk.2",
            "Build_Storage_C": "Storage Container",
        }.get(cls, cls),
        item_name=lambda item: {"Desc_SteelPipe_C": "Steel Pipe"}.get(item, item),
    )
    state = SimpleNamespace(
        projection=projection,
        game=game,
        inventory=SimpleNamespace(holdings=lambda: [holding]),
        age_note="sav:test world.sav",
    )
    monkeypatch.setattr(
        site_profile,
        "resolve_factory",
        lambda _st, _focus: ("steel", ["Machine_A"]),
    )
    monkeypatch.setattr(
        site_profile,
        "_anchor_candidates",
        lambda _st, _machines: [
            {"machines": 1, "centre_m": [0.0, 0.0], "anchor_radius_m": 0.0}
        ],
    )
    monkeypatch.setattr(site_profile.ffloors, "foundation_tops", lambda _projection: [])
    monkeypatch.setattr(site_profile.ffloors, "_platforms", lambda _tops: ([platform], {}))

    result = site_profile.build_site_profile(state, focus="product:Steel Ingot")

    assert result["counts"] == {
        "machines": 1,
        "supporting_infrastructure": 1,
        "storage": 1,
        "occupied_levels": 2,
        "unassigned_placements": 0,
    }
    assert [(level["site_level"], level["global_floor"]) for level in result["levels"]] == [
        (0, 13),
        (1, 23),
    ]
    assert result["levels"][0]["designation"] == "storage base"
    assert result["levels"][0]["storage_items"] == [{"count": 4800, "name": "Steel Pipe"}]
    assert result["supporting_infrastructure"][0]["building"] == "Miner Mk.2"


def test_level_matching_refuses_a_distant_lower_band() -> None:
    band = Band(ordinal=13, top_cm=1200, low_cm=1200, high_cm=1200, pieces=9, cells=9)
    platform = Platform(
        index=0,
        cells=1,
        pieces=1,
        centre_cm=(0, 0),
        extent_cm=(800, 800),
        cell_set={(0, 0)},
        bands=[band],
    )

    assert site_profile._level_for([platform], (0, 0, 1200)) == (
        platform,
        band,
        "foundation_exact",
    )
    assert site_profile._level_for([platform], (0, 0, 3000)) is None
