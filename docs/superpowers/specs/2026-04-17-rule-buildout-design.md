# X4 Changelog — Rule Buildout Design

Dated: 2026-04-17. Author: hunter.

## Summary

Add 18 new rules to the X4 changelog pipeline, taking rule coverage from 2 → 20 tags. Introduce shared helpers (`rule_output`, `entity_diff`, `file_level`, `macro_diff`) so most rules shrink to configuration. Maintain the existing rule interface and testing bar established by `shields` and `missiles`. Out of scope: grouping, LLM commentary, assembly (already tracked in `next.md`).

## Goal

Produce a changelog tool that, when run against two X4 versions, surfaces every player-facing change across ships, weapons, quests, factions, stations, economy, and script logic. "No silent changes" means: the 18 new rules do not drop data without a loud signal (incomplete marker, warning, or truncation flag). The existing `shields` and `missiles` rules are explicitly out of scope for migration, so any pre-existing coverage gaps in them (e.g., shields deriving slot type from component files but only scanning changed shield macro paths) remain until a future migration. Each new rule ships production-ready (module + sibling `.md` + tests).

## Prerequisites

The validation story depends on a local `x4-data/` tree containing extracted X4 game versions. The repo does NOT commit this corpus — it's too large, and extraction is user-managed.

Expected layout:
- `x4-data/8.00H4/` — baseline full game dump (core + all DLCs extracted).
- `x4-data/9.00B1/` through `x4-data/9.00B6/` — beta dumps.

The spec assumes these versions are present. Real-data tests (`tests/test_realdata_helpers.py` and `tests/test_realdata_<rule>.py`) auto-detect `x4-data/<version>/` presence and skip with a loud printed reason when a referenced version is missing. No env var required. Unit tests never depend on the corpus. Full-matrix runs (all consecutive pairs, not just canonical) are opt-in via `X4_REALDATA_FULL=1`; default dev iteration uses 8.00H4→9.00B6 only.

If a new X4 version ships and the dev extracts it into `x4-data/<new>/`, all rule `.md` docs list which version-pair the Tier B snapshot applies to; adding a new version doesn't invalidate existing snapshots.

## Architecture recap

Existing:
- `src/change_map.py` — file-level add/modify/delete index.
- `src/rules/` — one module per domain. Each emits tagged `RuleOutput`s. Each has a sibling `.md`.
- `src/lib/{locale, paths, xml_utils}.py` — shared primitives.
- `src/rules/shields.py`, `src/rules/missiles.py` — two reference rules.

Not built (still covered by `next.md`, out of scope here): grouping, LLM commentary, pipeline assembly.

Changes in this spec:
- Four new modules in `src/lib/`.
- 18 new modules in `src/rules/` (and their `.md` + tests).
- `shields.py`/`missiles.py` left untouched; opportunistic migration to shared helpers only if a later round finds it clean.

## Shared library additions

All under `src/lib/`.

### `rule_output.py`
Hoist the `RuleOutput` dataclass (`tag`, `text`, `extras: dict`) out of each rule module into one shared type. `shields.py` and `missiles.py` keep their local redefinitions for now; new rules import from here.

### `locale.py` additions
Extend the existing `Locale` class + `display_name` helper:
- New function `resolve_attr_ref(elem, locale, attr='name', fallback=None) -> str`: parses `{page,id}` out of any attribute (`<ware name="{20201,5}">`, `<faction name="{20203,801}">`, etc.). Resolves via locale, strips author hints, applies fallback on miss (default: raw attr value).
- `display_name` stays as the macro-specific convenience (wraps `resolve_attr_ref(elem.find('properties/identification'), ...)`).
- Locale loading: discovers all `extensions/*/t/0001-l044.xml` files via glob and merges them onto core `t/0001-l044.xml`. Merge order is deterministic (alphabetical DLC name). DLC entries override core on same `(page, id)`. **Collisions are surfaced** via `locale.collisions: list[tuple[str, dict]]` — the same `(text, extras)` shape `DiffReport.warnings` uses, so `forward_warnings` consumes both without a translator. Each entry: `(text="locale collision page=X id=Y", extras={"page": X, "id": Y, "core_text": ..., "dlc_text": ..., "dlc_name": ...})`. Alphabetical merge is a stability heuristic, not X4's real load order. In 9.00B6 only Ventures ships a locale dir, so collisions are hypothetical today; the warning channel catches the case if future DLCs add locale files.

New rules use `resolve_attr_ref` instead of re-implementing `{page,id}` parsing. Factoring this now avoids 15+ copies of the same 5-line fallback dance.

### `paths.py` additions
Extend the existing `source_of` helper:
- New function `resolve_macro_path(root, pkg_root, macro_ref, kind) -> Optional[Path]`: discovers the on-disk path for a `<component ref="...">` across X4's inconsistent casing and subdirectory layout. **Disk-driven discovery, cached index — no hardcoded family list, no per-call recursive scan.**
- **Lookup precedence**: `pkg_root` (the extension directory containing the referring file) first, then core.
- A DLC-local macro with the same ref as a core macro is a **legitimate override** — the game uses the extension's version when that extension is active. The helper returns the extension's macro silently (matches runtime).
- **Cross-extension override ambiguity** (conservative stub): when multiple extensions define the same macro ref with *differing contents* and the referring file doesn't belong to any of them (e.g., core asks for ref X, both boron and terran define X differently), the helper picks `pkg_root`'s version (core in this case) and emits a warning via `forward_warnings` naming the competing packages. Not a hard failure — a full effective-macro-overlay index across all extensions is its own `entity_diff`-scale problem, deferred until real data shows this ambiguity is common enough to warrant the engineering. The warning is the loud signal that catches the case today.
- **Cached macro index**: on first lookup for a given `(root, kind)` pair, the helper does one recursive walk and builds a `{macro_ref: path}` index, then answers subsequent lookups as dict reads. Avoids O(lookups × files) cost during Tier A runs across many version-pairs × rules.
- Asset-kind roots the helper knows: `engines` → `assets/props/Engines`, `weapons`/`turrets` → `assets/props/WeaponSystems`, `shields` → `assets/props/SurfaceElements`, `storage` → `assets/props/StorageModules`, `ships` → `assets/units/size_*`, `bullet` → `assets/fx/weaponfx` (case-insensitive: core is `weaponFx`, DLCs use `weaponfx`).
- Any family subdirectory present on disk is discovered by the walk — including `capital`, `dumbfire`, `energy`, `guided`, `heavy`, `mines`, `mining`, `missile`, `spacesuit`, `standard`, `torpedo` (actual weapon families in 9.00B6) plus any future additions. No rule hardcodes a path; disk is the source of truth.

### `entity_diff.py`
A real X4 patch evaluator. Materializes the effective XML tree for each version (core + all DLC contributions) then diffs entities across versions.

Division of labor: `change_map.py` stays file-level (step 1 — which files changed). `entity_diff.py` is responsible for content-level entity state: applying DLC contributions, tracking which files contributed each change.

**Two DLC contribution shapes** (helper handles both):
- **Patch wrapper**: DLC file root is `<diff>`; children are `<add>`/`<replace>`/`<remove>` ops targeting the core tree via XPath. Covers 109 DLC library files in 9.00B6.
- **Native fragment**: DLC file root matches the core file's root element (e.g., core `<plans>` + DLC `<plans>`, core `<loadouts>` + DLC `<loadouts>`, core `<groups>` + DLC `<groups>`, core `<ships>` + DLC `<ships>`, core `<regions>` + DLC `<regions>`). The DLC's children are merged into the core tree additively — treat every top-level child as an implicit `<add sel="/<root>">child</add>`. If the DLC file has the same id/name as a core entity, that's a native-fragment write-write collision (record as failure; choose one deterministically by alphabetical order as the effective state, alert the dev).

Files that may appear in either shape: `constructionplans.xml` (fragment in boron/mini_01, diff elsewhere), `loadouts.xml` (fragment in boron/mini_01), `modulegroups.xml` (fragment), `ships.xml` (fragment), `region_definitions.xml` (fragment). The helper inspects each DLC file's root element on load and picks the right code path per file. Both shapes feed the same write-set model.

```python
from typing import Callable, Hashable
from dataclasses import dataclass, field

@dataclass
class DiffReport:
    added:    list[tuple[Hashable, ET.Element, list[str]]]                              # (key, new_entity, new_source_files)
    removed:  list[tuple[Hashable, ET.Element, list[str]]]                              # (key, old_entity, old_source_files)
    modified: list[tuple[Hashable, ET.Element, ET.Element, list[str], list[str]]]       # (key, old, new, old_source_files, new_source_files)
    warnings: list[tuple[str, dict]] = field(default_factory=list)  # soft — odd but recovered
    failures: list[tuple[str, dict]] = field(default_factory=list)  # hard — patch could not be applied; downstream results may be wrong

    @property
    def incomplete(self) -> bool:
        return bool(self.failures)

def diff_library(
    old_root: Path,
    new_root: Path,
    file_rel: str,                                          # e.g. 'libraries/jobs.xml'
    entity_xpath: str,                                      # './/job'
    key_fn: Callable[[ET.Element], Hashable] = lambda e: e.get('id'),
    include_dlc: bool = True,
) -> DiffReport
```

`key_fn` lets rules use composite keys (loadouts, cosmetics equipmods, gamelogic behaviours/scriptproperties all need composites).

Supported XPath subset — grammar derived empirically from the Gate 0b.1 inventory across 8.00H4 + 9.00B1..B6 (grep-verified, not guessed):
- Path steps: `/tag`, `//tag`, chains of both.
- Predicates: `[@attr='value']`, multiple predicates stacked `[@a='b'][@c='d']`, absence predicates `[not(child_tag)]` (real usage: `job[@id='X']/location[not(factions)]` in terran/jobs.xml), and **literal integer positional predicates** `[N]` (63 real uses, all `append_to_list[@name='X'][1]` in mdscript cues — picks the Nth matching sibling, 1-indexed).
- Attribute terminator: `path/@attr` (targets an attribute, not an element).
- Predicate and `if=` functions: `not(XPATH)` plus bare XPath (truthy iff element matches).
- Not supported: `comment()`, other xpath functions, arithmetic predicates, non-literal positional predicates (e.g., `[position()>1]`, `[last()]`) — none appear in live diff ops in the inventory.

