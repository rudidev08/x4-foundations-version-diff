"""Per-source-path entity overrides for the chunker.

The auto-generated `src/x4_schema_map.generated.json` maps
`{basename → (entity_tag, id_attribute)}` for files whose direct-child
structure the scanner can classify. Some files need a manual override:

- The scanner can't reach them because the entity elements aren't direct
  children of the root (e.g. `md/setup_gamestarts.xml` has root `<mdscript>`
  → `<cues>` → `<cue>`; the scanner only inspects direct children of root).
- The XML tag name isn't the prefix we want in the emitted entity key
  (e.g. `<cue>` in `md/setup_gamestarts.xml` should emit `gamestart:<name>`
  so the changelog groups per-gamestart under the Game Starts category).

Each entry here is `(entity_tag, id_attribute, label_prefix)`:
- `entity_tag`, `id_attribute` drive XML parsing (same as the schema map).
- `label_prefix` is the string used in emitted entity keys; when `None`,
  `entity_tag` is used (matches default schema-map behaviour).

Overrides take precedence over the auto-generated schema map for the
listed files.
"""

FILE_ENTITY_OVERRIDES: dict[str, tuple[str, str, str | None]] = {
    "md/setup_gamestarts.xml": ("cue", "name", "gamestart"),
    # lib_generic.xml is a collection of reusable <library name="X"> handlers
    # used by other MD scripts; each library is the natural change unit.
    "md/lib_generic.xml":      ("library", "name", None),
    # rml_barterwares.xml mixes a top-level BarterWares <library> wrapper with
    # standalone <cue name="X"> blocks. build_entity_intervals tracks target-tag
    # depth only, so cue-tag matching catches top-level cues regardless of
    # whether they sit inside the library or as siblings to it.
    "md/rml_barterwares.xml":  ("cue", "name", None),
}


def file_entity_override(source_path: str) -> tuple[str, str, str | None] | None:
    """Return (entity_tag, id_attribute, label_prefix) for a manual override, or None."""
    return FILE_ENTITY_OVERRIDES.get(source_path)
