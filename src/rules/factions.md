# factions rule

Audience: humans + LLM. Explains what the factions rule sees and how it
interprets X4's faction and diplomacy data.

## What the rule processes

Two independent sub-sources under one `factions` tag, distinguished by
`extras.subsource`. They do not cross-reference; a single entity shows up
in exactly one sub-source.

- **`faction`** — `<faction>` entries in `libraries/factions.xml` (core +
  DLC, materialized via `diff_library`). Keyed by `@id`. Display name via
  `resolve_attr_ref(faction, locale, attr='name')`.
- **`action`** — `<action>` entries in `libraries/diplomacy.xml` (core +
  DLC, materialized via `diff_library`). Keyed by `@id`. Display name via
  `resolve_attr_ref(action, locale, attr='name')`.

Generic-token filter across both: `frozenset({'faction', 'action'})` — the
literal subsource-name tokens are stripped from classification rows so the
remaining tokens stay informative.

## Data model

### Faction

```
libraries/factions.xml
  <faction id="argon" name="{20203,201}" ...
           primaryrace="argon" behaviourset="default" policefaction="argon">
    <licences>
      <licence type="generaluseship" name="{20207,2321}" minrelation="-0.01" />
      <licence type="capitalship"    name="{20207,3111}" minrelation="0.1" />
    </licences>
    <relations>
      <relation faction="xenon" relation="-1" />
      <relation faction="khaak" relation="-1" />
    </relations>
  </faction>
```

### Action

```
libraries/diplomacy.xml
  <action id="improve_relations_low" category="negotiation" name="{20235,10201}">
    <agent experience="0" type="negotiation" risk="none" />
    <cost influence="1" money="250000" />
    <reward text="{20235,10231}" />
    <time duration="1800" cooldown="900" />
    <icon active="diplomacy_negotiation" image="..." />
    <success chance="100" text="{20235,10221}" />
    <params>
      <param name="station" text="{20235,111}" type="object" ...>
        <input_param name="isfactionhq" value="true" />
      </param>
    </params>
  </action>
```

## Classifications

### Faction sub-source

`['faction', @primaryrace, @behaviourset]` with `faction` stripped by the
generic filter. Missing attrs are omitted. Example: `['argon', 'default']`.

### Action sub-source

`['action', @category]` with `action` stripped. Example: `['negotiation']`.

## Fields diffed

### Faction

- Top-level attrs: `@behaviourset`, `@primaryrace`, `@policefaction`.
- `<licences>/<licence>` entries **keyed by composite `(@type, @factions)`**.
  Spec originally called for keying by `@type` alone, but real X4 data has
  multiple licences per faction with the same `@type` differentiated by
  `@factions` (the whitelist of recipient factions — e.g., `type=
  "capitalequipment"` once for the owning faction and once for a list of
  allied factions). The composite key is unique across every licences
  block in both 8.00H4 and 9.00B6; single-key `@type` would silently
  mispair. Each licence contributes one label per changed attribute. Add
  and remove surface as `licence[type=<T>] added|removed` (or
  `licence[type=<T>,factions=<F>] ...` when `@factions` is set).
- `<relations>/<relation>` default-relation entries **keyed by `@faction`**.
  Add/remove/change surface as `relation[faction=<F>] ...`.

**Parse-time uniqueness assertion.** Within a single faction's
`<licences>` block, the `(type, factions)` composite must be unique. If
duplicates appear the faction row is flagged incomplete with reason
`licence_type_not_unique`; the diff still produces a best-effort line for
visibility.

### Action

Actions are diffed as a full subtree via an explicit per-child matcher
table — there is no generic recursion fallback. The enumerated children:

- **`<cost>` / `<reward>`** — singleton root. Root attrs diff directly.
  Nested `<ware ware="...">` entries key by `@ware` (their own attrs
  diff per entry). Nested elements without a `@ware` attribute fall into a
  multiset keyed by canonical attribute signature (sorted
  `(name, value)` tuples).
- **`<params>/<param>`** — keyed by `@name`. Each param's attrs diff
  directly; nested `<input_param>` children key by their own `@name`.
- **`<time>`, `<icon>`, `<success>`, `<failure>`, `<agent>`** — singleton
  children; attrs diffed in place.
- **Any other direct child tag** → incomplete with reason
  `no_child_matcher`. The rule refuses to silently recurse over unknown
  structure; the `subtree` extras field names the offending tag.

**Parse-time uniqueness assertions.** Within a single `<params>` block,
`@name` must be unique across `<param>` children. Within a single
`<param>`, `@name` must be unique across `<input_param>` children. Both
violations flag the action row incomplete with reason
`param_name_not_unique`.

### Cross-version assumption

Param `@name` is assumed stable for the same semantic role across
versions. If a rename changes `@name` without other changes, the diff
shows remove+add — the honest signal. Rename tracking is not implemented.

## Output

```
tag:   "factions"
text:  "[factions] <display name> (<classifications>) [<sources>]: <changes>"
extras: {
    entity_key:      ("faction", "<faction_id>")
                   | ("action",  "<action_id>")
    subsource:       "faction" | "action"
    kind:            "added" | "removed" | "modified"
    classifications: [...]
    faction_id:      "<id>"   # faction sub-source
    action_id:       "<id>"   # action sub-source
    sources / old_sources / new_sources: per canonical schema
    ref_sources:     { ... }
}
```

## DLC handling

- `Locale.build(root)` merges all `extensions/*/t/0001-l044.xml` on top
  of core; collisions surface as warnings.
- Both sub-sources materialize their library file via `diff_library`,
  which applies `<diff>` ops across DLC extensions. Contributor
  attribution populates `sources` / `ref_sources` per entity.

## Diagnostic channel

Failures ride `forward_incomplete_many` with per-subsource scoping. Two
kinds:

1. **DLC-patch failures** — routed through `_MergedReport`, which
   prefixes `affected_keys` from the underlying DiffReport with the
   subsource tag so the `entity_key=(subsource, id)` form matches.
2. **Rule-level assertions** — `licence_type_not_unique`,
   `param_name_not_unique`, `no_child_matcher`. Each is pre-tagged with
   `(subsource, id)` affected_keys at emission time.

Warnings (locale collisions, DLC positional overlaps) emit
`[factions] WARNING: ...` rows and do not contaminate normal outputs.

## What the rule does NOT cover

- `<signals>` / `<response>` structure at the top of factions.xml — the
  rule filters the entity xpath to `.//faction`, so changes inside
  `<signals>` only surface indirectly via any licence/relation edit on a
  faction.
- Individual `<response>` weights / relations inside signals. Would need
  its own sub-source if it ever matters for release notes.
- The leading comment block at the top of factions.xml (default relation
  ranges) — it's author metadata, not data the rule should report on.
