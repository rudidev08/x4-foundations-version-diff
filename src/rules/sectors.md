# sectors rule

Tracks galaxy layout, map structure, highway topology, region yields, and
region definitions across versions. Emits one row per changed entity plus one
row per changed `<connection>` child of every map/highway parent macro.

## Data model

Five USER-FACING sub-sources back this rule; eight `diff_library` calls feed
them. The map and highway groups concatenate multiple sibling files into one
user-facing token, but internally each file keeps a distinct label so
contamination from a single-file failure cannot bleed across siblings.

### Sub-sources

- **`galaxy`** — `maps/xu_ep2_universe/galaxy.xml`. Flat `<connection>` list
  under the single galaxy macro. Key `(subsource, @name)`. Fields: `@ref`,
  `@path`, nested `<macro @ref>`, `<offset>` position/rotation tuples.
- **`map`** — three files, each backed by its own internal label:
  `map_clusters`, `map_sectors`, `map_zones`
  (`maps/xu_ep2_universe/{clusters,sectors,zones}.xml`). Entity = `<macro>`;
  per-connection sub-entities are emitted for each `<connections>/<connection>`
  child (keys become 3-tuples).
- **`highway`** — two files: `highway_sec` (`sechighways.xml`) and
  `highway_zone` (`zonehighways.xml`). Same shape as map.
- **`regionyield`** — `libraries/regionyields.xml` (9.x `<definition id="…">`
  shape). Key `(subsource, @id)`.
- **`regiondef`** — `libraries/region_definitions.xml`. Entity = `<region>`;
  key `(subsource, @name)`.

### Entity keys

- Galaxy / regionyield / regiondef: 2-tuple `(subsource, name_or_id)`.
- Map / highway parent macros: 2-tuple `(internal_label, macro_name)` — note
  the internal label (`map_clusters`, `highway_sec`, …) not the user-facing
  token.
- Map / highway connections: 3-tuple
  `(internal_label, parent_macro_name, connection_name)`.

## Internal label stability contract (Tier B)

The eight internal labels listed above are frozen as part of the public
snapshot contract. Renaming any of them — `map_clusters` → `clusters`,
`highway_sec` → `highways_sec`, etc. — is a breaking Tier B change. The
entity keys carry the label so a rename reshapes every snapshot row. A
refactor that renames must regenerate `tests/snapshots/sectors_*.txt`
alongside the code change; otherwise the diff will look like a regression.

Classification tokens (`galaxy`, `map`, `highway`, `regionyield`,
`regiondef`, plus the structural `connection` token and macro `@class`
values) are part of the same contract.

## Contamination scoping

The rule collects `(report, internal_label)` pairs and passes them to
`forward_incomplete_many`. Because every emitted output carries
`extras.subsource == <internal_label>`, the per-label scoping in
`forward_incomplete_many` keeps a single file's patch failures from
contaminating sibling-file outputs within the same user-facing group.

### Per-connection propagation

`diff_library` failures carry `affected_keys=[parent_macro_name]` (bare
name) when they can infer the parent entity. The rule emits 3-tuple keys
for per-connection rows. Before calling `forward_incomplete_many`, the rule
walks every report's failures and expands each bare parent name into the
list of 3-tuple child keys that were emitted beneath it. Without this
expansion, a broken parent would still mark its parent row incomplete but
every child row would stay marked complete — a silent-changes hole.

The expansion uses an `(internal_label, parent_name)` key so two
different map files with identically-named parent macros (historically
possible in modded X4) cannot cross-contaminate each other's failures.

## Classifications & filter

- Classifications per row: `[user_facing_token]` plus `@class` (when
  present), plus `connection` for per-connection rows.
- No generic filter set — all tokens are meaningful.
- Output format: `[sectors] <name> (<classifications>) [<sources>]: <changes>`.

## Known limitations

- DLC map files in real X4 use prefixed basenames
  (`dlc_mini_01_clusters.xml`, `dlc4_clusters.xml`, …) rather than the core
  basename. `diff_library`'s default DLC discovery uses exact path match, so
  those DLC contributions are not merged by this rule. The rule currently
  tracks core-file changes only for the map/highway families; extending
  discovery is a larger library change out of scope for the sectors rule.
- The `regionyields.xml` shape changed between 8.x (`<resource>/<yield>`)
  and 9.x (`<definition id="…">`). This rule keys on 9.x `<definition @id>`
  only; 8.x files (no `<definition>`) simply produce zero regionyield rows,
  and the 8.x→9.x canonical pair registers every 9.x definition as `added`.
