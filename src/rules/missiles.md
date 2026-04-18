# missiles rule

Audience: humans + LLM. Explains what the rule sees and how it interprets X4's missile data.

## What the rule processes

- **Missile wares** — all `<ware group="missiles">` entries in `{core|extensions/*/}libraries/wares.xml`
- **Missile macros** — `{pkg}/assets/props/WeaponSystems/missile/macros/{ware_component_ref}.xml` (the ware's `<component ref="..._macro" />` points to the macro)
- **Locale** — `t/0001-l044.xml`, page `20105` for missile names

Unlike the shields rule, the missiles rule is **ware-driven, not change-map-driven**. It enumerates missiles by iterating `wares.xml` in both versions rather than by looking at changed files. Reasoning: ware deprecation (flipping `tags="equipment missile"` → `tags="deprecated"`) is a line-level change inside `libraries/wares.xml` — a file that changes on nearly every release for unrelated reasons. A file-level change map can't pinpoint it; parsing the wares XML in both versions does.

## X4 missile data model

Each missile has:

```
libraries/wares.xml
  <ware id="missile_gen_m_guided_01_mk1" name="{20105,6394}" group="missiles" tags="equipment missile">
    <component ref="missile_gen_m_guided_01_mk1_macro" />

assets/props/WeaponSystems/missile/macros/missile_gen_m_guided_01_mk1_macro.xml
  <macro class="missile">
    <properties>
      <identification name="{20105,6394}" ... />
      <missile amount="1" barrelamount="1" lifetime="18" range="14000" guided="1" tags="mediumguided" />
      <explosiondamage value="1100" shielddisruption="10" />
      <reload time="14" />
      <hull max="20" />
      <lock time="2" range="14000" />
      <countermeasure resilience="0.92" />
```

The `<missile tags>` attribute classifies the missile (e.g. `mediumguided`, `smalldumbfire`, `largetorpedo`). Stats are spread across sibling elements (`explosiondamage`, `missile`, `reload`, etc.).

## Missile class

Taken directly from the `tags` attribute on `<properties/missile>`. Observed values form a `<size><type>` grid:

- **Sizes**: `small`, `medium`, `large`, plus un-prefixed legacy (`dumbfire`, `guided`, `torpedo`)
- **Types**: `dumbfire`, `guided`, `torpedo`, `smart`, `swarm`, `heatseeker`, `emp`, `cluster`
- Legacy missiles (pre-9.00) have un-prefixed tags: `dumbfire`, `guided`, `torpedo`
- New `missile_gen_*` missiles have size-prefixed tags: `smallguided`, `mediumdumbfire`, `largetorpedo`

When class changes between versions, the rule surfaces it as `class X→Y`.

## Stats tracked

Per-stat diff emitted as `<label> <old>→<new>`:

- `HP` — `hull/@max`
- `damage` — `explosiondamage/@value`
- `shielddisruption` — `explosiondamage/@shielddisruption` (new in 9.00)
- `hull_dmg` — `explosiondamage/@hull` (split damage type)
- `range` — `missile/@range`
- `lifetime` — `missile/@lifetime`
- `guided` — `missile/@guided` (flag 0/1)
- `reload` — `reload/@time`
- `CMres` — `countermeasure/@resilience`
- `locktime` — `lock/@time`
- `lockrange` — `lock/@range`

New attributes appearing (e.g. `shielddisruption` on torpedoes in 9.00) show as `None→<value>`. Removed attributes show `<value>→None`.

## Deprecation detection

X4 marks retired wares by setting `tags="deprecated"` on the ware element (replacing `tags="equipment missile"`). The rule emits `DEPRECATED` as a headline for missiles where this transition happened in the new version. Inverse transition is surfaced as `un-deprecated`.

This is a major lifecycle signal — deprecated wares no longer appear in the normal equipment catalog.

## DLC handling

The rule globs both `libraries/wares.xml` (core) and `extensions/*/libraries/wares.xml`. For each missile ware, the macro path is inferred relative to the ware's source package (core vs the specific extension), with a core fallback. Source label in output derives from the ware's containing path.

## 9.00 missile-line replacement (real-data pattern)

Observed in 8.00H4 → 9.00B6:

- **21 legacy missiles deprecated** — every pre-9.00 missile (both core and Split DLC) was marked `tags="deprecated"`
- **16 new `missile_gen_*` missiles added** — a fresh size-standardized lineup with `smallX`, `mediumX`, `largeX` class tags
- Several deprecated torpedoes got `shielddisruption="10"` added — presumably to keep them functional during the transition
- Net effect: the entire missile system was swapped out

LLM commentary should frame this as a single headline ("missile line fully replaced in 9.00; legacy missiles deprecated, new `gen_*` lineup introduced with size-standardized classes") rather than 37 independent events.

## Output

Each rule output is a `RuleOutput`:

```
tag:   "missiles"
text:  "[missiles] Heavy Torpedo Missile Mk1 (torpedo): DEPRECATED, shielddisruption None→10"
extras: {
    ware_id:          "missile_torpedo_heavy_mk1"
    name:             "Heavy Torpedo Missile Mk1"
    source:           "core" | "split" | ...
    class_old:        "torpedo"  # for modified / removed
    class_new:        "torpedo"  # for modified / added
    stat_diff:        {"shielddisruption": (None, "10"), ...}  # modified only
    newly_deprecated: bool                                      # modified only
    kind:             "added" | "removed" | "modified"
}
```

Text format: `[missiles] <name> (<class>) [<source>]: <comma-separated changes>`. `(class)` omitted when None, `[source]` omitted when core.

## What the rule does NOT cover

- Ware-level `price`, `production` recipe, or `owner faction` changes (a future `wares` rule)
- Missile `<physics>` (mass, inertia, drag) — not player-surfaceable without heavy interpretation
- `<effects>` (visual/audio refs) — cosmetic
- Ship-class restrictions from `<tags>` beyond the class token (e.g. `noplayerbuild` on production methods)
- DLC-only locale pages — the rule reads only the core `t/0001-l044.xml`. Observed missiles all use core name tokens.
