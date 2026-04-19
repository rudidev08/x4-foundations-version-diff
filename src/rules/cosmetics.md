# cosmetics rule

Audience: humans + LLM. Explains what the cosmetics rule sees and how it
interprets X4's paintmod, adsign, and equipmod data.

## What the rule processes

Three USER-FACING sub-sources under one `cosmetics` tag, backed by
multiple internal labels for contamination scoping:

- **`paint`** — `<paint>` entries in `libraries/paintmods.xml`. Keyed by
  `@ware`. Internal label `paint`.
- **`adsign`** — `<adsign>` entries in `libraries/adsigns.xml`. TWO
  internal labels (`adsign_ware`, `adsign_waregroup`) because real X4
  data has both `<adsign ware="...">` AND `<adsign waregroup="...">`
  variants. Keying only on `@ware` silently drops every `@waregroup`
  row. Both internal labels emit with user-facing classification
  `['adsign']`.
- **`equipmod`** — leaf mod entries in `libraries/equipmentmods.xml`.
  The file's top-level children are FAMILY tags (`<weapon>`, `<shield>`,
  `<engine>`, `<ship>`, ...). The rule **discovers families at runtime**
  from the effective tree root, then builds one manual sub-report per
  family with internal label `equipmod_<family>`. Leaf mods are children
  of the family element with varying tag names (`<damage>`, `<cooling>`,
  `<speed>`, ...), so the rule re-indexes per family rather than calling
  `diff_library` per family (which needs one tag name per call).

## Data model

### Paint

```
libraries/paintmods.xml
  <paintmods>
    <paint ware="paintmod_0006" quality="1"
           hue="143" brightness="-0.8" saturation="-0.8" metal="0.2"/>
    ...
  </paintmods>
```

Fields diffed (`PAINT_ATTRS`): `quality`, `hue`, `brightness`,
`saturation`, `metal`, `smooth`, `dirt`, `extradirt`, `pattern`,
`scale`, `strength`, `sharpness`, `invert`, `red`, `green`, `blue`,
`alpha`, `personal`.

### Adsign

```
libraries/adsigns.xml
  <adsigns>
    <type ref="highway">
      <adsign ware="advancedcomposites" macro="props_adsigns_warez_01_macro"/>
      <adsign ware="energycells"        macro="props_adsigns_warez_energy_01_macro"/>
    </type>
    <type ref="station">
      <adsign ware="foodrations" macro="props_adsign_arg_foodrations_macro"/>
      <adsign waregroup="drones" macro="props_adsign_gen_advancedcomposites_macro"/>
      <adsign waregroup="shields" macro="props_adsign_gen_advancedcomposites_macro"/>
    </type>
  </adsigns>
```

Key is `(internal_label, type_ref, value)` where:

- `type_ref` is the enclosing `<type @ref>` string (`"highway"`,
  `"station"`, ...) — without it, two sibling `<type>` blocks with the
  same ware would collide.
- `value` is `@ware` (for `adsign_ware`) or `@waregroup`
  (for `adsign_waregroup`).

Fields diffed: `@macro`.

**Dual-attr assertion.** If an `<adsign>` carries BOTH `@ware` AND
`@waregroup` simultaneously, the rule emits a `WARNING` with reason
`'adsign_dual_attr'` (not an `incomplete` sentinel — the row itself is
still emitted under the `@ware` variant; the warning alerts a
maintainer that the source data has an ambiguity). "Ware wins" is the
conflict-resolution rule: the `@ware` variant claims the row; the
`@waregroup` variant skips it.

### Equipmod

```
libraries/equipmentmods.xml
  <equipmentmods>
    <weapon>
      <damage ware="mod_weapon_damage_01_mk1" quality="1" min="1.05" max="1.2"/>
      <damage ware="mod_weapon_damage_02_mk1" quality="1" min="1.35" max="1.45">
        <bonus chance="1.0" max="1">
          <cooling min="0.684" max="0.736"/>
        </bonus>
      </damage>
    </weapon>
    <shield>
      <capacity ware="mod_shield_capacity_01_mk1" quality="1" min="1.05" max="1.2"/>
    </shield>
    <engine>...</engine>
    <ship>...</ship>
    ...
  </equipmentmods>
```

- Families discovered in 9.00B6 (reference only, NOT hardcoded):
  `engine`, `shield`, `ship`, `weapon`. If a future version adds
  `scanner` / `armor` / `ammo`, the runtime-discovery path catches it
  automatically.
- Key: `(internal_label, family, ware, quality)`. `@quality` is part of
  the key because the same `@ware` can appear at multiple qualities in
  theory (today it's one-per-ware but keying by quality is cheap
  defensive shape).
- Fields diffed (leaf): `@min`, `@max`. The mod's parent tag name
  (e.g., `damage`, `cooling`) is the STAT the mod boosts — not a
  diffable field; it's captured in the family classification.
