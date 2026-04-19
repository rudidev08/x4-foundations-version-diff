"""Stat-diff helper for the (xpath, attribute, label) field-spec pattern.

Extracted from missiles.py so every stat-heavy rule uses the same shape.
"""
import xml.etree.ElementTree as ElementTree
from typing import Optional


def _elem_attr(root: ElementTree.Element, xpath: str, attribute: str) -> Optional[str]:
    """Read `attribute` from the first element matching `xpath` under `root`.
    `xpath='.'` reads the attribute off `root` itself (used by ware-root
    fields like `volume`).
    """
    if xpath == '.':
        return root.get(attribute)
    element = root.find(xpath)
    return None if element is None else element.get(attribute)


def diff_attrs(old: ElementTree.Element, new: ElementTree.Element,
               field_spec: list[tuple[str, str, str]]
               ) -> dict[str, tuple[Optional[str], Optional[str]]]:
    """Return {label: (old_val, new_val)} only for changed attributes."""
    out: dict[str, tuple[Optional[str], Optional[str]]] = {}
    for xpath, attribute, label in field_spec:
        old_value = _elem_attr(old, xpath, attribute)
        new_value = _elem_attr(new, xpath, attribute)
        if old_value != new_value:
            out[label] = (old_value, new_value)
    return out


def diff_labels(old: ElementTree.Element, new: ElementTree.Element,
                field_spec: list[tuple[str, str, str]]) -> list[str]:
    """Format `diff_attrs` output as `[f'{label} {old}→{new}', ...]` strings —
    the shape every emit function appends to its `changes` list.
    """
    return [f'{label} {old_value}→{new_value}'
            for label, (old_value, new_value)
            in diff_attrs(old, new, field_spec).items()]


def collect_attrs(element: ElementTree.Element,
                  field_spec: list[tuple[str, str, str]]
                  ) -> dict[str, str]:
    """Return {label: value} for attributes present on element. Skip missing."""
    out: dict[str, str] = {}
    for xpath, attribute, label in field_spec:
        v = _elem_attr(element, xpath, attribute)
        if v is not None:
            out[label] = v
    return out


def diff_attr_map(old_map: dict[str, str],
                  new_map: dict[str, str]) -> list[str]:
    """Compare two flat attribute maps; emit `key old→new` labels for differences."""
    out: list[str] = []
    for k in sorted(set(old_map) | set(new_map)):
        old_value = old_map.get(k)
        new_value = new_map.get(k)
        if old_value == new_value:
            continue
        out.append(f'{k} {old_value}→{new_value}')
    return out
