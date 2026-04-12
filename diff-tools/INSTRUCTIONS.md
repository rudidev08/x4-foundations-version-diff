# X4 Diff Pipeline — Agent Instructions

You are processing X4 Foundations game diffs to produce changelogs. Follow these instructions exactly.

## How to Run

Read `diff/_versions_to_compare.md` for the version pairs to process. For each pair (V1-V2):

1. Run `python3 diff-tools/pipeline.py status V1 V2` to check current state
2. Run `python3 diff-tools/pipeline.py next V1 V2` — this prints a task prompt
3. Follow the prompt: read the specified file(s), analyze the content, write your output to the specified path
4. Run `python3 diff-tools/pipeline.py done V1 V2` to mark the task complete
5. Repeat steps 2-4 until `next` says "All phases complete" (phases run in order: analyze → summarize → write → dedup)
6. Run `python3 diff-tools/pipeline.py assemble V1 V2` to produce the final changelog

If `next` says a phase is complete and advances to the next phase, just run `next` again to get the first task of the new phase.

## What Each Task Asks You To Do

The `next` command prints a full prompt. It always tells you:
- **What file(s) to read** — batch diff files, analysis results, or summary files
- **What to analyze for** — impact classification, specific values, mechanics vs. lore
- **What format to use** — markdown with impact-level headers
- **Where to write output** — an exact file path (create parent directories if needed)

Your job: read the input files completely, follow the analysis instructions, write the output file, then run `done`.

## Rules (Apply to Every Task)

- **SEQUENTIAL ONLY.** Process one task at a time: run `next`, do the work, run `done`, repeat. Never run multiple tasks in parallel, never use subagents/task agents, never spawn concurrent workers. The pipeline tracks state in a single progress file that will corrupt under parallel writes.
- **ONE VERSION PAIR AT A TIME.** Only process the version pair you were given. Do not read `_versions_to_compare.md` and process additional pairs on your own.
- **ONLY WRITE THE FILE THAT `next` TELLS YOU TO.** Each `next` command specifies exactly one output path. Do not write files for other tasks, future tasks, or tasks you haven't been assigned. The `done` command validates that only the expected file was created.
- **Stay focused.** Do not run git commands or browse files outside of diff batches and source directories. Your inputs are the batch files from each task and the `source/{V1}/` and `source/{V2}/` directories — searching source files to understand what a change means is expected and encouraged.
- Only report changes that are explicitly in the diff files or source code. Never invent or infer.
- Include specific numeric values (old -> new) for every stat change.
- Never use markdown tables. Use inline bullets: `Item: old -> new detail | detail`
- Only English localization (`l044`) matters. Ignore other languages.
- When a change is ambiguous, search `source/{V2}/` and `source/{V1}/` for context on what the values mean.
- Read ALL assigned files completely before writing output.
- Keep output concise but complete — every unique finding matters, duplicates don't.

## Recovery

If interrupted (ran out of tokens, crashed, etc.):
- Run `status V1 V2` to see where you left off
- Run `next V1 V2` to get the next unfinished task — it picks up exactly where you stopped
- If an output file was partially written or corrupted, delete it and redo the task
- To redo an entire phase: `python3 diff-tools/pipeline.py reset V1 V2 phase`

## Before You Start

Check your `.env` file and confirm with the user that `LLM_MODEL` is set correctly. Users sometimes forget to update it after switching models. **Do not update `.env` yourself** — always ask the user to verify and change it if needed.

## Preparation

Before the first run, version pairs must be prepared. Run:
```sh
./setup.sh
```
Or for a single pair:
```sh
python3 diff-tools/pipeline.py prepare V1 V2
```

This generates diffs, batches them by domain, and creates the progress file. You only need to do this once per pair.

## File Layout

```
diff/_versions_to_compare.md  ← version pairs (one per line, format: OLD-NEW)
diff/V1-V2/
  _batches/                   ← input: prepared batch files the LLM reads
  _runs/{LLM_MODEL}/
    _analysis/                ← output: analyze phase (one .md per task)
    _summary/                 ← output: summarize phase (one .md per multi-part domain)
    _sections/                ← output: write phase (one .md per theme), then dedup phase edits these in-place
    _progress.json            ← tracks which tasks are done
diff-results/
  diff-V1-V2-{LLM_MODEL}.md               ← final assembled changelog
source/V1/, source/V2/        ← full game source (for context lookups)
```
