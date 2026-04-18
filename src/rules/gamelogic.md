# gamelogic rule

Audience: humans + LLM. Explains what the gamelogic rule sees and how it
interprets X4's aiscript, behaviour, and scriptproperty data.

## What the rule processes

Three sub-sources share the `gamelogic` tag, distinguished by
`extras.subsource`. They do not cross-reference; a single entity appears in
exactly one sub-source.

- **`aiscript`** — HYBRID file-level + patch-engine materialization.
  `aiscripts/*.xml` under core plus every `extensions/*/aiscripts/*.xml`
  sibling. DLC files are `<diff>` patches, not standalone scripts — a raw
  file-level diff would show patch-op XML instead of the script delta. The
  rule applies every DLC patch in alphabetical DLC order onto the core
  script, canonicalizes the effective XML, and diffs the two byte streams.
- **`behaviour`** — `<behaviour>` entries in `libraries/behaviours.xml`
  materialized via `diff_library`. Behaviours nest `<set name="...">/<normal|
  evade|...>/<behaviour>`; neither ancestor alone disambiguates, so the key
  is a composite tuple.
- **`scriptproperty`** — `<property>` entries in
  `libraries/scriptproperties.xml` materialized via `diff_library`.
  Properties nest directly under `<datatype name="...">`; the same
  `@name` can appear under multiple datatypes so the key is a composite.

## Data model

### Aiscript (file-level hybrid)

Pipeline per filename:

1. Materialize core `aiscripts/<filename>` (or empty if core-absent).
2. Apply every `extensions/*/aiscripts/<filename>` patch in alphabetical
   DLC order via `src.lib.entity_diff.apply_patch`. Accumulate failures +
   warnings into the parallel `_AiscriptReport`.
3. Serialize the effective tree via `src.lib.canonical_xml.canonical_bytes`
   to get deterministic UTF-8 bytes with sorted attributes and `ET.indent`
   whitespace.
4. Diff the old-effective vs new-effective canonical bytes through
   `src.lib.file_level.render_modified`.

The canonical-bytes serializer is required for snapshot stability across
Python versions — plain `ET.tostring` varies whitespace, attribute order,
and the XML declaration prefix enough to thrash the Tier B snapshot.

Key: `(subsource, filename_stem)`.

Display name: `@name` from the root `<aiscript>` element of the effective
script (or nested `<aiscript>` child when wrapped). Falls back to the
filename stem when neither is available.

Classifications: `[aiscript, <filename_prefix>]`. The filename prefix is the
substring before the first `.` or `_` (whichever comes first), mapped
through a fixed token set:

- `fight_*` → `"fight"`
- `interrupt_*` / `interrupt.*` → `"interrupt"`
- `build_*` → `"build"`
- `move_*` → `"move"`
- `order_*` / `order.*` → `"order"`
- `trade_*` → `"trade"`
- `plan_*` → `"plan"`
- `anon_*` → `"anon"`
- anything else → no prefix token (just `["aiscript"]`)

Extending the mapping is a conscious step — filenames that don't match stay
classified by only the `aiscript` token, which keeps classifications
deterministic even when a new script family lands in a later version.

