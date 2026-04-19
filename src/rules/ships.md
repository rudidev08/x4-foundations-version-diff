# ships rule

Audience: humans + LLM. Explains what the ships rule sees and how it
interprets X4's ship data.

## What the rule processes

Ships are the only Wave 2 rule that fans across **three independent sub-sources**
under one tag. Each sub-source is self-contained — they don't cross-reference
or de-duplicate — and `extras.subsource` distinguishes the three. A single
ship can emit rows from all three subsources in one run.

- **`macro`** — ship macro files at
  `assets/units/size_*/macros/ship_*_macro.xml` (core) and
  `extensions/*/assets/units/**/ship_*_macro.xml` (DLC). Each file IS one
  ship macro (NOT `<diff>`-wrapped), enumerated via `file_level.diff_files`.
  Added/removed files = added/removed macros; modified files run
  `diff_attrs` over `MACRO_STATS`.
- **`ware`** — `<ware>` entries in core + DLC `libraries/wares.xml`
  (materialized via `diff_library`) filtered by
  `@transport="ship" OR "ship" in @tags OR @group="drones"`. Macro
  resolution for display name + classifications goes through
  `<component ref>` → `resolve_macro_path(kind='ships')` so drones
  (macro names `ship_gen_xs_cargodrone_*`) surface correctly even though
  their filenames don't match the `ship_*_macro.xml` glob.
- **`role`** — `<ship>` entries in `libraries/ships.xml`
  (materialized via `diff_library`). Simple entity model — `@id` keyed,
  no locale lookup, fields under `<category>`, `<pilot><select>`,
  `<basket>`, `<drop>`, `<people>`.

**Locale** page 20101 ("Ships") drives display names for the `macro` and
`ware` sub-sources. The `role` sub-source uses `@id` directly (no locale
ref on role rows).

## Data model

### Macro file

```
assets/units/size_m/macros/ship_arg_m_fighter_01_macro.xml
  <macro name="ship_arg_m_fighter_01_macro" class="ship_m">
    <component ref="ship_arg_m_fighter_01" />
    <properties>
      <identification name="{20101,10101}" ... />
      <hull max="12000" />
      <people capacity="9" />
      <physics mass="28.75" ... />
      <jerk forward="0.5" strafe="0.6" angular="0.6" />
      <purpose primary="fight" />
      <storage missile="12" />
      <ship type="gunboat" />
      ...
```

Ship-macro files are standalone XML — they are NOT DLC-patched, so there
is no `DiffReport`. Parse errors flow into a synthetic `_MacroReport`
wrapper alongside the ware/role DiffReports; `forward_incomplete_many`
applies the subsource-scoped contamination rule uniformly.

### Ware entry

```
libraries/wares.xml
  <ware id="ship_arg_m_fighter_01_a" name="{20101,11001}"
        transport="ship" tags="ship" volume="1">
    <price min="500000" average="560000" max="620000" />
    <production time="180" amount="1" method="default">...</production>
    <component ref="ship_arg_m_fighter_01_macro" />
    <restriction licence="generaluseship" />
    <owner faction="argon" />
    <owner faction="hatikvah" />
```

Drones use `group="drones"`:

```
  <ware id="ship_gen_xs_cargodrone_01_a" group="drones" transport="ship" ...>
    <component ref="ship_gen_xs_cargodrone_01_a_macro" />
```

### Role entry

```
libraries/ships.xml
  <ship id="argon_trader_container_m" group="arg_trader_container_m">
    <category tags="[trader, container, mission]" faction="[argon, hatikvah]"
              size="ship_m" />
    <pilot>
      <select faction="argon" tags="traderpilot" />
    </pilot>
    <basket basket="all_container" />
    <drop ref="ship_medium_civilian" />
    <people ref="argon_freighter_crew" />
```

## Classifications

### Macro sub-source

`[macro@class, ship@type]` — e.g., `['ship_m', 'gunboat']`,
`['ship_xs', 'drone']`. Either element missing → that token is omitted.

### Ware sub-source

`[@transport, ...tags, restriction@licence]` minus the generic filter
(`frozenset({'ship'})`) and minus `deprecated`. Note `transport="ship"` and
`tags="ship"` both strip to the same filtered token — ship wares typically
end up with `(generaluseship)` or `(noplayerbuild, generaluseship)` after
filtering.

### Role sub-source

`[...tags_from_category, category@size]`. The `@tags` attribute on
`<category>` is a bracket-list (`[trader, container]`) which is split on
`,`. Size is e.g. `ship_s`, `ship_m`, `ship_l`, `ship_xl`.

Generic-token filter across all three: `frozenset({'ship'})`.

## Fields diffed

### Macro (`MACRO_STATS`)

