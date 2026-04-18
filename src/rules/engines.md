# engines rule

Audience: humans + LLM. Explains what the engines rule sees and how it interprets
X4's engine data.

## What the rule processes

- **Engine wares** — every `<ware group="engines">` entry in core + DLC
  `libraries/wares.xml`, selected via `owns(ware, 'engines')` (Wave 1 shared
  ownership predicate; disjoint from `missiles`, `shields`, `weapons`, `turrets`,
  `equipment`, `wares`).
- **Engine macros** — `{pkg}/assets/props/Engines/macros/{ware_component_ref}.xml`;
  resolved via `resolve_macro_path(root, pkg_root, ref, kind='engines')` with
  ref-source attribution (the DLC that last wrote `component/@ref` owns the
  lookup).
- **Locale** — `t/0001-l044.xml`, page `20107` for engine names. Built via
  `Locale.build(root)` so DLC locale overrides are merged on top of core.

The rule is **ware-driven via `diff_library`** — it materializes the full
`libraries/wares.xml` effective tree (core + DLC diffs applied), then runs
entity-level diffing keyed by ware id. This is the same shape as the other
Wave 1 rules (weapons, turrets, equipment, wares).

## Data model

```
libraries/wares.xml
  <ware id="engine_arg_m_combat_01_mk1" name="{20107,2204}" group="engines"
        transport="equipment" volume="1" tags="engine equipment">
    <price min="14357" average="15952" max="17547" />
    <production time="15" amount="1" method="default">
      <primary>
        <ware ware="antimatterconverters" amount="3" />
        <ware ware="engineparts" amount="7" />
      </primary>
    </production>
    <component ref="engine_arg_m_combat_01_mk1_macro" />

assets/props/Engines/macros/engine_arg_m_combat_01_mk1_macro.xml
  <macro name="..._macro" class="engine">
    <properties>
      <boost thrust="7.9" acceleration="7.79" ... />
      <travel thrust="8.04" attack="44.3" ... />
      <thrust forward="1084.8" reverse="1084.8" />
      <hull max="..." />
      ...
```

## Classifications

Ware id → `[race, size, type, mk]` via regex
`^engine_([a-z]+)_([a-z])_([a-z]+)_\d+_([a-z0-9]+)$`.

- `race`: `arg`, `par`, `tel`, `bor`, `ter`, `spl`, `pir`, `kha`, `xen`, `gen`, …
- `size`: `s`, `m`, `l`, `xl`.
- `type`: `allround`, `combat`, `travel`, `racer`, `mining`, …
- `mk`: `mk1`, `mk2`, `mk3`, …

All four tokens are meaningful — the generic-token filter is empty. Ids that
don't match the regex (anomalies) classify to `[]` and are emitted without a
paren label.

## Ware fields diffed

Via `diff_attrs`-style walk over `WARE_STATS`:

- `price_min` — `price/@min`
- `price_avg` — `price/@average`
- `price_max` — `price/@max`
- `volume` — ware root `@volume`

Production entries diffed via the shared Wave 1 `diff_productions` helper,
keyed by `production/@method`. Labels:

- `production[method=<M>] added` / `removed`
- `production[method=<M>] time <ov>→<nv>` or `amount <ov>→<nv>`
- `production[method=<M>] primary.<ware_id> <oa>→<na>` / `added` / `removed`

## Macro fields diffed

Via `diff_attrs` over `MACRO_STATS`:

- `boost_thrust` — `properties/boost/@thrust`
- `boost_accel` — `properties/boost/@acceleration`
- `travel_thrust` — `properties/travel/@thrust`
- `travel_attack` — `properties/travel/@attack`
- `thrust_forward` — `properties/thrust/@forward`
- `thrust_reverse` — `properties/thrust/@reverse`
- `hull_max` — `properties/hull/@max`

## Lifecycle

Deprecation toggle on the ware `tags` attribute. When the `deprecated` token
appears in the new version but not the old, the change list is **prepended** with
`DEPRECATED`; inverse transition is prepended with `un-deprecated`. Parity with
missiles/shields even though the 9.00 release didn't mass-deprecate engines.

## Output

```
tag:   "engines"
text:  "[engines] Argon M Combat Engine Mk1 (arg, m, combat, mk1) [core]: price_max 17547→19000, boost_thrust 7.9→8.5"
extras: {
    entity_key:       "engine_arg_m_combat_01_mk1"
    macro:            "engine_arg_m_combat_01_mk1_macro"
    kind:             "added" | "removed" | "modified"
    classifications:  ["arg", "m", "combat", "mk1"]
    sources:          ["core"]          # added/removed only
    old_sources:      ["core"]          # modified/removed
    new_sources:      ["core", "boron"] # modified/added; shows DLC provenance
    ref_sources:      {"component/@ref": "boron"}
}
```

Text format: `[engines] <name> (<r, s, t, mk>) <sources>: <comma-separated changes>`.
Source bracket follows `render_sources` — `[core]`, `[core+boron]`, or
`[core→core+boron]` when provenance shifts.

## DLC handling

- `Locale.build(root)` merges all `extensions/*/t/0001-l044.xml` on top of core,
  records collisions as warnings.
- `diff_library` materializes `libraries/wares.xml` for both versions with all
  DLC `<diff>` ops applied; contributor attribution populates `sources` and
  `ref_sources` per entity.
- Macro resolution follows the ref-source attribution: if the DLC that last
  wrote `component/@ref` is on disk, look there first; fall back to core.
- Cross-DLC conflict classification runs during materialization — failures
  bubble up as an `incomplete` sentinel; warnings (e.g. `positional_overlap`)
  get a `kind='warning'` RuleOutput.

## What the rule does NOT cover

- `<effects>` cosmetics (visual/audio refs) on the macro.
- `<decelerationcurve>` / `<strafecurve>` — curve-shape tuning data, not a
  player-surfaceable single number.
- `<sounds>` refs.
- Ship-class restrictions (`restriction licence="..."`) on the ware. Covered
  globally by the `wares` rule where relevant.
- Engine skin variants that are pure visual (`_skin_*` suffixes) — these fall
  outside the id regex and emit without classifications.
