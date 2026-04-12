# X4 Foundations Version Diff Tools

Generate diffs between X4 Foundations source versions and produce changelogs with LLM assistance.

## Quick Start — Running a Diff with an LLM

Once source files are extracted (see below), paste this to your LLM:

> Read `diff-tools/INSTRUCTIONS.md` and run the full diff pipeline for versions `9.00B4` to `9.00B5`.

Replace the version numbers with whatever pair you're comparing. The LLM handles everything — reading diffs, writing analysis, and assembling the final changelog.

## Requirements

- Python 3.12+
- [X4 Foundations](https://www.egosoft.com/games/x4/info_en.php) (you need the game files)
- Any LLM with ~32k+ context (Claude, GPT, Qwen, Llama, etc.) — the pipeline uses standard unified diff format that all models handle well

## Configuration

The pipeline tags outputs with a model name so you can compare results from different LLMs side by side (each gets its own run directory under `diff/V1-V2/_runs/`).

To set the model name, copy `.env.example` to `.env` and edit it:

```sh
cp .env.example .env
```

If `.env` doesn't exist when you run `setup.sh`, it creates one with `LLM_MODEL=default`. You can change it anytime — set it to whatever identifies your model, e.g. `qwen3.5-27b`, `claude-sonnet`, `llama-70b`.

## Usage

### 1. Extract source files

```sh
python3 diff-tools/cat_extract.py /path/to/X4/Foundations source/9.00B4 --all-folders
python3 diff-tools/cat_extract.py /path/to/X4/Foundations source/9.00B5 --all-folders
```

Use `--all-folders` to extract assets, UI, and other directories needed by the diff pipeline. Without it, only core data (libraries, aiscripts, md, maps, index, t) is extracted.

### 2. Run the pipeline

```sh
python3 diff-tools/pipeline.py prepare 9.00B4 9.00B5
python3 diff-tools/pipeline.py next 9.00B4 9.00B5      # get prompt for next task
# ... do the work (feed prompt to LLM, write output file) ...
python3 diff-tools/pipeline.py done 9.00B4 9.00B5      # mark complete
# ... repeat next/done until all phases finish (analyze → summarize → write → dedup) ...
python3 diff-tools/pipeline.py assemble 9.00B4 9.00B5  # build final changelog
python3 diff-tools/pipeline.py status 9.00B4 9.00B5    # check progress
python3 diff-tools/pipeline.py reset 9.00B4 9.00B5 [phase]  # redo a phase
```

### 3. Output

Final changelog lands in `diff-results/diff-9.00B4-9.00B5-{LLM_MODEL}.md`.

## Structure

- `diff-tools/pipeline.py` — Orchestrator (prepare, track progress, render prompts, assemble)
- `diff-tools/version_diff.py` — Generates per-file unified diffs between versions
- `diff-tools/cat_extract.py` — Extracts text files from X4's `.cat/.dat` archives
- `diff-tools/INSTRUCTIONS.md` — LLM prompt reference
- `source/` — Extracted game data by version (not in repo)
- `diff/` — Generated diffs and intermediate analysis
- `diff-results/` — Final changelogs
