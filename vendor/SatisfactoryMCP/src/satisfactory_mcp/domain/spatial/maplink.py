"""Deep links into satisfactory-calculator.com's interactive map.

The fragment format, read off a working link::

    #4.75;40351;-208857|gameLayer|oilWellPure;oilNormal;oilWellNormal;oilImpure;...
     ^zoom ^x    ^y     ^group    ^sublayers, semicolon-separated

**That fragment carries save centimetres.** Every other tool in this MCP quotes metres, so
the conversion happens here and nowhere else. The sublayer tokens below are read off the
page itself and must not be derived from a class name: a wrong token opens the map
correctly with the overlay silently missing, which is the failure hardest to notice.
"""

from __future__ import annotations

from urllib.parse import quote

__all__ = ["BASE", "COLLECTIBLES", "LAYERS", "LOCAL_BASE", "layers_for", "local_map_url", "map_url"]

BASE = "https://satisfactory-calculator.com/en/interactive-map"

#: This project's own web map. The port duplicates the one pinned in
#: ``interfaces.web.__main__``, because domain may not import from interfaces.
LOCAL_BASE = "http://127.0.0.1:8712/"

#: The site's layer group resource markers live on; it has others (map/game).
GROUP = "gameLayer"

#: Resource class -> the site's token, read from the interactive map page itself. Wells
#: carry their own stems: oil is ``oilWell*`` but nitrogen is ``nitrogenGasWell*``.
LAYERS: dict[str, str] = {
    "Desc_LiquidOil_C": "oil",
    "Desc_OreIron_C": "iron",
    "Desc_OreCopper_C": "copper",
    "Desc_OreGold_C": "caterium",
    "Desc_Coal_C": "coal",
    "Desc_Stone_C": "limestone",
    "Desc_Sulfur_C": "sulfur",
    "Desc_RawQuartz_C": "quartz",
    "Desc_OreBauxite_C": "bauxite",
    "Desc_OreUranium_C": "uranium",
    "Desc_SAM_C": "sam",
    "Desc_NitrogenGas_C": "nitrogenGas",
    "Desc_Water_C": "water",
    #: Geysers DO take purities on this map, despite having no purity in our node table.
    "Desc_Geyser_C": "geyser",
}

#: Resources whose markers are wells, so the token carries ``Well``.
WELL_STEMS = frozenset({"oil", "nitrogenGas", "water"})

#: Resources that appear ONLY as wells, so a bare ``<stem><Purity>`` token does not exist.
WELL_ONLY = frozenset({"nitrogenGas", "water"})

#: Collectibles, each a single token with no purity.
COLLECTIBLES: dict[str, str] = {
    "slugs_green": "greenSlugs",
    "slugs_yellow": "yellowSlugs",
    "slugs_purple": "purpleSlugs",
    "hard_drives": "hardDrives",
    "mercer_spheres": "mercerSpheres",
    "somersloops": "somersloops",
}

_PURITIES = ("Impure", "Normal", "Pure")


def layers_for(resources: list[str], kinds: list[str] | None = None) -> list[str]:
    """Sublayer tokens for a set of resource classes.

    Every purity is included rather than only the one asked about: a link showing one
    impure node and hiding the pure one beside it answers a narrower question than the
    player asked. ``kinds`` filters to ``node`` or ``well``, but the stems decide what is
    possible -- nitrogen and water have no bare node token whatever is asked for.
    """
    wanted = set(kinds or ("node", "well"))
    out: list[str] = []
    for cls in resources:
        stem = LAYERS.get(cls)
        if not stem:
            continue
        for purity in _PURITIES:
            if "node" in wanted and stem not in WELL_ONLY:
                out.append(f"{stem}{purity}")
            if "well" in wanted and stem in WELL_STEMS:
                out.append(f"{stem}Well{purity}")
    return list(dict.fromkeys(out))


def map_url(
    x_cm: float,
    y_cm: float,
    layers: list[str] | None = None,
    zoom: float = 4.75,
) -> str:
    """A deep link centred on a save coordinate, with the given sublayers enabled.

    ``x_cm``/``y_cm`` are SAVE centimetres; a caller holding metres must multiply by 100.
    """
    fragment = f"{zoom:g};{round(x_cm)};{round(y_cm)}|{GROUP}"
    if layers:
        fragment += "|" + ";".join(layers)
    # The fragment's own semicolons and pipes are delimiters and must survive escaping.
    return f"{BASE}#{quote(fragment, safe=';|.-')}"


def local_map_url(x_m: float, y_m: float, zoom: float = 1, world: str = "") -> str:
    """A deep link into this project's own web map, centred on a coordinate in METRES.

    The fragment matches the frontend's own writer (``writeHash`` in ``map.ts``):
    ``#world=…&z=…&c=x,y``, with ``c`` in metres on save axes, rounded to one decimal.
    ``save`` is omitted, so an absent save means "follow the newest" -- which is what a
    link pasted later should do.
    """
    parts = []
    if world:
        parts.append("world=" + quote(world, safe=""))
    parts.append(f"z={zoom:g}")
    parts.append(f"c={round(x_m, 1):g},{round(y_m, 1):g}")
    return LOCAL_BASE + "#" + "&".join(parts)