Supported diff operations:
- `<add sel="XPATH">children</add>` — by default, append children to the element matched by XPath.
  - `pos="prepend"` (70 real uses per inventory): insert children at the start of the matched parent's children.
  - `pos="after"` (603 real uses): `sel` points to a **sibling anchor**; inserted children become siblings positioned immediately after the anchor.
  - `pos="before"` (112 real uses): symmetric to `after` — `sel` points to a sibling anchor; inserted children become siblings positioned immediately before the anchor.
- `<replace sel="XPATH">node</replace>` — replace the element or attribute at XPath. If `sel` ends in `/@attr`, the patch body is the new attribute value (text); otherwise it's a new element replacing the old.
- `<remove sel="XPATH"/>` — remove the element or attribute at XPath. Accepts `silent="true"` or `silent="1"` (both variants exist in real data — `boron/factions.xml` uses `silent="1"`, `terran/gamestarts.xml` uses `silent="true"`). Silent suppresses "target not found" warnings, matching X4 runtime.
- `if="XPATH"` on any op gates evaluation: patch applies only when the XPath is truthy against the in-progress effective tree.

**Write-set model for conflict classification.** Each applied patch contributes to a "write set" keyed by its effective target. Not every overlap is a failure — X4 has whole classes of multi-DLC overlap that are ordinary. Three severity tiers:

*FAILURE (hard — run marked incomplete):*
- Two `<replace>` (or one `<replace>`+one `<remove>`) on the same element xpath or the same `(element_xpath, attr_name)` pair, WITH different replacement bodies. Same body = deduplicate, no failure.
- Two `<remove>` on the same target: deduplicate, not a failure.
- Two `<add>` whose resulting children collide by id/name uniqueness (both adding `<ware id="X">` under the same parent): failure.
- **Subtree invalidation**: `<remove sel="E">` and element-level `<replace sel="E">` (not attr-level) conflict with any other DLC's op targeting E OR any descendant of E (attributes, children, nested descendants). Catches cases like DLC A `<replace sel="//ware[@id='X']/@price">` vs DLC B `<remove sel="//ware[@id='X']"/>`: order determines whether A's attribute change persists or vanishes along with the ware. Same-subtree conflicts are failures; without authoritative load order we can't resolve them.

*WARNING (soft — applied in alphabetical DLC order, flagged in `warnings`):*
- Two `<add pos="after">` anchored to the same sibling from different DLCs. X4's real runtime resolves order via `content.xml` dependencies; without that metadata, the final sibling order is alphabetically-picked and flagged. The *set* of inserted children is fully preserved — only their relative order is indeterminate. Real data hits this regularly (Split/Terran/Boron/Mini01/Mini02/Timelines all `<add pos="after">` under `custom_budgeted/budget/story[@ref='story_paranid_esc_3']` in gamestarts.xml), so treating it as a hard failure would block normal full-DLC runs.
- Two `<add pos="prepend">` from different DLCs under the same parent. Same rationale — set preserved, order picked alphabetically.
- Two plain `<add>` (no `pos`) under the same parent with non-colliding children. Treated as commutative (no warning) — X4's rules in scope iterate children by id/attribute match, not by document order. If a future rule discovers a strict-order dependency, treat that as a surprise and upgrade the helper then rather than pre-bake an API knob nobody uses.
*FAILURE (additional case — semantic, not just structural):*
- **Read-after-write dependency via `if=`**: a later DLC's `if=` XPath reads state a prior DLC wrote. Example: `<add if="not(//faction[@id='terran'])">` is order-sensitive when another DLC adds a terran faction. Unlike pure positional overlaps (where the *set* of inserted children is preserved and only sibling order changes), an `if=` RAW dependency can change whether the patch applies at all — producing materially different effective trees. Detection: for each patch's `if=` condition and each `sel` XPath's predicates, extract read paths. If any read path intersects another DLC's write set (by xpath scope, not just exact equality), flag as failure. Without authoritative load order we cannot pick the right interpretation; incompleteness is the honest answer.

The distinction matters: FAILURES mean the effective tree content may be wrong in ways that affect what the changelog reports. WARNINGS mean the tree's content is stable-set but document order of siblings is alphabetically-picked. Positional overlaps → warnings (content preserved). `if=` RAW → failures (content may differ). Write-write on same target → failures (content contradictory).

Multi-DLC load order and conflict detection:

X4's real load order is driven by each DLC's `content.xml` dependency graph. That file is NOT present in the extracted data we consume — extraction strips it. We can't build a real topological sort from what we have.

Behavior:
- For iteration stability, extensions are discovered via `extensions/*/` glob and sorted alphabetically by directory name. No hardcoded DLC list — new DLCs (e.g., `ego_dlc_mini_02`) are picked up automatically. This iteration order is NOT treated as authoritative; see the Write-set model tiers above.
- The "Write-set model for conflict classification" section above defines exactly what triggers a failure, what triggers a warning, and what's a non-conflict. Critical design choice:
  - **Positional overlaps** (`pos="after"` same anchor, `pos="prepend"` same parent) → WARNING. The *set* of inserted children is preserved; only sibling order is alphabetically-picked. Canonical X4 data hits this often (`custom_budgeted/budget/story[@ref='story_paranid_esc_3']` is anchor-patched by six DLCs), so failing would block entire rules.
  - **`if=` RAW dependencies** → FAILURE. These can change whether a patch applies at all, producing materially different effective-tree content. Different from positional overlaps, where content is stable.
  - **Write-write on same target** (differing bodies) → FAILURE.
- Read-after-write (RAW) detection (conservative v1 algorithm). Three classes of reads are tracked, not just `if=`:
  1. **`if=` conditions**: XPath expressions that gate whether a patch applies at all. Example: `<add ... if="not(//faction[@id='terran'])">`.
  2. **`sel` target xpath**: the base selector reads the tree at the target path. If another DLC writes to that path (or adds/removes it), order determines whether the target exists or matches. Example: DLC A `<replace sel="//ware[@id='X']/@price">` depends on ware X existing; if DLC B adds or removes ware X, order matters.
  3. **`pos="after"` anchor**: the sibling anchor is read. If another DLC writes to the anchor's existence (adds/removes it), order determines whether the insertion lands correctly.

  Algorithm:
  1. Compute each patch op's write set (see tiers above) AND read set (union of the three classes above, as xpath targets).
  2. For each op's read set, check whether any OTHER DLC's write set intersects at the same target or within the target's subtree (write to E or any descendant of E counts as a write on E for read-intersection purposes).
  3. If yes → FAILURE for the entity involved.

  This is deliberately conservative — it over-flags rather than under-flags. Over-flags land in the reviewed allowlist (`tests/realdata_allowlist.py`); under-flags silently produce wrong trees. Real data in 9.00B6 shows these cases are rare enough that allowlist maintenance stays tractable.
- Upgrade path: if a future extraction pipeline preserves `content.xml`, replace alphabetical iteration with real topological sort. At that point, positional warnings can be auto-resolved (true load order known) and `if=` RAW cases become deterministic; only genuine write-write collisions remain as failures.

Provenance (per-version, tracked separately for modified entities):
- For each entity, the helper tracks per-version the list of files that contributed: `old_source_files`, `new_source_files` (file paths) and `old_sources`, `new_sources` (short DLC-name labels).
- Rules forward these into `extras.old_source_files` / `extras.new_source_files` / `extras.old_sources` / `extras.new_sources`. For added/removed entities only one side exists; use `extras.source_files` / `extras.sources`.
- Text rendering follows the Canonical rule-output schema section — contributor sets joined with `+`, `→` separates old/new when they differ. No singular "winning DLC" label (alphabetical "last" is arbitrary and misleading).
- **Per-reference provenance** for downstream macro resolution: when an entity has a child element that references a macro (`<component ref="...">`, `<bullet class="...">`, etc.), the helper records which contributor file most-recently wrote the reference's value. `extras.ref_sources: dict[str, str]` maps reference attribute paths (e.g., `"component/@ref"`) to the pkg short-name that owns the current value (`"core"` or a DLC name). `resolve_macro_path(..., pkg_root=...)` uses this to pick the right package directory first: if the `<component ref>` was last written by the boron DLC, macro resolution tries `extensions/ego_dlc_boron/...` before core. Contributor sets alone wouldn't answer this — they say who touched the entity, not who owns each specific field.

Note on path scope: `diff_library` accepts any relative path — `libraries/wares.xml`, `maps/xu_ep2_universe/galaxy.xml`, etc. The "library" in the name is conventional, not a limit. Galaxy.xml in particular is `<diff>`-wrapped in DLCs and routes through the same patch engine as library files.

Soft warnings (patch applied correctly despite oddity):
- `<remove silent="true">` whose target doesn't exist → `warnings` entry (X4 runtime would do the same).

Hard failures (patch could NOT be applied correctly):
- XPath selector outside the supported subset.
- `if=` condition unparseable.
- `sel` points at nothing without `silent="true"`.
- Unknown diff op wrapper.
- Multi-DLC conflict on the same target (see above).

Contract for rule authors: if `report.incomplete` is True after calling `diff_library`, the rule MUST:
1. Emit at least one sentinel `RuleOutput` with `extras.incomplete=True` and `extras.failures=[...]`.
2. **Flag every normal output derived from that same sub-report with `extras.incomplete=True`**, not just the sentinel. A broken effective-tree means any row derived from it is potentially wrong — marking only the sentinel lets contaminated rows slip through snapshot tests as valid. The `forward_incomplete` / `forward_incomplete_many` helpers do this propagation automatically when rules use the canonical pattern (rule passes all its sub-reports to the helper; helper returns the sentinel plus a map of contaminated entity keys, and the rule applies `extras.incomplete=True` to outputs matching those keys).

Minimal enforcement now (in-scope): a tiny helper `src/lib/check_incomplete.py` exposing:

- `assert_complete(outputs: Iterable[RuleOutput])` — raises `IncompleteRunError` if any output has `extras.incomplete=True`. Used by tests and by the later pipeline runner.
- `forward_incomplete(report: DiffReport, outputs: list[RuleOutput], tag: str, subsource: str | None = None) -> None` — in-place helper. If `report.incomplete`:
  1. Mutates every existing output in `outputs` whose `extras.entity_key` was touched by a failed patch (the helper correlates via `report.failures[].extras.affected_keys`) to add `extras.incomplete=True`.
  2. Appends one sentinel `RuleOutput(tag=tag, text=f'[{tag}] RULE INCOMPLETE: {len(report.failures)} patch failures{maybe subsource}', extras={'incomplete': True, 'failures': [...], 'subsource': subsource})`.
  3. No-op if the report is complete.

  Single-report rules use it this way:
  ```python
  outputs = [...]  # regular outputs
  forward_incomplete(report, outputs, tag='<ruletag>')
  return outputs
  ```