- **Bonus children**: each `<bonus>` wraps `@chance` + `@max` and
  contains one or more typed sub-children (tag name IS the bonus type
  per plan semantics). Bonus sub-tags are unique per leaf mod in real
  9.00B6 data, so the rule flattens them into `bonus[type=<tag>].*`
  dotted keys:
  - `bonus[type=cooling].chance`        — enclosing `<bonus @chance>`
  - `bonus[type=cooling].max_enclosing` — enclosing `<bonus @max>`
    (renamed from `max` to avoid colliding with the inner `@max`)
  - `bonus[type=cooling].min`           — inner typed `<cooling @min>`
  - `bonus[type=cooling].max`           — inner typed `<cooling @max>`
  - `bonus[type=cooling].value`         — inner typed `<cooling @value>`,
    or enclosing `<bonus @value>` when the inner doesn't carry one (the
    enclosing write happens first; the inner write overrides if present)
  - `bonus[type=cooling].weight`        — inner typed `<cooling @weight>`

  Changes surface as rows like
  `bonus[type=secondary].chance 0.1→0.15`.

No generic recursion — only the explicit bonus matcher. A future
unknown child tag inside a leaf mod would be silently ignored; that's
acceptable since `<bonus>` is the only structured child in real data.

## Classifications

- `paint`: `['paint']`.
- `adsign`: `['adsign']` (user-facing; internal label stays on
  `extras.subsource`).
- `equipmod`: `['equipmod', '<family>']` where `<family>` is
  `weapon`/`shield`/`engine`/... Generic filter: `frozenset()` —
  nothing stripped.

## Output

```
tag:   "cosmetics"
text:  "[cosmetics] <display> (<classifications>) [<sources>]: <changes>"
extras: {
    entity_key:      ("paint", "<ware>")
                   | ("adsign_ware",      "<type_ref>", "<ware>")
                   | ("adsign_waregroup", "<type_ref>", "<waregroup>")
                   | ("equipmod_<family>", "<family>", "<ware>", "<quality>")
    subsource:       "paint"
                   | "adsign_ware" | "adsign_waregroup"
                   | "equipmod_<family>"
    kind:            "added" | "removed" | "modified"
    classifications: [...]
    ware:            "<ware>"       (paint + equipmod)
    adsign_key:      "<value>"      (adsign)
    parent_type_ref: "<type_ref>"   (adsign)
    family:          "<family>"     (equipmod)
    old_sources / new_sources: as applicable
}
```

Adsign display: `"<type_ref>/<value>"` (or just `<value>` when no
enclosing type).

## Internal label stability contract (Tier B)

The following internal labels are frozen as part of the public
snapshot contract:

- `paint`
- `adsign_ware`, `adsign_waregroup`
- `equipmod_<family>` for every discovered family

Renaming any of them reshapes every snapshot row — the label is part of
the `entity_key` tuple. A refactor that renames must regenerate
`tests/snapshots/cosmetics_*.txt` alongside the code change; otherwise
the diff will look like a regression.

User-facing classification tokens (`paint`, `adsign`, `equipmod`, plus
each family name like `weapon`, `shield`, ...) are part of the same
contract.

## DLC handling

Each library is materialized via `diff_library`, which applies `<diff>`
ops across DLC extensions before entity keying. Real 9.00B6 has:

- `ego_dlc_split/libraries/adsigns.xml`
- `ego_dlc_terran/libraries/adsigns.xml`
- `ego_dlc_timelines/libraries/equipmentmods.xml`

Contributor attribution populates `sources`. Because adsigns lack `@id`
/ `@name` at the element level, `diff_library`'s default
contributor-attribution path can't tag individual rows — the rule
surfaces `'core'` as the sources fallback. Source granularity at the
adsign row level would require extending `_seed_sources_from_tree` to
recognize `<adsign>` explicitly; out of scope for this rule.

## Contamination scoping

Every report `(report, internal_label)` pair feeds
`forward_incomplete_many`. `extras.subsource == internal_label` on each
output lets the per-label scoping keep a single-file patch failure from
contaminating sibling sub-sources. For adsigns, both `adsign_ware` and
`adsign_waregroup` share the same file, so a patch failure contaminates
BOTH sub-reports (which is correct — the file as a whole is broken).

## Diagnostic channel

- Failures ride `forward_incomplete_many` with per-internal-label
  scoping.
- Warnings:
  - `adsign_dual_attr` — element carries both `@ware` and `@waregroup`.
    Row still emits (ware wins); warning alerts maintainer.
  - Any warning passed through from `diff_library` (e.g., positional
    overlap among DLCs) — forwarded unchanged via `forward_warnings`.

## What the rule does NOT cover

- Comment text in source XML (ElementTree discards comments before the
  rule sees them). For paintmods.xml the HSV-attrs comment block at the
  top documents the parameter space; that context is lost in the diff.
- Internal structure of macros referenced by `@macro` on adsigns. The
  rule surfaces the ref but doesn't chase it into the props macro
  tree.
- Sources attribution for adsign rows beyond `'core'` — see DLC
  handling section.
