# Analyze Version Diffs

Generate per-file diffs between two X4 source versions, batch them by domain, and analyze each domain with subagents.

## Version Selection

Check if `diff/_versions_to_compare.md` exists. If it does, read `$V1` and `$V2` from it and skip to Prerequisites.

If it does not exist, ask the user which versions to compare. List the available directories under `source/` so they can pick. Once confirmed, write `diff/_versions_to_compare.md`:

```
OLD_VERSION=$V1
NEW_VERSION=$V2
```

Only this agent creates or updates `diff/_versions_to_compare.md`.

## Prerequisites
Source directories must exist at `source/$V1/` and `source/$V2/`.

**Before launching any subagents**, prompt the user: "This workflow uses background agents that need write access. Please make sure edit mode is on (press Enter to continue)." Use AskUserQuestion and wait for their response before proceeding. Background agents cannot prompt for permissions interactively — if writes are not pre-approved, they will silently fail.

## Progress Tracking

This workflow uses a progress file at `diff/$V1-$V2/_analysis/_progress.md` to track state. **Before doing anything**, check if this file exists. If it does, read it and resume from the first unchecked task. If it doesn't, create it during Step 3.

The progress file uses checkbox format:
```
- [x] libraries — done
- [ ] aiscripts — pending
```

After completing each analysis task, mark it `[x]` in the progress file immediately. This ensures context loss is recoverable.

## Steps

### Step 1: Generate per-file diffs
Run `python3 diff-tools/prepare_diff_analysis.py $V1 $V2` if batches don't already exist.

If diffs don't exist yet, first run `python3 diff-tools/version_diff.py $V1 $V2` to generate per-file unified diffs at `diff/$V1-$V2/`.

### Step 2: Prepare batches
Run `python3 diff-tools/prepare_diff_analysis.py $V1 $V2` to group diffs by domain and create concatenated batch files at `diff/$V1-$V2/_analysis/_batches/` with a `manifest.json`.

### Step 3: Build task list and progress file

Read `diff/$V1-$V2/_analysis/_batches/manifest.json`. Create **one analysis task per batch file**, using the batch filename without the `.diff` extension as the task ID (e.g., `libraries--part1`, `extensions--ego_dlc_terran--assets--props`).

**Special case — Localization:** If `t.diff` exists in the manifest, do NOT create a standalone `t` or `index` task. Instead create two localization tasks:
- `localization_mechanics` — will read `t.diff` + `index.diff` (if it exists) using the Localization Mechanics prompt
- `localization_lore` — will read `t.diff` using the Localization Story & Lore prompt

If `index.diff` exists but `t.diff` does not, create a standalone `index` task with the general prompt.

Write the progress file at `diff/$V1-$V2/_analysis/_progress.md` with one `- [ ] {task_id}` checkbox per task. Generate the list dynamically from the manifest — the number of tasks varies per diff.

### Step 4: Analyze domains

Process unchecked tasks in **user-sized batches**:

1. Count the remaining unchecked tasks and report the number remaining, then ask how many to launch next (e.g., "N tasks remaining. How many to launch in the next batch?").
2. Take the next N unchecked tasks from the progress file
3. Launch all N as background subagents simultaneously
4. Wait for all N to complete
5. If any failed (quota exhaustion, errors), **stop immediately** — do not launch more. Tell the user which tasks failed and that they can resume later.
6. If all N succeeded, mark them `[x]` in the progress file, then repeat from step 1

For each unchecked task, determine the batch file(s) and prompt to use:

- **`localization_mechanics`** → read `t.diff` and `index.diff` from `_analysis/_batches/`, use the **Localization Mechanics** prompt
- **`localization_lore`** → read `t.diff` from `_analysis/_batches/`, use the **Localization Story & Lore** prompt
- **All other tasks** → read `{task_id}.diff` from `_analysis/_batches/`, use the **general analysis** prompt with domain label from `manifest.json`

Launch an **opus** model subagent with the appropriate prompt from the sections below. Save the analysis output to `diff/$V1-$V2/_analysis/{task_id}.md`. Mark the task `[x]` in the progress file immediately after saving.

#### General Analysis Prompt

```
Analyze the following X4 Foundations game diff files for gameplay-relevant changes between version $V1 and $V2. Read each batch file listed below completely.

Domain: {domain_label}
Files to read:
{list of full batch file paths}

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

Domain: {domain_label}
Files to read:
{list of full batch file paths}

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

Domain: {domain_label}
Files to read:
{list of full batch file paths}

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

After all analysis tasks are marked `[x]`, delete the progress tracker:
- `diff/$V1-$V2/_analysis/_progress.md`

Keep `diff/$V1-$V2/_analysis/` — it is needed by `/diff-summarize` and `/diff-write`.

## Formatting Rules

- **Never use markdown tables.** They render poorly on mobile. Use inline bullet lists instead.
- For stat changes, use the format: `Item: old → new detail | detail | detail`
- Example: `Shield (Argon L): 0s → 16s delay | +30–40% capacity | +50–70% rate`
