from __future__ import annotations

from x4_rules_macro_registry import MacroInfo, MacroLabel, is_macro_source_path, make_macro_label


def resolve(info: MacroInfo) -> MacroLabel | None:
    if not is_macro_source_path(info.source_path):
        return None
    if not info.macro_name.startswith("prod_"):
        return None
    if info.macro_class != "production":
        return None
    return make_macro_label("module", "prod", info.macro_name)
