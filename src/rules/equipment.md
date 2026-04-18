# equipment rule

Audience: humans + LLM. Explains what the equipment rule sees and how it interprets X4's equipment data.

## What the rule processes

- **Equipment wares** — every `<ware>` in core + DLC `libraries/wares.xml` that the Wave 1 ownership predicate routes to `'equipment'`:
  - `@group` in `{software, hardware, countermeasures}` (straightforward equipment).
  - `@id` starting with `satellite_` (even when `@group=hardware`).
  - `@tags` containing `personalupgrade` (spacesuit gear, regardless of `@group`).
  - `spacesuit` token in the ware id (e.g. `engine_gen_spacesuit_01_mk1`, `weapon_gen_spacesuit_laser_01_mk1`).
- **Locale** — `t/0001-l044.xml`, resolved via `Locale.build(root)` so DLC locale overrides are merged on top of core.

Ships (`transport=ship` / `tags=ship` / `group=drones`) and shields/missiles are claimed by their own rules and never reach equipment — even if they carry a `personalupgrade` marker. That ordering is pinned in `_wave1_common.ware_owner`.

## Display name

`resolve_attr_ref(ware, locale, attr='name')` — **no heuristic dispatch**. The ware's `@name` attribute is a `{page,id}` ref that already names the correct locale page per-ware. Real 9.00B6 counterexamples where a heuristic `group→page` mapping would mispage:

- `bomb_player_limpet_emp_01_mk1` — `personalupgrade`, but the name lives on page 20201 (satellites/bombs), not 20113 (spacesuit gear).
- `software_scannerobjectmk3` — `personalupgrade + software`, but the name lives on page 20108 (software), not 20113.

Relying on the embedded ref is both simpler and correct.

## Classifications

`[<effective_category>, ...markers]` — the effective category is the branch of `ware_owner`'s equipment dispatch spelled in English:

- `spacesuit` — `@id` contains `spacesuit` OR `@tags` contains `personalupgrade`. If the ware's `@group` is present, a `<group>_origin` marker is appended so the LLM can see both facets (e.g. `['spacesuit', 'engines_origin']` for `engine_gen_spacesuit_01_mk1`).
- `satellite` — `@id` starts with `satellite_`.
- `software`, `hardware`, `countermeasures` — `@group` match.

Generic-token filter: `frozenset({'equipment'})` documented for completeness; the current classifier doesn't emit raw tags, so nothing is filtered at runtime.

## Ware fields diffed

- `price_min`, `price_avg`, `price_max` — `price/@min`, `price/@average`, `price/@max`
- `volume` — ware root `@volume`
- `transport` — ware root `@transport`
- Per-method `<production>` entries via `_wave1_common.diff_productions` (keyed by `@method`); labels pinned to the shared Wave 1 format:
  - `production[method=<M>] added` / `removed`
  - `production[method=<M>] time <ov>→<nv>` or `amount <ov>→<nv>`
  - `production[method=<M>] primary.<ware_id> <oa>→<na>` / `added` / `removed`
- `owner.<faction>` added/removed — one row per faction
- `tag.<token>` added/removed — one row per tag token
- Lifecycle: `DEPRECATED` / `un-deprecated` prepended when `tags="deprecated"` toggles

**Macros are NOT diffed.** Equipment wares point at a heterogeneous macro zoo (software bundles, scanner discs, satellites, spacesuit engines/weapons) with no single stat tuple that applies across the set. Instead the rule emits loud warnings on macro changes — see next section.

## Macro-gap warnings

Equipment's macro tree is diverse enough that we can't pick a meaningful stat table, but we also can't silently drop macro-file changes. The rule builds a reverse index per side via `equipment_macro_reverse_index(root)`:

```
{macro_ref: [ware_ids]}
```

For every `FileChange` in the `changes` arg whose path stem matches a key in `old_index` or `new_index`, the rule emits ONE `kind='warning'` output per impacted ware. Filter: `_macro.xml` files OR any file under `assets/props/` / `assets/fx/` — the filename-stem lookup key space the reverse index uses.

Key properties:

- **Dual-state indexing**: a ware that starts OR stops referencing a macro between versions is still impacted. Union of `old_index[stem] ∪ new_index[stem]`.
- **Independent of ware rows**: a ware with a `<ware>` delta AND a macro change emits TWO outputs (a modified row AND a warning). Suppressing the warning when the row exists would drop the macro-change signal.
- **One warning per (file, ware)**: multiple changes against the same file don't multi-fire per ware.

Warning text: `[equipment] WARNING: equipment macro <path> changed but equipment rule does not diff macros; ware=<ware_id>`.

## DLC handling

- `Locale.build(root)` merges all `extensions/*/t/0001-l044.xml` on top of core; collisions surface as warnings.
- `diff_library` materializes `libraries/wares.xml` for both versions with DLC `<diff>` ops applied; contributor attribution populates `sources` and `ref_sources` per entity.
- Reverse macro index is built from CORE `libraries/wares.xml` on each side (pre-DLC-application) — the underlying helper deliberately limits scope. Covers the majority of equipment macros; DLC-owned equipment gets the same macro-gap warning via the same path lookup if its file changes.

## Output

```
tag:   "equipment"
text:  "[equipment] Object Scanner Mk1 (software) [core]: price_max 13331→14000"
extras: {
    entity_key:       "software_scannerobjectmk1"
    ware_id:          "software_scannerobjectmk1"
    kind:             "added" | "removed" | "modified" | "warning"
    classifications:  ["software"] | ["spacesuit", "engines_origin"] | ...
    sources:          ["core"]             # added/removed
    old_sources:      ["core"]             # modified
    new_sources:      ["core", "boron"]    # modified
    ref_sources:      {"component/@ref": "core"}
}
```

Text format: `[equipment] <name> (<classifications>) <sources>: <comma-separated changes>`. Source bracket follows `render_sources` — `[core]`, `[core+boron]`, or `[core→core+boron]` when provenance shifts.

## What the rule does NOT cover

- Equipment macro stats. By design — a single stat tuple across software/scanner/satellite/spacesuit macros doesn't exist. Any macro change surfaces as a warning instead.
- `<icon>`, `<software predecessor>`, `<restriction licence>`, `<use threshold>` on the ware. Not player-surfaceable balance changes.
- Ship drones (`group=drones`) — handled by the ships rule.
- DLC-only wares that route outside equipment (e.g. DLC-specific shield with `personalupgrade` token) — excluded by the ownership predicate's shield-hard-exclude branch.
