# X4 Diff Pipeline

Generates a changelog between two versions of X4: Foundations by diffing the
extracted game source and running the diffs through an LLM.

## Prerequisites

- Python 3.10+
- An LLM CLI that accepts a prompt on stdin and writes the response to stdout
  (the default is the `claude` CLI with Opus)
- Two extracted game versions in `source/{version}/` (use `tools/extract.py`
  if you need to extract from `.cat`/`.dat` archives)

## Quick start

```sh
cp .env.example .env                          # fill in LLM_CLI and LLM_MODEL
python3 tools/pipeline.py 9.00B4 9.00B5 -j 5  # run pipeline with 5 concurrent LLM calls
```

Final output: `diff-results/{v1}-{v2}-{model}.md`.

## What the pipeline does

```
python3 tools/pipeline.py V1 V2 [-j N] [--mock]
```

- `V1`, `V2` — folder names under `source/`
- `-j N` — concurrent LLM calls for the parallel steps (default 1, recommended 5)
- `--mock` — replace LLM calls with stub output; useful for exercising
  orchestration, resume, and retry logic without spending quota

Pipeline stages:

1. **Raw diff** — `tools/diff.py` writes per-file unified diffs to `diff/raw/{pair}/`
2. **Pin settings** — `diff/models/{model}/settings.json` locks the chunk size for this model
3. **Chunk** — group diffs by domain, split to chunk size, write `diff/models/{model}/{pair}/chunks/`
4. **Analyze** — one LLM call per chunk (parallel) → `analysis/`
5. **Per-domain concat** — deterministic concat of multi-part analyses → `analysis-by-domain/`
6. **Topic synthesis** — one LLM call per topic (parallel) → `topics/`
7. **Dedup** — heuristic pre-filter surfaces candidate pairs; LLM decides (parallel, JSON); serial apply → `topics-deduped/`
8. **Assemble** — deterministic concat with TOC → `diff-results/{pair}-{model}.md`

## Resume semantics

- File existence is the checkpoint. Rerun `python3 tools/pipeline.py V1 V2` to pick up exactly where a prior run stopped.
- Within a run, tasks retry up to 3× on transient errors or bad output. After 3, a `*.failed` sibling file is written and that single task is blocked for the rest of the run (others continue).
- Between runs, `*.failed` markers rotate: `.failed → .failed.previous → .failed.previous.1` ... up to `.previous.4`. A fresh invocation always gives blocked tasks a new retry budget. Tasks at `.previous.4` (5+ failed runs) trigger a loud warning.
- Changing `LLM_CHUNK_SIZE` in `.env` after the first run does NOT re-chunk. To reset: `rm -rf diff/models/{model}/{pair}/`.

## Directory layout

```
tools/                       Python scripts
  diff.py                    Generate raw diffs between two versions
  extract.py                 Extract .cat/.dat game archives
  pipeline.py                Main orchestrator
  llm.py                     Subprocess + retry + validation
  prompts.py                 Prompt templates + topic definitions
  chunking.py                Domain classification + hunk splitting

source/{version}/            Extracted game data (gitignored)
diff/                        Pipeline working state (gitignored)
  raw/{pair}/                Per-file unified diffs, model-agnostic
  models/{model}/
    settings.json            Pinned config for this model
    {pair}/
      chunks/                Domain-grouped diff batches
      analysis/              Per-chunk LLM analyses
      analysis-by-domain/    Consolidated per-domain (deterministic concat)
      topics/                Per-topic syntheses
      dedup-decisions/       JSON deletion plans
      topics-deduped/        After dedup apply

diff-results/{pair}-{model}.md   Final deliverable
```

## Cleanup

Once you're happy with a result, the per-model working directory is safe to
delete: `rm -rf diff/models/{model}/{pair}/`. The final `diff-results/...md` is
the only artifact that needs to survive.

## Further reading

- `plan.md` — design document covering principles, flow, and open tradeoffs
- `CLAUDE.md` — guide to the X4 source tree for anyone (including LLMs)
  working with this codebase
