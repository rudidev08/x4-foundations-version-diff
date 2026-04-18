"""Path helpers shared across rules."""
import re
from pathlib import Path
from typing import Optional


_DLC_PATH = re.compile(r'^extensions/([^/]+)/')


def source_of(rel_path: str) -> str:
    """Return 'core' for main-tree paths, else the DLC short name."""
    m = _DLC_PATH.match(rel_path)
    if not m:
        return 'core'
    ext = m.group(1)
    return ext[len('ego_dlc_'):] if ext.startswith('ego_dlc_') else ext


# kind → list of (asset subdir glob, case variants)
_KIND_ROOTS = {
    'engines':  [('assets/props/Engines',         ('Engines', 'engines'))],
    'weapons':  [('assets/props/WeaponSystems',   ('WeaponSystems', 'weaponsystems'))],
    'turrets':  [('assets/props/WeaponSystems',   ('WeaponSystems', 'weaponsystems'))],
    'shields':  [('assets/props/SurfaceElements', ('SurfaceElements', 'surfaceelements'))],
    'storage':  [('assets/props/StorageModules',  ('StorageModules', 'storagemodules'))],
    'ships':    [('assets/units',                 ('units',))],
    'bullet':   [('assets/fx/weaponFx',           ('weaponFx', 'weaponfx'))],
}


_INDEX: dict[tuple[str, str], dict[str, list[tuple[Path, str]]]] = {}


def reset_index() -> None:
    _INDEX.clear()


def _index_for(root: Path, kind: str) -> dict[str, list[tuple[Path, str]]]:
    """{macro_ref: [(path, pkg_shortname), ...]} — multimap."""
    key = (str(root.resolve()), kind)
    if key in _INDEX:
        return _INDEX[key]
    idx: dict[str, list[tuple[Path, str]]] = {}
    subdirs = _KIND_ROOTS.get(kind, [])
    for asset_sub, _ in subdirs:
        for location, pkg_short in _iter_pkg_locations(root, asset_sub):
            if not location.exists():
                continue
            for p in location.rglob('*_macro.xml') if kind != 'bullet' else location.rglob('*.xml'):
                if not p.is_file():
                    continue
                ref = p.stem
                idx.setdefault(ref, []).append((p, pkg_short))
    _INDEX[key] = idx
    return idx


def _iter_pkg_locations(root: Path, asset_sub: str):
    """Yield (abs_path, pkg_shortname) for core and each extension, both casing variants."""
    for variant in (asset_sub, asset_sub.lower()):
        yield (root / variant), 'core'
    for ext in sorted((root / 'extensions').glob('*')):
        if not ext.is_dir():
            continue
        pkg = ext.name
        short = pkg[len('ego_dlc_'):] if pkg.startswith('ego_dlc_') else pkg
        for variant in (asset_sub, asset_sub.lower()):
            yield (ext / variant), short


def resolve_macro_path(root: Path, pkg_root: Path, macro_ref: str,
                       kind: str) -> Optional[Path]:
    """Discover on-disk macro path for a <component ref="..."> reference.

    Lookup precedence: pkg_root's own package first, then core, then first candidate.
    """
    if macro_ref is None:
        return None
    idx = _index_for(root, kind)
    candidates = idx.get(macro_ref, [])
    if not candidates:
        return None

    try:
        rel = pkg_root.resolve().relative_to(root.resolve())
        parts = rel.parts
        pkg_short = 'core'
        if parts and parts[0] == 'extensions' and len(parts) >= 2:
            ext = parts[1]
            pkg_short = ext[len('ego_dlc_'):] if ext.startswith('ego_dlc_') else ext
    except ValueError:
        pkg_short = 'core'

    own = [c for c in candidates if c[1] == pkg_short]
    if own:
        return own[0][0]

    core = [c for c in candidates if c[1] == 'core']
    if core:
        return core[0][0]

    return candidates[0][0]
