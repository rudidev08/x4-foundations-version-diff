"""Stat-diff helper for the (xpath, attr, label) field-spec pattern.

Extracted from missiles.py so every stat-heavy rule uses the same shape.
"""
import xml.etree.ElementTree as ET
from typing import Optional


def _elem_attr(root: ET.Element, xpath: str, attr: str) -> Optional[str]:
    el = root.find(xpath)
    return None if el is None else el.get(attr)


def diff_attrs(old: ET.Element, new: ET.Element,
               field_spec: list[tuple[str, str, str]]
               ) -> dict[str, tuple[Optional[str], Optional[str]]]:
    """Return {label: (old_val, new_val)} only for changed attrs."""
    out: dict[str, tuple[Optional[str], Optional[str]]] = {}
    for xpath, attr, label in field_spec:
        ov = _elem_attr(old, xpath, attr)
        nv = _elem_attr(new, xpath, attr)
        if ov != nv:
            out[label] = (ov, nv)
    return out


def collect_attrs(elem: ET.Element,
                  field_spec: list[tuple[str, str, str]]
                  ) -> dict[str, str]:
    """Return {label: value} for attrs present on elem. Skip missing."""
    out: dict[str, str] = {}
    for xpath, attr, label in field_spec:
        v = _elem_attr(elem, xpath, attr)
        if v is not None:
            out[label] = v
    return out
