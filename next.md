# Next steps — 2026-04-18 (all gates + all 18 rules done)

## Status

**Plan complete — Gates 0a through 0e + Waves 1 through 4.**

- **Full suite: 458/458 pass, 6 intentional skips.**
- **20 rules on disk** (2 pre-existing shields/missiles + 18 new across 4 waves).
- **20 Tier B snapshots committed** under `tests/snapshots/`.
- **Git HEAD still `10af54d refactor`** — nothing committed today.

## Rules summary

| Wave | Rule | LOC (rule.py) | Tests | Snapshot lines |
|---|---|---:|---:|---:|
| 1 | engines | 288 | 9 | 1 |
| 1 | weapons | — | 10 | 185 |
| 1 | turrets | — | 11 | 224 |
| 1 | equipment | — | 12 | 4 |
| 1 | wares | 241 | 10 | 28 |
| 2 | ships | — | 22 | 202 |
| 2 | storage | 230 | 9 | 12 |
| 2 | sectors | — | 25 | 203 |
| 3 | factions | 841 | 14 | 1 |
| 3 | stations | 930 | 28 | 179 |
| 3 | jobs | 365 | 11 | 213 |
| 3 | loadouts | 660 | 14 | 69 |
| 3 | gamestarts | — | 9 | 22 |
| 3 | unlocks | — | 13 | 0 |
| 3 | drops | — | 16 | 15 |
| 3 | cosmetics | — | 18 | 0 |
| 4 | quests | — | 18 | 133 |
| 4 | gamelogic | 855 | 23 | 148 |

Plus the pre-existing `shields` (3 tests) and `missiles` (4 tests) and the `_wave1_common.py` helper (15 tests).

## Infrastructure added

- `src/lib/file_level.py` — `diff_files` + size-bounded `render_modified`.
- `src/lib/canonical_xml.py` — deterministic XML serializer (Wave 4 needed this).
- `src/rules/_wave1_common.py` — ownership predicate + `diff_productions` + equipment reverse-index.
- `tests/_realdata.py` — corpus detection + loud-skip helper.
- `tests/realdata_allowlist.py` — with entries for wares/turrets/ships/factions/jobs/gamestarts/gamelogic diagnostic sentinels (each justified).

## Deferred items for engineer review

- **Oracle tests in `test_realdata_helpers.py`** (3 skip-stubs) — still need real-data op lookup for replace/add-after/remove-silent verification.
- **`test_at_least_one_entity_source_changed`** (1 skip-stub) — provenance attribution investigation.
- **`test_helper_failures_within_allowlist`** (1 skip-stub) — cross-rule allowlist triage.
- **Old `tests/TEST-1.00/` + `tests/TEST-2.00/`** untracked dirs remain (sandbox denied deletion). Safe to `git clean -fd` or `rm -rf` manually.
- **Quests prefix inventory** — 13 prefixes appear >5 times but aren't in the classification mapping (`rml`, `tutorial`, `npc`, `setup`, `lib`, `x4ep1`, `cm`, `gmc`, `factiongoal`, `factionsubgoal`, `terraforming`, `inituniverse`, `cinematiccamera`). Rows classify as empty; engineer may want to extend the mapping in a follow-up.
- **Stations modulegroup dangling refs** — 178/178 `select @macro` values resolve to on-disk macro files rather than module library ids; aggregate warning per owner instead of per-ref.

## Real-data allowlist entries

Each entry justifies a diagnostic sentinel that represents known cross-DLC conflicts (mostly `closedloop` if-gate RAW collisions between terran/timelines/split DLCs) — none contaminate actual rule entity rows. Entries are keyed by `('diagnostic', <tag>, <stable-hash>)` so they survive snapshot rewrites.

## State of the repo

- `src/lib/`: 11 modules (rule_output, cache, locale, paths, macro_diff, check_incomplete, entity_diff, file_level, canonical_xml, xml_utils, plus the implicit `__init__.py`).
- `src/rules/`: 21 modules (20 rules + `_wave1_common.py`).
- `tests/`: ~45 test files including `_realdata.py`, `realdata_allowlist.py`, and per-rule unit + realdata tests.
- `tests/fixtures/`: per-rule fixture trees + shared `_locale`/`_paths`/`_entity_diff_golden`/`_diff_library_real`/`_file_level`.
- `tests/snapshots/`: 20 snapshots committed.

## Next: final project gate

Per plan — the Final Project Gate at the end of the spec. Items to check:
- Full suite green under `X4_REALDATA_FULL=1` (more canonical pairs).
- All rules' `.md` docs present and up-to-date.
- Plan itself revisited for any outstanding TODO checkboxes.
- Commit decisions: user hasn't committed anything today; the entire codebase is ready to be grouped into reviewable commits (suggested grouping per plan: one commit per gate/wave or one per rule).

## Reminders

- User doesn't auto-commit; ask before any git operation.
- Every defensive piece added was backed by real-data evidence — keep that discipline.
- Subagent prompts pinned every decision; engineers stayed in scope. Keep doing this.
- All 18 new rules used `_wave1_common` for ownership / `forward_incomplete_many` for multi-subsource / per-fixture 9-case coverage. The pattern works.
