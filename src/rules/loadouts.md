# loadouts rule

Audience: humans + LLM. Explains what the loadouts rule sees and how it
interprets X4's loadout + loadoutrules data.

## What the rule processes

Two sub-sources share the `loadouts` tag, distinguished by `extras.subsource`:

- **`loadout`** — `<loadout>` entries in `libraries/loadouts.xml` (core + DLC,
  materialized via `diff_library`). Each loadout attaches a specific equipment
  configuration (engine/shield/turret/weapon/ammunition/software/
  virtualmacros) to a target ship macro. Key is the loadout `@id`.
- **`rule`** — `<rule>` entries in `libraries/loadoutrules.xml`. Loadoutrules
  are anonymous — their `@id` (when present) is not stable across patches. The
  rule keys each `<rule>` by a composite applicability tuple derived from its
  position in the tree plus its matching attributes.

Locale page 20101 ("Ships") drives the display name for the `loadout`
sub-source. The rule sub-source uses a synthetic display name built from
the applicability tuple — there's no locale entry for loadoutrules.

## Data model

### Loadout entry

```
libraries/loadouts.xml
  <loadout id="arg_fighter_default" macro="ship_arg_m_fighter_01_macro">
    <macros>
      <engine  macro="engine_arg_m_travel_01_mk1_macro"  path="../con_engine_01" />
      <shield  macro="shield_arg_m_standard_01_mk1_macro" path="../con_shield_01" />
      <weapon  macro="weapon_arg_m_gun_01_mk1_macro"     path="../con_weapon_01" />
      <turret  macro="turret_arg_m_laser_01_mk1_macro"   path="../con_turret_01" />
    </macros>
    <software>
      <software ware="software_dockmk1" />
      <software ware="software_scannerobjectmk1" />
    </software>
    <virtualmacros>
      <thruster macro="thruster_gen_m_allround_01_mk1_macro" />
    </virtualmacros>
    <ammunition>
      <ammunition macro="eq_arg_satellite_01_macro" exact="5" />
    </ammunition>
  </loadout>
```

### Rule entry

```
libraries/loadoutrules.xml
  <rules>
    <unit>
      <rules>
        <ruleset type="default">
          <rule category="transport" mk="1" weight="20" classes="ship_l ship_xl"
                important="true" purposes="trade mine auxiliary" />
          <rule category="police"    mk="1" weight="5"  classes="buildmodule"
                factiontags="watchdoguser" />
        </ruleset>
      </rules>
    </unit>
    <deployable>
      ...
    </deployable>
  </rules>
```

## Composite applicability key

Rule rows are keyed by an 8-tuple:

```
(container, ruleset_type, category, mk,
 tuple(sorted(classes)),
 tuple(sorted(purposes)),
 tuple(sorted(factiontags)),
 tuple(sorted(cargotags)))
```

- `container` — `'unit'` or `'deployable'` (walked up from the rule element).
- `ruleset_type` — the enclosing `<ruleset @type>`.
- `category`, `mk` — attrs on the rule itself.
- The four tuples come from the rule's matcher attrs (`@classes`, etc.),
  space-split, sorted, dedup'd via `set → tuple(sorted(...))`.

**`tuple(sorted(set))`, NOT `frozenset`.** `repr(frozenset(...))` is
insertion-order-dependent in CPython, which breaks output determinism (rows
sort by `repr(entity_key)`).

## Multiset duplicate-applicability matching

Multiple rules can share the same composite applicability key — e.g. two
`police mk1 buildmodule watchdoguser` rules with different weights.
`diff_library`'s default `_index_by_key` is a plain dict, which would
overwrite the earlier rule with the later one. To preserve the multiset:

1. The rule sub-source calls `diff_library` with `key_fn=lambda e: id(e)` so
   every `<rule>` gets a unique key. We keep only the returned
   `effective_old_root` / `effective_new_root` — the added/removed/modified
   lists are discarded.
2. The rule's own bucketing pass groups `<rule>` elements by composite
   applicability key on each side.
3. Per bucket:
   - Empty-old → one `added` per new rule.
   - Empty-new → one `removed` per old rule.
   - len==1 on both sides → paired diff; emit `modified` iff signatures
     differ.
   - Multiset on either side → bag-diff signatures. Common signatures pair
     up (`min(count)` pairs, no output). Emit `added` per new-only sig and
     `removed` per old-only sig. **NO `modified` in the multiset path** —
     this prevents cascade false positives when rules shift within a bucket.