- `hull_max` — `properties/hull/@max`
- `people_cap` — `properties/people/@capacity`
- `mass` — `properties/physics/@mass`
- `jerk_forward` / `jerk_strafe` / `jerk_angular` —
  `properties/jerk/@forward|@strafe|@angular`
- `purpose_primary` — `properties/purpose/@primary`
- `storage_missile` — `properties/storage/@missile`

### Ware (`WARE_STATS` + domain diffs)

- `price_min` / `price_avg` / `price_max` — `price/@min|@average|@max`
- `volume` — ware root `@volume`
- `owner_factions added={...} removed={...}` — set-diff of
  `<owner @faction>` children
- `licence <ov>→<nv>` — `<restriction @licence>`
- Production: via shared `diff_productions` (keyed by `@method`).

### Role (`ROLE_STATS` + pilot select)

- `category_tags` / `category_faction` / `category_size` —
  `<category @tags|@faction|@size>`
- `basket` — `<basket @basket>`
- `drop_ref` — `<drop @ref>`
- `people_ref` — `<people @ref>`
- `pilot_faction` / `pilot_tags` — `<pilot><select @faction|@tags>`

## Parse-error diagnostic channel

Ship macro files can be malformed. The rule maintains a shared
`_MacroReport` with two contributor paths:

1. **Macro sub-source** — during `diff_files` iteration, `ET.ParseError`
   on an added/removed/modified macro file appends a failure with
   `affected_keys=[]` (global contamination within `subsource='macro'`).
2. **Ware sub-source** — when resolving a ware's `<component ref>` to a
   macro file, a parse error appends a failure with
   `affected_keys=[ware_id]`. The ware row still emits (with
   macro-derived fields missing) but is contaminated via
   `forward_incomplete_many`.

`forward_incomplete_many` receives three `(report, label)` pairs:

```python
forward_incomplete_many(
    [(macro_report, 'macro'),
     (_PrefixedReport(ware_report, 'ware'), 'ware'),
     (_PrefixedReport(role_report, 'role'), 'role')],
    outputs, tag=TAG,
)
```

`_PrefixedReport` is a wrapper that rewrites `affected_keys` from raw ids
to `(subsource, id)` tuples on the fly, matching the rule's tuple
`entity_key` form.

## Output

```
tag:   "ships"
text:  "[ships] Argon Fighter (ship_m, gunboat) [core]: hull_max 12000→14000"
extras: {
    entity_key:      ("macro", "ship_arg_m_fighter_01_macro")
                     | ("ware",  "ship_arg_m_fighter_01_a")
                     | ("role",  "argon_trader_container_m")
    subsource:       "macro" | "ware" | "role"
    kind:            "added" | "removed" | "modified"
    classifications: [...]
    macro:           "ship_..._macro"   # macro sub-source
    ware_id:         "ship_..."         # ware sub-source
    role_id:         "..."              # role sub-source
    path:            "assets/..."       # macro sub-source
    source:          "core" | "<dlc>"   # macro sub-source single token
    sources / old_sources / new_sources: per canonical schema
    ref_sources:     { "component/@ref": "<dlc>" }   # ware / role
}
```

Text format: `[ships] <name> (<classifications>) [<sources>]: <changes>`.
Source bracket follows `render_sources` — `[core]`, `[core+boron]`, or
`[core→core+boron]`.

## DLC handling

- `Locale.build(root)` merges all `extensions/*/t/0001-l044.xml` on top
  of core; collisions surface as warnings.
- Macro sub-source globs DLC extensions explicitly
  (`extensions/*/assets/units/**/ship_*_macro.xml`); each DLC-sourced
  macro carries `source=<dlc_short_name>` in extras.
- Ware + role sub-sources materialize their library via `diff_library`
  (applies `<diff>` ops); contributor attribution populates `sources` /
  `ref_sources` per entity.
- Ware-side macro resolution uses ref-source attribution: if the DLC
  that last wrote `component/@ref` is on disk, look there first; fall
  back to core via `resolve_macro_path`.

## What the rule does NOT cover

- `<connections>` inside ship macros — storage/turret/shield parent
  links are covered by the storage / turrets / shields rules respectively
  (via their own reverse indices).
- `<software>` default/compatible entries on a ship macro — software
  compatibility diffing is left to a future rule; today the relevant
  signal surfaces via the `equipment` rule when software wares change.
- `<explosiondamage>`, `<secrecy>`, `<glow>`, `<steeringcurve>`,
  `<thruster>`, `<sound_occlusion>` — cosmetic / noise-level tuning
  attributes that don't map to a single player-surfaceable number.
- Ship groups in `libraries/shipgroups.xml` — a separate (future)
  subsource if needed. Not modelled today.
