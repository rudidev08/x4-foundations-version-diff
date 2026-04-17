from __future__ import annotations

import re
import xml.parsers.expat


_SEL_KEY_RE = re.compile(r"\[@(id|name|macro)\s*=\s*['\"]([^'\"]+)['\"]\]")


def key_from_sel(sel: str) -> str | None:
    """Extract the innermost entity key from an XPath-like `sel=` expression."""
    matches = _SEL_KEY_RE.findall(sel or "")
    if not matches:
        return None

    attr, value = matches[-1]
    last_match_idx = sel.rfind(f"[@{attr}")
    before = sel[:last_match_idx]
    step_idx = max(before.rfind("/"), before.rfind(":"))
    element_step = before[step_idx + 1:] if step_idx >= 0 else before
    bracket = element_step.find("[")
    tag = element_step[:bracket] if bracket >= 0 else element_step
    tag = tag.strip() or "entity"
    return f"{tag}:{value}"


def build_dlc_diff_intervals(xml_text: str) -> list[tuple[int, int, str]]:
    """Extract interval keys from DLC patch operations inside a `<diff>` document."""
    parser = xml.parsers.expat.ParserCreate()
    results: list[tuple[int, int, str]] = []
    stack: list[tuple[str, int, str | None]] = []
    depth = 0

    def extract_key(tag: str, attrs: dict) -> str | None:
        sel = attrs.get("sel")
        if sel is not None:
            return key_from_sel(sel) or f"{tag}:{sel}"
        for attr in ("id", "name", "macro"):
            if attr in attrs:
                return f"{tag}:{attrs[attr]}"
        return None

    def on_start(name: str, attrs: dict):
        nonlocal depth
        line = parser.CurrentLineNumber
        if depth == 0 and name == "diff":
            depth = 1
            return

        if depth < 1:
            return

        key: str | None = None
        if depth == 1 and name in {"add", "replace", "remove"}:
            key = extract_key(name, attrs)
        elif depth >= 2:
            candidate = extract_key(name, attrs)
            if (
                candidate is not None
                and ":" in candidate
                and not candidate.endswith(":")
                and any(attr in attrs for attr in ("id", "name", "macro"))
            ):
                key = candidate

        stack.append((name, line, key))
        depth += 1

    def on_end(name: str):
        nonlocal depth
        if depth == 1 and name == "diff":
            depth = 0
            return

        if depth < 2:
            return

        if stack:
            _tag, start, key = stack.pop()
            end = parser.CurrentLineNumber
            if key is not None:
                results.append((start, end, key))
        depth -= 1

    parser.StartElementHandler = on_start
    parser.EndElementHandler = on_end
    try:
        parser.Parse(xml_text, True)
    except xml.parsers.expat.ExpatError:
        pass

    results.sort(key=lambda interval: interval[0])
    return results


class _StopParsing(Exception):
    pass


def is_dlc_diff_file(xml_text: str) -> bool:
    """Return True when the document root is `<diff>`."""
    parser = xml.parsers.expat.ParserCreate()
    found = {"root": None}

    def on_start(name: str, _attrs: dict):
        if found["root"] is None:
            found["root"] = name
            raise _StopParsing

    parser.StartElementHandler = on_start
    try:
        parser.Parse(xml_text, True)
    except _StopParsing:
        pass
    except xml.parsers.expat.ExpatError:
        pass
    return found["root"] == "diff"
