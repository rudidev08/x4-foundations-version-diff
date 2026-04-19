# storage rule

Audience: humans + LLM. Explains what the rule sees and how it interprets X4 storage-module data.

## What the rule processes

- **Storage macros** at `{core|extensions/*/}assets/props/StorageModules/macros/storage_*_macro.xml` (case-insensitive — DLC packages sometimes lowercase the directory as `storagemodules/`).
- **Ship macros** under `{core|extensions/*/}assets/units/**/ship_*_macro.xml` — scanned once per (side, tree_root) to build the parent-ship reverse index.

Unlike other macro rules there is NO ware entry and NO per-macro locale lookup. The storage macro's `@name` attribute is the rule's stable identifier.

## X4 storage data model

One macro defines one storage module:

```
assets/props/StorageModules/macros/storage_arg_s_trans_container_01_b_macro.xml
  <macro name="storage_arg_s_trans_container_01_b_macro" class="storage">
    <component ref="generic_storage" />
    <properties>
      <cargo max="2352" tags="container" />
      <hull integrated="1" />
    </properties>
```

Ships attach storage via nested connection refs (not `<connection @ref>`):

```
assets/units/size_s/macros/ship_arg_s_fighter_01_a_macro.xml
  <macro name="ship_arg_s_fighter_01_a_macro" class="ship_s">
    <connections>
      <connection ref="con_storage01">                         ← connection anchor
        <macro ref="storage_arg_s_fighter_01_a_macro" ... />   ← storage reference
```

The rule reads the inner `<macro @ref>`, NOT the outer `<connection @ref>`.

## Fields diffed

On the macro (under `<properties>`):

- `<cargo @max>`
- `<cargo @tags>`
- `<hull @integrated>`

`cargo_tags` is diffed as a raw string — a ware-type change (e.g. `solid` → `solid container`) emits a `cargo_tags old→new` label. Classifications are recomputed from the new side's tags.

## Classifications

`[*<cargo @tags>]` — whitespace-split, no generic filter. Typical values:

- `['container']` — manufactured goods, ship parts.
- `['liquid']` — hydrogen, helium, water.
- `['solid']` — ore, silicon, ice.
- Multi-tag macros (rare) preserve every token in declaration order.

Missing or empty `@tags` yields an empty classifications list.

## Parent-ship reverse index

Ship macros reference storage via nested `<connections>/<connection>/<macro ref="storage_..."/>`. The rule builds a full reverse index `{storage_ref: [ship_name, ...]}` across ALL ship macros on the relevant side (old for removed, new for added/modified):

- 0 parents — both `parent_ship` and `parent_ships` are absent from `extras`.
- 1 parent — `extras.parent_ship = <ship_macro_display_name>`.
- 2+ parents — `extras.parent_ships = [<names>...]` (alphabetical) and the singular form is omitted.

The index is cached per `('storage_parent_ship_index', side, resolved_tree_root)` via `src.lib.cache`, so a single run indexes each tree once even when many storage macros change. Tests and real-data runs must call `cache.clear()` between pairs to avoid stale entries.

**Why index ALL ships, not just change-set ships:** unchanged ships can still be the real parents of a changed storage module. Scanning only ship macros in the file-change set misses real parents and produces wrong singular/plural hints on real pairs.

## Lifecycle

- File add — emits `kind='added'`, `NEW` in text.
- File remove — emits `kind='removed'`, `REMOVED` in text.
- File modified — emits `kind='modified'` when any of the three attrs diff.
- Otherwise no output (whitespace-only changes that don't affect the diffed attrs are silently skipped — same shape as every macro-driven rule).

## DLC handling

- Paths are matched case-insensitively against both `StorageModules/` and `storagemodules/`; the `_GLOBS` list enumerates all four core/extension × pascal/lowercase combinations.
- The source label is derived from `extensions/ego_dlc_<name>/` → `<name>` (e.g. `[boron]`). Core storage macros render `[core]` via `render_sources`.
- A storage macro added under a DLC is emitted `kind='added'` with its DLC source — mirror of the shields add-under-DLC shape.

## Output shape

Each `RuleOutput`:

```
tag:   "storage"
text:  "[storage] <macro_name> (<classifications>) [<sources>]: <changes>"
extras: {
    entity_key:      <macro_name>,
    macro:           <macro_name>,
    kind:            "added" | "removed" | "modified",
    classifications: [<cargo_tag>, ...],
    source:          "core" | "boron" | "split" | ...,
    parent_ship:     <ship_name>,         # only when exactly 1 parent
    parent_ships:    [<ship_name>, ...],  # only when 2+ parents (alphabetical)
}
```

## What this rule does NOT cover

- `<identification @makerrace>` and other cosmetic fields on the macro.
- The referenced `<component ref>` XML (every core storage macro uses `generic_storage`; the component is shared and opaque to gameplay).
- Storage references from non-ship entities (stations, platforms) — ships are the scoped relationship; stations use a different connection shape not covered by the parent-ship hint.
- Wares, prices, production. Storage modules are equipped directly on ships, not bought as wares.
