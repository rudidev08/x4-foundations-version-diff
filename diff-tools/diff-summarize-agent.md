# Summarize Version Diff Results

Consolidate multi-part domain analysis results into single summary files, combining redundant data while preserving all information.

**Important:** Always use the provided shell scripts in `diff-tools/` for workflow operations. Do not write ad-hoc bash commands or loops to replace script functionality. If a needed capability is missing, suggest creating a new script rather than improvising.

## Version Selection

Read `diff/_versions_to_compare.md`. Each non-empty line is a version pair in the format `{OLD}-{NEW}` (e.g., `8.00H4-9.00B1`). Parse `$V1` (old) and `$V2` (new) from each line. If the file does not exist, tell the user to run `/diff-analyze` first.

## Multi-Version Iteration

The prepare script (Step 1) handles all pending pairs at once, skipping completed pairs and those missing analysis prerequisites. After preparation, process each remaining pair's tasks sequentially (Step 2). After completing a pair's tasks, run the finish script (Step 3) and move to the next pair. When all pairs are done, report completion.

## Prerequisites

The following must exist (produced by `/diff-analyze`):
- `diff/$V1-$V2/_analysis/*.md` — individual domain analysis files

**Before launching any subagents**, prompt the user: "This workflow uses background agents that need write access. Please make sure edit mode is on (press Enter to continue)." Use AskUserQuestion and wait for their response before proceeding. Background agents cannot prompt for permissions interactively — if writes are not pre-approved, they will silently fail.

## Shell Scripts

Shell scripts in `diff-tools/` handle all workflow operations:
- `bash diff-tools/summarize-prepare.sh` — Step 1 for all pending pairs (check prerequisites, init tasks). Auto-creates flag for pairs with nothing to summarize. Reports total remaining tasks.
- `bash diff-tools/task-next.sh summarize V1 V2` — Get next domain as JSON: `{domain, type, file_count, files, remaining, output}` (hierarchical domains also include `chunks` and `total_chunks`)
- `bash diff-tools/task-done.sh summarize V1 V2 DOMAIN [OUTPUT]` — Verify output exists + mark domain complete, JSON: `{marked, remaining}`
- `bash diff-tools/finish-pair.sh summarize V1 V2` — Cleanup intermediate files + create completion flag

## Steps

### Step 1: Prepare all pairs

Run:
```
bash diff-tools/summarize-prepare.sh
```

This processes all pending version pairs: checks prerequisites, initializes summarize task lists, and auto-creates completion flags for pairs with nothing to summarize. Already-completed pairs are skipped. The output reports total remaining tasks across all pairs.

### Step 2: Summarize domains

**Batch count is asked once before processing the first version pair.** The prepare script already reported the total remaining tasks. Ask the user once using AskUserQuestion how many to process, offering options like "5", "10", "25", and "All (N)". This count applies across all version pairs — do not ask again between pairs.

Then process tasks **one at a time**, working through each version pair sequentially:

1. Run `bash diff-tools/task-next.sh summarize $V1 $V2` to get the next domain's info
2. If `{"done": true}` for this pair, proceed to Step 3 for this pair, then continue with the next pair's tasks
3. Report which domain is being processed and how many remain (e.g., "Processing: libraries (N remaining)")
4. Process using the appropriate method based on the `type` field:

#### Single-pass domains (`type: "single-pass"`)

Launch one **opus** model subagent that reads all files listed in `files` and writes the summary to the `output` path. Use the **Consolidation Prompt** below.

#### Hierarchical domains (`type: "hierarchical"`)

The `next` output includes a `chunks` array with pre-computed file groupings.

**Pass 1 — Chunk summaries:** Process chunks **one at a time sequentially**. For each chunk, launch an **opus** subagent that reads the chunk's `files` and writes to the chunk's `intermediate_output` path. Use the **Consolidation Prompt**. After each chunk completes, verify the output file exists before launching the next. If a chunk fails, stop and ask the user what to do.

