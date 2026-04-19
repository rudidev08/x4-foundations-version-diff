# X4 release-notes generator

Turns the raw game-data diff between two X4 Foundations versions
(e.g. 8.00H4 → 9.00B6) into player-facing release notes, via a two-stage
pipeline: focused rules extract structured changes, then an LLM stage
writes them up in plain English.

## Quick start

```bash
./run.sh 8.00H4 9.00B6
```

That's the whole thing. Takes a pair of extracted game versions under
`x4-data/`, produces `artifacts/8.00H4_9.00B6/RELEASE_NOTES_<tag>.md` as
the final output. The `<tag>` comes from the active LLM profile in
`.env` (or the `--reasoning` flag if no profile is set).

The run is **resumable**: every chunk file and aggregated file is
written only after its LLM call succeeds. If an LLM call fails, the
pipeline stops immediately and prints the error. Rerun the same
command and it picks up from where it stopped — completed chunks
aren't redone.

More examples:

```bash
./run.sh 9.00B5 9.00B6                       # canonical pair with .env defaults
./run.sh 8.00H4 9.00B6 --model haiku         # override active LLM profile
./run.sh 8.00H4 9.00B6 --reasoning medium    # Codex fallback if no .env profile
./run.sh 8.00H4 9.00B6 --max-tokens 12000    # shrink per-call budget for weaker LLMs
```

## How it works

Three stages run under the hood:

1. **Rule pass.** 20 rules walk the two game-data trees and emit
   structured change records to `artifacts/<old>_<new>/<rule>.json`.
   Each record has a rule tag, a one-line text, and `extras` with
   fields like entity keys, classifications, source DLCs, attribute
   diffs. ~30 seconds for the canonical pair.

2. **LLM per-rule pass.** Each non-empty rule JSON is turned into one
   or more markdown files by `scripts/release_notes_llm.py`. Large
   rules (quests, gamelogic, weapons, etc.) get split into size-limited
   chunks; each chunk is one LLM call. Outputs:
   `artifacts/<old>_<new>/llm_<rule>_<tag>.md` or
   `...llm_<rule>_chunk<N>of<M>_<tag>.md`.

3. **Aggregation.** `scripts/aggregate_release_notes.py` runs a
   tree-reduce merge: multi-chunk rules get collapsed into one
   `llm_<rule>_aggregated_<tag>.md`, then all 15+ per-rule summaries
   get combined into the top-level `RELEASE_NOTES_<tag>.md`. The
   tree-reduce is size-aware — if too many summaries to fit in one
   LLM call, inputs are packed into batches, each batch is merged
   with a partial-merge prompt, and the batch outputs are merged
   recursively until a single doc remains. Works on weak models
   (8k–16k context) just as well as large ones (200k+).

## LLM configuration

Copy `.env.example` to `.env`, pick a profile via `DEFAULT_MODEL`, and
the pipeline reads its `LLM_CMD` and `CHUNK_KB` from the matching
`<PREFIX>_*` entries. Per-model `CHUNK_KB` values let each model run at
a budget that suits its context window and output quality.

Recommended `CHUNK_KB` starting points (KB of input chars per LLM call):

- 8k context (GPT-4 base, local 7B models) → 15
- 16k context (GPT-3.5-turbo-16k, Llama 3 8B) → 30
- 32k context (GPT-4-32k, Mixtral 8x7B) → 80
- 128k context (GPT-4-turbo, GPT-4o, Llama 3 70B) → 300
- 200k context (Claude 3/3.5 family) → 600
- 400k context (GPT-5.4 / o-series full) → 1000
- 1M+ context (Gemini 1.5+) → 2000

These are starting suggestions; drop them lower when a model loses
detail at bigger inputs.

Budget resolution order, highest precedence first:

1. `--max-tokens N` CLI flag.
2. `X4_LLM_MAX_TOKENS` env var.
3. Active profile's `CHUNK_KB × 256`.
4. Hardcoded default (24000).

## Repository layout

- `run.sh` — one-shot entry point.
- `scripts/`
  - `generate_release_notes.py` — driver; chains the three stages
    with resumable, skip-existing behavior.
  - `run_rules.py` — stage 1: run all 20 rules against a version pair.
  - `release_notes_llm.py` — stage 2: per-rule LLM summaries with
    chunking.
  - `aggregate_release_notes.py` — stage 3: tree-reduce merge into a
    top-level release-notes document.
  - `inventory_xpath_ops.py` — one-off tool to audit `<diff>` patch
    shapes in real game data.
- `src/lib/` — shared machinery. Core piece is `entity_diff.py`: an
  XPath subset evaluator, the DLC patch-engine that replays `<diff>`
  ops, the `diff_library` function the rules call, three-tier conflict
  classification, and contributor-set tracking that records which
  DLCs touched which entity.
- `src/rules/` — one module per rule. Each exports
  `run(old_root, new_root, changes=None) -> list[RuleOutput]`. Every
  rule has a sibling `.md` documenting its data model and coverage.
- `src/change_map.py` — builds the file-level change list.
- `src/rules/_wave1_common.py` — shared ware-ownership predicate for
  the five ware-driven rules so no ware gets emitted twice.
- `.env.example` — LLM profile catalog. Copy to `.env` and edit.
- `x4-data/` — extracted game versions you provide. Not committed.
- `artifacts/` — pipeline outputs. Not committed; safe to delete if
  you want a clean regeneration, but the pipeline's resume logic
  means you usually don't need to.
- `docs/` — design docs (plan + spec). Not required at runtime.

## The 20 rules

- **Ware-driven** (5): `engines`, `weapons`, `turrets`, `equipment`, `wares`.
- **Macro-driven** (3): `ships`, `storage`, `sectors`.
- **Library entity-diff** (8): `factions`, `stations`, `jobs`, `loadouts`,
  `gamestarts`, `unlocks`, `drops`, `cosmetics`.
- **File-level** (2): `quests`, `gamelogic`.
- **Pre-existing** (2): `shields`, `missiles`.

Ownership across the ware-driven rules is enforced by
`src/rules/_wave1_common.owns(ware, tag)` — each ware belongs to
exactly one rule. Spacesuit gear, satellites, and personalupgrade items
route to `equipment` regardless of their `@group`.

## Error handling

Any LLM call that returns a non-zero exit code or empty output stops
the pipeline with the full stderr/stdout from that call. Nothing is
written for the failing chunk. Rerun the same command to resume —
completed chunks are detected and skipped.

If you want to force a rebuild, delete the relevant file:

```bash
rm artifacts/8.00H4_9.00B6/llm_quests_chunk7of15_<tag>.md  # one chunk
rm artifacts/8.00H4_9.00B6/llm_quests_aggregated_<tag>.md  # one rule
rm artifacts/8.00H4_9.00B6/RELEASE_NOTES_<tag>.md          # just the top merge
```

The next `./run.sh` run rebuilds only what's missing.

## Adding a new rule

1. Pick a skeleton from the docs (`docs/superpowers/plans/…` if that
   folder is still present) or copy an existing rule in `src/rules/`.
2. Create `src/rules/<name>.py` + `<name>.md`.
3. Add `'<name>'` to the `RULES` list in `scripts/run_rules.py`.
4. If the rule claims wares (Wave 1 pattern), extend `ware_owner` in
   `src/rules/_wave1_common.py` so no other rule emits overlapping
   rows.

## Conventions

- No commits happen from scripts or from the pipeline. Output files
  under `artifacts/` are regeneratable and gitignored.
- LLM chunk outputs are idempotent: same inputs always go to the
  same filename, so the pipeline can be resumed after a failure or
  re-run after a partial delete.