- `forward_incomplete_many(reports: Iterable[tuple[DiffReport, str]], outputs: list[RuleOutput], tag: str) -> None` — for multi-sub-source rules. Takes `(report, subsource_label)` pairs; iterates `forward_incomplete` on each, using the subsource label to scope contaminated-output marking (only outputs whose `extras.subsource` matches the failing report get marked). Example:
  ```python
  outputs = [...]
  forward_incomplete_many(
      [(station_report, 'station'),
       (modulegroup_report, 'modulegroup'),
       (constructionplan_report, 'constructionplan')],
      outputs, tag='stations',
  )
  return outputs
  ```

Both helpers mutate `outputs` in place — rule authors don't have to remember to also mark contaminated rows by hand. Tests assert: when a synthetic report has failures, every affected entity in the rule's output has `extras.incomplete=True`, not just the sentinel.

The patch engine populates `DiffReport.failures` entries with `extras.affected_keys: list[Hashable]` listing the entity keys whose effective state is untrustworthy because of that failure. The helper uses this to correlate failures to outputs. For failures that can't be narrowed to specific entities (e.g., unparseable XPath), `affected_keys = []` and the helper marks ALL outputs from that sub-report as incomplete.

### Warning forwarding (same "no silent changes" contract for soft signals)

`check_incomplete.py` also exposes `forward_warnings(warnings: Iterable[tuple[str, dict]], outputs: list[RuleOutput], tag: str) -> None`. It appends one RuleOutput per warning with `extras.warning=True` and the warning's text/extras preserved. Rules that call `diff_library` forward both `report.failures` (via `forward_incomplete`) AND `report.warnings` (via `forward_warnings`) — `DiffReport.warnings` collected by the patch engine (positional overlaps, `silent="1"` misses) appear in the rule's output stream instead of dying inside the helper. Locale collisions surface the same way: `locale.collisions` is a separate channel, and the rule's boot pulls them through `forward_warnings(locale.collisions, outputs, tag='<ruletag>')` at run start. Without this, warnings recorded internally never make it to the changelog — violating the "bounded, never silent" guarantee.

Pipeline contract (applies when grouping/commentary/assembly land): the assembly stage reads the **reviewed allowlist** (`tests/realdata_allowlist.py`, also consumed at runtime) and classifies each `extras.incomplete=True` output:
- **Allowlisted** → rendered in the final changelog under a dedicated "Known Incomplete Entries" section with the allowlist's justification. Does NOT block emission.
- **Not allowlisted** → hard-blocks emission (the historical `assert_complete` behavior) unless `--allow-incomplete` is passed.

This aligns test-time and runtime semantics: the allowlist is the single source of truth for "this incomplete marker is known and acceptable". Keeping the spec and runtime contracts unified avoids the earlier trap where a rule could pass tests (allowlisted) but still block production emission.

This is how "no silent changes" is enforced end-to-end: warnings annotate the output stream; unreviewed failures hard-block; reviewed failures render with justification.

Rough line budget: XPath subset evaluator ~150 LOC, patch application ~100 LOC, entity materialization + diff ~80 LOC, provenance + warnings ~50 LOC, tests ~250 LOC. Wave 0 gets correspondingly bigger; called out in Implementation order.

### `file_level.py`
Enumerate files matching a glob in both trees, emit per-file changes.

```python
def diff_files(
    old_root: Path,
    new_root: Path,
    globs: list[str],              # e.g. ['md/*.xml', 'extensions/*/md/*.xml']
) -> list[tuple[
    str,                           # rel path
    ChangeKind,                    # added/modified/removed
    Optional[bytes],               # old file bytes (None if added)
    Optional[bytes],               # new file bytes (None if removed)
]]
```

Returns raw bytes, not parsed trees. Rationale: file-level output convention requires a full unified text diff in `extras.diff`, which needs the original file text (comments, whitespace, and all). `xml.etree.ElementTree` drops comments and normalizes whitespace on parse — returning `ET.Element` would force the caller to re-read the file. If a rule needs the parsed tree too (e.g., to pull `<mdscript @name>` as display name), it calls `ET.fromstring(bytes)` itself. Bytes + optional parse = no data loss.

### `macro_diff.py`
A stat-diff helper for the `(xpath, attr, label)` field-spec pattern already used in `missiles.py`.

```python
def diff_attrs(
    old: ET.Element,
    new: ET.Element,
    field_spec: list[tuple[str, str, str]],   # (element xpath, attr, label)
) -> dict[str, tuple[Optional[str], Optional[str]]]:
    # returns {label: (old_val, new_val)} only for changed attrs
```

## File-level modification output (quests, gamelogic aiscripts)

Principle 2 of the project is "low data loss — bounded, never silent". File-level rules must not reduce a modified file to just its size delta, or the LLM commentary stage has nothing to work with.

Convention for modified file-level outputs:
- `text`: terse — `[<tag>] <filename>: modified (+A/-B lines)`.
- `extras.diff`: full unified diff (Python `difflib.unified_diff`) between old and new file contents, as a string.
- `extras.path`: rel path.
- `extras.added_lines`, `extras.removed_lines`: counts for quick sorting.

The user sees a condensed line; the LLM receives the actual change text.

Bound the diff by size, not hunk count. Hard cap: **100 KB of diff text OR 5000 lines, whichever hits first**. On truncation:
- `extras.diff_truncated: True`.
- `extras.diff` keeps the first 40 KB of hunks + a `... [N hunks truncated, M total bytes] ...` separator + the last 20 KB of hunks. Middle is dropped.
- `extras.total_added_lines`, `extras.total_removed_lines`: full counts even when truncated.
This actually bounds the downstream LLM context budget; hunk-count caps don't (one huge hunk can still blow the budget).

## Canonical rule-output schema

One schema, used by every new rule, by the multi-sub-source rules (ships, factions, stations, sectors, cosmetics, unlocks, gamelogic), by Tier B snapshots, and by the later grouping/commentary stages. Freezing this up front avoids the churn of rule authors building against divergent versions.

Every `RuleOutput.extras` contains:
- **`entity_key`** (str or tuple): canonical identity. Simple rules: the entity id (`"engine_arg_m_allround_01_mk1"`). Multi-sub-source rules: a composite `(subsource, inner_key)` (e.g., `("module", "prod_bor_medicalsupplies")`). **Diagnostic outputs** (warnings, incomplete sentinels) get a synthetic `entity_key`: `("diagnostic", tag, short_hash_of_message)` — stable across runs so snapshots don't thrash.
- **`kind`** (str): one of `"added"`, `"removed"`, `"modified"`, `"deprecated"`, `"undeprecated"`, `"warning"`, `"incomplete"`. The last two are reserved for diagnostic outputs emitted by `forward_warnings` / `forward_incomplete`. Rules may add domain-specific kinds (stable strings), documented in the rule's `.md`.
- **`subsource`** (str, optional): present on every multi-sub-source rule. The previously inconsistent `subcategory`/`subsource`/`subloader` names all unify to `subsource`. Examples: `"macro"`/`"ware"`/`"role"` (ships), `"faction"`/`"action"` (factions), `"station"`/`"stationgroup"`/`"module"`/`"modulegroup"`/`"constructionplan"` (stations), `"galaxy"`/`"map"`/`"highway"`/`"regionyield"`/`"regiondef"` (sectors), `"paint"`/`"adsign"`/`"equipmod"` (cosmetics), `"discount"`/`"chapter"`/`"info"` (unlocks), `"aiscript"`/`"behaviour"`/`"scriptproperty"` (gamelogic), `"bullet"` (weapons/turrets bullet-macro sub-diff). Diagnostic outputs use `subsource="diagnostic"`.
- **`classifications`** (list[str]): tokens per the Classification policy section.
- **`refs`** (dict[str, list[str] | str], optional): cross-entity references — e.g., `{"group_ref": "shipyard_arg", "plan_refs": ["arg_shipyard_basic"]}` on a station output, or `{"used_by_stations": ["shipyard_arg", "wharf_arg"]}` on a stationgroup output. Lets the future grouping/commentary stage connect a changed constructionplan to affected stations without rebuilding the dependency graph from scratch. Present on rules with meaningful cross-entity relationships (stations, loadouts-to-ships, factions-to-diplomacy); omitted or `{}` otherwise.

**Provenance fields (contributor sets, not singular labels — without authoritative load order, alphabetical "winner" is arbitrary):**

For added entities (new in this version):
- `extras.source_files: list[str]` — files that contributed, sorted for determinism.
- `extras.sources: list[str]` — short labels: `"core"` plus DLC names, deduplicated + alphabetical.

For removed entities (gone in this version):
- Same fields, reflecting old-version state.

For modified entities:
- `extras.old_source_files`, `extras.new_source_files`.
- `extras.old_sources`, `extras.new_sources`.

Text channel rendering:
- Equal contributor sets on both sides: render once as `[src1+src2+…]`.
- Differing sets: `[old_src1+old_src2+…→new_src1+new_src2+…]`.
- Core-only collapses to `[core]`. `"core"` is always explicit when present.

`shields.py` and `missiles.py` predate this schema and don't populate `entity_key`/`kind`/`subsource`. For Tier B regression against shields/missiles, the test file applies a small shim extracting equivalent values from `extras.macro`/`extras.ware_id` + `extras.kind`. The shields/missiles rule code is not modified.

## Subtree-diff child matching

When a rule diffs "the full subtree" of an entity (jobs, factions/diplomacy action, stations/module, gamelogic/behaviour, gamelogic/scriptproperty), repeated children within the subtree need a pairing strategy across old/new versions. Without one, inserting or removing a child cascades false "modified" diffs down the list.

Per-collection strategies, chosen up-front in each rule's `.md`:
- **Keyed** (preferred when a child has a natural id): pair old↔new children by a stable identifying attribute (e.g., `<licence @type>` in factions, `<production @method>` in wares, `<select @ref>` in stationgroups). Modified when signature differs at same key; added/removed by key presence.
- **Multiset** (when children have no natural id): collect old and new child signatures (canonical attribute-tuple repr) into multisets; emit adds for new-side-only, removes for old-side-only, no "modified" for duplicates. Same pattern loadout rules use for duplicate-applicability rules.
- **Positional** (rare — only when order has semantic meaning): pair by index. The rule's `.md` must justify why.
- **Incomplete** (when no safe matcher applies): emit `extras.incomplete=True` for that entity with `extras.failures=[{'reason': 'no_child_matcher', 'subtree': <xpath>}]`. Preserves "no silent changes" — the output surfaces, the dev sees the structure that resisted matching, and can extend the rule or add an allowlist exception.