Output lifecycle: added / removed / modified per filename (one row per
filename; DLC patches collapse into the parent file's single row).

### Behaviour (entity-diff)

```
libraries/behaviours.xml
  <behaviours>
    <set name="default">
      <normal>
        <behaviour name="dogfight1" chance="60" />
      </normal>
      <evade>
        <behaviour name="hardbrake" chance="5" />
      </evade>
    </set>
  </behaviours>
```

Key: `(subsource, (set_name, parent_collection_tag, behaviour_name))`.
`set_name` = nearest `<set @name>` ancestor; `parent_collection_tag` =
immediate parent tag (`normal`, `evade`, ...); `behaviour_name` =
`<behaviour @name>`.

Display name: `@name`.

Classifications: `[behaviour, <set_name>, <parent_collection_tag>]`.

Fields diffed:

- Full `<behaviour>` attribute set (minus `@name` which is part of the
  key).
- `<param>` children keyed by `@name`.
- `<precondition>` / `<script>` singleton children — attrs diffed in
  place.

**Child-tag whitelist** is `{param, precondition, script}`. Any direct
child with a different tag flags the row incomplete with reason
`unhandled_child_tag`; the rule refuses to silently recurse over unknown
structure. Extending the whitelist is a conscious change that extends
`_diff_behaviour_children` too.

Real 9.00B6 behaviours.xml has no children on any `<behaviour>` — the
whitelist + diff logic is present for structural robustness and to match
the spec.

### Scriptproperty (entity-diff)

```
libraries/scriptproperties.xml
  <scriptproperties>
    <datatype name="component">
      <property name="exists" result="..." type="boolean" />
      <property name="isclass.{$class}" result="..." type="boolean">
        <param name="class" type="class" />
      </property>
    </datatype>
    <datatype name="object">
      ...
    </datatype>
  </scriptproperties>
```

Key: `(subsource, (datatype_name, property_name))`.

Display name: `<datatype_name>.<property_name>` (e.g.,
`component.isclass.{$class}`).

Classifications: `[scriptproperty]`.

Extras carry `@result` in `extras.result` when present.

Fields diffed:

- Full `<property>` attribute set (minus `@name` which is part of the
  key).
- `<param>` children keyed by `@name` (parameterized properties like
  `isclass.{$class}` use `<param>` with names).
- `<example>` children as a multiset by canonical attribute signature
  (examples have no stable key).

**Child-tag whitelist** is `{param, example}`. Any direct child with a
different tag flags the row incomplete with reason `unhandled_child_tag`,
same policy as the behaviour sub-source.

Real 9.00B6 scriptproperties.xml has no children on any `<property>` — the
whitelist + diff logic is present for structural robustness and to match
the spec.

## Output

```
tag:   "gamelogic"
text:  "[gamelogic] <display> (<classifications>) [<sources>]: <changes>"
extras: {
    entity_key:      ("aiscript",       "<filename_stem>")
                   | ("behaviour",      (<set_name>, <parent_collection_tag>, <behaviour_name>))
                   | ("scriptproperty", (<datatype_name>, <property_name>))
    subsource:       "aiscript" | "behaviour" | "scriptproperty"
    kind:            "added" | "removed" | "modified"
    classifications: [...]
    filename:        "<filename.xml>"       # aiscript only
    path:            "aiscripts/<filename>" # aiscript only (file-level)
    diff:            "<unified_diff>"       # aiscript only (file-level)
    set_name:        "<name>"               # behaviour
    parent_collection_tag: "<tag>"          # behaviour
    behaviour_name:  "<name>"               # behaviour
    datatype_name:   "<name>"               # scriptproperty
    property_name:   "<name>"               # scriptproperty
    result:          "<result>"             # scriptproperty, when present
    old_sources / new_sources: as applicable
}
```

## Contamination bridge (composite keys)

`forward_incomplete`'s subsource-wide contamination path triggers only when
a failure has `affected_keys=[]`. `_infer_affected_keys` in the patch
engine extracts bare `@id='X'` / `@name='X'` strings from XPath selectors;
those bare strings never match the composite-tuple `entity_key`s this rule
emits for behaviour and scriptproperty. The `run()` bridge rewrites
bare-string `affected_keys` on the behaviour + scriptproperty reports to
`[]` before `forward_incomplete_many`, so patch failures in
`libraries/behaviours.xml` / `libraries/scriptproperties.xml` contaminate
every row within the affected subsource (conservative v1 fallback).

Aiscript failures already carry tuple `affected_keys` of the form
`[('aiscript', <filename_stem>)]` and match the aiscript `entity_key`
directly — no rewrite needed.

Rule-level failures (`unhandled_child_tag`) carry composite-tuple
`affected_keys` at emission time and match the per-entity `entity_key`
precisely; the specific row is flagged incomplete, siblings stay clean.

## DLC handling

- Behaviour + scriptproperty libraries materialize via `diff_library`,
  which applies `<diff>` ops across DLC extensions before entity keying.
- Aiscripts materialize manually via `apply_patch` in the rule — see the
  "Aiscript (file-level hybrid)" section above.
- Real 9.00B6 has no DLC files for `libraries/behaviours.xml` or
  `libraries/scriptproperties.xml`; the DLC path is fully exercised only
  by aiscripts (pirate DLC patches several scripts).

## Diagnostic channel

- Failures ride `forward_incomplete_many` with per-subsource scoping.
  Three kinds:
  1. **DLC-patch failures** — aiscript `apply_patch` errors, carrying
     tuple `affected_keys=[('aiscript', filename_stem)]`.
  2. **DLC-patch failures on library files** — from the underlying
     `diff_library` report on behaviours.xml / scriptproperties.xml;
     rewritten to `affected_keys=[]` via the contamination bridge.
  3. **Rule-level assertions** — `unhandled_child_tag` on behaviour /
     scriptproperty with composite-tuple `affected_keys` scoped to the
     specific entity.
- Warnings forwarded via `forward_warnings` (parse warnings, patch
  warnings on library files).

## What the rule does NOT cover

- Comment text inside aiscripts / library files (ElementTree discards
  comments before the rule sees them).
- Semantic meaning of aiscript opcodes — the rule surfaces raw XML
  deltas, not behavior-level interpretation.
- Cross-references between aiscripts (e.g., `<run_script script="X">`
  resolution).
- The `<textdb>` stanza at the top of scriptproperties.xml — it's locale
  metadata, not diffed here.
