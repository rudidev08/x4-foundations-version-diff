# shields rule

Audience: humans + LLM. Explains what the rule sees and how it interprets X4's shield data.

## What the rule processes

- **Shield macros** at `{core|extensions/*/}assets/props/surfaceelements/macros/shield_*_macro.xml` (case-insensitive — DLC sometimes lowercase, `ego_dlc_split` is PascalCase)
- **Component files** at `{.../}surfaceelements/{ref}.xml` — referenced by each macro for slot-type tags
- **Locale** at `t/0001-l044.xml` (English, page `20106` for shields) — resolves display names

Skipped:
- `*_video_macro.xml` (UI preview assets, not real shields)
- any path containing `tutorial` (combat-tutorial shields aren't player-relevant)

## X4 shield data model

Three files contribute to one shield:

```
libraries/wares.xml
  <ware id="shield_tel_m_standard_01_mk1" name="{20106,2024}" ...>
     <component ref="shield_tel_m_standard_01_mk1_macro" />

assets/props/SurfaceElements/macros/shield_tel_m_standard_01_mk1_macro.xml
  <macro class="shieldgenerator">
    <component ref="shield_gen_m_virtual_01" />      ← slot-type lives here
    <properties>
      <identification name="{20106,2024}" ... />
      <recharge max="6500" rate="72" delay="14.3" />  ← stats
      <hull max="800" integrated="1" />
    </properties>
  </macro>

assets/props/SurfaceElements/shield_gen_m_virtual_01.xml
  <component>
    <connections>
      <connection name="con_shield_01" tags="advanced component medium shield unhittable" />
```

The macro holds stats; the referenced component holds the **slot type** (via its connection tags). One component is typically shared across many macros.

## Slot type classification

Connection `tags` mix generic descriptors with slot-type tokens. The rule strips generics (`component`, `shield`, `small`, `medium`, `large`, `extralarge`, `hittable`, `unhittable`, `mandatory`) and picks a meaningful token in this priority order:

1. **`standard`** — Pre-9.00 default player slot. Still used by L/XL shields and legacy `_02` variants.
2. **`advanced`** — 9.00+ default player slot (see "slot rework" below).
3. **`*_racer`** (e.g. `small_racer`) — Racer-ship slot, Timelines DLC.
4. **`ship_*_*`, `*_mothership_*`, `*_battleship_*`** — Locked to a specific ship class (e.g. Envoy, Astrid yacht, Erlking, Xenon mothership, Terran Frontier).
5. **`khaak`, `xenon`** — NPC faction-only; not player-equippable.
6. **First remaining non-generic token** — fallback.

The classification is **lossy on purpose** — it surfaces the most meaningful label for the player. The raw component ref and macro name stay in `extras` for the LLM to reason about.

## DLC handling

Paths are matched case-insensitively. Component resolution tries **co-located directory first, then core**:

```
macro at extensions/ego_dlc_timelines/.../macros/shield_arg_s_racer_01_mk1_macro.xml
  → component lookup:
      1. extensions/ego_dlc_timelines/.../{ref}.xml  (preferred)
      2. assets/props/SurfaceElements/{ref}.xml       (core fallback)
```

The source label in output is derived from `extensions/ego_dlc_<name>/` → `<name>` (e.g. `[timelines]`, `[boron]`). Core shields get no source label.

## Display name collisions are real

Multiple distinct macros can resolve to the same display name. Examples:

- `shield_arg_m_standard_01_mk1_macro` and `shield_arg_m_standard_02_mk1_macro` both display as "ARG M Shield Generator Mk1" but are distinct shields with different components. In 9.00B6 the `_01` variant is `advanced`, the `_02` variant is still `standard`.
- `shield_par_s_standard_01_mk1` (core) and `shield_par_s_racer_01_mk1` (Timelines DLC) both display as "PAR S Shield Generator Mk1". Different stats, different slots.

The output text uses `(type) [source]` to disambiguate; `extras.macro` carries the unambiguous macro name for the LLM.

## 9.00 slot rework (real-data pattern)

Observed in 8.00H4 → 9.00B6:

- **All core M/S shield components** retagged `standard` → `advanced`. Ship shield slots were updated in parallel, so the "advanced"-tagged shields became the effective default for most ships.
- **L/XL shields were NOT retagged** — they stayed `standard`. The slot rework only touched S/M ship classes.
- Some shields show `type <old>→<new>` with no stat change (e.g., the combat-tutorial shield switched component refs without rebalancing). The rule surfaces this — the type transition is a real balance-relevant change.

LLM commentary should treat the systemic retag as a single headline ("shield slot system reworked for S/M ships in 9.00") rather than 50 per-shield stat reports. The per-shield rows provide the backing data.

## Output

Each rule output is a `RuleOutput`:

```
tag:   "shields"
text:  "[shields] TEL M Shield Generator Mk1 (advanced): HP 5662→6500, rate 25→72, delay 0.57→14.3, hull 500→800, type standard→advanced"
extras: {
    macro:    "shield_tel_m_standard_01_mk1_macro"
    name:     "TEL M Shield Generator Mk1"
    source:   "core" | "timelines" | "boron" | ...
    type_old: "standard" | None
    type_new: "advanced" | None
    changes:  ["HP 5662→6500", "rate 25→72", ...]   # stat diffs only, not type
}
```

Text format: `[shields] <name> (<type>) [<source>]: <comma-separated changes>`, with `(type)` omitted when None and `[source]` omitted when core.

## What the rule does NOT cover

- `<icon>` element additions in `wares.xml` (cosmetic metadata)
- Ware-level changes (prices, production recipes, faction ownership) — a separate concern, probably a future `wares` rule
- DLC-only locale pages — the rule reads only the core `t/0001-l044.xml`. DLC shields that reference DLC-only text IDs would fall back to the raw macro name. In practice none observed in 8.00H4 → 9.00B6.
- Additional shield properties beyond `recharge max/rate/delay` and `hull max` (e.g., `disruptionstability`) — extend the diff loop if new attributes become relevant.
