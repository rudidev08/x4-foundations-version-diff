# turrets rule

Audience: humans + LLM. Explains what the rule sees, how it diffs it, and how classifications are assembled.

## What the rule processes

Three sources, unioned:

- **Turret wares** in `libraries/wares.xml` (core + DLC patches) with `group="turrets"`. Ownership goes through `_wave1_common.owns(ware, 'turrets')` — the shared Wave 1 predicate — so spacesuit or missile-launcher wares routed to `equipment`/`missiles` never show up here.
- **Turret macros** under `{core|extensions/*/}assets/props/WeaponSystems/<subtype>/macros/turret_*_macro.xml`. Shared directory tree with weapons; the rule filters by `turret_` filename prefix.
- **Bullet macros** under `{core|extensions/*/}assets/fx/weaponFx/macros/bullet_*_macro.xml`. One bullet macro can back multiple turret macros — see Bullet fan-out below.

## X4 turret data model

Three files contribute to one turret:

```
libraries/wares.xml
  <ware id="turret_arg_m_standard_01_mk1" name="{20105,4004}" group="turrets" tags="equipment turret">
    <price min="..." average="..." max="..." />
    <production method="default" time="10" amount="1">
      <primary>
        <ware ware="energycells" amount="5" />
        <ware ware="turretcomponents" amount="1" />
      </primary>
    </production>
    <component ref="turret_arg_m_standard_01_mk1_macro" />

assets/props/WeaponSystems/standard/macros/turret_arg_m_standard_01_mk1_macro.xml
  <macro name="turret_arg_m_standard_01_mk1_macro" class="turret">
    <component ref="turret_arg_m_standard_01_mk1" />
    <properties>
      <identification name="{20105,4004}" ... />
      <bullet class="bullet_arg_m_standard_01_mk1_macro" />
      <rotationspeed max="30" />
      <rotationacceleration max="50" />
      <hull max="1200" />

assets/fx/weaponFx/macros/bullet_arg_m_standard_01_mk1_macro.xml
  <macro name="bullet_arg_m_standard_01_mk1_macro" class="bullet">
    <properties>
      <ammunition value="6" reload="2" />
      <bullet speed="2800" lifetime="1" amount="1" barrelamount="2"
              timediff="0.01" reload="1" heat="0.1" />
```

## Fields diffed

Ware-level (under `<ware>`):
- `price/@min`, `price/@average`, `price/@max`
- `volume` (attribute on `<ware>`)
- `<production>` entries keyed by `@method`, diffing `@time`, `@amount`, and nested `<primary><ware @ware @amount/>` entries. Label forms are pinned in `_wave1_common.diff_productions`.
- `tags` — free-form tag string; deprecation toggles surface as `DEPRECATED` / `un-deprecated` at the head of the change list, and other token drift emits `tags '<old>'→'<new>'`.

Turret macro (under `<properties>`):
- `bullet/@class` — the bullet macro this turret fires.
- `rotationspeed/@max`
- `rotationacceleration/@max`
- `hull/@max`

Bullet macro (sub-source `"bullet"`, under `<properties>`):
- `ammunition/@value`
- `bullet/@speed`, `@lifetime`, `@amount`, `@barrelamount`, `@timediff`, `@reload`, `@heat`

## Classifications

`[subtype, ...tag_tokens, maybe 'guided']`, assembled from:

- `subtype` — the WeaponSystems subdirectory the macro lives in: one of `standard`, `energy`, `heavy`, `capital`, `guided`, `dumbfire`, `torpedo`, `mining`, `missile`, `mines`, `spacesuit`, etc. Same slicing used for weapons.
- `tag_tokens` — connection `tags` on the turret's referenced component file, filtered through `GENERIC_CLASSIFICATION_TOKENS = {'turret', 'component'}`. Meaningful tokens remaining include the turret's size (`small`/`medium`/`large`/`extralarge`), its variant tier (`standard`/`advanced`/...), its role (`combat`/`mining`/...), and any ship-class restrictions.
- `'guided'` — appended when the turret launches guided missiles. Triggered by any of:
  - the turret macro's `<bullet @class>` matches `bullet_*missilelauncher*` (a ref id starting with `bullet_` and containing `missilelauncher`),
  - any nested element under the turret macro carries `missilelauncher` in its `tags` attribute,
  - the ware itself carries `missilelauncher` in its top-level `tags` (as observed on every real 9.00B6 `turret_*_guided_*` / `turret_*_dumbfire_*` / `turret_*_torpedo_*` ware).

