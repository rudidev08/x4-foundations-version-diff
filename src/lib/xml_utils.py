"""Common XML helpers for rules."""
import xml.etree.ElementTree as ET
from pathlib import Path


def load(path: Path) -> ET.Element:
    return ET.parse(path).getroot()
