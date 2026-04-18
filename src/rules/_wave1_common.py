"""Shared helpers for Wave 1 ware-driven rules.

Every Wave 1 rule imports `owns`, `diff_productions`, and (equipment only)
`equipment_macro_reverse_index`. Creating them here once guarantees:
- disjoint ownership via the single-source-of-truth predicate,
- identical production-label forms across rules,
- one macro-reverse-index implementation.
"""
from pathlib import Path
import xml.etree.ElementTree as ET
from typing import Optional


def ware_owner(ware_elem) -> Optional[str]:
    """Return the rule tag that owns this ware, or None if no Wave 1 rule claims it.

    Ordering is load-bearing — see plan's "Shared Wave 1 ownership predicate"
    for rationale on each branch.
    """
    tags = (ware_elem.get('tags') or '').split()
    group = ware_elem.get('group')
    ware_id = ware_elem.get('id') or ''
    transport = ware_elem.get('transport')
    if transport == 'ship' or 'ship' in tags or group == 'drones':
        return None
    if group in ('shields', 'missiles'):
        return None
    if 'personalupgrade' in tags or 'spacesuit' in ware_id.split('_'):
        return 'equipment'
    if ware_id.startswith('satellite_'):
        return 'equipment'
    if group == 'engines':
        return 'engines'
    if group == 'weapons':
        return 'weapons'
    if group == 'turrets':
        return 'turrets'
    if group in ('software', 'hardware', 'countermeasures'):
        return 'equipment'
    return 'wares'


def owns(ware_elem, tag: str) -> bool:
    return ware_owner(ware_elem) == tag


def diff_productions(old_ware, new_ware) -> list[str]:
    """Return labels for production changes. Label forms are pinned so every
    Wave 1 rule produces identical text for equivalent changes.

    Forms:
    - `production[method=<M>] added` / `production[method=<M>] removed`
    - `production[method=<M>] <field> <old>→<new>` (field ∈ {time, amount})
    - `production[method=<M>] primary.<ware_id> <old_amount>→<new_amount>`
    - `production[method=<M>] primary.<ware_id> added` / `removed`
    """
    out: list[str] = []
    old_by_method = _productions_by_method(old_ware)
    new_by_method = _productions_by_method(new_ware)
    for method in sorted(new_by_method.keys() - old_by_method.keys()):
        out.append(f'production[method={method}] added')
    for method in sorted(old_by_method.keys() - new_by_method.keys()):
        out.append(f'production[method={method}] removed')
    for method in sorted(old_by_method.keys() & new_by_method.keys()):
        old_p = old_by_method[method]
        new_p = new_by_method[method]
        for field in ('time', 'amount'):
            ov = old_p.get(field)
            nv = new_p.get(field)
            if ov != nv and not (ov is None and nv is None):
                out.append(f'production[method={method}] {field} {ov}→{nv}')
        old_primary = _primary_wares(old_p)
        new_primary = _primary_wares(new_p)
        for wid in sorted(new_primary.keys() - old_primary.keys()):
            out.append(f'production[method={method}] primary.{wid} added')
        for wid in sorted(old_primary.keys() - new_primary.keys()):
            out.append(f'production[method={method}] primary.{wid} removed')
        for wid in sorted(old_primary.keys() & new_primary.keys()):
            oa = old_primary[wid]
            na = new_primary[wid]
            if oa != na:
                out.append(f'production[method={method}] primary.{wid} {oa}→{na}')
    return out


def _productions_by_method(ware_elem) -> dict[str, ET.Element]:
    return {p.get('method'): p for p in ware_elem.findall('production') if p.get('method')}


def _primary_wares(prod_elem) -> dict[str, str]:
    primary = prod_elem.find('primary')
    if primary is None:
        return {}
    out: dict[str, str] = {}
    for w in primary.findall('ware'):
        wid = w.get('ware')
        if wid is not None:
            out[wid] = w.get('amount') or ''
    return out


def equipment_macro_reverse_index(root: Path) -> dict[str, list[str]]:
    """Build {macro_ref: [ware_ids]} for equipment-owned wares.

    Reads wares from core `libraries/wares.xml` only — DLC application is the
    caller's concern if they want extensions merged. Multi-ware-per-macro is
    preserved (one macro can be referenced by multiple wares).
    """
    out: dict[str, list[str]] = {}
    wares_file = root / 'libraries' / 'wares.xml'
    if not wares_file.is_file():
        return out
    try:
        tree = ET.parse(wares_file).getroot()
    except ET.ParseError:
        return out
    for ware in tree.iter('ware'):
        if ware_owner(ware) != 'equipment':
            continue
        component = ware.find('component')
        if component is None:
            continue
        ref = component.get('ref')
        if not ref:
            continue
        out.setdefault(ref, []).append(ware.get('id') or '')
    return out