Classifications are lossy on purpose — they surface the most useful labels for the LLM. Raw macro/component refs stay in `extras` for downstream reasoning.

## Bullet fan-out

Bullet macros are 1-to-N with turret macros. When `bullet_gen_turret_std_01_mk1_macro` changes:

1. Build `{bullet_ref: [turret_ware_id, ...]}` for BOTH the old and new effective trees (dual-state indexing — a turret that either referenced the bullet in the old version OR the new version is impacted).
2. Union both sides into `impacted_wares`.
3. For each impacted turret, emit one `RuleOutput` with `extras.subsource = 'bullet'`, stat deltas from `BULLET_STATS`.

A turret whose ware entry ALSO diffed in wares.xml gets its bullet row emitted once from the ware-modified path; the macro-only path skips it to avoid duplicates.

## Lifecycle

- `tags="deprecated"` toggles produce a `DEPRECATED` or `un-deprecated` marker at the head of the change list. 9.00 deprecated a large batch of legacy turret variants — same pattern as weapons.
- Pure add/remove ware entries emit `kind='added'` / `kind='removed'` rows.

## DLC handling

- Wares.xml is merged via `entity_diff.diff_library` — core + every `extensions/*/libraries/wares.xml` overlaid in alphabetical order, with `_classify_conflicts` catching cross-DLC write/write collisions.
- Macro/component files live under the same `assets/props/WeaponSystems/` subdir structure in every package. The rule consults `ref_sources` to know which package last wrote the `<component ref>` attribute on a ware; that package is tried first when resolving the macro, then core as fallback. If the attributed DLC directory isn't on disk, the resolver falls back to core (a warning is left as a TODO — real 9.00B6 data doesn't exhibit missing attributed DLCs for turrets).
- Bullet refs resolve via `src.lib.paths.resolve_macro_path(..., kind='bullet')`, which indexes every `assets/fx/weaponFx/macros/*.xml` across core + extensions.

## Output shape

Each `RuleOutput`:

```
tag:   "turrets"
text:  "[turrets] <name> (<classifications>) [<sources>]: <comma-joined changes>"
       "[turrets] <name> (<classifications>) [<sources>] [bullet]: <bullet changes>"  (subsource row)
extras: {
    entity_key:        <ware_id>,
    kind:              "added" | "removed" | "modified",
    subsource:         None | "bullet",
    classifications:   [subtype, tag_tokens..., maybe "guided"],
    old_source_files:  [...],  new_source_files: [...],
    old_sources:       [...],  new_sources:      [...],
    ref_sources:       {"component/@ref": <dlc_short>, ...},
}
```

Warnings and RULE-INCOMPLETE sentinels carry `extras.entity_key = ('diagnostic', 'turrets', <hash>)` and `kind in {'warning', 'incomplete'}`.

## What this rule does NOT cover

- Turret `<effects>` (visual/audio cosmetics); not diffed.
- Turret damage stats live on the bullet macro — surfaced via the bullet sub-source, not a turret field.
- Turret visibility/ownership restrictions (`<owner faction>`, `<restriction licence>`) on the ware; ware equality covers these but the rule doesn't break them out as separate labels.
- Missile macros that back missile-launcher turrets — diffed by the missiles rule. This rule emits the bullet fan-out row for the turret, but the downstream missile stats are the missiles rule's concern.
- The turret component XML beyond its connection `tags` — LOD/geometry/animation data are considered cosmetic.
