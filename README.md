# X4 Foundations Version Diff Tools

Generate diffs between X4 Foundations source versions. Optionally use an LLM agent pipeline to summarize changes in three steps: extract, analyze, summarize.

## Requirements

You must own [X4 Foundations](https://www.egosoft.com/games/x4/info_en.php) and have access to the extracted source files.

## Tools

- **`cat_extract.py`** — Extracts text files from X4's `.cat/.dat` archives. Filters to gameplay-relevant folders by default (libraries, md, aiscripts, maps, etc.).
- **`version_diff.py`** — Generates per-file unified diffs between two extracted source versions. Outputs to `diff/{V1}-{V2}/`. English-only localization by default.
- **`prepare_diff_analysis.py`** — Batches diffs by domain (libraries, aiscripts, md, etc.) and produces a manifest for LLM agent processing. Skips cosmetic-only domains (shaders, FX, environments) by default.
- **`diff-analyze-agent.md`** — Claude Code agent prompt. Analyzes each domain batch and writes per-domain markdown summaries.
- **`diff-summarize-agent.md`** — Claude Code agent prompt. Consolidates multi-part domain analyses into single summaries.
- **`diff-write-agent.md`** — Claude Code agent prompt. Synthesizes all domain summaries into a final thematic changelog.

## Usage

### 1. Extract source files

Extract each game version you want to compare.

```sh
python3 diff-tools/cat_extract.py /path/to/X4/Foundations source/9.00B3
python3 diff-tools/cat_extract.py /path/to/X4/Foundations source/9.00B4
```

### 2. Generate diffs

```sh
python3 diff-tools/version_diff.py 9.00B3 9.00B4
```

### 3. Prepare batches for LLM analysis (optional)

```sh
python3 diff-tools/prepare_diff_analysis.py 9.00B3 9.00B4
```

### 4. Run LLM agent pipeline (optional)

Requires [Claude Code](https://docs.anthropic.com/en/docs/claude-code). Run the three agent prompts in order:

1. `/diff-analyze` — Analyze each domain batch
2. `/diff-summarize` — Consolidate multi-part results
3. `/diff-write` — Write the final changelog

## Structure

- `diff-tools/` — Scripts and agent prompts
- `source/` — Extracted game data by version (not included in repo)
- `CLAUDE.md` — LLM agent context for the codebase
