# stations rule

Audience: humans + LLM. Explains what the rule sees and how it interprets
X4's station/module data and how it validates the cross-entity reference
graph between them.

## What the rule processes

Stations are the largest Wave 3 rule: five independent sub-sources under one
tag, backed by five `diff_library` calls over the relevant library files.

- **`station`** — `libraries/stations.xml`, xpath `.//station`. Stations list
  the top-level `<station @id>` entities with `@group` pointing at a
  stationgroup `@name`.
- **`stationgroup`** — `libraries/stationgroups.xml`, xpath `.//group`. Each
  group holds one or more `<select @constructionplan>` children that pick a
  construction plan by id.
- **`module`** — `libraries/modules.xml`, xpath `.//module`. Modules are the
  building blocks (production, storage, defence, dockarea, ...). Classifications
  come from `@class` plus `<category>` attributes.
- **`modulegroup`** — `libraries/modulegroups.xml`, xpath `.//group`. Each
  group holds `<select @macro>` children. The attribute is `@macro` but the
  *value* is a module `@id` — see the naming quirk below.
- **`constructionplan`** — `libraries/constructionplans.xml`, xpath `.//plan`.
  Plans are ordered `<entry index="N" macro="…">` lists where each entry
  references either a module directly or a modulegroup as a bridge.

Each sub-source scopes its own contamination via
`forward_incomplete_many`; a failure in one library never bleeds to another
library's rows.

## Data model

### station

```xml
<station id="shipyard_arg" group="shipyard_arg">
  <category tags="shipyard" faction="[argon, antigone, hatikvah]" />
</station>
```

- Key: `("station", @id)`.
- Display: `@id` (no locale lookup).
- Classifications: `["station", ...<category @tags>]`.
- Fields diffed: `@group`, `<category @tags>` (list), `<category @faction>` (list).
- Refs: `{"group_ref": <@group>}` when present. `station_group_unresolved=True`
  flags dangling refs (target missing from stationgroups.xml).

### stationgroup

```xml
<group name="shipyard_arg">
  <select constructionplan="arg_shipyard" />
</group>
```

- Key: `("stationgroup", @name)`.
- Display: `@name`.
- Classifications: `["stationgroup"]`.
- Fields diffed: `<select>` entries keyed by `@constructionplan` (`@chance`
  per entry), plus `total_entry_count`.
- Refs: `{"plan_refs": [<@constructionplan>, ...]}`.

### module

```xml
<module id="prod_gen_advancedelectronics" group="…">
  <category ware="advancedelectronics" tags="[production, module]"
            race="[argon, paranid]" faction="[argon, paranid]" />
  <compatibilities>
    <limits production="2"/>
    <maxlimits production="6"/>
    <production ware="advancedelectronics" chance="15" />
  </compatibilities>
</module>
```

- Key: `("module", @id)`.
- Display: `<identification @name>` via `resolve_attr_ref`; fallback `@id`.
- Classifications: `["module", @class, ...<category @tags>, ...<category @faction>,
  ...<category @race>]` minus the generic filter (`frozenset({'station',
  'module', 'stationgroup', 'modulegroup', 'constructionplan'})`), de-duped,
  preserving order.
- Fields diffed: `@class`, `<category @ware>` (single value), `<category @tags>`
  / `@faction` / `@race` (lists), `<compatibilities><limits>` +
  `<maxlimits>` attribute-by-attribute, and `<compatibilities><production>`
  entries keyed by `@ware` (diff `@chance`).
- Refs: `{"ware_produced": <category @ware>}` when present.

### modulegroup

```xml
<group name="prod_gen_advancedelectronics">
  <select macro="prod_gen_advancedelectronics_macro" />
</group>
```

- Key: `("modulegroup", @name)`.
- Display: `@name`.
- Classifications: `["modulegroup"]`.
- Fields diffed: `<select>` entries keyed by `@macro` (`@chance` per entry),
  plus `total_entry_count`.
- Refs: `{"module_macro_refs": [<@macro>, ...]}`.

### constructionplan

```xml
<plan id="par_wharf" name="{20102,1241}">
  <entry index="1" macro="defence_par_claim_01_macro">
    <offset>…</offset>
  </entry>
  <entry index="2" macro="defence_par_tube_01_macro" connection="connectionsnap001">
    <predecessor index="1" connection="connectionsnap004"/>
  </entry>
</plan>
```

- Key: `("constructionplan", @id)`.
- Display: `@id` (the `@name` locale ref is ingame voiceover text, not a
  stable data identifier).
- Classifications: `["constructionplan"]`.
- Fields diffed: `@race`, `<entry>` entries keyed by `(@macro, @index)` (diff
  `@connection`), plus `total_entry_count`.
- Refs: **typed** — see below.

## Typed constructionplan refs

An `<entry @macro>` value can point at either a module `@id` or a modulegroup
`@name`. The rule disambiguates at run time using the loaded trees so
downstream consumers never have to re-resolve:

- `entry_module_refs` — values that exist in modules.xml's `{@id}` set.
- `entry_modulegroup_refs` — values that exist in modulegroups.xml's
  `{@name}` set.
