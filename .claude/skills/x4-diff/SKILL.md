---
name: x4-diff
description: Generate player-facing X4 Foundations release notes for a version pair, interactively, using in-session subagents (no external LLM, no .env). Use when the user wants to diff two extracted X4 versions under x4-data/ into release notes. Invoke as /x4-diff.
---

# /x4-diff — interactive X4 release notes

You are the driver. Run the deterministic Python via bash (stage 1 +
`scripts/interactive_plan.py`); do ALL the writing by dispatching subagents.
No external LLM, no `.env`. Keep the user informed; never silently drop a
section.

## 0. Preconditions
Run from the repo root (the directory holding `scripts/run_rules.py`).
- `command -v python3` — if missing, stop: "This skill needs Python 3 on PATH."
- `ls x4-data/` — list extracted versions. If fewer than two exist, stop and
  point the user at `cat_extract.py --all-folders` to extract one.

## 1. Tag (frozen for the whole run)
These notes are produced by the model YOU are running as. Slugify its id:
`python3 scripts/interactive_plan.py tag "<your model id, e.g. claude-opus-4-8>"`
and read the `tag` field from the JSON output. If you can't determine your
model id, ask the user for a short label, or use `claude-session` — never
guess a model name.

## 2. Ask the user (one pause)
Use AskUserQuestion to collect together:
- **Version pair** — `old → new`, chosen from the `x4-data/` list.
- **Tone** — e.g. "concise patch notes" vs "narrative what's-new recap".
Then build (plain bash):
`PAIR_DIR=artifacts/<old>-<new>-<tag>`, `FINAL=output/<old>-<new>-<tag>.md`,
`BUDGET=30000` — input tokens per LLM call, a *quality knob* not a context cap
(subagents could take more, but detail drops when one call carries too much;
lower toward 20000 if a section reads thin, raise if merges feel choppy).
**Freeze BUDGET for the whole run** (like the tag): a rule's
chunk count depends on it, and the engine refuses to merge two chunkings of
one rule. If you ever must change it for a rule, first delete that rule's
chunk files (`rm -f "$PAIR_DIR"/llm_<rule>*.md`) so it re-chunks cleanly. Keep
`<tone>` for the dispatch prompts below.

## 3. Stage 1 — rules (no LLM)
`python3 scripts/run_rules.py <old> <new> --game-data x4-data --out "$PAIR_DIR"`
Tell the user it's running (~30s). Read `"$PAIR_DIR/summary.json"`; the
**changed categories** are the rules whose `rules.<name>.count > 0`.
- If **zero** categories changed → tell the user "No data changes between
  `<old>` and `<new>`." and STOP. Do not run stages 2–3.
- Otherwise report "N categories changed" and continue.

## 4. Stage 2 — per-category notes (subagents)
Keep a list `PRODUCED` of rules that actually wrote a result file. For each
changed rule:
1. `python3 scripts/interactive_plan.py prep-rule "$PAIR_DIR" <rule> --max-tokens $BUDGET`
2. If `empty_after_diagnostic` is true → say "<rule>: no player-facing
   changes (N diagnostic records)" and skip it (do NOT add to `PRODUCED`).
3. If any chunk has `oversized: true` → first clear that rule's non-compact
   outputs (`rm -f "$PAIR_DIR"/llm_<rule>.md "$PAIR_DIR"/llm_<rule>_chunk*.md`),
   then re-run prep-rule for the rule with `--compact` and use that manifest.
   If a chunk is STILL `oversized`, stop and ask the user (raise budget / skip
   the whole rule via `--allow-missing` at top).
4. For each chunk with `done: false`, dispatch ONE subagent — run them in
   parallel, but **at most 5 concurrent Agent calls per message**. More than
   that risks server-side rate limiting that silently fails the extra
   dispatches. If a rule (or your remaining queue) has more than 5 chunks, send
   them in successive batches of 5, waiting for each batch to return before
   dispatching the next. Give each its
   `prompt_path` and `out_path` and this instruction:
   > Read the prompt file at `<prompt_path>` — it has full instructions and
   > the data. Write ONLY the resulting markdown release notes to `<out_path>`
   > (no preamble, no sign-off, no commentary). Voice/audience: `<tone>`.
   > Then reply with one line: `<out_path> (<byte size of the file>): <one sentence>`.
5. Verify each `out_path` is non-empty (`[ -s "<out_path>" ]`). Any missing
   or empty result → STOP and ask (retry / raise effort or switch model /
   skip this chunk). No silent skips. When a rule's chunks are all present,
   add it to `PRODUCED`.
Stream progress: `weapons ✓  quests ✓ (4 chunks)  …`.

If `PRODUCED` is empty (every changed rule was diagnostic-only) → tell the
user "No player-facing changes (only diagnostic records)." and STOP before
stage 3.

## 5. Stage 3 — merge (subagents, driven by plan-round)
Finalize EACH rule in `PRODUCED` first (as `rule:<name>`), THEN `top`. Order
matters: the engine refuses `top` until every multi-chunk rule is aggregated
AND every changed, non-diagnostic rule produced notes — so a holed or skipped
category hard-stops here rather than silently vanishing. For each scope, loop:
`python3 scripts/interactive_plan.py plan-round <scope> "$PAIR_DIR" --max-tokens $BUDGET`
(add `--out "$FINAL"` for the `top` scope). If you deliberately skipped a rule
earlier, pass `--allow-missing <rule>[,<rule>]` to the `top` call and name
those categories as omitted in the final summary.
- `phase:"done"` → that scope is finished; move on.
- otherwise → dispatch ONE subagent per entry in `tasks` (parallel, but **at
  most 5 concurrent Agent calls per message** — batch in fives if there are
  more, waiting for each batch before the next), each:
  > Read the merge prompt at `<prompt_path>` and write ONLY the merged
  > markdown to `<out_path>` (no preamble/commentary). Reply one line:
  > `<out_path> (<byte size of the file>)`.
  Verify each `out_path` is non-empty (same failure pause as stage 2), then
  call `plan-round` again. Repeat until `done`.
If a round returns `fallback:true`, that scope was merged in one oversized
pass (detail may be truncated) — note it for the end summary so the user knows
to skim or re-run that section at a smaller BUDGET.

## 6. Finish
When `top` is `done`, the notes are at `$FINAL` — the coverage gate guarantees
every changed, non-diagnostic category is in it (minus any you passed to
`--allow-missing`, which you must name here as omitted). Tell the user, name
any `fallback` sections, and ask them to read it. `$PAIR_DIR` can stay (a
re-run resumes from it) or be deleted.

## Rules
- Dispatch subagents with NO model override — they inherit the user's active
  model and effort. You may recommend a change; never make it.
- Every output file is verified non-empty before you advance. A 0-byte or
  missing file is always stop-and-ask.
- `interactive_plan.py` prints one JSON object on stdout. If a call exits
  non-zero or stdout won't parse as JSON, stop and show the user its stderr.
