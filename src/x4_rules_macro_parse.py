from __future__ import annotations

import xml.parsers.expat

from x4_rules_macro_registry import MacroInfo


def parse_singleton_macro(source_path: str, xml_text: str) -> MacroInfo | None:
    """Parse a singleton `<macro>` document without applying semantic rules."""
    parser = xml.parsers.expat.ParserCreate()
    root_tag: str | None = None
    root_attrs: dict[str, str] | None = None
    depth = 0
    direct_children: list[tuple[str, dict[str, str]]] = []

    def on_start(name: str, attrs: dict):
        nonlocal root_tag, root_attrs, depth
        if depth == 0:
            root_tag = name
            root_attrs = dict(attrs)
        elif depth == 1:
            direct_children.append((name, dict(attrs)))
        depth += 1

    def on_end(_name: str):
        nonlocal depth
        depth -= 1

    parser.StartElementHandler = on_start
    parser.EndElementHandler = on_end
    try:
        parser.Parse(xml_text, True)
    except xml.parsers.expat.ExpatError:
        return None

    attrs: dict[str, str] | None = None
    if root_tag == "macro" and root_attrs and "name" in root_attrs:
        attrs = root_attrs
    elif root_tag == "macros" and len(direct_children) == 1:
        child_name, child_attrs = direct_children[0]
        if child_name == "macro" and "name" in child_attrs:
            attrs = child_attrs

    if attrs is None:
        return None

    return MacroInfo(
        source_path=source_path,
        macro_name=attrs["name"],
        macro_class=attrs.get("class", "").lower(),
    )
