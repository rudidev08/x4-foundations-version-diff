# X4 Extraction Tool

## Goal

Given two extracted versions (`x4-data/V1/`, `x4-data/V2/`), produce one human-readable changelog of gameplay-relevant changes. The diff is mechanical (`difflib`); the LLM turns XML/Lua/script changes into player-readable prose and is the only non-deterministic part.

## Priorities

In order — higher overrides lower:

1. **Resumable** from any failure based on already-saved files.
2. **Low data loss** — bounded and never silent.
3. **Deduplication**, only where it doesn't conflict with (1) or (2). Dedup that hides a finding is worse than no dedup.
4. **Categorization**, only where it doesn't conflict with (1)–(3). A finding with no clear category must still appear in the output.

## Contract

The only contract: given two `x4-data/{V}/` directories, produce a changelog Markdown file. Intermediate formats, filenames, `.env` keys, CLI flags, and on-disk layouts are disposable — no backwards-compat constraints.

Artifacts are disposable. If pipeline code, prompt contract, or on-disk contracts change, clear generated artifacts and rerun. Step 04 finding caches are existence-only by design; do not add cache-busting, migration, or backward-compatibility code for old artifacts.
