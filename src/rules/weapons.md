# weapons rule

Audience: humans + LLM. Explains what the rule sees and how it interprets X4's weapon data.

## What the rule processes

- **Weapon wares** — `<ware group="weapons">` entries in `{core|extensions/*/}libraries/wares.xml`. This group includes mines (`weapon_gen_mine_*` + `tags="equipment mine"`).
- **Weapon macros** — `{pkg}/assets/props/WeaponSystems/<subtype>/macros/<ref>.xml` (core PascalCase; DLC is lowercase `weaponsystems/<subtype>/macros/`). `<subtype>` is the on-disk parent dir (`standard`, `energy`, `heavy`, `capital`, `boron`, `highpower`, `guided`, `mines`).
- **Bullet macros** — `{pkg}/assets/fx/weaponFx/macros/<bullet_class>.xml` (core `weaponFx`, DLC lowercase `weaponfx`). Resolved from the weapon macro's `<bullet @class>`.
- **Locale** — `t/0001-l044.xml`, page `20105` for weapon display names.

The rule is **ware-driven** (parity with missiles): it iterates the wares list rather than relying on a file-level change map. This surfaces lifecycle transitions (deprecation) that are line-level changes inside `libraries/wares.xml` and that a file-level change map cannot pinpoint.

## Classification

`[subtype, ...tag_tokens]`:

- **subtype** — the parent-of-`macros` dir the weapon macro is stored in. This is the definitive X4 subtype signal (ids are opaque). Discovered via `resolve_macro_path(kind='weapons')` and path slicing.
- **mine prefix** — if the macro `class="mine"` OR the ware id matches `^weapon_.*_mine_`, prepend `"mine"` to the classification list.
- **tag_tokens** — the `<ware tags="...">` tokens, minus:
  - the **generic set** `{weapon, component}` (present on every weapon connection),
  - `deprecated` (surfaced as a DEPRECATED headline, not a classification).

## Ware-level diff

Standard Wave 1 ware fields:

- `price/@min`, `price/@average`, `price/@max`
- `./@volume`
- `./@tags` (deprecation lifecycle, plus classification re-derivation)
- `<production>` entries keyed by `@method`, per `_wave1_common.diff_productions`.

## Macro-level diff

Fields under the weapon macro `<macro>/properties/`:

- `<bullet @class>` — the bullet macro ref. A change here is a different bullet; the bullet fan-out runs once per shared bullet, so a swap surfaces both the `bullet_class` change here AND zero bullet rows (since the refs differ between sides and don't intersect).
- `<heat @overheat/@coolrate/@cooldelay>` — heat management.
- `<rotationspeed @max>` — turn rate when mounted.
- `<hull @max>` — weapon hull HP.

## Bullet-macro diff (subsource `"bullet"`)

Bullets fan out 1:N — one bullet macro can back multiple weapon wares. The rule:

1. Scans both old and new **effective** trees (post-DLC-merge) to build `{weapon_macro_ref → [ware_ids]}`.
2. Resolves each weapon macro on disk and reads `<bullet @class>` to extend that into `{bullet_ref → [ware_ids]}` on each side.
3. Intersects the bullet-ref sets; for each bullet ref present on BOTH sides and whose macro stats differ, emits one `subsource='bullet'` row per **unioned** impacted ware (old ∪ new impact lists). This preserves the emit when a shared bullet changes and one of its consumer weapons has been removed — the removed weapon still gets a row showing the bullet delta.

Fields diffed on the bullet macro:

- `<ammunition @value>` — labeled `damage`.
- `<bullet @speed/@lifetime/@amount/@barrelamount/@timediff/@reload/@heat>`.

## Lifecycle

- `tags="deprecated"` transitions (in either direction) show as `DEPRECATED` / `un-deprecated` headlines on modified rows. 9.00 deprecated many mk1/mk2 launcher wares — this signal is how the rule surfaces them.

## Output shape

One `RuleOutput` per event:

```
tag:    "weapons"
text:   "[weapons] <name> (<classifications>) <sources>: <changes>"
extras: {
  entity_key:     "weapon_arg_m_standard_01_mk1"
  kind:           "added" | "removed" | "modified"
  subsource:      None or "bullet" (bullet fan-out rows)
  classifications: ["standard", "equipment"]
  old_sources:    ["core"] (modified/removed)
  new_sources:    ["core", "boron"] (modified/added)
  sources:        mirrors the "current" side
  ref_sources:    {"component/@ref": "boron", ...}
}
```

Main-row text: `[weapons] <name> (<classifications>) [<sources>]: <changes>`.
Bullet-row text: `[weapons] <weapon_name> (bullet) [<sources>]: damage 100→200, speed 500→600`.

## What the rule does NOT cover

- Weapon `<physics>` (mass, drag) — not player-surfaceable without interpretation.
- `<effects>` (visual/audio refs) on bullets — cosmetic.
- Ware-level `<owner faction>` changes — Wave 1 scope doesn't diff ownership.
- DLC-only locale pages beyond page 20105 — rule reads core + DLC `t/0001-l044.xml` via `Locale.build`, but only looks up names on page 20105.
- Turret macros (same dir structure, different classification rule — handled by the turrets rule).
