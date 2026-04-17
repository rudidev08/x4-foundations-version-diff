from __future__ import annotations

from x4_rules_macro_registry import (
    MacroInfo,
    MacroLabel,
    is_macro_source_path,
    make_macro_label,
    normalized_macro_source_path,
)


_SHIP_CLASSES = {"ship_xs", "ship_s", "ship_m", "ship_l", "ship_xl"}


def resolve(info: MacroInfo) -> MacroLabel | None:
    path = normalized_macro_source_path(info.source_path)
    if not path.startswith("assets/units/"):
        return None
    if not is_macro_source_path(info.source_path):
        return None
    if not info.macro_name.startswith("ship_"):
        return None
    if info.macro_class not in _SHIP_CLASSES:
        return None
    return make_macro_label("ship", "ship", info.macro_name)
