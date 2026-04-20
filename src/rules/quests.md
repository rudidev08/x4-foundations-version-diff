# quests rule

Audience: humans + LLM. Explains what the quests rule sees and how it
interprets X4's `md/*.xml` mdscript changes at file granularity.

## What the rule processes

A single file-level sub-source under the `quests` tag: every XML file under
the core `md/` directory and under each DLC's `extensions/*/md/` directory.

Globs:
- `md/*.xml`
- `extensions/*/md/*.xml`

Scanning uses `src.lib.file_level.diff_files`, which returns one entry per
`(rel_path, ChangeKind, old_bytes, new_bytes)` tuple across added, removed,
and modified files. Unchanged files do not appear.

## Identity: one row per rel path

X4's `md/` tree is **additive** — a DLC cannot override a core mdscript by
filename the way `libraries/*.xml` entries can be replaced. `md/foo.xml` and
`extensions/ego_dlc_boron/md/foo.xml` are two separate mdscripts that both
load at runtime.

The rule honors that: each unique rel path is a distinct entity. The same
filename appearing in core and in a DLC surfaces as TWO rows, each with its
own `extras.sources` / `extras.source_files`. If a future DLC ships a same-
named replacement, it still surfaces as two rows — the honest view.

## Classifications

Derived from the filename (minus `.xml`):

1. If the whole stem matches a literal entry, use it.
2. Otherwise, the prefix is the chars before the first `_` (or the whole
   stem if there's no underscore).
3. Unknown prefix → empty list. This is explicit; no `["unknown"]` fallback.
   Empty keeps outputs stable as new mdscript prefixes appear.

Literal stem mapping:

- `notifications` → `notification`

Prefix mapping:

- `gm_*` → `generic_mission`
- `story_*` → `story`
- `factionlogic_*` → `factionlogic`
- `scenario_*` → `scenario`
- `gs_*` → `gamestart`
- `trade_*` → `trade`

Generic-token filter: `frozenset()` — every mapped token survives.

## Display name

Parsed from the XML root: if `<mdscript name="...">` is the document root,
`@name` is used. Parse errors (non-UTF-8 bytes, malformed XML) fall back to
the filename stem. This keeps one bad file from crashing the rule while
still surfacing its change.

## Render contract

All three lifecycle kinds route through `render_modified(rel, old, new,
tag='quests', name=...)` with empty bytes on the absent side:

- **MODIFIED**: `render_modified(rel, old, new)` — unified diff of old vs
  new; summary is `modified (+A/-B lines)`.
- **ADDED**: `render_modified(rel, b'', new)` — unified diff shows the whole
  file with `+` prefixes; summary text is swapped to
  `[quests] <name>: ADDED (+A lines)`.
- **REMOVED**: `render_modified(rel, old, b'')` — unified diff shows the
  whole file with `-` prefixes; summary text is swapped to
  `[quests] <name>: REMOVED (-B lines)`.

Truncation: `render_modified` caps the diff at 100 KB / 5000 lines, slicing
at line boundaries on decoded text so multibyte-UTF-8 codepoints are
preserved. `extras.diff_truncated=True` flags the cap.

Stability: `render_modified` is a pure function of its byte inputs;
repeated calls on the same inputs produce identical output (load-bearing
for output determinism).

## Output

```
tag:   "quests"
text:  "[quests] <name>: <summary>"
       where summary is "ADDED (+A lines)" | "REMOVED (-B lines)"
                      | "modified (+A/-B lines)"
extras: {
    entity_key:      "<rel_path>"
    kind:            "added" | "removed" | "modified"
    sources:         ["core"] | ["<dlc_short>"]
    source_files:    ["<rel_path>"]
    classifications: [<token>] | []
    path:            "<rel_path>"
    diff:            "<unified diff, possibly truncated>"
    added_lines:     <int>
    removed_lines:   <int>
    total_added_lines:   <same as added_lines>
    total_removed_lines: <same as removed_lines>
    diff_truncated:  <bool>
}
```

## DLC handling

Each DLC `md/` directory is scanned as-is; no merge. DLC paths populate
`extras.sources` with the DLC short name (via `src.lib.paths.source_of`),
core paths with `"core"`. A same-named file in core and a DLC produces two
independent rows — see the identity section above.

## What the rule does NOT cover

- **Lifecycle state inside cues.** The `<cue>` / `<library>` / `<action>`
  structure inside an mdscript is opaque to this rule. The unified diff
  shows the raw text change; semantic understanding of what a cue does is
  out of scope.
- **Cross-mdscript references.** `signal_cue` / `find_cue` references that
  point at other files are not resolved or correlated.
- **Lifecycle sub-case for file-level** (plan case 4). Files don't have a
  "lifecycle" notion beyond add/remove/modify, which are already covered.
- **Moved files.** A rename (same content at a different path) surfaces as
  one ADDED and one REMOVED row. That's the honest view; no rename detection.
- **Localized `@name` strings.** `<mdscript @name>` is a plain identifier,
  not a locale reference, so no locale lookup is needed.
