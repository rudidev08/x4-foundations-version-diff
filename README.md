# X4 release-notes generator

Turns the raw game-data diff between two X4 Foundations versions
(e.g. 8.00h4 → 9.00b6) into player-facing release notes, via a
three-stage pipeline: focused rules extract structured changes, an
LLM stage writes themed notes per rule, and a tree-reduce stage merges
everything.

## Quick start

```bash
./run.sh 8.00h4 9.00b6 --model gpt-5.5-mini-low
```

`--model` is required and must match a `*_MODEL_NAME` entry in `.env`
(see the catalog in `.env.example`).

The pipeline takes a pair of extracted game versions under `x4-data/`
(override with `SOURCE_PATH_PREFIX` in `.env` or `--game-data`) and
writes one file into `output/`:

- `8.00h4-9.00b6-<MODEL>.md` — LLM-written, player-facing release notes

Intermediate per-rule and per-chunk files live under
`artifacts/8.00h4-9.00b6-<MODEL>/` while the run is in progress, and
are deleted automatically once the final output is written. Parallel
runs on different models never share any files.

The run is **fully resumable at LLM-call granularity**: per-chunk LLM
outputs, per-rule aggregated files, and every intermediate tree-reduce
batch are persisted on disk before being used. If a run fails, the
artifacts folder is kept and a rerun picks up from the last successful
call, regardless of where in the pipeline the previous run failed.

More examples:

```bash
./run.sh 9.00b5 9.00b6 --model haiku-4.5                   # smaller pair, cheaper model
./run.sh 8.00h4 9.00b6 --model opus-4.7-max                # higher-quality run
./run.sh 8.00h4 9.00b6 --model haiku-4.5 --max-tokens 8000 # shrink per-call budget
```

## How it works

Three stages run under the hood:

Every run owns a per-model artifact folder at
`artifacts/<old>-<new>-<MODEL>/` for the duration of the run. Parallel
runs on different models share nothing; all deliverables in `output/`
are model-tagged. The artifact folder is deleted on success and kept on
failure so a rerun can resume.

1. **Rule pass.** 20 rules walk the two game-data trees and emit
   structured change records to
   `artifacts/<old>-<new>-<MODEL>/<rule>.json`. Each record has a rule
   tag, a one-line text, and `extras` with fields like entity keys,
   classifications, source DLCs, attribute diffs. ~30 seconds for the
   canonical pair.

2. **LLM per-rule pass.** Each non-empty rule JSON is turned into one
   or more markdown files by `scripts/release_notes_llm.py`. Large
   rules (quests, gamelogic, weapons, etc.) get split into size-limited
   chunks; each chunk is one LLM call. Outputs:
   `artifacts/<old>-<new>-<MODEL>/llm_<rule>.md` or
   `...llm_<rule>_chunk<N>of<M>.md`.

3. **Aggregation.** `scripts/aggregate_release_notes.py` runs a
   tree-reduce merge: multi-chunk rules get collapsed into one
   `artifacts/<old>-<new>-<MODEL>/llm_<rule>_aggregated.md`, then all
   per-rule summaries get combined into the top-level
   `output/<old>-<new>-<MODEL>.md`. The tree-reduce is size-aware — if
   too many summaries to fit in one LLM call, inputs are packed into
   batches, each batch is merged with a partial-merge prompt, and the
   batch outputs are merged recursively until a single doc remains.
   Every intermediate batch response is persisted under
   `artifacts/<old>-<new>-<MODEL>/.treereduce/` keyed by prompt hash,
   so a rerun after a partial failure picks up exactly where it
   stopped. Works on weak models (8k–16k context) just as well as
   large ones (200k+).

## LLM configuration

Copy `.env.example` to `.env`. Each profile is three keys:
`<PREFIX>_MODEL_NAME` (the value you pass to `--model`), `<PREFIX>_LLM_CMD`
(shell command run with the prompt on stdin), and `<PREFIX>_CHUNK_KB`
(per-call budget). Per-model `CHUNK_KB` values let each model run at a
budget that suits its context window and output quality.

Recommended `CHUNK_KB` starting points (KB of input chars per LLM call):

- 8k context (GPT-4 base, local 7B models) → 15
- 16k context (GPT-3.5-turbo-16k, Llama 3 8B) → 30
- 32k context (GPT-4-32k, Mixtral 8x7B) → 80
- 128k context (GPT-4-turbo, GPT-4o, Llama 3 70B) → 300
- 200k context (Claude 3/3.5 family) → 600
- 400k context (GPT-5.5 / o-series full) → 1000
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
- `cat_extract.py` — standalone helper to extract X4's `.cat`/`.dat`
  archives into `x4-data/<version>/`. Run with `--all-folders` so DLC
  content under `extensions/ego_dlc_*/` ends up in the tree the
  pipeline expects.
- `scripts/`
  - `generate_release_notes.py` — driver; chains the three stages
    with resumable, skip-existing behavior, and deletes the artifact
    folder once the final output is written.
  - `run_rules.py` — stage 1: run all 20 rules against a version pair.
  - `release_notes_llm.py` — stage 2: per-rule LLM summaries with
    chunking.
  - `aggregate_release_notes.py` — stage 3: tree-reduce merge into a
    top-level release-notes document.
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
- `artifacts/` — intermediate pipeline outputs (rule JSON, per-chunk
  LLM summaries, per-rule aggregates). Not committed. The driver
  deletes the per-pair folder once the final output is written; folders
  remain only for runs that failed partway, so a rerun can resume.
- `output/` — release-notes documents. One LLM-written
  `<old>-<new>-<MODEL>.md` per pair. Not committed.
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
written for the failing chunk. The artifact folder is preserved on
failure, so rerunning the same command resumes from the last successful
call — completed chunks are detected and skipped.

After a successful run the artifact folder is removed; a forced rebuild
deletes `output/<old>-<new>-<MODEL>.md` and reruns the whole pipeline
from scratch. While a run is still in flight (or after a failure), you
can target one stage by deleting its file:

```bash
rm artifacts/8.00h4-9.00b6-<MODEL>/llm_quests_chunk7of15.md  # one chunk
rm artifacts/8.00h4-9.00b6-<MODEL>/llm_quests_aggregated.md  # one rule
rm output/8.00h4-9.00b6-<MODEL>.md                           # just the top merge
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
  under `artifacts/` and `output/` are regeneratable and gitignored.
- LLM chunk outputs are idempotent: same inputs always go to the
  same filename, so the pipeline can be resumed after a failure or
  re-run after a partial delete.