Each rule's `.md` enumerates every repeated-child collection its subtree diff visits and names the matching strategy. No "just diff everything" hand-waves.

## Classification policy

Every rule populates `extras.classifications: list[str]` — every structural category/type token the rule can extract about the entity. Rules do NOT pre-pick one "most meaningful" label: we cannot know ahead of time which token the LLM commentary stage will need, and losing tokens loses information.

Examples:
- Shield macro with connection `tags="advanced component medium shield unhittable"` → classifications `["advanced", "medium", "unhittable"]`. Generic structural tokens (`component`, `shield`) are filtered as noise.
- Ware `<ware group="food" tags="economy stationbuilding">` → classifications `["food", "economy", "stationbuilding"]`.
- Ship macro with `@class="ship_m"` and `<ship type="fighter">` → classifications `["ship_m", "fighter"]`.

What's filtered out: tokens that apply to every entity of the domain (e.g., `shield` on every shield connection). Each rule's `.md` doc names its generic-token filter set explicitly, so the filter is reviewable.

Text rendering: the rule joins classifications with `, ` inside parentheses in the user-facing text. For a ship classified `["ship_m", "fighter"]`:

```
[ships] Nemesis (ship_m, fighter) [core]: hull 26000→28000
```

If a rule's classification list is empty, parens are omitted. If a rule routinely emits >4 classifications, its `.md` doc justifies the verbosity.

Shields and missiles predate this policy and still emit a single classification token. Migrating them is out of scope here; see the Out of scope section.

## Rule catalogue (18 new)

All rules implement the existing interface:

```python
def run(old_root: Path, new_root: Path, changes: list[FileChange]) -> list[RuleOutput]
```

Rules that don't use `changes` still accept it (uniform interface, same as `missiles.run`).

Each rule ships with `src/rules/<tag>.md` describing data model, classification, filters, output format, and what's NOT covered — matching the shape of `shields.md` and `missiles.md`.

### Group 1 — Ware-driven (use `entity_diff` on `libraries/wares.xml` + DLC, UNION with changed referenced macros)

**Ware-driven rules union the ware diff with changed macro files.** A weapon/engine/turret balance patch can touch only the macro file (bullet damage, engine thrust) without any `wares.xml` delta. Driving purely from ware diffs would silently miss these. For each rule below, the input set is `{entities_with_changed_wares} ∪ {entities_whose_macro_or_bullet_file_appears_in_change_map}`.

Macro-to-ware fan-out (1:N for bullets): a single bullet macro is referenced by many weapon/turret macros, and each weapon/turret macro is referenced by one ware. The rule builds **reverse indices for both effective versions** (old and new): `bullet_macro_id → [weapon/turret_macro_ids] → [ware_ids]`. The impacted-ware set for a changed bullet file is the **union** of old-state and new-state impacts — a ware that *starts* or *stops* referencing a shared bullet between versions counts on the side that references it. Single-state indexing would miss the side that doesn't reference the bullet, underreporting balance changes. Same fan-out principle (dual indices, union) applies to shared engine macros or shared shield component files.

**Production is keyed by method.** A single ware can carry multiple `<production @method="...">` entries (default, teladi, terran, xenon, recycling, closedloop, etc.). Verified: 93 wares have >1 production method in 9.00B6 core alone, more once DLC diffs are applied. Every ware-driven rule below diffs productions as a keyed collection keyed by `@method`. Fields per production entry: `@time`, `@amount`, `<primary><ware @ware @amount>` list (recipe inputs). A ware gaining a new method, losing a method, or changing recipe inputs on an existing method all surface as distinct diffs (e.g., `production[method=terran] added`, `production[method=teladi] time 24→30`).

**Macro path resolution uses a shared helper** (added to `src/lib/paths.py` in Wave 0 Gate 0a): `resolve_macro_path(root, pkg_root, macro_ref, kind)` discovers the actual on-disk path for a macro ref, handling case variance (`Engines/engines`, `WeaponSystems/weaponsystems`, `SurfaceElements/surfaceelements`, `StorageModules/storagemodules`) and family subdirectories. Supported `kind` values: `engines`, `weapons`, `turrets`, `shields`, `storage`, `ships`, `bullet` (for weapon/turret bullet-macro lookup under `assets/fx/weaponFx/macros/`), with weapon-family subdirs (`standard`, `energy`, `heavy`, `capital`, `boron`, `highpower`) discovered on demand. No rule hardcodes a case-specific path.

#### engines
- Source: `<ware group="engines">` in `libraries/wares.xml` (core + DLC).
- Display name: locale page 20107 via `resolve_attr_ref(elem, locale, attr='name')`.
- Classifications: `[race, size, type, mk]` parsed from id token `engine_{race}_{size}_{type}_{variant}_{mk}` (e.g., `["arg", "m", "allround", "mk1"]`).
- Stats diffed:
  - Ware: `<price @min/@average/@max>`, `@volume`, per-method `<production>` entries (time, amount, primary recipe — see keyed-production note above).
  - Macro (path resolved via `resolve_macro_path`): `<boost @thrust/@acceleration>`, `<travel @thrust/@attack>`, `<thrust @forward/@reverse>`, `<hull @max>`.
- Lifecycle: `tags="deprecated"` on ware (none observed in 9.00B6, but keep the check — parity with other ware rules).
- Output: `[engines] <name> (<classifications>) [<sources>]: <comma-separated changes>`.

#### weapons
- Source: `<ware group="weapons">` (includes mines).
- Display name: locale 20105.
- Classifications: `[subtype, ...tag_tokens]` — subtype = macro-path fragment (`standard`/`energy`/`heavy`/`capital`); mines always include the token `"mine"`.
- Stats diffed:
  - Ware: price, per-method production, volume.
  - Weapon macro: `<bullet @class>`, `<heat @overheat/@coolrate/@cooldelay>`, `<rotationspeed @max>`, `<hull @max>`.
  - **Bullet macro** (resolved via `resolve_macro_path` against `assets/fx/weaponFx/macros/<bullet_class>.xml` and DLC overlays): `<ammunition @value>` (damage per hit), `<bullet @speed/@lifetime/@amount/@barrelamount/@timediff/@reload/@heat>` (projectile speed, time-to-live, burst size, rate of fire, heat per shot). Bullet-macro stats are where real combat balance changes live; diffing them is critical for "no silent changes" on weapon balance patches.
- Lifecycle: `tags="deprecated"`. 9.00 deprecated all mk1/mk2 generations of dumbfire/guided/torpedo launchers; rule must detect this.
- Output: `[weapons] <name> (<classifications>) [<source>]: <changes>`. Bullet-macro diffs appear as their own rows under the same weapon name, with `extras.subsource="bullet"` so grouping can cluster them.

#### turrets
- Source: `<ware group="turrets">`.
- Display name: locale 20105.
- Classifications: `[subtype, ...tag_tokens]` — subtype = macro-path fragment; tag tokens include `"guided"` for missilelauncher-turrets.
- Stats diffed:
  - Ware: price, per-method production, volume.
  - Turret macro: `<bullet @class>`, `<rotationspeed @max>`, `<rotationacceleration @max>`, `<hull @max>`.
  - **Bullet macro** (same treatment as weapons; resolved via `resolve_macro_path`): `<ammunition @value>`, `<bullet @speed/@lifetime/@amount/@barrelamount/@timediff/@reload/@heat>`. Combat stats live here.
- Lifecycle: `tags="deprecated"`.
- Output: `[turrets] <name> (<classifications>) [<source>]: <changes>`. Bullet-macro diffs tagged with `extras.subsource="bullet"`.

#### equipment
- Source: wares with `@group in {software, hardware, countermeasures}`, plus satellites (`@id="satellite_*"`) and spacesuit gear (`tags` contains `personalupgrade`).
- **Drones are NOT in this rule.** Drones (`@group="drones"`) are small NPC ships with their own macros; the `ships` rule picks them up via its **ware sub-source** (matches `@transport="ship"` OR `@tags` containing `"ship"` OR `@group="drones"`). Drone macros are resolved from each drone ware's `<component ref>` value, NOT by pattern-matching the macro filename — drone macros don't always match the `ship_*_macro.xml` glob used by the ship-macros sub-source (some are named `ship_gen_xs_cargodrone_*`, some may diverge). Ware-to-macro resolution via component ref is authoritative.
- Display name: locale pages 20108 (software), 20201 (hardware), 20113 (spacesuit). Rule picks page by group.
- Classifications: `[@group, ...id_pattern_marker]` — e.g., `["software"]` for scanner software, `["hardware", "satellite"]` for satellites, `["engines", "spacesuit"]` for suit gear.
- Stats diffed: price, per-method production, volume, tags. No deep per-sub-category fields — equipment is a grab-bag for genuinely ware-only items; a future dedicated `software`/`satellites` rule could split it if needed.
- Lifecycle: add/remove; tag transitions; any price change.
- Output: `[equipment] <name> (<classifications>) [<source>]: <changes>`.
- **Scope is ware-only.** Equipment macros (software behavior, spacesuit scanner/engine stats) are not diffed in v1. When a changed macro file path appears in the change_map and maps to an equipment ware with no wares.xml delta, the rule emits a **warning** (via `forward_warnings`) with the macro path and ware id — surfacing the gap loudly without tripping the hard-blocking `incomplete` contract. This keeps "no silent changes" honest: the user sees "equipment macro X changed but equipment rule doesn't diff macros; consider a follow-up rule" rather than either a silent miss or a blocked run.

