# gamestarts rule

Audience: humans + LLM. Explains what the gamestarts rule sees and how it
interprets X4's `libraries/gamestarts.xml` data.

## What the rule processes

- **Gamestart definitions** — every `<gamestart>` entry in core + DLC
  `libraries/gamestarts.xml`, keyed by `@id`. Materialized via
  `diff_library` so DLC `<diff>` ops apply first, then entity-level diff
  runs on the effective tree.
- **Locale** — `t/0001-l044.xml`, merged via `Locale.build(root)`. The
  `<gamestart>` element's `@name` attribute carries the `{page,id}` ref
  directly (most refs land on page 30178; `resolve_attr_ref` handles any
  page). Some gamestarts carry plain text in `@name` (e.g., the `test_*`
  internal entries with `TS* ... (Test)` labels) — plain text returns
  verbatim.

Single-source, single-tag. Output tag is `gamestarts`; no sub-sources.

## Classifications

Whitespace-split `@tags`, generic `gamestart` filtered.

Examples observed in real 9.00B6 data:

- `tutorial nosave` — tutorial scenarios that can't save.
- `nosave timelineshub` — timelines hub entry.
- `customeditor budget` — custom-editor gamestart with budget system.
- `stationdesigner nosave` — station-designer sandbox.
- Empty tags — the internal `test_*` / default entries.

Gamestarts without `@tags` classify empty. Generic-token filter:
`frozenset({'gamestart'})`.

## Fields diffed

### Gamestart attributes (no whitelist)

Every attribute on `<gamestart>` itself is diffed — `@id`, `@name`,
`@description`, `@image`, `@tags`, `@group`, and anything else that
shows up. The label is bare: `<attr> old→new`.

### Nested singleton children (strict set)

The rule diffs the **own attributes** of a small set of nested singleton
children, as `<child>.<attr> old→new`:

- `<cutscene>` — `@ref`, `@voice` (intro cutscene wiring).
- `<player>` — starting-character `@macro`, `@money`, `@name`, `@female`.
- `<universe>` — universe flags (`@ventures`, `@visitors`,
  `@onlineinventory`). Nested `<jobs>` / `<god>` / `<masstraffic>` toggles
  under `<universe>` are NOT surfaced — they're boolean enable knobs
  duplicated across every gamestart.

### Nested one-deeper: player/ship

`<player><ship>` is a singleton under `<player>` when present (some
gamestarts start the player disembarked with no ship). Its attributes
diff as `player.ship.<attr> old→new`. The loadout ref and inventory list
beneath `<ship>` are NOT surfaced by this rule.

### Lifecycle

- Added / removed gamestarts surface as `NEW` / `REMOVED`.
- Tag set changes surface through the raw `@tags` attr-level diff — no
  separate deprecation token.

## Output

```
tag:   "gamestarts"
text:  "[gamestarts] <display name> (<classifications>) [<sources>]: <changes>"
extras: {
    entity_key:      "<gamestart_id>"
    kind:            "added" | "removed" | "modified"
    classifications: [...]
    gamestart_id:    "<id>"
    sources / old_sources / new_sources: per canonical schema
    ref_sources:     { ... }
}
```

Source bracket follows `render_sources` — `[core]`, `[core+split]`, or
`[core→core+split]` when provenance shifts.

## DLC handling

- `Locale.build(root)` merges all `extensions/*/t/0001-l044.xml` on top of
  core; collisions surface as warnings forwarded by the rule.
- `diff_library` materializes `libraries/gamestarts.xml` with all DLC
  `<diff>` ops applied; contributor attribution populates `sources` /
  `ref_sources` per gamestart id.
- Cross-DLC conflict classification runs during materialization — real
  9.00 game data has boron+split+terran all replacing the same
  `x4ep1_gamestart_tutorial1` attributes (canonical write-write
  contention); that surfaces as the `RULE INCOMPLETE` sentinel with
  `affected_keys=['x4ep1_gamestart_tutorial1', ...]`. See
  `tests/realdata_allowlist.py` for the justification.

## What the rule does NOT cover

- `<info><item>` blocks — loading-screen attribute/value pairs, per-DLC
  overridden. Long tail, not a high-value changelog signal.
- `<location>` descendants — galaxy/sector/zone/station wiring. Tens of
  attrs per location, diff noise would dominate.
- `<player><inventory>` — starting ware list. Hundreds of entries.
- `<player><blueprints>` — starting blueprint list.
- `<universe><factions>` — per-faction relation overrides. DLC-heavy,
  rarely a changelog signal.
- `<budget>` / `<custom>` children — custom-gamestart editor data.
- `<type>`, `<extension>`, `<unlock>`, `<loadingscreen>`, `<intro>` —
  cosmetic metadata.
