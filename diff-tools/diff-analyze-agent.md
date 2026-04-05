# Analyze Version Diffs

Generate per-file diffs between two X4 source versions, batch them by domain, and analyze each domain with subagents.

**Important:** Always use the provided shell scripts in `diff-tools/` for workflow operations. Do not write ad-hoc bash commands or loops to replace script functionality. If a needed capability is missing, suggest creating a new script rather than improvising.

## Version Selection

Read `diff/_versions_to_compare.md`. Each non-empty line is a version pair in the format `{OLD}-{NEW}` (e.g., `8.00H4-9.00B1`). Parse `$V1` (old) as the part before the `-` separator and `$V2` (new) as the part after.

If the file does not exist, ask the user which versions to compare. List the available directories under `source/` so they can pick. Once confirmed, write `diff/_versions_to_compare.md` with one pair per line in the `{OLD}-{NEW}` format.

Only this agent creates or updates `diff/_versions_to_compare.md`.

## Multi-Version Iteration

The prepare script (Steps 1–3) handles all pending pairs at once, skipping any with `_completed_analyze` flags. After preparation, process each remaining pair's tasks sequentially (Step 4). After completing a pair's tasks, run the finish script (Step 5) and move to the next pair. When all pairs are done, report completion.

## Prerequisites
Source directories must exist at `source/$V1/` and `source/$V2/`.

**Before launching any subagents**, prompt the user: "This workflow uses background agents that need write access. Please make sure edit mode is on (press Enter to continue)." Use AskUserQuestion and wait for their response before proceeding. Background agents cannot prompt for permissions interactively — if writes are not pre-approved, they will silently fail.

## Shell Scripts

Shell scripts in `diff-tools/` handle all workflow operations:
- `bash diff-tools/analyze-prepare.sh` — Steps 1-3 for all pending pairs (generate diffs, prepare batches, init tasks). Reports total remaining tasks.
- `bash diff-tools/task-next.sh analyze V1 V2` — Get next task as JSON: `{task_id, label, prompt_type, batch_files, remaining, output}`
- `bash diff-tools/task-done.sh analyze V1 V2 TASK_ID [OUTPUT]` — Verify output exists + mark task complete, JSON: `{marked, remaining}`
- `python3 diff-tools/task-batch.py analyze V1 V2 N` — Get N pending tasks at once as JSON: `{batch: [{task_id, label, prompt_type, batch_files, output}, ...], remaining}`. Use this instead of task-next.sh when launching multiple agents in parallel.
- `bash diff-tools/finish-pair.sh analyze V1 V2` — Delete progress tracker + create completion flag

## Steps

### Steps 1–3: Prepare all pairs

Run:
```
bash diff-tools/analyze-prepare.sh
```

This processes all pending version pairs: generates per-file diffs, prepares analysis batches, and initializes task lists. Already-completed pairs are skipped. The output reports total remaining tasks across all pairs.

### Step 4: Analyze domains

**Batch count is asked once before processing the first version pair.** The prepare script already reported the total remaining tasks. Ask the user once using AskUserQuestion how many to process, offering options like "5", "10", "25", and "All (N)". This count applies across all version pairs — do not ask again between pairs.

Then process tasks **one at a time**, working through each version pair sequentially:

1. Run `bash diff-tools/task-next.sh analyze $V1 $V2` to get the next task's info
2. If `{"done": true}` for this pair, proceed to Step 5 for this pair, then continue with the next pair's tasks
3. Report which task is being launched and how many remain (e.g., "Launching: libraries--part1 (N remaining)")
4. Launch one **opus** model background subagent with the appropriate prompt (see below), using the `batch_files`, `label`, and `prompt_type` from the JSON output
5. Wait for it to complete
6. **If it failed** (quota exhaustion, errors, unexpected output), **stop immediately**. Report the error to the user and wait for them to tell you what to do
7. If it succeeded, run `bash diff-tools/task-done.sh analyze $V1 $V2 TASK_ID OUTPUT` (verifies output exists + marks complete)
8. If the batch limit is reached, stop and report. Otherwise repeat from step 1

#### Prompt Selection

The `prompt_type` field from `next` determines which prompt to use:
- `"general"` → General Analysis Prompt
- `"localization_mechanics"` → Localization Mechanics Prompt
- `"localization_lore"` → Localization Story & Lore Prompt

#### General Analysis Prompt

```
Analyze the following X4 Foundations game diff files for gameplay-relevant changes between version $V1 and $V2. Read each batch file listed below completely.

Domain: {label}
Files to read:
{batch_files, one per line}

For each change found, classify its impact:
- **Critical / High Impact**: Changes that alter combat balance, economy flow, new game mechanics, ship stats, weapon behavior, AI behavior, mission structure
- **Medium Impact**: Narrower scope changes — specific ship classes, faction-specific, tactical scenarios, UI functionality, quality-of-life
- **Low Impact / Cosmetic**: Visual effects, sounds, code cleanup, whitespace, internal architecture, icons

Structure your output as:
### {Domain} — Critical / High Impact
(detailed findings with specific values: old → new)

### {Domain} — Medium Impact
(findings)

### {Domain} — Low Impact / Cosmetic
(brief summary)

Important:
- Include specific numeric values (old → new) for all stat changes
- Don't skip small-looking changes — a single number change can be a major balance shift
- Note new files (entirely new content) separately from modified files
- For XML attribute additions/removals, note what was added/removed
- Read ALL batch files assigned to you completely before writing your analysis
- When a change is ambiguous or you need more context to understand its impact, the full game source code is available at `source/$V2/` (and `source/$V1/` for removed content). Don't limit yourself to the diff — search the broader codebase to understand how a changed value is used, what references it, or what related systems it connects to. Context from entirely different files can be key to understanding a change's real significance
- NEVER use markdown tables. Use inline bullet lists: `Item: old → new detail | detail`
```

