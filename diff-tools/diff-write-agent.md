# Write Version Diff Changelog

Synthesize domain analysis results into a single thematic changelog using subagents per section.

**Important:** Always use the provided shell scripts in `diff-tools/` for workflow operations. Do not write ad-hoc bash commands or loops to replace script functionality. If a needed capability is missing, suggest creating a new script rather than improvising.

## Version Selection

Read `diff/_versions_to_compare.md`. Each non-empty line is a version pair in the format `{OLD}-{NEW}` (e.g., `8.00H4-9.00B1`). Parse `$V1` (old) and `$V2` (new) from each line. If the file does not exist, tell the user to run `/diff-analyze` first.

## Multi-Version Iteration

The prepare script (Step 1) handles all pending pairs at once, skipping completed pairs and those missing prerequisites (analyze/summarize). After preparation, process each remaining pair's tasks sequentially (Step 2). After completing a pair's sections and deduplication (Step 3), run the finish script (Steps 4–5) and move to the next pair. When all pairs are done, report completion.

## Prerequisites

The following must exist:
- `diff/$V1-$V2/_analysis/*.md` — individual domain analysis files (produced by `/diff-analyze`)
- `diff/$V1-$V2/_summary/*.md` — consolidated summaries for multi-part domains (produced by `/diff-summarize`)

**Before launching any subagents**, prompt the user: "This workflow uses background agents that need write access. Please make sure edit mode is on (press Enter to continue)." Use AskUserQuestion and wait for their response before proceeding. Background agents cannot prompt for permissions interactively — if writes are not pre-approved, they will silently fail.

## Shell Scripts

Shell scripts in `diff-tools/` handle all workflow operations:
- `bash diff-tools/write-prepare.sh` — Step 1 for all pending pairs (check prerequisites, init tasks). Reports total remaining tasks.
- `bash diff-tools/task-next.sh write V1 V2` — Get next section as JSON: `{section_id, label, focus, files, remaining, output}`
- `bash diff-tools/task-done.sh write V1 V2 SECTION [OUTPUT]` — Verify output exists + mark section complete, JSON: `{marked, remaining}`
- `bash diff-tools/finish-pair.sh write V1 V2` — Assemble final changelog + cleanup + create completion flag

## Sections

The `next` command returns sections in this order, with pre-filtered file lists:

1. `combat` — Combat System (shields, weapons, missiles, turrets, AI targeting, weapon heat, disruption mechanics)
2. `new_mechanics` — New Game Systems (new attributes, new gameplay features, new AI behaviors)
3. `economy_trade` — Economy & Trade (ware pricing, production recipes, trade AI, resource flow)
4. `missions` — Mission System (mission logic, subscriptions, rewards, faction goals)
5. `ui` — UI & Interface (menus, HUD, panels, Lua scripts, notifications)
6. `ship_balance` — Ship Balance (hull, mass, thrust, inertia, drag, crew, storage, engine stats, physics)
7. `dlc` — DLC-Specific (content unique to specific DLCs that doesn't fit other sections)
8. `new_content` — New Content (new ships, wares, stations, story, characters, missions)
9. `bug_fixes` — Bug Fixes (corrected values, fixed logic, resolved issues)
10. `miscellaneous` — Miscellaneous (anything not covered by other sections)

The script automatically routes result files: multi-part domains use `_summary/` consolidated files, single-file domains use `_analysis/` files directly. Sections with domain `*` receive all eligible files.

## Steps

### Step 1: Prepare all pairs

Run:
```
bash diff-tools/write-prepare.sh
```

This processes all pending version pairs: checks prerequisites (analysis complete, summaries exist for multi-part domains) and initializes task lists. Already-completed pairs are skipped. The output reports total remaining tasks across all pairs.

### Step 2: Write sections

**Batch count is asked once before processing the first version pair.** The prepare script already reported the total remaining tasks. Ask the user once using AskUserQuestion how many to process, offering options like "5", "10", and "All (N)". This count applies across all version pairs — do not ask again between pairs.

Then process tasks **one at a time**, working through each version pair sequentially:

1. Run `bash diff-tools/task-next.sh write $V1 $V2` to get the next section's info
2. If `{"done": true}` for this pair, proceed to Step 3 for this pair, then continue with the next pair's tasks
3. Report which section is being launched and how many remain (e.g., "Launching: combat (N remaining)")
4. Launch one **opus** model background subagent with the Section Writing Prompt below, using `label`, `focus`, and `files` from the JSON output
5. Wait for it to complete
6. **If it failed** (quota exhaustion, errors, unexpected output), **stop immediately**. Report the error and wait for the user
7. If it succeeded, run `bash diff-tools/task-done.sh write $V1 $V2 SECTION_ID OUTPUT` (verifies output exists + marks complete)
8. If the batch limit is reached, stop and report. Otherwise repeat from step 1

#### Section Writing Prompt

```
Read the following analysis result files and synthesize everything relevant to the theme into a single cohesive changelog section.

Theme: {label}
Focus: {focus}

Files to read:
{files, one per line}

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

### Step 3: Deduplicate sections

After all sections are marked `[x]`, read every section file from `diff/$V1-$V2/_sections/` and identify items that appear in more than one section (same underlying change described separately). For each duplicate:

1. **Pick the primary section** — the one where the change is most thematically central. Use this priority order when ambiguous: the section whose Focus best matches the change's main impact > the section with the most detailed write-up > the earlier section in the numbered list.
2. **Remove the item entirely from all non-primary sections.** Do not leave cross-references, stubs, or "see other section" notes — just delete it. If removing an item leaves a subsection header with no content, remove the header too.
3. **Save each modified section file** back to `diff/$V1-$V2/_sections/`.

### Steps 4–5: Assemble and cleanup

After deduplication, run:
```
bash diff-tools/finish-pair.sh write $V1 $V2
```

This assembles all section files into the final changelog at `diff-results/diff-$V1-$V2.md` (skipping any containing only "No changes."), cleans up the progress file and sections directory, and creates the completion flag. The `_analysis/` and `_summary/` directories are preserved. Proceed to the next uncompleted version pair, or report that all pairs are complete.

## Formatting Rules

- **Never use markdown tables.** They render poorly on mobile. Use inline bullet lists instead.
- For stat changes, use the format: `Item: old → new detail | detail | detail`
- Example: `Shield (Argon L): 0s → 16s delay | +30–40% capacity | +50–70% rate`
