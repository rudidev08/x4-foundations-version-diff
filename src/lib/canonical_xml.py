"""Deterministic XML serialization for snapshot-stable rule outputs.

Rules that serialize XML trees to bytes for diffing (e.g. gamelogic aiscripts
after DLC patch materialization) must go through `canonical_bytes` rather than
plain `ET.tostring`. Plain `tostring` exposes Python-version-dependent behavior
around whitespace, attribute order, and the XML declaration — enough drift to
break Tier B snapshot stability across environments.

Policy pinned by this helper:
- Attributes sorted alphabetically per element.
- Whitespace normalized via `ET.indent(..., space='  ')`.
- Fixed `<?xml version="1.0" encoding="utf-8"?>\\n` prefix.
- Comments already dropped by `ET.parse` / `ET.fromstring`; we do not preserve
  them.
"""
import xml.etree.ElementTree as ET


def canonical_bytes(elem: ET.Element) -> bytes:
    """Serialize an element tree as UTF-8 bytes with deterministic shape.

    Operates on a deep copy so the input tree's attribute ordering and
    whitespace are not mutated by the canonicalization pass.
    """
    copy = ET.fromstring(ET.tostring(elem))
    for e in copy.iter():
        if e.attrib:
            e.attrib = {k: e.attrib[k] for k in sorted(e.attrib)}
    ET.indent(copy, space='  ')
    return b'<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(copy, encoding='utf-8')
