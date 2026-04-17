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
    if not path.startswith("assets/props/storagemodules/"):
        return None
    if not is_macro_source_path(info.source_path):
        return None
    if not info.macro_name.startswith("storage_"):
        return None
    if info.macro_class != "storage":
        return None
    return make_macro_label("module", "storage", info.macro_name)
