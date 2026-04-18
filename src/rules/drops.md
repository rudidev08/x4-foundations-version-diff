# drops rule

Audience: humans + LLM. Explains what the drops rule sees and how it
interprets X4's `libraries/drops.xml` data.

## What the rule processes

Three independent sub-sources under one `drops` tag, distinguished by
`extras.subsource`. They do not cross-reference; a single entity shows
up in exactly one sub-source.

- **`ammo`** — `<ammo id="...">` entries in `libraries/drops.xml`
  (core + DLC, materialized via `diff_library`). Keyed by `@id`.
  Display name is the `@id`.
- **`wares`** — `<wares id="...">` entries in `libraries/drops.xml`.
  Keyed by `@id`. Display name is the `@id`.
- **`droplist`** — `<droplist id="...">` entries in
  `libraries/drops.xml`. Keyed by `@id`. Display name is the `@id`.

Generic-token filter across all three: `frozenset()` — no tokens are
stripped. The subsource label (`ammo`, `wares`, `droplist`) is the sole
classification.

## Data model

```
libraries/drops.xml
  <droplists>
    <!-- ammo sub-source -->
    <ammo id="basket_ammo_01" selection="random">
      <select weight="7" macro="missile_cluster_light_mk1_macro" min="2" max="4"/>
      <select weight="12" macro="missile_dumbfire_light_mk1_macro" min="3" max="6"/>
      ...
    </ammo>

    <!-- wares sub-source -->
    <wares id="basket_wares_common_01" selection="random">
      <select weight="7">
        <ware ware="inv_algaescrubber" min="1" max="3"/>
      </select>
      ...
    </wares>

    <!-- droplist sub-source -->
    <droplist id="drops_crystal_s_01">
      <drop macro="collectable_crystal_s_01_macro" min="5" max="10">
        <collectable>
          <wares>
            <ware ware="inv_crystal_01" min="1" max="1"/>
          </wares>
        </collectable>
      </drop>
      <drop macro="collectable_crystal_s_01_macro" min="1" max="1" chance="20">
        ...
      </drop>
    </droplist>
  </droplists>
```

**`<drop>` is nested under `<droplist>` and has NO `@id` of its own.**
It is NOT a top-level entity — it surfaces as part of the droplist
sub-source's per-drop multiset.

## Classifications

`[<subsource>]` — one of `["ammo"]`, `["wares"]`, or `["droplist"]`.
The generic filter is empty, so the subsource label is always emitted.

## Fields diffed — one shape per sub-source

Each kind uses a matcher shape tailored to its structure. A single
"recurse everything" diff is not appropriate because each sub-source has
different identity semantics.

### `ammo` — keyed by `@macro` on `<select>`

Every `<select>` under an `<ammo>` carries a `@macro` attribute that's
unique within the block. Children are indexed by `@macro`; add / remove /
attr change surface as:

- `select[macro=<M>] added (<weight=... min=... max=...>)`
- `select[macro=<M>] removed (was ...)`
- `select[macro=<M>] <attr> <old>→<new>` for `weight`, `min`, `max`.

### `wares` — multiset of `(weight, (ware, amount)+)` on `<select>`

Identity lives in the nested `<ware>` children, not on the select's own
attrs. Two `<select weight="5">` blocks in the same basket might carry
different ware payloads. The signature is:

```python
signature(select) = (
    select.get('weight'),
    tuple(sorted((w.get('ware'), w.get('amount')) for w in select.findall('ware'))),
)
```

Multiset semantics:
- Old-only sigs → `select removed (was weight=... wares=[...])`.
- New-only sigs → `select added (weight=... wares=[...])`.
- Common sigs pair up with no output.

**No "modified" output under multiset.** This prevents cascade false
positives when a select shifts within a basket (e.g., a select's weight
bumps 5→7; under a pairwise matcher that same select would report as
"modified" AND an unrelated select with weight 7 would report as
"removed, then re-added").

### `droplist` — multiset of `(drop.attrib, nested_ware_payload)` on `<drop>`

`<drop>` elements have no `@id`; they're identified by the full tuple of
their own attrs plus their nested ware payload:

```python
signature(drop) = (
    tuple(sorted(drop.attrib.items())),
    tuple(sorted(
        (w.get('ware'), w.get('amount'), w.get('chance'))
        for w in drop.findall('ware')
    )),
)
```

Ignoring the drop's own attrs would collapse distinct drops with
identical ware payloads into one multiset entry, losing changes (e.g., a
drop gaining a `@macro` while keeping the same ware payload).

Per the task spec, the "ware payload" projection uses
`drop.findall('ware')` — direct `<ware>` children only. In X4 real data
wares live under `<collectable>/<wares>/<ware>`, so the payload tuple is
usually empty `()` and drops match purely on their own attrs. Changes
nested deeper than direct children (e.g., a `<ware @ware>` swap inside
`<collectable>/<wares>`) still surface because a drop's attr-sig shifts
indirectly when the parent droplist's content is replaced wholesale by
DLC patches; for attribute-identity-only shifts on the same drop, use
the dedicated rule for that macro kind (wares sub-source) to inspect the
basket.

Multiset semantics match the `wares` sub-source: no "modified", old-only
→ removed, new-only → added.

## Output

```
tag:   "drops"
text:  "[drops] <id> (<classifications>) [<sources>]: <changes>"
extras: {
    entity_key:      ("ammo",     "<ammo_id>")
                   | ("wares",    "<wares_id>")
                   | ("droplist", "<droplist_id>")
    subsource:       "ammo" | "wares" | "droplist"
    kind:            "added" | "removed" | "modified"
    classifications: ["ammo"] | ["wares"] | ["droplist"]
    ammo_id / wares_id / droplist_id: "<id>"
    sources / old_sources / new_sources: per canonical schema
    ref_sources:     { ... }
}
```

## DLC handling

- All three sub-sources materialize `libraries/drops.xml` via
  `diff_library`, which applies `<diff>` ops across DLC extensions.
  Contributor attribution populates `sources` / `ref_sources` per entity.
- Warnings (DLC positional overlaps, cross-DLC RAW flags) emit
  `[drops] WARNING: ...` rows and do not contaminate normal outputs.

## Diagnostic channel

Failures ride `forward_incomplete_many` with per-subsource scoping via
`_PrefixedReport`, which rewrites `affected_keys` from raw ids to the
`(subsource, id)` tuple form that matches rule output `entity_key`s. An
ammo-side patch failure cannot contaminate wares or droplist rows and
vice versa.

## What the rule does NOT cover

- Deep-nested ware payloads inside drops (e.g., `<collectable>/<wares>/
  <ware>`) — the spec multiset signature uses direct `drop.findall('ware')`
  only. A drop's ware payload is captured as empty `()` when wares are
  nested under `<collectable>`; drops then match purely on their own
  attrs. This matches the spec's simplicity/cascade-prevention tradeoff.
- Cross-entity references between ammo baskets and droplists — baskets
  are pure data, X4's mission scripts reference them by id.
- The `xsi:noNamespaceSchemaLocation` and the top-level `<droplists>`
  wrapper comments — author metadata, not rule signal.