#### wares (non-equipment)
- Source: `<ware>` with `@group` NOT in the equipment set `{engines, weapons, turrets, shields, missiles, countermeasures, drones, hardware, software}`, AND NOT a ship ware (ship wares have no `@group` — they're identified by `@transport="ship"` or `@tags` containing `"ship"`, and belong to the ships rule).
- Display name: locale 20201.
- Classifications: `[@group, ...@tags tokens]` — e.g., `["food", "economy", "stationbuilding"]`.
- Stats diffed: price, volume, transport, `<production @time/@amount/@method>`, owner factions (`<owner @faction>`), tags.
- Lifecycle: `tags="deprecated"`.
- Output: `[wares] <name> (<classifications>) [<source>]: <changes>`.

### Group 2 — Macro-driven (iterate `change_map`, filter to domain paths; classification via macro contents)

#### ships
- Sources (three sub-loaders under one tag — each emits outputs tagged `ships` with `extras.subsource` distinguishing):
  1. **Ship macros**: `assets/units/size_*/macros/ship_*_macro.xml` (case-insensitive; core + DLC). Stats diffed: `<hull @max>`, `<people @capacity>`, `<physics @mass>`, `<jerk forward/@strafe/@angular>`, `<purpose @primary>`, `<storage @missile>`.
  2. **Ship wares**: `<ware>` elements in `libraries/wares.xml` (+ DLC) with `@transport="ship"`, `@tags` containing `"ship"`, OR `@group="drones"` (drones are small NPC ships — folded into this sub-source rather than the equipment rule because they have macros with real behavior stats). Fields diffed: `<price @min/@average/@max>`, per-method `<production>` entries (see Group 1 keyed-production note — multiple methods per ware are common), `<restriction @licence>`, `<owner @faction>` list, `@volume`. Ware entries are keyed by `@id`; the ware's `<component ref>` ties back to the macro for display name and classifications.
  3. **Ship roles**: `libraries/ships.xml` (+ DLC) `<ship @id>` entries. Fields diffed: `<category @tags>`, `<category @faction>`, `<category @size>`, `<pilot><select>` faction/tags, `<basket @basket>`, `<drop @ref>`, `<people @ref>`.
- Display name: macro → locale 20101 via `<identification @name>`. Ware → same locale via `@name`. Ships.xml role entries have no display name; fall back to `@id`.
- Classifications: `[macro_class, ship_type]` from macro (e.g., `["ship_m", "fighter"]`); for ware sub-source, add `@transport`, `...@tags`, `@licence`; for ships.xml sub-source, add `...@tags` and `@size` from the role entry.
- Lifecycle: file add/remove (macros); ware/role entity add/remove.
- Note: `_a`/`_b` macro letter-suffix = variant. Ware ids and macro names align one-to-one. A single ship's changes may produce outputs from more than one sub-loader in the same run; group-by-tag keeps them together.
- Output: `[ships] <name> (<classifications>) [<source>]: <changes>`. `extras.subsource` ∈ `{macro, ware, role}`.

#### storage
- Source: `assets/props/StorageModules/macros/storage_*_macro.xml` (case-insensitive; core + DLC with `storagemodules` lowercase fallback).
- Display name: no dedicated locale. Emit the storage macro's `@name`. If another macro in the change set references this storage via `<connection ref>` and its display name resolves, include that parent ship name in `extras.parent_ship`; otherwise omit.
- Classifications: cargo `@tags` split on whitespace — e.g., `["container"]`, `["liquid"]`, `["solid"]`.
- Stats diffed: `<cargo @max>`, `<cargo @tags>`, `<hull @integrated>`.
- Lifecycle: file add/remove.
- Output: `[storage] <macro> (<classifications>) [<source>]: <changes>`.

#### sectors
File families differ in shape enough that one loader can't serve all. Rule has five sub-loaders under one tag; each uses its own explicit key and fields.

Sub-loaders:
1. **galaxy wiring** — `maps/xu_ep2_universe/galaxy.xml` (core) + `extensions/*/maps/xu_ep2_universe/galaxy.xml` (DLC).
   - Entities: `<connection>` children of the top `<macro>` (these wire clusters into the universe).
   - Key: `@ref` (connection ref) — or composite `(@ref, @path)` if `@ref` repeats.
   - Fields diffed: `<macro @ref>`, position/rotation offsets, connection target refs.
2. **map macros** — `maps/xu_ep2_universe/{clusters,sectors,zones}.xml` (core) + `extensions/*/maps/xu_ep2_universe/*clusters.xml` + `extensions/*/maps/xu_ep2_universe/*sectors.xml` + `extensions/*/maps/xu_ep2_universe/*zones.xml` (DLC; catches `dlc_boron_clusters.xml`, `dlc_boron_sectors.xml`, `dlc_boron_zones.xml`). Glob is narrowed by basename to avoid double-parsing files that belong to sub-loaders 1 (`galaxy.xml`) or 3 (`*highways.xml`).
   - Entities: `<macro @name>` where `@class ∈ {cluster, sector, zone}`.
   - Key: `@name`.
   - Fields diffed: `<connection>` children (emitted as per-connection sub-entities keyed by `(parent_macro, @ref)`), region refs.
3. **highways** — `maps/xu_ep2_universe/{sechighways,zonehighways}.xml` (core) + `extensions/*/maps/xu_ep2_universe/*sechighways.xml` + `extensions/*/maps/xu_ep2_universe/*zonehighways.xml` (DLC).
   - Entities: `<macro @name>` where `@class ∈ {highway, sechighway, zonehighway}`.
   - Key: `@name`.
   - Fields diffed: endpoint refs, entry/exit gates, speed attr.
4. **region yields** — `libraries/regionyields.xml`.
   - Entities: `<definition @id>` (verified against 9.00B6).
   - Key: `@id` (e.g., `"sphere_tiny_ore_verylow"`).
   - Fields diffed: `@tag`, `@ware`, `@respawndelay`, `@yield`, `@rating`, `@objectyieldfactor`, `@scaneffectcolor`, `@gatherspeedfactor` (gas only).
5. **region definitions** — `libraries/region_definitions.xml`.
   - Entities: `<region @name>` (verified against 9.00B6).
   - Key: `@name`.
   - Fields diffed: `@density`, `@rotation`, `@noisescale`, `@seed`, `@minnoisevalue`, `@maxnoisevalue`, `<boundary>` child (class + size), `<falloff>` steps, `<fields>` child refs (volumetric fog, nebula medium).

Per-connection diff replaces the earlier "count of connections" approach: each connection is its own sub-entity, so a gate add and a gate remove both surface as discrete entity changes, not lossy count deltas.

- Display name: macro `@name` (no dedicated locale for most; cluster/sector locale best-effort).
- Classifications: `[<subsource>]` plus relevant structural tokens (e.g., macro `@class`).
- Output: `[sectors] <name> (<classifications>) [<source>]: <changes>`. `extras.subsource` ∈ `{galaxy, map, highway, regionyield, regiondef}`.
- Note: the graph-level coarse-signal framing from earlier is dropped. Each connection/region is a tracked entity; LLM commentary still synthesizes the big-picture headline but works from explicit per-entity diffs, not counts.

### Group 3 — Library entity-diff (use `entity_diff` on specific XML library files)

#### factions
Two sub-entity types, each with its own contract.

- Source files: `libraries/factions.xml` + `libraries/diplomacy.xml` (+ DLC diffs).

**Sub-entity: faction**
- Xpath: `//faction`.
- Key: `@id`.
- Display: `@name` → locale via `resolve_attr_ref`.
- Classifications: `["faction", @primaryrace if present, @behaviourset if present]`.
- Fields diffed: `@behaviourset`, `@primaryrace`, `@policefaction`, `<licences>` entries (each licence's `@type`, `@factions`, `@minrelation`), default relations.

**Sub-entity: diplomacy action**
- Xpath: `//action` in diplomacy.xml.
- Key: `@id`.
- Display: `@name` → locale via `resolve_attr_ref`.
- Classifications: `["action", @category if present]`.
- Fields diffed: **full subtree**, not just top-level attrs. Actions nest `<agent>`, `<cost>`, `<reward>`, `<time>`, `<icon>`, `<success>`, `<failure>`, `<params>` children, and meaningful changes often live inside those children (e.g., cost amount or reward composition). The rule diffs the entire action subtree using a recursive attribute/child diff and emits a labeled change per difference (e.g., `cost.amount 500→750`, `reward.ware energycells→hullparts`). For sections too structurally complex to represent as simple `old→new` labels (unexpected deep nesting beyond what the rule's inventory covered), emit an incomplete marker for that action rather than silently dropping the change.

- Lifecycle: faction/action add/remove/rename; licence threshold changes.
- Note: runtime relation values are not spec-visible data; the rule focuses on structural fields only.
- Output: `[factions] <name> (<classifications>) [<source>]: <changes>`. `extras.subsource` ∈ `{faction, action}`.

#### stations
Five sub-entity types, each with its own contract. Note the real chain: `stations.xml` → `stationgroups.xml` → `constructionplans.xml`. A station's `@group` resolves via stationgroups (each group is a weighted selection of constructionplans), not directly to a plan.

- Source files: `libraries/stations.xml` + `libraries/stationgroups.xml` + `libraries/modules.xml` + `libraries/modulegroups.xml` + `libraries/constructionplans.xml` (all + DLC diffs).

**Sub-entity: station**
- Xpath: `//station` in stations.xml.
- Key: `@id`.
- Display: `@id` (stations use ids like `shipyard_arg`; no locale ref on the station entry itself).
- Classifications: `["station", ...<category @tags>]` — e.g., `["station", "shipyard"]`.
- Fields diffed: `<category @tags>` list, `<category @faction>` list, `@group` (resolves to a stationgroup — see below).

**Sub-entity: stationgroup**
- Xpath: `//group` in stationgroups.xml.
- Key: `@name` (verified empirically in 9.00B6: all 65 groups use `@name`, zero use `@id`).
- Display: `@name`. All `refs` entries on other sub-entities that reference a stationgroup use the group's `@name` value, not an id (e.g., `station.refs = {"group_ref": "shipyard_arg"}` where `"shipyard_arg"` is the stationgroup's `@name`).
- Display: `@id`.
- Classifications: `["stationgroup"]`.
- Fields diffed: child `<select>` entries (each entry's constructionplan `@ref`, `@chance` or weight), total entry count.
- Cross-references via `extras.refs`:
  - Station output: `refs = {"group_ref": "<stationgroup @name>"}` (stationgroups are keyed by `@name`, never `@id`).
  - Stationgroup output: `refs = {"plan_refs": [<list of plan @ref from select children>]}`.
  - Constructionplan output: `refs = {"module_refs": [<list of @module from <entry> children>]}`.
  - Module output: `refs = {"ware_produced": "<category @ware>"}` when present.
  This lets the future grouping stage answer "which stations are affected by changed plan X" in one hop by reading refs, without the rule itself building the transitive closure. Rule stays simple (per-file loaders + per-entity refs); the dependency graph assembles at grouping time.

**Sub-entity: module**
- Xpath: `//module` in modules.xml.
- Key: `@id`.
- Display: `<identification @name>` → locale via `resolve_attr_ref`; fallback `@id`.
- Classifications: `["module", @class, ...<category @tags>, ...<category @faction>, ...<category @race>]`.
- Fields diffed: `@class`, `<category @ware>` produced, `<category @tags>`, `<category @faction>`, `<category @race>`, `<compatibilities>` `<limits>` and `<maxlimits>`, `<production>` entries (ware + chance).

**Sub-entity: modulegroup**
- Xpath: `//group` in modulegroups.xml.
- Key: `@name`.
- Display: `@name`.
- Classifications: `["modulegroup"]`.
- Fields diffed: child `<select>` entries (each entry's `@ref`, `@chance`), total entry count.

**Sub-entity: constructionplan**
- Xpath: `//plan` in constructionplans.xml.
- Key: `@id`.
- Display: `@id`.
- Classifications: `["constructionplan"]`.
- Fields diffed: `@race` if present, child `<entry>` entries (each entry's `@module`, `@index`, `@connection`), total entry count.

- Lifecycle: entity add/remove per file.
- Output: `[stations] <name> (<classifications>) [<source>]: <changes>`. `extras.subsource` ∈ `{station, stationgroup, module, modulegroup, constructionplan}`.

#### jobs
- Source: `libraries/jobs.xml` (+ DLC).
- Entity: `<job @id>`.
- Display name: `@name` → locale page 20204; fallback to @id.
- Classifications: `[<category @faction>, ...<category @tags>, <category @size>]` — e.g., `["argon", "trade", "medium"]`.
- Fields diffed: **full attribute set of the `<job>` element and every direct child's attributes**, not a curated whitelist. Real jobs have many behavior-bearing fields beyond the headline `quota`/`category`/`orders`/`location`/`startactive` — `@friendgroup` changes on 53 entries in 9.00B6 alone, and other fields (e.g., `<environment @buildatshipyard>`, `<modifiers @rebuild/@commandeerable>`, `<ship @overridenpc>`) carry AI behavior that can shift between versions. The rule diffs every attribute on `<job>` and every child's attributes, emitting a labeled change per difference. Documented filter list: job `.md` names attrs that are cosmetic/internal (if any observed during implementation); everything else is diffed.
- Lifecycle: add/remove; `@startactive="false"` disables.
- Note: hundreds of jobs exist. The rule does not emit anything for truly unchanged jobs.
- Output: `[jobs] <name> (<classifications>) [<source>]: <changes>`.

#### loadouts
- Source: `libraries/loadouts.xml` + `libraries/loadoutrules.xml`.
- Entities:
  - loadout: `<loadout @id>` — simple key.
  - rule: composite key split explicitly into **stable applicability** (keyed) vs **mutable behavior** (diffed), with multiset handling for duplicates:
    - Applicability key: `(container, ruleset_type, category, mk, frozenset(classes), frozenset(purposes), frozenset(factiontags), frozenset(cargotags))`. `container ∈ {"unit", "deployable"}` from top-level element; `ruleset_type` = parent `<ruleset @type>`. Sets avoid attribute-order false-diffs.
    - Diffed (the "how" fields): `weight`, `important`, `requiredocking`, `requireundocking`, plus any other non-applicability attrs.
    - **Duplicate-applicability matching is multiset-based, not positional.** For each applicability key, group the rules that share it into an old-version multiset and a new-version multiset, where each member's "signature" is the tuple of all diffed attrs. Compare multisets:
      - Members present on both sides with identical signatures: no change.
      - Members on one side only: emit add (new-side only) or remove (old-side only). No "modified" for multiset entries — avoids the cascading false-positive from positional matching when one duplicate is inserted/removed.
    - For unique-applicability rules (the vast majority), signature matching is trivial — one old, one new, diff normally.
  - This keeps weight changes as modified (not remove+add) when applicability is unique, and it keeps inserting/removing one duplicate from cascading into every subsequent duplicate being flagged as modified.
- Display name: loadout `@macro` → ship macro name → locale 20101. Fallback: loadout @id. For rules: `f"{container}/{ruleset_type}/{category}/mk{mk}"` as a readable synthetic name.
- Classifications: `[<kind>, ...]` — kind = `"loadout"` or `"rule"`. For rules: `[<kind>, <container>, <ruleset_type>, <category>, <mk>, ...classes, ...purposes, ...factiontags]`. For loadouts: `[<kind>]`.
- Fields diffed: loadout equipment slots (engine/shield/turret/weapon macros), software list, virtualmacros, ammunition counts; rule `weight`, `important`, `requiredocking`, `requireundocking`.
- Lifecycle: add/remove.
- Output: `[loadouts] <name> (<classifications>) [<source>]: <changes>`.

#### gamestarts
- Source: `libraries/gamestarts.xml` (+ DLC diffs).
- Entity: `<gamestart @id>`.
- Display name: `@name` → locale.
- Classifications: `[...@tags]` — e.g., `["tutorial"]`, `["nosave"]`, or empty.
- Fields diffed: `@image`, `@tags` (tutorial/nosave), `@group`, cutscene ref, `<player @macro/@money/@name>`, starting ship + loadout, universe flags.
- Lifecycle: add/remove; tag changes.
- Output: `[gamestarts] <name> (<classifications>) [<source>]: <changes>`.

#### unlocks
- Source: `libraries/unlocks.xml` + `libraries/chapters.xml` + `libraries/infounlocklist.xml`.
- Entities:
  - discount: `<discount @id>` (unlocks.xml).
  - chapter: `<category @id>` (chapters.xml).
  - info: `<info @type>` (infounlocklist.xml).
- Display name: locale (discounts 20210, chapters 55101); info `@type` is an enum key, emit as-is.
- Classifications: `[<subcategory>]` — one of `"discount"`/`"chapter"`/`"info"`.
- Fields diffed: discount `<conditions>` (scannerlevel, relation range, ware filters) + `<actions>` (amount min/max, duration); chapter group/highlight/teamware; info percent threshold.
- Lifecycle: add/remove.
- Output: `[unlocks] <name> (<classifications>) [<source>]: <changes>`.

#### drops
- Source: `libraries/drops.xml`.
- Entities: `<ammo @id>`, `<wares @id>` baskets. (Optionally `<drop @id>` if present.)
- Display name: `@id` (no locale).
- Classifications: `[<kind>]` — `"ammo"`, `"wares"`, or `"drop"`.
- Fields diffed: `<select>` entry count, per-entry `@weight/@macro/@min/@max`, nested ware refs.
- Lifecycle: basket add/remove; select-entry add/remove.
- Output: `[drops] <id> (<classifications>) [<source>]: <changes>`.

#### cosmetics
- Source: `libraries/paintmods.xml` + `libraries/adsigns.xml` + `libraries/equipmentmods.xml`.
- Entities (three sub-strategies under one tag):
  - paint: `<paint @ware>`.
  - adsign: `<adsign @ware>` under `<type @ref>`; composite key `(type_ref, ware)`.
  - equipmod: nested `<weapon|shield|engine|…>/*[@ware]`; composite key `(category, ware, quality)`.
- Display name: `@ware` (the ware id). The cosmetics XML files contain author-friendly hints in XML comments (e.g., `<!-- player - painttheme_01 -->`), but `xml.etree.ElementTree` discards comments on parse and the project is stdlib-only. Using `@ware` as the display is the straightforward compromise. Upgrade path (if nicer names are wanted later): add a text-regex pre-pass in Wave 0 that extracts comment-to-element pairings, or switch to a comment-preserving parser — not in scope here.
- Classifications: `[<subcategory>, ...]` — subcategory = `"paint"`/`"adsign"`/`"equipmod"`. For equipmods, extras include the category tag (`"weapon"`/`"shield"`/`"engine"`/…) and `@quality`.
- Fields diffed: paint HSV + pattern fields; adsign macro ref; equipmod quality, min/max bonus, secondary bonus chance.
- Lifecycle: add/remove.
- Output: `[cosmetics] <id> (<classifications>) [<source>]: <changes>`.

### Group 4 — File-level coarse (use `file_level`)

#### quests
- Source: `md/*.xml` (core) + `extensions/*/md/*.xml`.
- Type: file-level. For modified files, emit per the "file-level modification output" convention (terse text + full unified diff in `extras.diff`).
- Display name: root `<mdscript @name>`; fallback to filename stem.
- Classifications: `[<filename_prefix>]` if filename matches a known prefix pattern (e.g., `gm_` → `"generic_mission"`, `story_` → `"story"`, `factionlogic_` → `"factionlogic"`, `scenario_` → `"scenario"`, `gs_` → `"gamestart"`); otherwise empty.
- Lifecycle: add/remove/modify.
- Output: `[quests] <name> (<classifications>) [<source>]: <ADDED|REMOVED|modified +A/-B lines>`.

#### gamelogic
- Source (hybrid — one tag, three sub-strategies):
  - `aiscripts/*.xml` (core + DLC) — file-level via `file_level`.
  - `libraries/behaviours.xml` — entity-diff keyed by composite `(set/@name, behaviour/@name)`.
  - `libraries/scriptproperties.xml` — entity-diff keyed by composite `(datatype/@name, property/@name)`.
- Display name: aiscript `@name`; behaviour `@name`; property `@name` (+ `@result` in extras).
- Classifications: `[<subcategory>, ...]` — subcategory = `"aiscript"`/`"behaviour"`/`"scriptproperty"`. For behaviours, extras include the set name (race/NPC type). For aiscripts, extras include the filename prefix (e.g., `"fight"`, `"build"`, `"interrupt"`).
- Fields diffed:
  - aiscripts: file-level (unified diff in extras).
  - behaviours: **full attribute set on the behaviour element + any child subtree attributes**, not just `@chance`/`@minskill`. Same inventory-then-diff approach as jobs — a curated whitelist would silently drop any behaviour tuning X4 adds in future versions.
  - scriptproperties: **full attribute set + child subtree**, not just `@result`/`@type`. Parameterized properties (e.g., `isclass.{$class}`) carry nested structure worth diffing.
- Lifecycle: file/entity add/remove/modify.
- Output: `[gamelogic] <name> (<classifications>) [<source>]: <changes>`.

## Testing

Two layers, three files.

### Unit tests (per-rule, synthetic fixtures)
- `tests/test_<tag>.py`, one per rule.
- **Per-rule fixture trees**: `tests/fixtures/<rule>/TEST-1.00/` and `tests/fixtures/<rule>/TEST-2.00/`. Each rule's tests point at its own fixture roots; fixture growth for one rule never affects another.
- Existing `tests/TEST-1.00/` / `tests/TEST-2.00/` migrate to `tests/fixtures/shields/` and `tests/fixtures/missiles/` in Wave 0 Gate 0d.
- Minimum unit-test case set per rule:
  1. added entity / added file.
  2. removed entity / removed file.
  3. modified entity (each distinct stat category covered at least once).
  4. lifecycle transition (e.g., deprecation toggle) where applicable.
  5. DLC-sourced entity (co-located / extension path).
  6. Provenance handoff (core → core+DLC or vice versa).
  7. `DiffReport.failures` non-empty → rule emits incomplete-marker output; contaminated rows also marked `extras.incomplete=True`.
  8. `DiffReport.warnings` non-empty → rule emits warning outputs via `forward_warnings`.
  9. no change (empty result).
- Helpers get their own tests in `tests/test_lib_<helper>.py`.
- Unit tests run via `python3 -m unittest discover tests`.

### Real-data acceptance tests (per-rule, against `x4-data/`)
**One canonical layout** to prevent harness drift across waves:
- `tests/test_realdata_helpers.py` — Gate 0e's helper-level probes (entity_diff on real library files, file_level on md/*, locale + macro path resolution). Ships in Wave 0.
- `tests/test_realdata_<rule>.py` — one per rule; holds that rule's `BASELINE` constant, sentinels, and Tier A/B assertions. Rule authors ship this alongside their rule.
- No central `tests/test_realdata.py` — deleted from earlier drafts to converge on the per-rule file convention.

All real-data tests auto-detect `x4-data/<version>/` presence. When versions are missing, the affected tests skip with a loud printed reason so the dev sees exactly what was skipped and why. Env var `X4_REALDATA` is not required.

**Scope by run type:**
- Dev iteration (default): runs Tier A smoke on 8.00H4→9.00B6 only (the canonical pair) + Tier B named baselines. Fast enough to run per commit.
- Full matrix (manual/release): runs Tier A across every consecutive version-pair (B1→B2, B2→B3, …, B5→B6) plus 8.00H4→each 9.00Bx. Opt-in via env `X4_REALDATA_FULL=1`. This is I/O-bound; running the full matrix on every dev loop would dominate iteration time, so it's explicitly reserved for manual/nightly runs.

Split into two distinct check tiers:

#### Tier A — Smoke checks (run against every version-pair)
Applies to each consecutive 9.00 pair (B1→B2, B2→B3, …, B5→B6), plus the canonical 8.00H4→9.00B6 full transition.

- **Runs without crash**: rule completes `run()` without raising.
- **Zero unexpected incomplete markers**: `assert_complete(outputs)` passes, OR any incomplete marker is on a documented allow-list in the test file (with justification).
- **Output-count is non-negative integer**: trivially, just a type-shape sanity check.

Zero output is a **valid result** for stable pairs (e.g., `missiles` returns 0 on `9.00B4→B5` and `9.00B5→B6`). Smoke checks do NOT assert non-zero output.

#### Tier B — Named-baseline regression (one pair at a time, per rule)

Tier B depends on the **canonical rule-output schema** defined earlier so snapshots are stable and multi-sub-source rules don't collide. Every rule output MUST populate these extras:

- `extras.entity_key` (string or tuple): the canonical identity of the entity this output describes. For simple rules this is the entity id (`"engine_arg_m_allround_01_mk1"`). For multi-sub-source rules this is `(subsource, inner_key)` (e.g., `("module", "prod_bor_medicalsupplies")`).
- `extras.kind` (enum string): one of `"added"`, `"removed"`, `"modified"`, `"deprecated"`, `"undeprecated"`. Rules may add domain-specific kinds (missiles uses `"deprecated"`); the string is free-form but must be stable across runs.
- `extras.subsource` (string, optional): present on multi-sub-source rules. Value from the rule's declared subsource list (e.g., `"faction"` vs `"action"` for factions rule).

Snapshot format: one line per emitted output, `(entity_key, kind, subsource, text_hash)` serialized deterministically, lexicographically sorted. `text_hash` is a SHA-256 of the rule's `text` field — this catches regressions in stat diff labels, classifications, or provenance rendering that leave `entity_key`/`kind`/`subsource` stable but change the user-visible output. If a snapshot comparison fails, the test prints the offending line alongside the expected one and the full `text` for both sides, so the dev can tell whether the change is real (regenerate snapshot) or a regression (fix rule).

Each rule's test file defines:

```python
# tests/test_<rule>_realdata.py
BASELINE = {
    'pair': ('8.00H4', '9.00B6'),
    'expected_output_count': 37,                                 # exact count; no tolerance
    'entity_snapshot': 'snapshots/missiles_8.00H4_9.00B6.txt',   # committed; normalized (entity_key, kind, subsource) tuples sorted
    'sentinels': [
        {'entity_key': 'missile_gen_m_guided_01_mk1', 'kind': 'added'},
        {'entity_key': 'missile_torpedo_heavy_mk1',   'kind': 'deprecated'},
    ],
}
```

Baselines live as Python constants in the test file — not parsed out of `.md` prose. The `.md` stays human-oriented narrative; the test file holds the machine-checkable values. No markdown parser, no drift.

**Exact count + entity snapshot, no percentage tolerance.** A 10% tolerance would let a rule silently lose or invent outputs while passing, which contradicts "no silent changes". Instead:
- Exact count assertion catches any drift.
- The entity-snapshot file (committed alongside the test) lists the full set of `(key, kind)` pairs the rule is expected to emit for this pair. Test compares actual output against snapshot; any difference is surfaced.
- When the rule legitimately changes (new stat added, new entity type covered), the dev regenerates the snapshot and commits the delta along with the code change. The snapshot update is the "I meant to change this" signal.

Tier B runs ONLY against its configured pair. Running it against a random consecutive pair would legitimately fail (missile_gen_m_guided_01_mk1 doesn't exist as "ADDED" on 9.00B3→B4).

Different version-pairs surface different edge cases (DLC patch shapes, lifecycle transitions, locale variance) — Tier A's breadth catches these; Tier B anchors regression to known states.

A rule is **production-ready** when: unit tests pass, Tier A passes across all configured pairs, Tier B passes on its named baseline pair.

## Implementation order

Wave 0 — Infrastructure. Biggest wave; split into explicit gates with exit criteria so subsequent waves have a solid floor.

Gate 0a — Shared primitives:
- `src/lib/rule_output.py`: shared dataclass + `extras.incomplete` / `extras.entity_key` / `extras.kind` / `extras.subsource` / `extras.sources` normalized fields (diagnostics use synthetic entity_key + `kind="warning"|"incomplete"` per Canonical schema).
- `src/lib/check_incomplete.py`: `assert_complete(outputs)` + `forward_incomplete` + `forward_incomplete_many` + `forward_warnings` helpers.
- `src/lib/locale.py` extensions: `resolve_attr_ref()` + DLC locale glob-merge with `locale.collisions` in warning shape.
- `src/lib/paths.py` extensions: `resolve_macro_path()` with cached per-(root, kind) multimap index and pkg_root-first precedence. No ambiguity warnings emitted — DLCs ship XML diff patches through the patch engine (Gate 0b/0c), not standalone replacement files, so cross-extension filename collisions with different content are a mod-ecosystem scenario we don't support.
- `src/lib/macro_diff.py`: attribute-diff spec helper.
- `src/lib/cache.py`: per-run memoization for `entity_diff.diff_library` results. Cache key is the **full parameter tuple** that shapes the result: `(str(old_root.resolve()), str(new_root.resolve()), file_rel, entity_xpath, key_fn_identity, include_dlc)`. Using resolved absolute paths prevents cross-fixture aliasing (per-rule `tests/fixtures/<rule>/TEST-1.00/` trees share the name but are distinct trees). `key_fn_identity` is either a string tag the caller passes (`"default_id"`, `"loadouts_rule_composite"`, etc.) or `id(key_fn)` if none given — two different `key_fn`s on the same file must produce distinct cache entries. Cache is scoped to a single run (Python dict in module scope, cleared between test invocations).
- Exit: unit tests for each pass; `Locale` resolves DLC-specific ids correctly; `resolve_macro_path` finds real macros across all X4 case variants seen in 9.00B6; cache hit rate >1 when two rules call `diff_library` on the same file in the same run.

Gate 0b — Patch engine core:
- `src/lib/entity_diff.py` minimal: XPath subset, `<add>`/`<replace>`/`<remove>` ops with `sel` + `if=` + `pos=` + `silent=`, single-version effective-tree materialization.
- Exit oracle: golden materialized fixtures checked into `tests/fixtures/_entity_diff_golden/`. Each fixture is a triple:
  - `core_input.xml`: a stripped-down core library fragment (e.g., a minimal `<gamestarts>` with the entities we care about).
  - `dlc_patch.xml`: a `<diff>` patch file with real X4 ops taken verbatim from a DLC (`ego_dlc_terran/libraries/gamestarts.xml` segments, `ego_dlc_boron/libraries/modules.xml` segments).
  - `expected_effective.xml`: the hand-verified post-patch tree. These are small enough to verify by reading (no mechanized oracle needed).
  Engine passes the gate when it produces byte-identical output (after whitespace normalization) against every golden triple. Add at least one triple per supported op × `pos=` combination: plain `<add>`, `<add pos="after">`, `<add pos="prepend">`, element `<replace>`, attr `<replace>`, `<remove>`, `<remove silent="1">`, `<remove silent="true">`, `<add>` gated by `if="not(...)"`.

Gate 0c — Provenance, conflict detection, three-tier failure model:
- Per-entity provenance tracking as **contributor sets** (old/new contributor files + DLCs), NOT a singular "winning DLC" label. Alphabetical order is arbitrary enough that claiming a primary attribution is misleading.
- **Three-tier write-set conflict classification** per the main helper spec:
  - FAILURE: write-write collisions on same target (including subtree invalidation via `<remove>`/element-`<replace>`), AND `if=` RAW dependencies per the conservative v1 algorithm.
  - WARNING: positional overlaps only (`pos="after"` same anchor, `pos="prepend"` same parent) — content preserved, order picked alphabetically.
  - Non-conflict: different attrs same entity, non-colliding appends, different ancestors.
- Contaminated-output propagation: when a sub-report is incomplete, every normal output derived from it inherits `extras.incomplete=True`. `forward_incomplete` / `forward_incomplete_many` do this automatically.
- Exit: synthetic tests cover each FAILURE case (write-write, id-collision, subtree invalidation, `if=` RAW), each WARNING case, and each commutative non-conflict. Real-data scan against every consecutive version-pair in `x4-data/` produces failures that **all match a reviewed allowlist** committed as `tests/realdata_allowlist.py`. The conservative detector is expected to over-flag some legitimate cases; the allowlist is how those are documented and approved, so the gate is crisp (every failure is either in the allowlist or it blocks). Any new unreviewed failure must either be added to the allowlist with written justification or resolved by tightening the detector.

Gate 0d — File-level helper + fixture migration:
- `src/lib/file_level.py`.
- Migrate `tests/TEST-1.00/` and `tests/TEST-2.00/` into `tests/fixtures/shields/` and `tests/fixtures/missiles/`; update the two existing tests to point at new roots. Verifies per-rule fixture convention before Wave 1 adds more.
- Exit: `python3 -m unittest discover tests` passes; shields + missiles tests see only their own fixtures.

Gate 0e — Real-data validation harness:
- `tests/test_realdata_helpers.py` (one of the three real-data test files; see Testing section). Auto-detects `x4-data/`; loud skip when absent.
- **Helper-level real-data exercises** — one probe per distinct patch-shape and file family, before any Wave 1-3 rule depends on the helper:
  - `libraries/jobs.xml` — entity xpath `.//job`; exercises attribute-heavy entity-diff.
  - `libraries/wares.xml` — entity xpath `.//ware`; exercises the largest/most-patched real file, keyed-production handling.
  - `libraries/diplomacy.xml` — entity xpath `.//action`; exercises recursive subtree diff.
  - `libraries/constructionplans.xml` — entity xpath `.//plan`; exercises native-fragment DLC shape (mini_01/boron ship these as `<plans>`, not `<diff>`).
  - `libraries/loadouts.xml` — entity xpath `.//loadout`; exercises native-fragment + large attr surface.
  - `libraries/region_definitions.xml` — entity xpath `.//region`; exercises native-fragment map-adjacent file.
  - `maps/xu_ep2_universe/galaxy.xml` — entity xpath `.//connection`; exercises `<diff>` shape on a non-library file.
  - `file_level.diff_files(...)` with `md/*.xml` glob on the canonical 8.00H4→9.00B6 pair — non-empty, valid XML bytes.
  - `resolve_attr_ref` on a known ware id + known faction id + known gamestart id — locale resolves across each page.
  - `resolve_macro_path` against one macro per asset kind (engines, weapons, turrets, shields, storage, ships, bullet) — finds the macro regardless of case/family subdir variance.
  Every probe runs against the canonical 8.00H4→9.00B6 pair on dev iteration; consecutive 9.00Bx pairs added when `X4_REALDATA_FULL=1`.
  
  Assertions:
  - No uncaught exceptions.
  - `forward_incomplete` report's failures all land in the reviewed allowlist (`tests/realdata_allowlist.py`).
  - Provenance handoff: at least one entity shows `old_sources != new_sources` across 8.00H4→9.00B6 (validates tracking).
  - **Oracle assertions** (correctness, not just survivability): three hand-checked real-data transformations, one per op kind. Example: apply DLC terran's `<replace sel="/gamestarts/gamestart[@id='x4ep1_gamestart_tutorial1']/@name">{1021,5290}</replace>` to 9.00B6 core gamestarts.xml; assert the effective tree's tutorial1 gamestart has `@name="{1021,5290}"`. Same shape for one `<add pos="after">` case (boron/gamestarts custom_budgeted story insert) and one `<remove silent="1">` case (terran/gamestarts blueprint removal). Oracles are small, hand-verified, committed alongside the test. Catches systematically-wrong patch application the synthetic goldens might miss.
- Tier A (smoke — every pair): rule outputs + helper invocations don't crash, `assert_complete` passes, output count is non-negative integer. Zero outputs is valid for stable pairs.
- Tier B (named baseline — for shields, the 8.00H4→9.00B6 slot rework; for missiles, the 8.00H4→9.00B6 line replacement): sentinel entities present with expected kinds, exact output count matches declared baseline (see Tier B contract in Testing section).
- Exit: shields and missiles pass Tier A across all consecutive/full pairs, Tier B against their canonical baseline, AND helper-level exercises run clean against all pairs.

`entity_diff` test coverage requirements (spread across gates 0b–0c):
- Real DLC patch shapes from 9.00B6: attribute-level `<replace>` (terran/gamestarts), conditional `if=` `<add>` (timelines/factions), nested child `<add>` + `<remove silent="true">` (terran/gamestarts blueprints, boron/modules).
- Multi-DLC conflict: two synthetic DLCs touching same entity → `report.failures` non-empty, `report.incomplete` True.
- Provenance handoff on modified entity: old_source_files vs new_source_files differ → text renders `[old→new]`.
- Unsupported XPath → failure (not warning), rule emits incomplete marker.

Each post-Wave-0 wave ends with a real-data acceptance gate instead of just a spot check: per-rule `tests/test_realdata_<rule>.py` with Tier A smoke coverage (canonical pair by default; all pairs when `X4_REALDATA_FULL=1`) and one Tier B named baseline (canonical pair). A rule is not "production-ready" until Tier A passes on the canonical pair AND Tier B passes for its baseline pair.

Wave 1 — Ware-driven (parallel subagents, 5 rules):
- engines, weapons, turrets, equipment, wares.
- Exit: real-data acceptance per rule (sentinel changes, non-zero output, zero unexpected incomplete markers).

Wave 2 — Macro-driven (parallel, 3 rules):
- ships, storage, sectors.
- Exit: real-data acceptance per rule.

Wave 3 — Library entity-diff (parallel, 8 rules):
- factions, stations, jobs, loadouts, gamestarts, unlocks, drops, cosmetics.
- Exit: real-data acceptance per rule.

Wave 4 — File-level (parallel, 2 rules):
- quests, gamelogic.
- Exit: real-data acceptance per rule.

Subagent dispatch is the primary work mode during rule implementation, per user direction (saves main-thread context).

## Out of scope

- Grouping stage (`src/group.py`).
- LLM commentary stage (`src/commentary.py`).
- Pipeline runner (`src/run.py`).
- Refactoring `shields.py` or `missiles.py` to use the new helpers or the array-classification policy — opportunistic only.
- Deduplication across rules.
- DLC locale discovery is glob-based — the Locale loader globs `extensions/*/t/0001-l044.xml` automatically, so any DLC that ships its own locale file is picked up. Only Ventures does so in 9.00B6; other DLCs' text (terran, boron, split, timelines, etc.) lives in core `t/0001-l044.xml` (14 "terran" hits, 6 "boron" hits). The loader still works if a future DLC starts shipping its own.

## Wave 0 open questions (resolve during implementation, not before)

Seven issues that Codex surfaced in the final review are genuinely cheaper to resolve against real data during Wave 0 than to pre-spec. Each has a concrete resolution criterion; none are blockers to starting:

1. **DiffReport API must expose `ref_sources` before Wave 1 relies on it.** The `resolve_macro_path(..., pkg_root=...)` flow depends on per-reference provenance. Gate 0a exit: `DiffReport.modified` and friends return structured records (not bare tuples) carrying `element`, `source_files`, `sources`, and `ref_sources`. Locked when Gate 0a helper tests pass.
2. **Cross-extension same-ref ambiguity**: **decided (Gate 0a): dropped.** Egosoft DLCs modify core macros via XML diff patches (handled by the patch engine in Gate 0b/0c), not by shipping standalone replacement files with colliding names. Cross-extension filename collisions with different content is therefore a mod-ecosystem scenario this pipeline explicitly doesn't support. If that assumption changes, reinstate the warning then.
3. **XPath subset corpus inventory**: add as first task in Gate 0b. Scan every targeted version (8.00H4 + 9.00B1..B6) for distinct patch ops, `sel`/`if=` constructs, and `pos=` values, not just 9.00B6. Lock the supported subset against the full inventory, not one version's census.
4. **"Every pair" vs "optional skip" gate contradiction**: resolve by declaring 8.00H4→9.00B6 the **mandatory pair** for all gates; consecutive-pair coverage (9.00Bx→9.00B(x+1)) moves to `X4_REALDATA_FULL=1` nightly. Update Gate 0c/0e and wave exits to say "canonical pair mandatory; consecutive pairs optional" consistently during Gate 0a.
5. **Corpus version manifest for Tier B**: Tier B's exact-count + text-hash snapshots are only stable against a pinned extraction. Add `x4-data/MANIFEST.txt` listing the exact X4 version + DLC set each extracted version directory was built from. Tier B tests read MANIFEST.txt and skip with a clear reason if the local corpus manifest doesn't match the snapshot's recorded one. Decision point: first Tier B snapshot lands in Wave 1.
6. **Shared subtree-diff helper**: spec leaves keyed/multiset/positional pairing per-rule. If 3+ rules end up implementing the same pattern during Wave 3, extract to `src/lib/subtree_diff.py`. Otherwise let per-rule implementations stand. Decision point: midway through Wave 3.
7. **Macro-path index as multimap**: **decided (Gate 0a): `{macro_ref: [(path, pkg), ...]}`.** Multimap needed for pkg_root-first precedence with core fallback; no content hash needed since ambiguity warning (open question 2) was dropped.

These are tracked here rather than continuing to edit the spec because iterative spec refinement hit diminishing returns three rounds ago — the remaining questions have concrete implementation answers that are cheaper to find by building Wave 0 than by continuing to speculate.

## Known limitations

- File-level rule diffs are capped by size (100 KB of diff text or 5000 lines, whichever first). Truncation preserves head + tail hunks with a middle-drop marker; `extras.diff_truncated: True` plus full add/remove line counts.
- XPath support in `entity_diff.py` is a subset (see helper spec). Selectors outside the subset record a **failure** (not a warning) — the affected run is marked incomplete and `assert_complete` blocks emission rather than silently ship possibly-wrong output. Upgrade the subset only when real data breaks it.
- Multi-DLC load order: extracted data lacks `content.xml`, so real topological order is not derivable. Helper uses alphabetical iteration for stability (discovered via `extensions/*/` glob, no hardcoded DLC list) and **records a failure on any per-field write-write conflict** (two DLCs replacing the same attribute or element, or adding conflicting children). Multi-DLC patches to *different* fields of the same entity are commutative and not flagged. If a future extraction pipeline preserves `content.xml`, swap iteration for a real topo sort; conflict detection stays.
- Shields and missiles predate the array-classification policy and still emit a single classification token. Rule-code migration is out of scope; test-fixture migration (to `tests/fixtures/<rule>/`) IS in scope.
