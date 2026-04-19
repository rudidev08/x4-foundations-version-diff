# X4 release-notes generator

Generates structured release notes for X4 Foundations by diffing two extracted
game versions (e.g. 8.00H4 → 9.00B6) with a set of focused rules — one per
kind of game content.

## What it produces

For each version pair, 20 rules scan the old and new game data and emit
`RuleOutput` records describing what changed. Each record has:

- `tag` — which rule produced it (`engines`, `weapons`, `ships`, …).
- `text` — a human-readable one-liner.
- `extras` — structured fields (entity key, classifications, source DLCs,
  added/removed attribute diffs, etc.) that a later stage can feed into an
  LLM to write themed release notes.

Example run on 8.00H4 → 9.00B6 (~28 seconds):

```
  engines          1 outputs  [incomplete=1]
  weapons        185 outputs  [added=6, modified=178, incomplete=1]
  turrets        224 outputs  [modified=223, incomplete=1]
  ships          202 outputs  [added=3, modified=194, removed=2, incomplete=3]
  ...
1775 total outputs across 20 rules.
```

## Running the pipeline

```bash
python3 scripts/run_rules.py 8.00H4 9.00B6
```

Expects the two versions as subdirectories of `x4-data/` (core files + any
`extensions/ego_dlc_*/` DLC trees). Writes one JSON file per rule to
`output/8.00H4_9.00B6/<rule>.json` plus a `summary.json` with counts.

Flags:

- `--only engines,weapons` — run a subset of rules.
- `--corpus path/to/versions` — override the default `x4-data/` location.
- `--out path/to/output` — override the default `output/` location.

## Repository layout

- `src/lib/` — shared machinery. The core piece is `entity_diff.py`:
  XPath subset evaluator, DLC patch-engine that replays `<diff>` ops, the
  `diff_library` function rules call, three-tier conflict classification
  (failure / warning / non-conflict), and contributor-set tracking that
  records which DLCs touched which entity.
- `src/rules/` — one module per rule. Each exports `run(old_root, new_root,
  changes=None) -> list[RuleOutput]`. Every rule also has a sibling `.md`
  documenting its data model and what it doesn't cover.
- `src/change_map.py` — builds the file-level change list the pipeline
  hands to rules that need it.
- `tests/` — per-rule unit tests against hand-crafted fixtures plus
  `tests/test_realdata_<rule>.py` that runs each rule against committed
  snapshots of real-game output at 8.00H4 → 9.00B6.
- `tests/snapshots/` — committed Tier B baselines that catch behavioral
  drift when rules change.
- `tests/realdata_allowlist.py` — reviewed list of known-benign cross-DLC
  conflicts (mostly closedloop if-gate collisions in real game data).
- `scripts/` — `run_rules.py` (end-to-end pipeline) and
  `inventory_xpath_ops.py` (one-shot tool to audit `<diff>` patch shapes
  in the corpus).
- `docs/` — the plan and spec that drove this build.
- `x4-data/` — extracted game versions. Not committed; drop your extracts here.

## The 20 rules

- **Ware-driven** (5): `engines`, `weapons`, `turrets`, `equipment`, `wares`.
- **Macro-driven** (3): `ships`, `storage`, `sectors`.
- **Library entity-diff** (8): `factions`, `stations`, `jobs`, `loadouts`,
  `gamestarts`, `unlocks`, `drops`, `cosmetics`.
- **File-level** (2): `quests`, `gamelogic`.
- **Pre-existing** (2): `shields`, `missiles`.

Ownership of wares across the ware-driven rules is enforced by
`src/rules/_wave1_common.owns(ware, tag)` — each ware belongs to exactly
one rule. Spacesuit gear, satellites, and personalupgrade-tagged items
route to `equipment` regardless of their `@group`.

## Running the tests

```bash
# Full suite against hand-crafted fixtures + canonical 8.00H4→9.00B6 pair:
python3 -m unittest discover tests/

# Validate against every consecutive 9.00 beta pair too (slower):
X4_REALDATA_FULL=1 python3 -m unittest discover tests/

# Regenerate a rule's snapshot after deliberate output changes:
X4_REGEN_SNAPSHOT=<rule> python3 -m unittest tests.test_realdata_<rule>
```

Current state: 455 tests pass across all 7 X4 versions (8.00H4 + 9.00B1 through
9.00B6) with 1 intentional skip.

## Adding a new rule

1. Pick a skeleton from `docs/superpowers/plans/2026-04-17-rule-buildout-implementation.md`
   (ware-driven / macro-driven / library entity-diff / file-level).
2. Create `src/rules/<name>.py` + `<name>.md`.
3. Create `tests/test_<name>.py` covering the 9 standard cases (added,
   removed, modified, lifecycle/deprecation, DLC-sourced, provenance handoff,
   incomplete sentinel, warning, unchanged).
4. Create `tests/fixtures/<name>/TEST-1.00/` and `TEST-2.00/` trees.
5. Create `tests/test_realdata_<name>.py` and seed the snapshot with
   `X4_REGEN_SNAPSHOT=<name> python3 -m unittest tests.test_realdata_<name>`.
6. Add `'<name>'` to the `RULES` list in `scripts/run_rules.py`.

If the rule claims wares (Wave 1 pattern), also extend `ware_owner` in
`src/rules/_wave1_common.py` so no other rule accidentally emits overlapping
rows.

## Conventions

- Tests use `unittest`.
- No commits happen from scripts or test helpers; regen and commit are
  human-driven.
- Real-data failures that are known-benign live in
  `tests/realdata_allowlist.py` with a written justification.
- Snapshots are authoritative: a behavior change requires an explicit regen.
