from __future__ import annotations

from x4_rules_macro_registry import (
    MacroInfo,
    MacroLabel,
    is_macro_source_path,
    make_macro_label,
    normalized_macro_source_path,
)


_WEAPON_CLASSES = {"weapon", "missilelauncher", "mine"}


def resolve(info: MacroInfo) -> MacroLabel | None:
    path = normalized_macro_source_path(info.source_path)
    if not path.startswith("assets/props/weaponsystems/"):
        return None
    if not is_macro_source_path(info.source_path):
        return None
    if not info.macro_name.startswith("weapon_"):
        return None
    if info.macro_class not in _WEAPON_CLASSES:
        return None
    return make_macro_label("ware", "weapon", info.macro_name)