#### Localization Mechanics Prompt

```
Analyze the following X4 Foundations localization and index diff files for MECHANICS-related text changes between version $V1 and $V2. Read each batch file listed below completely.

Domain: {label}
Files to read:
{batch_files, one per line}

Focus on the **English localization files** (`l044`). For index files, note new or removed macro/component entries.

You are looking for text changes related to GAME MECHANICS ONLY. This includes:
- **New or renamed wares, weapons, equipment, ships, station modules** — names and short functional descriptions
- **New or changed UI strings** — menu labels, button text, notifications, warnings, status messages, settings, tooltips
- **Weapon/equipment effect descriptions** — what a weapon does mechanically (damage type, disruption effects, etc.)
- **New game features revealed by text** — new settings, new order types, new stances, new UI panels
- **Index changes** — new/removed macro or component registrations

EXCLUDE from this analysis (covered by the Story & Lore task):
- Flavor text, cultural anecdotes, narrative paragraphs in encyclopedia descriptions
- NPC character names, titles, dialog
- Mission briefing text, story dialog
- Lore rewrites (e.g., removing faction-specific backstory from a module description)

Classify changes as:
- **High Impact** — new mechanics, renamed core items, new feature strings
- **Medium Impact** — UI polish, new tooltips, setting labels
- **Low Impact** — minor wording tweaks with same meaning, index-only changes

Structure your output as:
### Localization Mechanics — High Impact
(quote new/changed text)

### Localization Mechanics — Medium Impact
(findings)

### Localization Mechanics — Low Impact
(brief summary)

### Localization Mechanics — Index Changes
(brief list of added/removed entries)

Important:
- Quote actual text strings for renamed items and new mechanics text
- Group related changes together (e.g., all new missile types)
- Skip non-English language files entirely — only analyze `l044` (English)
- Read ALL batch files assigned to you completely before writing your analysis
- NEVER use markdown tables. Use inline bullet lists
```

#### Localization Story & Lore Prompt

```
Analyze the following X4 Foundations localization diff files for STORY and LORE text changes between version $V1 and $V2. Read each batch file listed below completely.

Domain: {label}
Files to read:
{batch_files, one per line}

Focus on the **English localization files** (`l044`). Ignore index files for this task.

You are looking for text changes related to STORY, LORE, and NARRATIVE CONTENT ONLY. Prioritize the most interesting finds. This includes (in rough priority order):
- **Unique stations, objects, and locations** — descriptions of named stations, landmarks, anomalies, special objects in the game world
- **NPC characters** — new, renamed, or removed character names and titles
- **Mission dialog and briefings** — new or changed mission text, conversation options, story hints
- **Faction/world lore** — references to factions, wars, politics, technology origins, cultural details
- **Removed lore** — narrative content that was deleted or stripped out (note what was lost)
- **Tutorial/onboarding narrative** — story framing in tutorials or game start scenarios
- **Encyclopedia description rewrites** — only mention these if something genuinely interesting changed (new lore, faction references added/removed). Do NOT exhaustively list every weapon/module description that got a tone polish — just note the overall trend briefly if many were rewritten

EXCLUDE from this analysis (covered by the Mechanics task):
- Ware/weapon/equipment names (just the name, not the description)
- UI labels, button text, settings, tooltips
- Index changes
- Purely mechanical effect descriptions

For rewritten descriptions, quote BOTH the old and new text so the reader can compare the narrative change.

Classify changes as:
- **New Lore** — entirely new narrative content, new characters, new story text
- **Rewritten Lore** — existing narrative substantially changed (quote old vs new)
- **Removed Lore** — narrative content deleted or stripped (quote what was lost)
- **Minor** — trivial grammar/typo fixes in narrative text

Structure your output as:
### Story & Lore — New Content
(quote new text)

### Story & Lore — Rewritten Content
(quote old and new text side by side for each change)

### Story & Lore — Removed Content
(quote what was lost)

### Story & Lore — Minor Fixes
(brief summary)

Important:
- ALWAYS quote the actual text — both old and new for rewrites, so readers can see exactly what changed
- For encyclopedia description rewrites, only quote if the lore content is genuinely interesting — if many descriptions just got a tone change (e.g., colorful → clinical), summarize the trend in one bullet rather than listing each one
- Skip non-English language files entirely — only analyze `l044` (English)
- Read ALL batch files assigned to you completely before writing your analysis
- NEVER use markdown tables. Use inline bullet lists
```

### Step 5: Cleanup

After all analysis tasks are marked `[x]`, run:
```
bash diff-tools/finish-pair.sh analyze $V1 $V2
```

This deletes the progress tracker and creates the completion flag. The `_analysis/` directory is preserved for `/diff-summarize` and `/diff-write`. Proceed to the next uncompleted version pair, or report that all pairs are complete.

## Formatting Rules

- **Never use markdown tables.** They render poorly on mobile. Use inline bullet lists instead.
- For stat changes, use the format: `Item: old → new detail | detail | detail`
- Example: `Shield (Argon L): 0s → 16s delay | +30–40% capacity | +50–70% rate`
