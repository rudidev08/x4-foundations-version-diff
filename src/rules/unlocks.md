# unlocks rule

Audience: humans + LLM. Explains what the unlocks rule sees and how it
interprets X4's discount / chapter / info-unlock data.

## What the rule processes

Three independent sub-sources under one `unlocks` tag, distinguished by
`extras.subsource`. They do not cross-reference; a single entity shows up
in exactly one sub-source.

- **`discount`** — `<discount>` entries in `libraries/unlocks.xml` (core +
  DLC, materialized via `diff_library`). Keyed by `@id`. Display name via
  `resolve_attr_ref(discount, locale, attr='name')` which resolves
  references into locale page 20210.
- **`chapter`** — `<category>` entries in `libraries/chapters.xml` (core +
  DLC; real data has an empty core file, the ventures DLC supplies all
  rows). Keyed by `@id`. Display name via locale page 55101.
- **`info`** — `<info>` entries in `libraries/infounlocklist.xml`. Keyed by
  `@type` (enum key). Display uses `@type` verbatim; no locale resolution.

Generic-token filter across all three: `frozenset()` — every
subsource-name token in the classification list is information-bearing and
is retained.

## Data model

### Discount

```
libraries/unlocks.xml
  <discount id="fruitfulcultivation" name="{20210,1011}" description="">
    <conditions weight="20">
      <wares sells="wheat meat spices spaceweed"/>
      <viewangle max="20"/>
      <distance exact="600m"/>
      <scannerlevel max="2" />
    </conditions>
    <actions>
      <amount min="2" max="6"/>
      <duration min="24h" max="48h"/>
    </actions>
    <rechecktime min="24h" max="36h"/>
  </discount>
```

Sibling top-level entities (`<commission>`, `<blueprint>`, `<globals>`) are
deliberately out of scope — only `<discount>` matches the xpath.

### Chapter

```
libraries/chapters.xml  (empty core; populated by ego_dlc_ventures)
  <category id="chapter_brane_fuel" group="1" name="{55101,1001}" />
  <category id="chapter_tactical_knowledge" group="2" name="{55101,2001}"
            teamware="ven_tactical_knowledge" />
  <category id="chapter_foreign_affairs" group="3" name="{55101,13001}"
            highlight="true" teamware="ven_operation_progress_1"
            progressmode="count" />
```

### Info

```
libraries/infounlocklist.xml
  <infos>
    <info type="name" percent="1" />
    <info type="production_products" percent="30" />
    <info type="storage_amounts" percent="50" />
    ...
  </infos>
```

The `<unlocks>` subtree (`<secrecy level="...">` → `<scan ...>`) is out of
scope — only `.//info` matches the xpath.

## Classifications

All three sub-sources: `['<subsource>']` — `['discount']`, `['chapter']`,
or `['info']`. The generic filter is empty so these tokens are always
retained.

## Fields diffed

### Discount

- Top-level attrs on `<discount>` excluding `@id`, `@name`, `@description`.
- `<conditions>` block — attrs on the block element (e.g., `@weight`,
  `@leak`), plus child entries **keyed by tag name**: each child tag may
  appear at most once per block. Per-tag diff surfaces:
  - Added: `conditions.<tag> added (<attrs>)` or just
    `conditions.<tag> added` if no attrs.
  - Removed: `conditions.<tag> removed (was <attrs>)`.
  - Changed: `conditions.<tag> <attr> <ov>→<nv>` per changed attr.
- `<actions>` block — same shape: attrs on the block element, children
  keyed by tag name.

**Parse-time uniqueness.** If two children under one `<conditions>` (or
`<actions>`) block share a tag, the discount row is flagged incomplete
with reason `condition_type_not_unique` (or `action_type_not_unique`).
The diff uses last-wins and still emits a best-effort line; the incomplete
flag surfaces via `forward_incomplete_many`.

### Chapter

- `@group`
- `@highlight`
- `@teamware`

Other attrs (`@name`, `@progressmode`, `@id`) are display / identity and
are not diffed directly. `@progressmode` is stable in real data and not
part of the core lifecycle semantics the rule tracks; add it later if it
ever changes in release notes.

### Info

- `@percent`

No other attrs are tracked. The plan specifies `@percent` only.

## Output

```
tag:   "unlocks"
text:  "[unlocks] <display name> (<classifications>) [<sources>]: <changes>"
extras: {
    entity_key:      ("discount", "<discount_id>")
                   | ("chapter",  "<category_id>")
                   | ("info",     "<info_type>")
    subsource:       "discount" | "chapter" | "info"
    kind:            "added" | "removed" | "modified"
    classifications: [...]
    discount_id / chapter_id / info_type: per-subsource
    sources / old_sources / new_sources: per canonical schema
    ref_sources:     { ... }
}
```

## DLC handling

- `Locale.build(root)` merges all `extensions/*/t/0001-l044.xml` on top of
  core; collisions surface as warnings. Page 55101 lives in the ventures
  DLC, not core — `Locale.build` picks it up automatically.
- All three sub-sources materialize their library file via `diff_library`,
  which applies `<diff>` ops across DLC extensions. Contributor
  attribution populates `sources` / `ref_sources` per entity.

## Diagnostic channel

Failures ride `forward_incomplete_many` with per-subsource scoping. Two
kinds:

1. **DLC-patch failures** — routed through `_MergedReport`, which
   prefixes `affected_keys` from the underlying DiffReport with the
   subsource tag so the `entity_key=(subsource, id)` form matches.
2. **Rule-level assertions** (discount only) —
   `condition_type_not_unique`, `action_type_not_unique`. Each is
   pre-tagged with `('discount', id)` affected_keys at emission time.

Warnings (locale collisions, DLC positional overlaps) emit
`[unlocks] WARNING: ...` rows and do not contaminate normal outputs.

## What the rule does NOT cover

- `<commission>` / `<blueprint>` / `<globals>` in `unlocks.xml`. They sit
  beside `<discount>` but have different shapes (commission has `<wares
  buys=...>`, blueprint has no actions block, globals is a singleton
  settings node). Adding coverage is a separate task.
- `<unlocks>` / `<secrecy>` / `<scan>` in `infounlocklist.xml`. These
  define per-scan-level probabilities for the "unlocks" system itself; the
  `<info>` list is the per-field threshold data, which is what release
  notes typically care about.
- `<category>`'s `@progressmode` attr. Stable in real data; add later if
  it ever changes.