## Classifications

### Loadout sub-source

Fixed `['loadout']` — the equipment inventory is a property of one loadout,
not a class it belongs to. Macro-derived classifications (ship class, ship
type) are owned by the `ships` rule.

### Rule sub-source

`['rule', container, ruleset_type, category, mk<mk>, ...classes,
...purposes, ...factiontags]` with the generic filter stripping the literal
`'rule'` and `'loadout'` tokens. Cargo tags (`@cargotags`) are NOT included
in classifications — they live in refs to keep the classification list
short and rule-category focused.

## Fields diffed

### Loadout sub-source

- `ship_macro <old>→<new>` — the loadout's `@macro` attribute (target ship).
- `engine / shield / turret / weapon added={...} removed={...}` —
  bag-diff over `<macros>/<slot @macro @path>` children; `(macro, path)`
  tuple is the identity. Missing `@path` falls back to `#<index>` for
  per-child stability.
- `software added={...} removed={...}` —
  set-diff over `<software>/<software @ware>` children.
- `virtualmacros added={...} removed={...}` —
  bag-diff over all children of `<virtualmacros>` (`thruster:<macro>`,
  etc.).
- `ammunition <macro>:<old>→<new>, ...` —
  per-macro map diff over `<ammunition>/<ammunition @macro @exact>`.

### Rule sub-source

- **Unique-applicability (single-rule buckets)**: `<attr> <old>→<new>` for
  every non-applicability attr that differs. Typical attrs:
  `@weight`, `@important`, `@requiredocking`, `@requireundocking`.
- **Multiset buckets**: add/remove rows carry the new/removed rule's full
  non-applicability attr list as a compact `key=val` summary — no `→`
  arrow, because no pairing.

## Output

```
tag:   "loadouts"
text:  "[loadouts] <name> (<classifications>) [<sources>]: <changes>"
extras: {
    entity_key:      ("loadout", "<loadout_id>")
                   | ("rule", <composite_applicability_tuple>)
    subsource:       "loadout" | "rule"
    kind:            "added" | "removed" | "modified"
    classifications: [...]
    # loadout sub-source only:
    loadout_id:      "..."
    refs:            {"ship_macro": "..."}
    sources / old_sources / new_sources: per canonical schema (DLC provenance)
    # rule sub-source only:
    rule_signature:  (("attr", "val"),...)        # add/remove only
    multiset:        True | False                 # always present (False on the index path)
    refs:            {"applicability": {...}}
    # rule sub-source has no sources/*_sources keys today (see DLC handling
    # below); the source bracket on rule rows always renders as `[core]`.
}
```

## Refs

- **Loadout**: `refs={'ship_macro': <loadout @macro>}`. Lets downstream
  grouping connect a changed loadout to the ship it equips.
- **Rule**: `refs={'applicability': {container, ruleset_type, category, mk,
  classes, purposes, factiontags, cargotags}}`. A single composite object —
  not separate fields — to preserve AND semantics (a ship matches a rule
  iff it satisfies every non-empty axis) and empty-set wildcard semantics
  (empty list on an axis = unrestricted on that axis). A single-composite
  shape prevents downstream tools from guessing AND vs OR — they read the
  composite and apply intersection.

## DLC handling

- `Locale.build(root)` merges all `extensions/*/t/0001-l044.xml` on top of
  core; collisions surface as warnings via `forward_warnings`.
- Loadouts: DLC files are `<diff>`-wrapped; `diff_library` applies the
  patches. Contributor attribution populates `sources` / `ref_sources`
  per loadout id.
- Rules: DLC loadoutrules.xml files are also `<diff>`-wrapped. Since we
  re-index manually by composite key on the materialized effective tree,
  per-rule contributor info is NOT exposed on rule outputs today — the
  source label defaults to `['core']` on rule rows. (Future refinement:
  attribute each bucket to its contributor set by threading
  `_materialize`'s contribs map through the composite-key index.)

## What the rule does NOT cover

- Validation that a loadout's slot macros exist or are a valid match for
  the target ship — that's X4's own engine concern.
- Faction ownership of loadouts — the `<ownership>` element on some
  loadouts is not diffed; the LLM stage can reason about the ship macro
  + ruleset combination instead.
- Loadout-to-loadoutrule cross-references — X4's loadout selection logic
  is complex enough that we leave inference to the LLM summary stage.
