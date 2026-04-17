from __future__ import annotations

from x4_rules_macro_registry import (
    MacroInfo,
    MacroLabel,
    is_macro_source_path,
    make_macro_label,
    normalized_macro_source_path,
)


def resolve(info: MacroInfo) -> MacroLabel | None:
    path = normalized_macro_source_path(info.source_path)
    if not path.startswith("assets/props/surfaceelements/"):
        return None
    if not is_macro_source_path(info.source_path):
        return None
    if not info.macro_name.startswith("shield_"):
        return None
    if info.macro_class != "shieldgenerator":
        return None
    return make_macro_label("ware", "shield", info.macro_name)
