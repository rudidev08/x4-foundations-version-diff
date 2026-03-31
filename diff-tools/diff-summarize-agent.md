# Summarize Version Diff Results

Consolidate multi-part domain analysis results into single summary files, combining redundant data while preserving all information.

## Version Selection

Read `$V1` and `$V2` from `diff/_versions.md`. If the file does not exist, tell the user to run `/diff-analyze` first.

## Prerequisites

The following must exist (produced by `/diff-analyze`):
- `diff/$V1-$V2/_analysis/*.md` — individual domain analysis files

**Before launching any subagents**, prompt the user: "This workflow uses background agents that need write access. Please make sure edit mode is on (press Enter to continue)." Use AskUserQuestion and wait for their response before proceeding. Background agents cannot prompt for permissions interactively — if writes are not pre-approved, they will silently fail.

## Progress Tracking

This workflow uses a progress file at `diff/$V1-$V2/_summary/_progress.md` to track state. **Before doing anything**, check if this file exists. If it does, read it and resume from the first unchecked task. If it doesn't, create it during Step 2.

After completing each domain summary, mark it `[x]` in the progress file immediately. This ensures context loss is recoverable.

## Steps

### Step 1: Discover and group result files

List all `.md` files in `diff/$V1-$V2/_analysis/`. Group them by **domain root** — the filename with `--partN` suffixes stripped. The part suffix pattern is `--part` followed by one or more digits at the end of the filename (before `.md`).

Examples:
- `libraries--part1.md` through `libraries--part35.md` → group `libraries` (35 files)
- `aiscripts--part1.md` through `aiscripts--part13.md` → group `aiscripts` (13 files)
- `extensions--ego_dlc_boron--libraries--part1.md` through `--part5.md` → group `extensions--ego_dlc_boron--libraries` (5 files)
- `localization_mechanics.md` → single file, skip

**Filtering rules:**
- **Only process groups with 2 or more files.** Single-file domains are already self-contained and have no redundancy to consolidate.
- **Skip files in `_batches/` subdirectory** — these are diff input files, not analysis results.
- **Skip `_progress.md`** — this is the analyze agent's progress tracker.

### Step 2: Build progress file

Create the progress file at `diff/$V1-$V2/_summary/_progress.md`:

```
# Summarize Progress: $V1 → $V2

## Domains
- [ ] libraries (35 files, hierarchical)
- [ ] aiscripts (13 files, hierarchical)
- [ ] assets--props (10 files, hierarchical)
- [ ] md (5 files, single-pass)
...
```

Classify each domain:
- **hierarchical** — more than 8 files in the group
- **single-pass** — 2 to 8 files in the group

Sort domains by file count descending (largest groups first).

### Step 3: Summarize domains

Process unchecked domains in **user-sized batches**:

1. Count the remaining unchecked domains and report the number remaining, then ask how many to launch next (e.g., "N domains remaining. How many to launch in the next batch?").
2. Take the next N unchecked domains from the progress file
3. For **single-pass** domains, launch one subagent per domain (all in parallel)
4. For **hierarchical** domains, process one at a time within the batch (pass 1 must complete before pass 2)
5. Wait for all to complete
6. If any failed (quota exhaustion, errors), **stop immediately** — do not launch more. Tell the user which domains failed and that they can resume later.
7. If all N succeeded, mark them `[x]` in the progress file, then repeat from step 1

**Batching guidance for hierarchical domains:** Each hierarchical domain requires multiple sequential subagent launches (chunk pass then merge pass). To avoid complexity, process at most 2 hierarchical domains per batch. Single-pass domains can all run in parallel within a batch.

#### Single-pass domains (2–8 files)

Launch one **opus** model subagent that reads all files in the group and writes the summary directly to `diff/$V1-$V2/_summary/{domain_root}.md`.

Use the **Consolidation Prompt** below.

#### Hierarchical domains (> 8 files)

**Pass 1 — Chunk summaries:**

Split the group's files into chunks of up to 8 files each, ordered by part number. Launch one **opus** subagent per chunk (all chunks in parallel). Each produces an intermediate summary at `diff/$V1-$V2/_summary/_intermediate/{domain_root}--chunk{N}.md`.

Use the **Consolidation Prompt** below for each chunk.

**Pass 2 — Final merge:**

After ALL chunks for a domain complete, launch one **opus** subagent that reads all intermediate chunk summaries and produces the final summary at `diff/$V1-$V2/_summary/{domain_root}.md`.

Use the **Merge Prompt** below.

After the final summary is written successfully, delete the intermediate chunk files for that domain.

### Step 4: Cleanup

After all domains are marked `[x]`:
- Delete `diff/$V1-$V2/_summary/_intermediate/` directory (if it exists)
- Delete `diff/$V1-$V2/_summary/_progress.md`

## Prompts

### Consolidation Prompt

Used for both single-pass domains and hierarchical pass-1 chunks.

```
Read the following domain analysis result files completely and consolidate them into a single unified summary. These files were produced by analyzing game diffs between version $V1 and $V2. They may contain redundant or overlapping entries because the source diffs were split into arbitrary size-based parts.

Domain: {domain_root}
Files to read:
{list of full file paths, one per line}

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
Read the following intermediate chunk summaries for domain "{domain_root}" and merge them into one final consolidated summary. These chunks were produced by summarizing subsets of the original analysis files for game diff $V1 → $V2. There may still be redundancy across chunks where the same change was captured in overlapping parts.

Files to read:
{list of chunk summary file paths, one per line}

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
