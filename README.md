# X4 Changelog Pipeline

> [!CAUTION]
> **This is a local dev experiment, not a publishing tool.** Output is decent for browsing but lacks the precision and quality needed for actual release notes. A proper X4 changelog parser would lean on scripts that understand X4's data structures, and use LLM to categorize findings or add commentary (ideally with understanding of X4 cod structure). Treat this as an experiment in generic extraction and organization.

Given two extracted X4 game versions, produce one human-readable changelog of gameplay-relevant changes. Mechanical diff in Python; one LLM call per chunk for the prose. Full design: `spec.md`.

## High-level summary

- `01_enumerate.py` finds gameplay-relevant files that changed between `V1` and `V2`.
- `02_diff.py` writes one deterministic artifact per changed file (`.diff`, `.added`, or `.deleted`).
- `03_chunk.py` splits large or dense artifacts into model-sized chunks and records the entity vocabulary each chunk is allowed to mention.
- `04_llm.py` sends each chunk to the configured LLM and stores the raw structured findings.
- `05_assemble.py` parses those raw findings, strips the internal `[entity:key]` wire format from the final markdown, groups findings by category/entity, reports malformed prefixes, normalizes only the truly broken ones to `file:<source-path>`, and can optionally fail hard with `--strict-findings`.

## Requirements

- Python 3.10+ (stdlib only — no `pip install` step).
- Bash (for `run.sh`).
- One LLM CLI that reads a prompt from stdin and writes the response to stdout. Profiles for `claude`, `ollama`, etc. are already in `.env.example`.

## Setup

```bash
cp .env.example .env
# edit .env — set DEFAULT_MODEL to the model you want.

# put extracted game versions under x4-data/ (gitignored)
ls x4-data/
# 8.00H4  9.00B1  9.00B4  9.00B5
```

If you build `x4-data/` with `cat_extract.py`, the expected invocation is with `--all-folders` so the pipeline sees every supported gameplay-relevant path, including `assets/` and `ui/`.

```bash
python3 cat_extract.py /path/to/X4/Foundations x4-data/9.00B5 --all-folders
```

## End-to-end run

```bash
./run.sh --v1 9.00B4 --v2 9.00B5
```

Outputs `output/9.00B4_to_9.00B5_opus-4.7-max.md`. Intermediate files land under `artifacts/9.00B4_to_9.00B5_opus-4.7-max/`. Both dirs are gitignored. Re-running resumes only when the pipeline code, prompt, and artifact contract are unchanged.

Development rule:

- This project does not support artifact migrations or backward compatibility for old intermediate formats.
- After changing pipeline code, prompt contract, chunk headers, assembly rules, or generated filenames, clear the relevant `artifacts/` dir and rerun from scratch.
- Step 04 finding caches are existence-only by design. If `src/prompt.md`, `--prompt`, or step 04 behavior changes, clear `04_llm/` and downstream outputs or just delete the whole artifact dir and rerun.
- When in doubt, delete generated artifacts and the generated changelog, then rerun the whole pipeline.

Path contract:

- Supported usage is one canonical source root with version-named subfolders, e.g. `x4-data/9.00B4` and `x4-data/9.00B5`.
- Run identity is version-based, not full-path-based. Do not point the same version names at alternate parallel trees and expect separate caches.
- If you want to compare a different extraction of the same version labels, replace the canonical `x4-data/<version>/` contents or use different version folder names.

### `run.sh` flags

```
./run.sh --v1 VERSION --v2 VERSION [--model NAME] [--force-split] [--llm-calls N] [--workers N] [--strict-findings]
```

- `--v1 VERSION`
  - "Before" source. Resolved under `SOURCE_PATH_PREFIX` from `.env` (default `x4-data/`). Required.
- `--v2 VERSION`
  - "After" source. Same rules. Required.
- `--model NAME`
  - LLM model name — must match a `<KEY>_MODEL_NAME` in `.env`. Defaults to `DEFAULT_MODEL`.