- `entry_unresolved_refs` — values matching neither. These emit a warning
  (`reason='ref_target_unresolved'`, `ref_kind='plan_entry_macro'`).
- Namespace collision (match BOTH) — rare but surfaces as
  `extras.incomplete=True` with reason `'ref_namespace_collision'` on the
  plan row. Both typed ref lists still record the value for diagnostic
  purposes.

## Cross-entity ref validation

Every ref hop is validated; warning shape varies by how anomalous unresolved
refs are in real-data X4:

- `station.group_ref` → stationgroup `@name` set. **Per-ref warning** — real
  data has 0/65 unresolved, so any warning is loud.
- `stationgroup.plan_refs` → constructionplan `@id` set. **Per-ref warning** —
  real data has 0/69 unresolved.
- `modulegroup.module_macro_refs` → module `@id` set. **Aggregate warning per
  owner** — real-data modulegroup `select @macro` values reference on-disk
  macro *files* (with `_macro` suffix) that aren't library modules, so
  literally every modulegroup has unresolved refs under the spec's literal
  resolution contract. Emitting one warning per (modulegroup, unresolved_ref)
  would be ~180 rows per run. The aggregate shape keeps the signal while
  avoiding noise. Consumers inspect the full `module_macro_refs` list on the
  row's extras.refs to see which refs resolved.
- `constructionplan` entries — typed via runtime inspection. **No per-entry
  warning** — ~3400 plan entries per run would fail a literal @id/@name match
  in real 9.00B6 data. The `entry_unresolved_refs` bucket on the plan row's
  extras.refs IS the surface. Namespace collisions (entry matches BOTH a
  module @id AND a modulegroup @name) surface loudly as
  `extras.incomplete=True` + rule-synthesized `ref_namespace_collision`
  failure; those are genuinely rare.

Resolution uses `DiffReport.effective_new_root` / `effective_old_root`; the
rule never reaches into the private `_materialize` helper. Validation sets
are built with `{el.get(attr) for el in tree.iter(tag)}` on each side.

**Why @macro holds module ids:** in modulegroups.xml the `<select @macro>`
attribute value is the *target module's `@id`* (e.g.
`prod_gen_advancedelectronics_macro`). X4 uses `@macro` to name the
attribute because the selected module's on-disk macro file matches its id.
We resolve against module `@id` anyway — the naming quirk is in X4's data,
not the rule.

## Locale

`Locale.build(root)` merges DLC `t/0001-l044.xml` over core. Only the
`module` sub-source uses locale (page 20201 for ware-named production
modules). Station, stationgroup, modulegroup, constructionplan display
names come straight from the `@id`/`@name` attribute — no locale hop.

## Output

```
tag:   "stations"
text:  "[stations] <name> (<classifications>) [<sources>]: <changes>"
extras: {
    entity_key:      (subsource, key)
    subsource:       "station" | "stationgroup" | "module" | "modulegroup" | "constructionplan"
    kind:            "added" | "removed" | "modified"
    classifications: [...]
    refs:            { … per sub-source … }
    sources / old_sources / new_sources: per canonical schema
    ref_sources:     { attr_path: "<dlc>" }
    incomplete:      True  # only on constructionplan rows with namespace
                           # collisions; other contamination flows via
                           # `forward_incomplete` based on affected_keys.
}
```

Text format: `[stations] <name> (<classifications>) [<sources>]: <changes>`.

## Contamination scoping

Five `(report, subsource)` pairs flow through `forward_incomplete_many`:

1. `station` — wraps station.xml diff + station-scope rule warnings.
2. `stationgroup` — same shape.
3. `module` — same shape.
4. `modulegroup` — same shape.
5. `constructionplan` — wraps plan.xml diff + namespace-collision failures.

Rule-synthesized failures (namespace collisions) and warnings (unresolved
refs) are routed through `_MergedReport` / `_ExtraFailuresReport` so the
per-subsource scoping stays intact. A modulegroup unresolved ref warning
never contaminates a station row.

## Generic-token filter

`frozenset({'station', 'module', 'stationgroup', 'modulegroup',
'constructionplan'})` — the rule's fixed tokens are stripped from
classifications so downstream filters stay meaningful. Applied to all
classification token sources uniformly.

## Known limitations

- Module `<identification @name>` locale resolution uses `resolve_attr_ref`
  directly on the `<identification>` child. Some modules omit the element
  entirely and fall through to `@id` — same shape as ship macros.
- The `<entry @macro>` attribute name conflicts with "macro" as used in
  ship/weapon/engine macros. In constructionplans the value is a module id
  or modulegroup name, NOT a macro filename. The typed-ref contract makes
  this explicit for consumers.
- `<production>` entries in modules.xml live under `<compatibilities>`,
  NOT directly under `<module>` like ware-driven rules. The module diff
  accounts for this.
- `@chance` is rarely set on real-data `<select>` entries (none in 9.00B6
  stationgroups or modulegroups). The diff still emits chance changes when
  they happen — not dead code, just rare.
