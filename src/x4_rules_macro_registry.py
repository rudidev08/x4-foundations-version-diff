from __future__ import annotations

from dataclasses import dataclass

from x4_rules_file_filter import normalize_source_path


@dataclass(frozen=True)
class MacroInfo:
    source_path: str
    macro_name: str
    macro_class: str


@dataclass(frozen=True)
class MacroLabel:
    prefix: str
    family: str


def normalized_macro_source_path(source_path: str) -> str:
    return normalize_source_path(source_path)


def is_macro_source_path(source_path: str) -> bool:
    return "/macros/" in normalized_macro_source_path(source_path)


def strip_macro_suffix(macro_name: str) -> str:
    return macro_name[:-6] if macro_name.endswith("_macro") else macro_name


def make_macro_label(prefix: str, family: str, macro_name: str) -> MacroLabel:
    return MacroLabel(prefix=f"{prefix}:{strip_macro_suffix(macro_name)}", family=family)


_RESOLVERS: tuple | None = None


def _resolvers() -> tuple:
    # Lazy tuple build: each rule module imports MacroInfo/MacroLabel/helpers
    # from this module, so importing them at the top would create a cycle.
    # Deferring until first call lets each rule module finish loading first;
    # subsequent calls reuse the cached tuple.
    global _RESOLVERS
    if _RESOLVERS is None:
        from x4_rules_macro_buildmodule import resolve as resolve_buildmodule
        from x4_rules_macro_dockarea import resolve as resolve_dockarea
        from x4_rules_macro_engine import resolve as resolve_engine
        from x4_rules_macro_prod import resolve as resolve_prod
        from x4_rules_macro_shield import resolve as resolve_shield
        from x4_rules_macro_ship import resolve as resolve_ship
        from x4_rules_macro_storage import resolve as resolve_storage
        from x4_rules_macro_struct import resolve as resolve_struct
        from x4_rules_macro_turret import resolve as resolve_turret
        from x4_rules_macro_weapon import resolve as resolve_weapon
        _RESOLVERS = (
            resolve_ship, resolve_engine, resolve_shield, resolve_turret, resolve_weapon,
            resolve_storage, resolve_dockarea, resolve_prod, resolve_buildmodule, resolve_struct,
        )
    return _RESOLVERS


def resolve_macro_label(info: MacroInfo) -> MacroLabel:
    for resolver in _resolvers():
        match = resolver(info)
        if match is not None:
            return match
    return MacroLabel(prefix=f"macro:{info.macro_name}", family="macro")


def resolve_macro_prefix(info: MacroInfo) -> str:
    return resolve_macro_label(info).prefix