- `--force-split`
  - When the chunker hits an oversize file it can't split structurally, fall back to cutting at blank lines / closing tags. Off by default and the step hard-fails instead.
- `--llm-calls N`
  - Cap on fresh LLM calls this run. Cached findings don't count, and bounded retries count against the same cap. If a chunk spends its last allowed call on a first-pass response that still requires retry, step 04 intentionally writes no finding for that chunk and leaves it pending; the next run reprocesses that chunk from the start. When the cap is reached the runner stops after step 04 and does not assemble `output/*.md`. Use this to throttle API spend.
- `--workers N`
  - Parallel LLM workers used by step 04 after the first preview/approval call. Defaults to `4`.
- `--strict-findings`
  - Make step 05 abort if any finding block lacks a valid chunk-approved prefix instead of tolerating/reporting it. Useful for release-grade or CI runs.

### Common invocations

```bash
# Cheapest first run: Haiku against a minor bump.
./run.sh --v1 9.00B4 --v2 9.00B5 --model haiku

# Full-quality run with Opus.
./run.sh --v1 9.00B4 --v2 9.00B5 --model opus-max

# Codex via the wrapper (see "Codex wrapper" below).
./run.sh --v1 9.00B4 --v2 9.00B5 --model gpt-5.4-xhigh

# First-time sanity check: approve one call, cap the rest at 9 more.
./run.sh --v1 9.00B4 --v2 9.00B5 --model gpt-5.4-xhigh --llm-calls 10

# Nightly quota throttle: 50 calls/day, run again tomorrow to continue.
./run.sh --v1 8.00H4 --v2 9.00B1 --model sonnet-low --llm-calls 50

# Faster step 04 after the first approval gate.
./run.sh --v1 9.00B4 --v2 9.00B5 --model gpt-5.4-xhigh --workers 8

# Release-grade check: fail if the model emits malformed finding prefixes.
./run.sh --v1 9.00B4 --v2 9.00B5 --model gpt-5.4-xhigh --strict-findings

# Force line-based split for a file the chunker can't cut structurally.
./run.sh --v1 9.00B4 --v2 9.00B5 --force-split
```

### Path resolution

`SOURCE_PATH_PREFIX` from `.env` (default `x4-data/`) defines the single canonical source root, so `--v1 9.00B4` resolves to `x4-data/9.00B4`. The supported contract is version subfolders under that one root; alternate parallel source trees with the same version names are out of scope.

## Individual steps

Each pipeline step is a standalone script with a small CLI. Running scripts directly bypasses `run.sh`'s `SOURCE_PATH_PREFIX` handling, so pass full paths:

```bash
ART=artifacts/9.00B4_to_9.00B5_opus-max

python3 src/01_enumerate.py --v1 x4-data/9.00B4 --v2 x4-data/9.00B5 --out "$ART"
python3 src/02_diff.py      --v1 x4-data/9.00B4 --v2 x4-data/9.00B5 --out "$ART"
python3 src/03_chunk.py     --v1 x4-data/9.00B4 --v2 x4-data/9.00B5 --out "$ART" --chunk-kb 50
python3 src/04_llm.py       --out "$ART" --llm-cmd "claude --print --model claude-opus-4-6 --effort max"
python3 src/05_assemble.py  --out "$ART" --v1-name 9.00B4 --v2-name 9.00B5 --model opus-max \
                            --changelog output/9.00B4_to_9.00B5_opus-max.md

# Strict assembly: abort if any finding block violates the prefix contract.
python3 src/05_assemble.py  --out "$ART" --v1-name 9.00B4 --v2-name 9.00B5 --model opus-max \
                            --strict-findings \
                            --changelog output/9.00B4_to_9.00B5_opus-max.md
```

Useful for debugging the pipeline one stage at a time — inspect `$ART/01_enumerate/enumeration.jsonl` or `$ART/03_chunk/chunks/*.txt` between runs.

### Step 05 notes

