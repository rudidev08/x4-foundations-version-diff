# X4 release-notes generator

Turns the raw game-data diff between two X4 Foundations versions
(e.g. 8.00h4 → 9.00b6) into player-facing release notes, via a
three-stage pipeline: focused rules extract structured changes, an
LLM stage writes themed notes per rule, and a tree-reduce stage merges
everything.

Two ways to run the same pipeline:

- **Batch, any model** — `./run.sh` drives all three stages non-interactively
  against any LLM you can call from a shell (an API, a local model, codex, or
  `claude --print`), configured in `.env`. Best when you already have a model
  wired up and just want the file.
- **Interactive, Claude Code** — the `/x4-diff` skill runs the same stages in a
  Claude Code session, doing the LLM work with in-session subagents. No `.env`,
  no API key — just your Claude subscription. Best for non-developers who only
  have Claude Code, and for watching progress and fielding a question or two as
  it runs.

Stage 1 (the deterministic rules) and every prompt are shared; the modes differ
only in how the LLM stages are executed.

## Quick start — batch

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

By default the report covers all 20 categories; pass `--categories`
with a comma-separated subset of the rule names to scope it, e.g.
`--categories ships,weapons,quests`.

More examples:

```bash
./run.sh 9.00b5 9.00b6 --model haiku-4.5                   # smaller pair, cheaper model
./run.sh 8.00h4 9.00b6 --model opus-4.7-max                # higher-quality run
./run.sh 8.00h4 9.00b6 --model haiku-4.5 --max-tokens 8000 # shrink per-call budget
```

## Interactive mode (Claude Code)

`/x4-diff` runs the whole pipeline inside a Claude Code session. Stage 1 runs
as before; the LLM stages — per-rule notes and the tree-reduce merge — are done
by **in-session subagents** instead of an external `LLM_CMD`. No `.env`, no API
key, no `--model`: subagents run on whatever model and effort the session has
active, and the output is tagged with that model's name.

```
/x4-diff
```

It asks once for the version pair and the tone (concise patch notes vs. a
narrative recap), then runs unattended — streaming progress per category and
stopping only if a subagent fails or returns nothing. Every generated file is
verified non-empty, and a coverage gate refuses to write the final document
unless every changed, non-diagnostic category produced notes, so a run never
silently drops a section. Output lands at `output/<old>-<new>-<model>.md`, same
as batch.

Built for players who have Claude Code but no API key or local model. Needs
Python 3 on PATH; both versions must already be extracted under `x4-data/` (use
`cat_extract.py`). Two pieces back it: a harness-agnostic engine
(`scripts/interactive_plan.py` — plain Python that reads/writes files and prints
JSON) and the orchestrating skill (`.claude/skills/x4-diff/SKILL.md`).

### Other harnesses

Technically portable, not yet tested elsewhere. The engine drives from any agent
that can run a shell and dispatch an LLM to turn a prompt file into an output
file. The `SKILL.md` format (frontmatter + instructions) is auto-discovered by
other skill-aware CLIs (Copilot, Gemini), but its instructions assume Claude
Code primitives — parallel in-session subagents, a user-question prompt, a
shell. Elsewhere it degrades rather than breaks: no parallel subagents → do the
chunks one at a time; no question prompt → pass the version pair and tone as
arguments.

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

Batch mode only — interactive `/x4-diff` reads no `.env` and runs on your
session's model.

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

- `run.sh` — one-shot entry point for batch mode.
- `.claude/skills/x4-diff/SKILL.md` — the `/x4-diff` interactive skill for
  Claude Code; drives the same pipeline with in-session subagents.
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
  - `interactive_plan.py` — interactive-mode engine: a pure planner that
    renders the stage-2/3 prompts and reports each round as JSON for the
    `/x4-diff` skill to drive with subagents. No LLM calls.
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

1. Copy an existing rule in `src/rules/` as a skeleton.
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