**Pass 2 — Final merge:** After ALL chunks complete, launch one **opus** subagent that reads all intermediate chunk summaries and writes the final summary to the `output` path. Use the **Merge Prompt**. After the final summary is written, delete the intermediate chunk files for that domain.

5. **If any subagent failed** (quota exhaustion, errors, unexpected output), **stop immediately**. Report the error and wait for the user
6. If it succeeded, run `bash diff-tools/task-done.sh summarize $V1 $V2 DOMAIN OUTPUT` (verifies output exists + marks complete)
7. If the batch limit is reached, stop and report. Otherwise repeat from step 1

### Step 3: Cleanup

After all domains are marked `[x]`, run:
```
bash diff-tools/finish-pair.sh summarize $V1 $V2
```

This cleans up intermediate files and creates the completion flag. Proceed to the next uncompleted version pair, or report that all pairs are complete.

## Prompts

### Consolidation Prompt

Used for both single-pass domains and hierarchical pass-1 chunks.

```
Read the following domain analysis result files completely and consolidate them into a single unified summary. These files were produced by analyzing game diffs between version $V1 and $V2. They may contain redundant or overlapping entries because the source diffs were split into arbitrary size-based parts.

Domain: {domain}
Files to read:
{files, one per line}

Consolidation rules:
- **Combine duplicate entries**: If the same game object, mechanic, or change appears in multiple files, merge into one entry with ALL relevant details from every source
- **PRESERVE ALL SPECIFIC VALUES**: Every old → new number, every stat change, every named item MUST appear in the output. Information loss is unacceptable. When in doubt, keep it.
- **Preserve impact classification**: Keep the Critical/High/Medium/Low Impact structure. If the same change is classified at different impact levels in different files, use the HIGHER impact level
- **Merge related changes**: Group changes to the same game system together (e.g., all shield changes, all weapon changes, all changes to the same ship class) even if they came from different part files
- **Remove pure redundancy only**: If two parts describe the EXACT same change with the SAME values, include it once. If there is ANY difference in detail, values, or explanation, keep both details in the merged entry
- **Keep explanatory context**: Preserve notes about why a change matters, how systems connect, or what the gameplay impact is
- **Cross-reference when combining**: Note when a consolidated entry draws from multiple source areas

Output structure:
Use ### headers organized by impact level, then by game system/topic within each level:

### {Domain} — Critical / High Impact
(grouped by system: combat, economy, AI, etc.)

### {Domain} — Medium Impact
(grouped by system)

### {Domain} — Low Impact / Cosmetic
(brief summary)

NEVER use markdown tables. Use inline bullet lists: `Item: old → new detail | detail | detail`
Read ALL files completely before writing. Do not start writing until you have read every file.
```

### Merge Prompt

Used for hierarchical pass-2 final merge.

```
Read the following intermediate chunk summaries for domain "{domain}" and merge them into one final consolidated summary. These chunks were produced by summarizing subsets of the original analysis files for game diff $V1 → $V2. There may still be redundancy across chunks where the same change was captured in overlapping parts.

Files to read:
{chunk summary file paths, one per line}

Apply the same consolidation rules:
- Combine duplicate entries across chunks into single entries with all details
- PRESERVE ALL SPECIFIC VALUES — every number, every stat, every named item
- Use the higher impact classification when chunks disagree
- Merge related changes into unified system-level descriptions
- Remove only exact duplicates; keep anything with unique detail
- Preserve explanatory context and cross-references

The final output should read as a single coherent analysis of ALL changes in this domain, organized by impact level and then by game system. A reader should not be able to tell it was assembled from multiple chunks.

Output structure:
### {Domain} — Critical / High Impact
### {Domain} — Medium Impact
### {Domain} — Low Impact / Cosmetic

NEVER use markdown tables. Use inline bullet lists: `Item: old → new detail | detail | detail`
Read ALL files completely before writing.
```

## Formatting Rules

- **Never use markdown tables.** They render poorly on mobile. Use inline bullet lists instead.
- For stat changes, use the format: `Item: old → new detail | detail | detail`
- Example: `Shield (Argon L): 0s → 16s delay | +30–40% capacity | +50–70% rate`
