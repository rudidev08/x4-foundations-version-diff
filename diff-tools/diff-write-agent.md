# Write Version Diff Changelog

Synthesize domain analysis results into a single thematic changelog using subagents per section.

## Version Selection

Read `$V1` and `$V2` from `diff/_versions_to_compare.md` (created by `/diff-analyze`). If the file does not exist, tell the user to run `/diff-analyze` first.

## Prerequisites

The following must exist:
- `diff/$V1-$V2/_analysis/*.md` — individual domain analysis files (produced by `/diff-analyze`)
- `diff/$V1-$V2/_summary/*.md` — consolidated summaries for multi-part domains (produced by `/diff-summarize`)

**Before launching any subagents**, prompt the user: "This workflow uses background agents that need write access. Please make sure edit mode is on (press Enter to continue)." Use AskUserQuestion and wait for their response before proceeding. Background agents cannot prompt for permissions interactively — if writes are not pre-approved, they will silently fail.

## Progress Tracking

This workflow uses a progress file at `diff/$V1-$V2/_write_progress.md` to track state. **Before doing anything**, check if this file exists. If it does, read it and resume from the first unchecked task. If it doesn't, create it during Step 1.

After completing each section, mark it `[x]` in the progress file immediately. This ensures context loss is recoverable.

## Sections

Each section defines its id, label, focus, and domain prefixes for routing analysis results. Domain prefixes match result filenames using `--` as the path separator (e.g., prefix `extensions` matches `extensions--ego_dlc_split--libraries.md`). A prefix of `*` means read all result files.

1. `combat` — Combat System
   Focus: Shields, weapons, missiles, turrets, AI targeting, weapon heat, disruption mechanics
   Domains: `libraries`, `aiscripts`, `assets--props`, `assets--units`, `md`, `extensions`

2. `new_mechanics` — New Game Systems
   Focus: New attributes, new gameplay features, new AI behaviors
   Domains: `libraries`, `aiscripts`, `md`, `maps`, `localization_mechanics`, `extensions`

3. `economy_trade` — Economy & Trade
   Focus: Ware pricing, production recipes, trade AI, resource flow
   Domains: `libraries`, `aiscripts`, `md`, `assets--structures`, `extensions`

4. `missions` — Mission System
   Focus: Mission logic, subscriptions, rewards, faction goals
   Domains: `md`, `localization_mechanics`, `extensions`

5. `ui` — UI & Interface
   Focus: Menus, HUD, panels, Lua scripts, notifications
   Domains: `ui`, `localization_mechanics`

6. `ship_balance` — Ship Balance
   Focus: Hull, mass, thrust, inertia, drag, crew, storage, engine stats, physics
   Domains: `libraries`, `assets--units`, `assets--props`, `extensions`

7. `dlc` — DLC-Specific
   Focus: Content unique to specific DLCs that doesn't fit other sections. General balance changes (shield stats, weapon stats) that happen to be in DLC files belong in the relevant thematic section, not here.
   Domains: `extensions`

8. `new_content` — New Content
   Focus: New ships, wares, stations, story, characters, missions
   Domains: `*`

9. `bug_fixes` — Bug Fixes
   Focus: Corrected values, fixed logic, resolved issues
   Domains: `*`

10. `miscellaneous` — Miscellaneous
    Focus: Anything not covered by other sections
    Domains: `*`

## Steps

### Step 1: Build progress file

Create the output directory `diff/$V1-$V2/_sections/` and the progress file at `diff/$V1-$V2/_write_progress.md`:
```
# Write Progress: $V1 → $V2

## Sections
- [ ] combat
- [ ] new_mechanics
- [ ] economy_trade
- [ ] missions
- [ ] ui
- [ ] ship_balance
- [ ] dlc
- [ ] new_content
- [ ] bug_fixes
- [ ] miscellaneous
```

### Step 2: Write sections

Process unchecked sections in **user-sized batches**:

1. Count the remaining unchecked sections and report the number remaining, then ask how many to launch next (e.g., "N sections remaining. How many to launch in the next batch?").
2. Take the next N unchecked sections from the progress file
3. Launch all N as background subagents simultaneously
4. Wait for all N to complete
5. If any failed (quota exhaustion, errors), **stop immediately** — do not launch more. Tell the user which sections failed and that they can resume later.
6. If all N succeeded, mark them `[x]` in the progress file, then repeat from step 1

For each unchecked section, determine which result files to include:

1. List all `.md` files in `diff/$V1-$V2/_analysis/` (exclude `_batches/` subdirectory and `_progress.md`)
2. Identify multi-part domains: any domain with `--partN.md` files (e.g., `libraries--part1.md`, `libraries--part2.md`). For these, use ONLY the corresponding `diff/$V1-$V2/_summary/{domain_root}.md` file. Never include individual `--partN.md` files. If a multi-part domain has no file in `_summary/`, stop and tell the user to run `/diff-summarize` first.
3. For single-file domains (no `--partN` suffix), use the file directly from `_analysis/`
4. If the section's domains list is `*`, include all eligible result files (summaries from `_summary/` + single-file domains from `_analysis/`)
5. Otherwise, include only eligible result files whose name (without `.md` extension) starts with one of the section's domain prefixes

Launch an **opus** model subagent with the filtered file list:

```
Read the following analysis result files and synthesize everything relevant to the theme into a single cohesive changelog section.

Theme: {section_label}
Focus: {section_focus}

Files to read:
{list of full result file paths}

Guidelines:
- Read every listed file completely before writing
- Lead with the most impactful changes
- Include specific old → new values for all stat changes
- Aggregate related changes from multiple result files into unified descriptions (e.g., if shields changed in both base game and DLC files, combine them)
- Note cross-cutting themes that span multiple files
- Use ### subsection headers to organize within your theme
- If no changes are relevant to this theme, write only: "No changes."
- NEVER use markdown tables. Use inline bullet lists: `Item: old → new detail | detail | detail`
```

Save each section to `diff/$V1-$V2/_sections/{section_id}.md`.

Mark the section `[x]` in the progress file immediately after saving.

### Step 3: Deduplicate sections

After all sections are marked `[x]`, read every section file from `diff/$V1-$V2/_sections/` and identify items that appear in more than one section (same underlying change described separately). For each duplicate:

1. **Pick the primary section** — the one where the change is most thematically central. Use this priority order when ambiguous: the section whose Focus best matches the change's main impact > the section with the most detailed write-up > the earlier section in the numbered list.
2. **Remove the item entirely from all non-primary sections.** Do not leave cross-references, stubs, or "see other section" notes — just delete it. If removing an item leaves a subsection header with no content, remove the header too.
3. **Save each modified section file** back to `diff/$V1-$V2/_sections/`.

### Step 4: Assemble changelog

Read all section files from `diff/$V1-$V2/_sections/` **in the order listed above** and concatenate them into a single file at `diff-results/diff-$V1-$V2.md`.

Add a header:
```
# X4 Foundations Changelog: $V1 → $V2
```

Only include sections that have actual content — skip any section whose file contains only "No changes."

### Step 5: Cleanup

Delete the progress file and sections directory:
- `diff/$V1-$V2/_write_progress.md`
- `diff/$V1-$V2/_sections/`

Keep `diff/$V1-$V2/_analysis/` and `diff/$V1-$V2/_summary/` — they are needed by `/diff-verify`.

## Formatting Rules

- **Never use markdown tables.** They render poorly on mobile. Use inline bullet lists instead.
- For stat changes, use the format: `Item: old → new detail | detail | detail`
- Example: `Shield (Argon L): 0s → 16s delay | +30–40% capacity | +50–70% rate`
