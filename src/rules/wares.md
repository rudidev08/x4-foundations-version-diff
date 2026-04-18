# wares rule

Audience: humans + LLM. Explains what the wares (non-equipment) rule sees and
how it interprets X4's ware data.

## What the rule processes

- **Non-equipment wares** — every `<ware>` entry in core + DLC
  `libraries/wares.xml` where `ware_owner(ware) == 'wares'`. This is the
  **residual bucket** after Wave 1's other rules (ships, shields/missiles,
  engines/weapons/turrets, equipment with its spacesuit + personalupgrade +
  satellite + software/hardware/countermeasures branches) have claimed their
  wares via the shared ownership predicate.
- **Locale** — `t/0001-l044.xml`, page `20201` (economy ware names), via
  `Locale.build(root)` so DLC overrides merge on top of core. Real 9.00B6
  wares carry embedded `@name="{20201,...}"` refs; `resolve_attr_ref` walks
  them directly — no heuristic page dispatch needed.

The rule is **ware-driven via `diff_library`** — it materializes the full
`libraries/wares.xml` effective tree (core + DLC `<diff>` ops applied), then
runs entity-level diffing keyed by ware id. Same shape as engines / weapons /
turrets / equipment / missiles, minus the macro resolution: this rule is
**pure ware-level**. Wares here reference generic `<container>` pickup macros
that no rule diffs.

## Classifications

Ware → `[@group, ...tag_tokens]`.

- `@group`: `minerals`, `refined`, `agricultural`, `food`, `pharmaceutical`,
  `energy`, `hightech`, `shiptech`, `stationbuilding`, `inventory`, …
- tag tokens: `economy`, `container`, `solid`, `liquid`, `minable`,
  `mineral`, `stationbuilding`, …

Generic-token filter: `frozenset({'ware'})`. Also excluded from
classifications: `deprecated` (lifecycle, not a descriptor).

Wares without `@group` still classify on remaining tag tokens (e.g.,
`inv_digitalseminar_boarding` group-less but tagged `inventory gift`).

## Ware fields diffed

Walked over `WARE_STATS` (mixes `price/@*` with ware-root attributes):

- `price_min` — `price/@min`
- `price_avg` — `price/@average`
- `price_max` — `price/@max`
- `volume` — ware root `@volume`
- `transport` — ware root `@transport` (container/solid/liquid/inventory/ship)

Production entries diffed via the shared Wave 1 `diff_productions` helper,
keyed by `production/@method`. Labels:

- `production[method=<M>] added` / `removed`
- `production[method=<M>] time <ov>→<nv>` or `amount <ov>→<nv>`
- `production[method=<M>] primary.<ware_id> <oa>→<na>` / `added` / `removed`

Multiple production methods on one ware (e.g., `default` + `teladi`) each
diff independently. A method appearing only in one version produces an
`added`/`removed` label; methods on both sides diff their fields and
recipe lists.

## Owner factions

`<owner @faction>` elements diffed as **sets**. When old and new differ,
emit one label:

```
owner_factions added={argon,paranid} removed={xenon}
```

Only `added`, only `removed`, or both — whichever non-empty. No change →
no label.

## Tags

Tag-set changes (excluding `deprecated`, which routes to the lifecycle
toggle) render as:

```
tags added={minable} removed={}
```

## Lifecycle

Deprecation toggle on the `@tags` attribute. When `deprecated` appears in
the new version but not the old, the change list is **prepended** with
`DEPRECATED`; inverse transition prepends `un-deprecated`. Added wares
carrying `deprecated` in the new version emit `already deprecated on
release`.

## Subtree-diff child matching

- `<production>` — **keyed** by `@method`.
- `<primary><ware>` recipe entries — **keyed** by `@ware` (ware-id under
  primary is stable).
- `<owner>` — **multiset** (adds/removes as a set; no natural per-owner key
  since the only attribute IS `@faction`).

## Output

```
tag:   "wares"
text:  "[wares] Silicon (minerals, economy, minable, mineral, solid) [core]: price_max 150→160"
extras: {
    entity_key:       "silicon"
    kind:             "added" | "removed" | "modified"
    classifications:  ["minerals", "economy", "minable", "mineral", "solid"]
    sources:          ["core"]              # added/removed
    old_sources:      ["core"]              # modified/removed
    new_sources:      ["core", "boron"]     # modified/added; shows DLC provenance
    ref_sources:      {}                    # wares rule has no macro refs to attribute
}
```

Text format: `[wares] <name> (<group>, <tags>) <sources>: <comma-separated changes>`.
Source bracket follows `render_sources` — `[core]`, `[core+boron]`, or
`[core→core+boron]` when provenance shifts.

## DLC handling

- `Locale.build(root)` merges all `extensions/*/t/0001-l044.xml` on top of
  core; collisions surface as warnings forwarded by the rule.
- `diff_library` materializes `libraries/wares.xml` with all DLC `<diff>`
  ops applied; contributor attribution populates `sources` / `ref_sources`
  per entity.
- Cross-DLC conflict classification runs during materialization — failures
  bubble up as an `incomplete` sentinel; warnings (e.g., `positional_overlap`)
  emit as a `kind='warning'` RuleOutput.

## What the rule does NOT cover

- `<restriction licence="...">` — ship/equipment licencing. Empty for most
  economy wares; not player-surfaceable as a changelog signal.
- `<use threshold="...">` — per-faction AI policy knob.
- `<icon>` and `<container ref>` — cosmetic / pickup-macro references.
- `<effects>` under production — station work-product effects; not a single
  scalar the LLM can summarize usefully, and duplicated per method makes
  diffs noisy.
- Macros: this rule is ware-level only. `<container ref>` points at generic
  `sm_gen_*` pickup macros that no Wave 1 rule diffs (container geometry
  is not a player-surfaceable attribute).
