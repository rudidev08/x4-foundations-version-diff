"""Common XML helpers for rules."""
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from typing import Optional


def load(path: Path) -> ElementTree.Element:
    return ElementTree.parse(path).getroot()


def load_macro(path: Optional[Path]) -> Optional[ElementTree.Element]:
    """Load a macro file and return its `<macro>` child, or None if the
    file is missing or malformed.
    """
    if path is None:
        return None
    try:
        return load(path).find('macro')
    except (FileNotFoundError, ElementTree.ParseError):
        return None
