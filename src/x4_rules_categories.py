from __future__ import annotations

import re

from x4_rules_file_filter import normalize_source_path as _normalize_source_path


CATEGORY_RULES = [
    ("Game Starts", [r"^gamestart:"]),
    ("Weapons & Equipment", [
        r"^ware:(weapon|turret|missile|shield|engine|thruster)_",
        r"^effect:",
        r"^influencelist:",
    ]),
    ("Ships, Stations & Modules", [r"^(ship|module|station|plan):", r"^macro:(ship|struct)_"]),
    ("Jobs & Spawns", [r"^job:"]),
    ("Wares & Economy", [r"^ware:"]),
    ("Factions & Diplomacy", [r"^(faction|race|people):"]),
    ("Map & Sectors", [r"^(region|definition|dataset|group):"]),
    ("Missions & Scripts", [r"^aiscript:"]),
]

CATEGORY_FILE_FALLBACK = [
    ("Weapons & Equipment", [
        "assets/props/engines/",
        "assets/props/surfaceelements/",
        "assets/props/weaponsystems/",
        "assets/fx/weaponfx/",
    ]),
    ("Ships, Stations & Modules", ["assets/units/", "assets/structures/", "assets/props/storagemodules/"]),
    ("Jobs & Spawns", ["libraries/jobs.xml"]),
    ("Wares & Economy", ["libraries/wares.xml"]),
    ("Factions & Diplomacy", ["libraries/factions.xml", "libraries/diplomacy.xml"]),
    ("Map & Sectors", [
        "maps/",
        "libraries/mapdefaults.xml",
        "libraries/region_definitions.xml",
        "libraries/regionobjectgroups.xml",
        "libraries/regionyields.xml",
    ]),
    ("Missions & Scripts", ["md/", "aiscripts/", "libraries/aicompat.xml"]),
    ("Game Starts", ["libraries/gamestarts.xml", "md/setup_gamestarts.xml", "md/gs_"]),
    ("UI", ["ui/", "t/0001-l044.xml"]),
]

OTHER = "Other"


def normalize_source_path(source_path: str) -> str:
    return _normalize_source_path(source_path)


def _is_game_start_context(prefix: str, source_path: str) -> bool:
    lower_prefix = prefix.lower()
    normalized_source = normalize_source_path(source_path)

    if lower_prefix.startswith("gamestart:"):
        return True
    if normalized_source == "libraries/gamestarts.xml":
        return True
    if normalized_source == "md/setup_gamestarts.xml" or normalized_source.startswith("md/gs_"):
        return True
    if lower_prefix.startswith(("station:", "plan:")) and (
        "gamestart" in lower_prefix or "customgamestart" in lower_prefix
    ):
        return True
    return False


def categorize(prefix: str, source_path: str) -> str:
    if _is_game_start_context(prefix, source_path):
        return "Game Starts"

    for category, patterns in CATEGORY_RULES:
        for pattern in patterns:
            if re.match(pattern, prefix):
                return category

    normalized_source = normalize_source_path(source_path)
    for category, path_prefixes in CATEGORY_FILE_FALLBACK:
        for path_prefix in path_prefixes:
            if normalized_source.startswith(path_prefix):
                return category
    return OTHER


def category_order() -> list[str]:
    seen: set[str] = set()
    order: list[str] = []
    for name, _ in CATEGORY_RULES + CATEGORY_FILE_FALLBACK:
        if name not in seen:
            seen.add(name)
            order.append(name)
    order.append(OTHER)
    return order