- `05_assemble.py` now parses multiple findings from a single `04_llm/findings/*.md` file instead of treating the whole file as one blob.
- The final changelog no longer includes raw `[entity:key]` lines; those prefixes stay internal to assembly.
- Tolerant mode keeps malformed findings, normalizes only the clearly broken prefixes to `file:<source-path>`, and records every violation in `05_assemble/malformed_findings.jsonl`.
- `--strict-findings` flips that behavior: the malformed report is still written, but assembly exits non-zero and leaves the changelog untouched.

## Running the tests

Each step has a `.test.py` next to it. Run any one directly, or run them all:

```bash
python3 src/01_enumerate.test.py           # just this step's tests
for t in src/*.test.py; do python3 "$t" || exit 1; done   # all tests
```

The test files are self-documenting: each test exercises the exact CLI of its step, so they double as usage examples.

## Step 04 (LLM) — extra flags

`04_llm.py` has a few knobs that are still useful when you're calling the step directly in a fresh artifact dir, or after clearing `04_llm/` and downstream outputs first. `run.sh` now forwards `--workers`; the step-only flags below are still not exposed at the top level:

```bash
ART=artifacts/9.00B4_to_9.00B5_opus-max
CMD="claude --print --model claude-opus-4-6 --effort max"

# Try an alternate prompt in a fresh artifact dir, or after clearing 04_llm/ and 05_assemble/.
python3 src/04_llm.py --out "$ART" --llm-cmd "$CMD" --prompt experiments/prompt_v2.md

# Automation: skip the interactive first-call approval.
python3 src/04_llm.py --out "$ART" --llm-cmd "$CMD" --no-approval

# Tune concurrency (default 4). 1 = sequential.
python3 src/04_llm.py --out "$ART" --llm-cmd "$CMD" --workers 2
```

The first LLM call each run always pauses to print its full input + response so you can sanity-check the prompt before spending on the rest. Any call that exits non-zero or returns empty stdout aborts the step with the full command + stderr — no silent retry loop. Under `--llm-calls`, the presence of `04_llm/findings/<id>.md` is still the only done-marker: a first-pass response that still needs the bounded retry is not cached if the cap is hit first.

## Codex wrapper

`codex exec` mixes session headers, "tokens used", and duplicated replies into stdout — unusable as an `LLM_CMD` directly. `src/codex-wrap.sh` is a thin adapter: it forwards all args to `codex exec`, redirects codex's noisy stdout to stderr, and uses `--output-last-message` to emit only the clean final reply on stdout. Use it in `.env` profiles that target codex-backed models (see `CODEX_GPT54_*` entries for the shape).

## Rescanning the XML schema map

`src/x4_schema_map.generated.json` tells the chunker which XML files can be cut at their repeating entity element. It's generated from the scanned source trees and records when the scan ran plus which sources it saw.

When a new X4 version or DLC lands, regenerate the map with the `rescan-schema` skill (inside Claude Code):

```
/rescan-schema
# then supply the source root(s) to scan, e.g. x4-data/9.00B5
```

The skill rewrites `src/x4_schema_map.generated.json` in place and records when the scan ran and which sources it saw.

## Working directory

```
artifacts/<V1>_to_<V2>_<model>/
  settings.json                 # config snapshot (chunk_kb, llm_cmd, ...)
  01_enumerate/enumeration.jsonl
  02_diff/diffs/<path>.{diff,added,deleted}
  03_chunk/chunks/<id>.txt
  04_llm/findings/<id>.md
  05_assemble/malformed_findings.jsonl
output/<V1>_to_<V2>_<model>.md  # final changelog
```

`artifacts/` is disposable — erase it to restart from scratch. During development, treat generated `output/*.md` the same way after pipeline changes: clear outputs and rerun. The only thing you keep is the final changelog from the current pipeline version.

## Design

See `spec.md` for priorities, the splitter levels, filter list, and category mapping. The short version: resumable > low data loss > grouping > categorization. Every artifact on disk is both the data and the done-marker; failure means no file.
