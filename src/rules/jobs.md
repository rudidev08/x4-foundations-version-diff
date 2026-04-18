# jobs rule

Audience: humans + LLM. Explains what the jobs rule sees and how it
interprets X4's NPC job data.

## What the rule processes

- **NPC jobs** — every `<job>` entry in core + DLC `libraries/jobs.xml`,
  keyed by `@id`. Materialized via `diff_library` so DLC `<diff>` ops
  apply first, then entity-level diff runs on the effective tree.
- **Locale** — `t/0001-l044.xml`, page `20204` (job display names), via
  `Locale.build(root)` so DLC overrides merge on top of core. The `<job>`
  element's `@name` attribute carries the `{page,id}` ref directly;
  `resolve_attr_ref(job, loc, attr='name', fallback=@id)` walks it.

Single-source, single-tag. Output tag is `jobs`; no sub-sources.

## Classifications

`[<category @faction>, ...<category @tags>, <category @size>]` with Nones
dropped.

- `@faction` — `argon`, `paranid`, `teladi`, `boron`, `split`, …
- `@tags` — bracketed or space-separated token list:
  `fighter`, `interceptor`, `mining`, `trader`, `police`, `capital`, …
- `@size` — `ship_s`, `ship_m`, `ship_l`, `ship_xl`.

Jobs without a `<category>` child classify empty. Generic-token filter:
`frozenset({'job'})`.

## Fields diffed

### Job attributes (no whitelist)

Every attribute on `<job>` itself is diffed — `@id`, `@name`,
`@startactive`, and anything else that shows up. The label is bare:
`<attr> old→new`.

### Direct-child diffing (strict enumeration)

The rule enumerates every direct-child tag of `<job>` as a SINGLETON (each
allowed at most once per job). Each singleton's own attributes diff as
`<child_tag>.<attr> old→new`:

Initial spec enumeration (bootstrap set for classification + lifecycle):

- `<category>` — `@faction`, `@tags`, `@size`, `@class` …
- `<environment>` — e.g. `@buildatshipyard`, `@forcemacromacro`.
- `<modifiers>` — e.g. `@commandeerable`, `@rebuild`, `@speedfactor`.
- `<ship>` — ship selection / loadout block root attrs.
- `<pilot>` — pilot assignment attrs.
- `<quota>` — `@galaxy`, `@maxgalaxy`, `@cluster`, `@sector`, `@zone`, …
- `<orders>` — orders block root attrs.
- `<startactive>` — rarely an element (usually the attr form); if the tag
  appears as a child, diff its attrs.
- `<location>` — `@class`, `@macro`, `@faction`, `@relation`, …

Real-data extensions (all verified singleton per job in the 8.00H4 and
9.00B6 corpora):

- `<basket>` — cargo-selection block for trader / miner jobs.
- `<subordinates>` — escort spec (`@size`, `@tags`).
- `<encounters>` — AI encounter rates.
- `<time>` — scheduling window.
- `<task>` — fleet-task hook.
- `<masstraffic>` — spawn density knob.
- `<expirationtime>` — TTL for transient job instances.
- `<order>` — rare dummy-job-only remnant (kept permissive rather than
  force-contaminate that job).

Any OTHER tag → incomplete for that job with reason
`unhandled_child_tag` (see diagnostic channel below).

### Lifecycle

- Added / removed jobs surface as `NEW` / `REMOVED`.
- When `@startactive="false"` flips on (v1 absent / not-false → v2
  `"false"`), the change list is **prepended** with
  `DEPRECATED (startactive=false)`. The inverse transition prepends
  `un-deprecated (startactive cleared)`. The job-attr diff still surfaces
  the raw `startactive None→false` — the lifecycle token is an explicit,
  searchable label on top.

## Output

```
tag:   "jobs"
text:  "[jobs] <display name> (<classifications>) [<sources>]: <changes>"
extras: {
    entity_key:      "<job_id>"
    kind:            "added" | "removed" | "modified"
    classifications: [...]
    job_id:          "<id>"
    sources / old_sources / new_sources: per canonical schema
    ref_sources:     { ... }
}
```

Source bracket follows `render_sources` — `[core]`, `[core+boron]`, or
`[core→core+boron]` when provenance shifts.

## DLC handling

- `Locale.build(root)` merges all `extensions/*/t/0001-l044.xml` on top of
  core; collisions surface as warnings forwarded by the rule.
- `diff_library` materializes `libraries/jobs.xml` with all DLC `<diff>`
  ops applied; contributor attribution populates `sources` /
  `ref_sources` per job id.
- Cross-DLC conflict classification runs during materialization —
  failures bubble up as the diagnostic `RULE INCOMPLETE` sentinel;
  warnings (e.g., `positional_overlap`) emit as `kind='warning'` rows.

## Diagnostic channel

Failures ride `forward_incomplete` via a `_MergedReport` that unions the
`diff_library` failures with rule-level failures. Three rule-level
reasons:

- `unhandled_child_tag` — a direct child of `<job>` falls outside
  `SINGLETON_CHILDREN`. `extras.tag` names the offending tag. One failure
  per distinct tag per job.
- `repeated_<tag>` — a singleton tag appears more than once on a single
  job. `extras.count` records the occurrence count.
- `<job>` itself-adds-or-removes — surfaced through the normal
  added/removed row, not as a rule-level failure.

The rule-level failures carry `affected_keys=[<job_id>]`, so
`forward_incomplete` marks ONLY that job's row as incomplete (not the
whole rule). Unaffected job rows stay complete.

## Strict enumeration contract

Adding a new singleton child requires extending `SINGLETON_CHILDREN` in
`src/rules/jobs.py` AND this document. The initial enumeration covers
every direct-child tag observed in X4 8.00H4 + 9.00B6 jobs.xml +
corresponding DLC overlays, each verified singleton per materialized
job. Future X4 releases that introduce a new direct child will trigger
`unhandled_child_tag` on affected jobs; decide whether to extend the
enumeration as a SINGLETON (attr-level diff only, no subtree recursion)
or design a richer matcher before adding.

## What the rule does NOT cover

- The top-level comment block at the head of jobs.xml (author notes on
  overextension concepts) — it's author metadata, not diffable signal.
- Deep subtree structure under `<orders>`, `<ship>`, `<modifiers>`, etc.
  The rule diffs ONLY the singleton child's own attrs, not its
  descendants. If a nested `<order order="...">` attribute changes and
  the singleton's root attrs are unchanged, nothing surfaces.
