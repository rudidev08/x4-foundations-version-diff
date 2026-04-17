# X4 Changelog Pipeline — Design Spec

Date: 2026-04-15
Status: Approved for implementation

## Goal

Given two extracted versions (`x4-data/V1/`, `x4-data/V2/`), produce one human-readable changelog of gameplay-relevant changes. The diff is mechanical (`difflib`); the LLM turns XML/Lua/script diffs into player-readable prose and is the only non-deterministic step.

## Priorities

From CLAUDE.md, in order (higher overrides lower):

1. **Resumable** from any failure based on already-saved files.
2. **Low data loss** — bounded and never silent.
3. **Grouping over dedup.** Empirical measurement showed cross-file duplication is 0–6% of hunks and heuristic similarity catches <2% of those. Dedup is not worth the complexity or the risk of hiding findings. Instead: group related findings clearly so the reader sees the pattern.
4. **Categorization** where it doesn't conflict with 1–3. A finding with no clear category appears in "Other" — never dropped.

## Development rule

- This is a small project. We do not support migrations or backward compatibility for intermediate artifacts, filenames, or output contracts.
- Resumability applies to repeated runs of the same pipeline contract, not to old artifacts after code or contract changes.
- If the pipeline changes its chunk headers, finding format, category rules, schema-map filename, prompt contract, or other on-disk behavior, clear the generated artifacts and rerun.
- Do not add compatibility fallbacks for stale artifacts. Delete and rerun instead.

## Pipeline shape

```
01_enumerate  Walk V1/V2, classify files as added/modified/deleted.
02_diff       Per-file diff (modified) or raw file copy (added/deleted).
03_chunk      File-level grouping, entity-aware splitting, emit LLM-ready chunks.
04_llm        Per-chunk LLM call → entity-labeled findings.   ← only LLM step
05_assemble   Group findings by entity/category, write changelog.
```

Only `04_llm` talks to a model. The other four steps are deterministic and should produce bit-identical output across runs with the same inputs.

Splitting enumerate from diff lets you inspect "what changed" before paying the I/O cost to diff it, and lets cheap deterministic work cache independently of expensive work.

## Config

### Models (`.env`)

Each model is three keys:

- `<KEY>_MODEL_NAME` — Kebab-case display name. Used in output paths and to look up the active model.
- `<KEY>_LLM_CMD` — Full shell command. Prompt piped to stdin; response read from stdout.
- `<KEY>_CHUNK_KB` — Max chunk size in kilobytes. Capability-first: bigger for more capable models.

`DEFAULT_MODEL` selects the active model by matching a `<KEY>_MODEL_NAME`.

### Env vars and CLI flags

Every knob has both an env var and a CLI flag. Precedence: CLI > env > default.

- `DEFAULT_MODEL`
  - CLI: `--model <name>`
  - Default: `opus-max`
  - Effect: active model.
- `SOURCE_PATH_PREFIX`
  - CLI: none
  - Default: `x4-data/`
  - Effect: defines the single canonical source root; `--v1 9.00B4` resolves to `x4-data/9.00B4`.
- `FORCE_SPLIT`
  - CLI: `--force-split`
  - Default: unset
  - Effect: structural-split failures fall back to line-based cuts instead of hard fail.

### Run settings file (`settings.json`)

The first time the pipeline creates an artifact dir for a (V1, V2, model) combo, it writes `settings.json` at the root of that dir:

```json
{
  "v1": "9.00B4",
  "v2": "9.00B5",
  "model_name": "opus-max",
  "llm_cmd": "claude --print --model claude-opus-4-6 --effort max",
  "chunk_kb": 50,
  "force_split": false,
  "created_at": "2026-04-15T12:34:56Z"
}
```

On every subsequent run the pipeline reads `settings.json` and compares it against the resolved config (from `.env` + CLI). Any mismatch aborts with a specific error:

```
[pipeline] ABORT: artifacts/9.00B4_to_9.00B5_opus-max/settings.json disagrees with current config.
  chunk_kb: settings=50, current=40
  llm_cmd:  settings="claude --print --model claude-opus-4-6 --effort max"
            current="claude --print --model claude-opus-4-6 --effort low"
Reset the artifact dir, or restore matching config, before re-running.
```

This stops accidental mixes — e.g. a partial Opus run being continued by Sonnet, producing a corrupt changelog. It is a same-contract safety check, not a migration path for old artifacts after pipeline changes.

Source-path contract: supported usage is one canonical source root with version-named subfolders, e.g. `x4-data/9.00B4` and `x4-data/9.00B5`. Run identity is version-based, not full-path-based. Alternate parallel trees that reuse the same version folder names are out of scope.

## Filter list

### Include (gameplay-relevant content)

All paths are relative to `x4-data/<V>/`. Each also applies under `extensions/ego_dlc_*/`.

- `libraries/*` minus `material_library*` and `sound_library*`
- `md/*`
- `aiscripts/*`
- `ui/**`
- `assets/{units,props,structures}/**/macros/*`
- `t/0001-l044.xml` (English localization only)
- `maps/*`

### Exclude

- `assets/*` not matching the macros pattern above
- `shadergl/`, `index/`, `cutscenes/`
- `material_library*`, `sound_library*`, `sound_env_library*`
- Non-gameplay files: binary, XSD (schema), JS, CSS, HTML

## Working directory layout

```
output/<V1>_to_<V2>_<model>.md          # final changelog

artifacts/<V1>_to_<V2>_<model>/
  settings.json                         # config snapshot (see above)
  01_enumerate/
    enumeration.jsonl                   # list of changed files (input for later steps)
  02_diff/
    diffs/<relative_path>.diff          # unified diff, status "modified"
    diffs/<relative_path>.added         # raw V2 file copy, status "added"
    diffs/<relative_path>.deleted       # raw V1 file copy, status "deleted"
  03_chunk/
    chunks/<chunk_id>.txt               # entities + part info in header
  04_llm/
    findings/<chunk_id>.md              # `[none]` content if no gameplay change
  05_assemble/
    malformed_findings.jsonl            # malformed prefixes tolerated or rejected by step 05
```

Unique per (V1, V2, model). No cross-contamination between runs. `artifacts/` is safe to erase once the changelog is extracted.

## Artifact semantics

No separate manifest files. **Presence of an artifact on disk is the done-marker.** The artifact is both the data and the record that the work happened.

- **Atomic writes.** Every artifact is written to a sibling `.tmp` path and atomically renamed into place. A visible file is always a complete file.
- **Resumability.** Each step walks its input list and skips any unit whose output artifact exists. No state to reconcile — the filesystem is the state. This applies only while the code and artifact contract are unchanged; after pipeline changes, delete artifacts and rerun.
- **Concurrency.** Only one pipeline run against a given artifact dir is supported. Concurrent runs against the same dir are not specced — atomic rename limits damage if it happens by accident, but that's not a design goal.
- **Failure = absence.** A failed unit writes nothing. Re-running retries it automatically. There is no distinction between "never tried" and "tried and failed" because both should be retried.
- **No-changes is still a success.** When the LLM emits `[none]` for a chunk, that content is written to the finding file so presence still means "processed." Assembly skips `[none]` findings.
- **Metadata in headers.** Per-artifact metadata lives in the artifact's own header, not a side file:
  - `02_diff/*.diff` header: `Source` (relative path), `Status`, `V1 bytes`, `V2 bytes`.
  - `02_diff/*.added` / `*.deleted`: the filename extension is the status; file contents are the raw source file; no header.
  - `03_chunk/*.txt` header: `Chunk` (path + `part N/M`), `Entities`; recursive sub-parts also carry `Sub-part`. For schema-backed modified XML, `Entities` / `Allowed prefixes JSON` can include deleted-side V1 entity labels in addition to the V2 entities that drove grouping.
  - `04_llm/*.md`: no header. The `[entity:key]` prefixes and the `[none]` marker carry the metadata.

## Step contracts

### 01_enumerate

`01_enumerate` walks both source trees under the filter list and produces a single manifest of changed files. No content is read — only directory walk plus `stat`. Each entry records the relative path, status (`added` / `modified` / `deleted`), and byte size on each side (`v1_bytes`, `v2_bytes`; zero on the absent side). The manifest is the worklist for every downstream step.

Byte sizes let you scan for outliers (`jq 'select(.v2_bytes > 500000)'`) before paying any diff cost.

**Inputs:** `x4-data/<V1>/`, `x4-data/<V2>/`, filter list.

**Process:**
1. Walk V2 filtered tree. For each file, check V1:
   - Present in both, bytes differ → `modified`.
   - Present in both, bytes identical → skip.
   - V2 only → `added`.
2. Walk V1 filtered tree for files missing in V2 → `deleted`.
3. Write one JSON line per changed file to a temp file: `{path, status, v1_bytes, v2_bytes}`. Atomically rename to `enumeration.jsonl`.

**Outputs:** `01_enumerate/enumeration.jsonl`.

**Resumability:** If `enumeration.jsonl` exists, the step is a no-op. Enumeration is cheap — delete the file to force a re-scan.

### 02_diff

Produces one file per changed path. The file extension encodes the status, so fully-added or fully-deleted files aren't carried as 500 KB of `+` / `-` prefixes — we save the raw file instead.

- `modified` → `<path>.diff` — unified diff with a header.
- `added` → `<path>.added` — raw V2 file content; no header (filename is the marker).
- `deleted` → `<path>.deleted` — raw V1 file content; no header.

**Inputs:** `01_enumerate/enumeration.jsonl`, `x4-data/<V1>/`, `x4-data/<V2>/`.

**Process per enumeration entry:**
1. If an artifact with any of the three extensions exists for this path → skip.
2. `modified` → run `difflib.unified_diff(V1_lines, V2_lines, n=3)`, prepend header, write `<path>.diff`.
3. `added` → copy V2 file bytes to `<path>.added`.
4. `deleted` → copy V1 file bytes to `<path>.deleted`.
5. All writes go to `<path>.<ext>.tmp`, then atomic rename.

**Modified diff header:**
```
# Source: <relative_path>
# Status: modified
# V1 bytes: <N> | V2 bytes: <N>
# ─────────────────────────────────────
<unified diff content>
```

V1/V2 bytes stay in the header so you can grep `V2 bytes:` across all diffs to spot outliers (a 600 KB rewrite vs. a 50 B tweak) without opening content.

**Outputs:** `02_diff/diffs/*.{diff,added,deleted}`.

**Resumability:** Skip paths whose artifact (any of the three extensions) exists.

### 03_chunk

**Inputs:** `02_diff/diffs/`, `CHUNK_KB`.

**Process per input file:**
1. Read the diff/added/deleted file.
2. If bytes ≤ `CHUNK_KB`, still check chunk complexity. Files that fit the byte budget can still be too dense for weaker local LLMs and must split.
3. Complexity caps:
   - at most 6 entity labels in one chunk
   - at most about 6 KB of chunk body
   - at most about 30 changed lines in one chunk
   - at most 3 diff hunks in one chunk
   - plus a weighted complexity score across entities, lines, and hunks so "moderately bad at everything" still splits
4. If either the byte cap or any complexity cap is exceeded → activate splitter.
5. A small central profile table can tighten those caps for proven weak-model outliers (currently `libraries/jobs.xml` after DLC normalization).

#### Splitter

Priority order — try each level, fall to the next on failure. "Pack" below means a simple linear fill: walk the candidate regions in order, add each to the current chunk until the next addition would exceed `CHUNK_KB`, then start a new chunk. No backtracking or optimization.

**Level 1: Schema-hinted split.**
- Look up `{basename → (entity_tag, id_attribute)}` in the schema map (see `src/x4_schema_map.generated.json`).
- Walk V2 source file with `xml.parsers.expat`, build `(start_line, end_line, entity_key)` intervals for the preferred entity tag.
- For modified XML, chunk grouping still stays V2-based, but the emitted chunk header can union in deleted-side V1 entity labels so the LLM may name removals precisely instead of collapsing to file scope.
- For singleton macro XML files outside the schema map: parse the macro, then let the X4 macro rule modules promote `macro:<name>` to a semantic label like `ship:...`, `ware:...`, or `module:...` when the path + class match a safe family rule.
- If no macro family rule claims a singleton macro, keep the fallback `macro:<raw-name>` label.
- For DLC `<diff>` patch files: extract entity key from `<add sel="...[@id='X']...">` via regex on the innermost `[@id='X']` selector. Nested entities inside `<add>` blocks get their own keys.
- Group diff hunks by enclosing entity. Pack entity groups into parts ≤ `CHUNK_KB`.
- If a single entity exceeds `CHUNK_KB`: recurse, splitting at the entity's child-element boundaries.

**Example — `libraries/wares.xml` diff, 180 KB, `CHUNK_KB=64`.**

V2 shape:
```
<wares>
  <ware id="weapon_arg_l_beam_01">...</ware>    <!-- lines 12-48 -->
  <ware id="weapon_arg_l_beam_02">...</ware>    <!-- lines 49-73 -->
  <ware id="shield_gen_s_mk1">...</ware>        <!-- lines 74-120 -->
  ...
</wares>
```

Schema map says `wares.xml → (ware, id)`. Expat records intervals; each diff hunk falls inside one interval; hunks above the first `<ware>` go to a synthetic `ware:__preamble__`. Pack into parts ≤ 64 KB.

Produced chunks:
```
03_chunk/chunks/libraries__wares.xml__part1of3.txt
  # Chunk: libraries/wares.xml part 1/3
  # Entities (12): ware:weapon_arg_l_beam_01, ware:weapon_arg_l_beam_02, ...

03_chunk/chunks/libraries__wares.xml__part2of3.txt
  # Chunk: libraries/wares.xml part 2/3
  # Entities (10): ware:shield_gen_s_mk1, ware:shield_gen_m_mk1, ...

03_chunk/chunks/libraries__wares.xml__part3of3.txt
  # Chunk: libraries/wares.xml part 3/3
  # Entities (8): ware:engine_par_m_01, ...
```

Recursive case: `libraries/constructionplans.xml` has `plan:wharf_xxl_megastation` at 180 KB on its own. The splitter recurses inside that entity, cutting at `<entry>` child boundaries:
```
03_chunk/chunks/libraries__constructionplans.xml__part14of24.txt
  # Chunk: libraries/constructionplans.xml part 14/24
  # Sub-part: 1/4 of plan:wharf_xxl_megastation (split at <entry> boundaries)
```

**Level 2: Generic XML split.**
- Walk diff content line-by-line, tracking nest depth via `<tag>` / `</tag>` / `<tag/>` events.
- Identify cut points where depth returns to `(min_depth_in_hunk + 1)`.
- Pack from cut point to cut point into parts ≤ `CHUNK_KB`.

**Example — `md/Setup.xml` diff, 120 KB, `CHUNK_KB=64`, no schema hint.**

Source shape:
```
<mdscript>
  <cues>
    <cue name="A"><actions>...</actions></cue>
    <cue name="B"><actions>...</actions></cue>
    ...
  </cues>
</mdscript>
```

Depth walk: `<mdscript>` 0→1, `<cues>` 1→2, `<cue>` 2→3, `</cue>` 3→2. `min_depth_in_hunk = 2`, so the splitter cuts where depth returns to 3 (each `</cue>` close).

Produced chunks:
```
03_chunk/chunks/md__Setup.xml__part1of2.txt
  # Chunk: md/Setup.xml part 1/2
  # Entities (3): lines:12-410, lines:411-790, lines:791-1050

03_chunk/chunks/md__Setup.xml__part2of2.txt
  # Chunk: md/Setup.xml part 2/2
  # Entities (2): lines:1051-1620, lines:1621-2100
```

Generic split doesn't invent entity keys. Findings from these chunks get prefixed with `[file:md/Setup.xml]` and assembly buckets by file-path heuristic.

**Level 3: Lua split.**
- Cut at blank lines or lines matching `^function ` / `^local function `.
- Pack.

**Example — `aiscripts/lib_combat.lua` diff, 88 KB, `CHUNK_KB=64`.**

```lua
local M = {}

local function helper_a() ... end
local function helper_b() ... end
function M.public_entry() ... end

return M
```

Produced chunks:
```
03_chunk/chunks/aiscripts__lib_combat.lua__part1of2.txt
  # Chunk: aiscripts/lib_combat.lua part 1/2
  # Entities (2): lua:helper_a, lua:helper_b

03_chunk/chunks/aiscripts__lib_combat.lua__part2of2.txt
  # Chunk: aiscripts/lib_combat.lua part 2/2
  # Entities (1): lua:public_entry
```

**Level 4: Force-split** (only when `FORCE_SPLIT=1` or `--force-split`).
- Cut at any blank line or closing tag. Pack.
- Chunk header carries `# WARNING: force-split — cut at line boundaries, not structural`.

**Level 5: Hard fail** (default when levels 1–3 fail and force-split is not set).
```
[03_chunk] FAIL: <file> lines <N>-<M> exceed CHUNK_KB=<X> (<Y>KB).
  Tried split at: <tags attempted>.
  Options: raise CHUNK_KB, exclude file, set FORCE_SPLIT=1, add custom splitter.
```

Chunks already written for prior files stay on disk. Re-run resumes from the failed file.

#### Schema map

The `{basename → (entity_tag, id_attribute)}` table lives in `src/x4_schema_map.generated.json`. It's generated by scanning the source trees — don't hand-edit. The file also records when the scan last ran and which source versions it saw.

File shape:
```json
{
  "last_scanned_at": "2026-04-15T12:00:00Z",
  "scanned_sources": ["x4-data/9.00B5"],
  "entries": [
    {"file": "libraries/constructionplans.xml", "entity_tag": "plan", "id_attribute": "id"},
    {"file": "libraries/wares.xml",             "entity_tag": "ware", "id_attribute": "id"}
  ]
}
```
Entries sort alphabetically by `file`.

To rescan (when a new source version lands, or a new DLC is added): use the `rescan-schema` skill. It takes a source root path, re-runs the scan, and writes the updated map.

Files not in the schema map (e.g. `parameters.xml`, `god.xml`) have no per-entity boundaries; they stay file-level and fall through to generic XML split if oversized. Path-known families can still expose stable semantic labels without schema entries; for example schema-less `aiscripts/*.xml` chunks use `aiscript:<stem>` as their allowed entity prefix even when the body is packed by generic line/depth boundaries.

Singleton macro files are the exception: they still get level-1 labels without a schema-map entry because the chunker can parse the lone `<macro ...>` and apply the X4 macro-family rule registry. Safe matches promote to semantic labels; unmatched macros stay `macro:`.

#### Chunk header format

`part N/M` — **N** is the current part index (1-based); **M** is the total number of parts this source file produced. `part 3/7` means "the third of seven chunks for this file." Single-chunk files are `part 1/1`.

Standard chunk:
```
# Chunk: <relative_file_path> part <N>/<M>
# Entities (<count>): <entity:key>, <entity:key>, ...
# Allowed prefixes JSON: ["<entity:key>", "file:<relative_file_path>", ...]
# ─────────────────────────────────────
<diff content>
```

Unsplit file (part 1/1):
```
# Chunk: libraries/regionyields.xml part 1/1
# Entities: entire file
# ─────────────────────────────────────
```

Recursive sub-part (single entity too big):
```
# Chunk: libraries/constructionplans.xml part 14/24
# Sub-part: 2/4 of plan:wharf_xxl_megastation (split at <entry> boundaries)
# ─────────────────────────────────────
```

The header is the only metadata the LLM sees. `Chunk` gives domain context from the path; `Entities` is the short human summary; `Allowed prefixes JSON` is the exact label vocabulary the LLM must draw its `[entity:key]` prefixes from; `Sub-part` signals it's looking at a fragment of one entity. We do **not** send byte counts or a redundant `Group:` path — they're noise in the prompt.

`Entities` is a human summary and is capped at 10 inline; if it exceeds 10 the header shows `first 10, +N more`.
`Allowed prefixes JSON` is the authoritative machine-readable list consumed by step 04 and step 05. It includes the full allowed entity set plus the `file:<relative-path>` fallback.

**Chunk IDs** are derived deterministically from the diff path and part number, e.g. `libraries__wares.xml__part3of7`. Stable across runs as long as `CHUNK_KB` is unchanged.

**Outputs:** `03_chunk/chunks/*.txt`.

**Resumability:** For each input file, compute its full set of chunk IDs. Skip the input if every expected chunk file already exists. If any are missing, re-chunk (the splitter is deterministic; existing chunks will be rewritten with identical content).

### 04_llm

**Inputs:** `03_chunk/chunks/`, `LLM_CMD`.

**Process per chunk:**
1. If `findings/<chunk_id>.md` already exists → skip.
2. Read chunk file.
3. Pipe its content to `LLM_CMD` via stdin. Read response from stdout.
4. If the first response is `[none]` for a tiny, entity-scoped diff chunk (real entity prefix present; low changed-line / hunk count), do one immediate retry with a stricter "recheck carefully" addendum. This is a bounded false-negative recovery path for weaker local models.
5. The retry consumes the same `--llm-calls` budget as any other fresh call. If the first response says "retry required" but the budget is exhausted before the retry can happen, write nothing and leave the chunk pending. Re-running step 04 retries that chunk from the start; the first response alone is not a done-marker.
6. On success (non-empty stdout, zero exit): write the final response to `findings/<chunk_id>.md.tmp`, atomic rename. If the response is just `[none]`, that literal content is the file body — presence still means "processed."
7. On failure (non-zero exit or empty stdout): write nothing. Re-run retries.

**Prompt contract.** The system prompt instructs the LLM to:
- Describe each gameplay-relevant change as a separate finding.
- Prefix each finding with `[entity:key]` drawn from the chunk header's `Allowed prefixes JSON` list.
- Use player-readable labels. Prefer display names when they appear in the diff. Raw IDs (`weapon_arg_l_beam_01`, ware id `medicalsupplies`, faction id `argon`, macro ids) are fine when that's the clearest name. Don't paste raw XML tag/attribute paths (`<ware><production><primary>`).
- Treat small but meaningful changes as gameplay-relevant: loadout variation, quotas, hostility/relations, attack permissions, gamestart/station/job/script renames, and similar one-line AI/spawn changes are not auto-`[none]`.
- Do not invent hidden defaults or old values that are not shown in the chunk.
- If no gameplay-relevant change exists in this chunk, emit `[none]`.

The `[entity:key]` prefix is the wire format between the LLM and the deterministic assembly step. `05_assemble` parses the prefix to bucket findings by entity and to map them to categories via pattern matching (`ware:weapon_*` → Weapons). Without it, assembly would have to NLP-infer the subject of each finding — not worth the unreliability.

Exact prompt wording is an implementation detail, not pinned here.

**Outputs:** `04_llm/findings/*.md`.

**Resumability:** Skip chunks whose finding file exists. Missing findings = never processed, previously failed, or intentionally left pending because a bounded retry could not be completed under `--llm-calls`. In all three cases, the next run retries the whole chunk.

**Concurrency:** The first call runs sequentially and shows its input + response to the user for approval. After approval, the remaining chunks run across `--workers` workers (default 4) via a thread pool — each worker handles a distinct chunk, so they never collide. Atomic rename keeps accidents harmless.

**Failure handling:** Any LLM call that exits non-zero or returns empty stdout aborts the pipeline with the full command, returncode, stdout, and stderr. Partial findings from concurrent workers that already completed stay on disk; re-running picks up where the pipeline left off.

### 05_assemble

**Inputs:** `03_chunk/chunks/` (for chunk IDs and headers), `04_llm/findings/`.

**Process:**
1. Walk chunk files to get the full set of chunk IDs. Count chunks missing findings → `failed_count` in the footer.
2. Read each existing finding. Skip findings whose body is exactly `[none]`.
3. Split each finding file into one or more finding blocks; each block begins with a bracket prefix line and contains the bullet(s) that follow it.
4. Validate each block's prefix against the chunk header's visible entity list plus the sanctioned `[file:<source-path>]` fallback.
5. Tolerant mode: malformed blocks are always recorded in `05_assemble/malformed_findings.jsonl`; clearly broken prefixes are normalized to `[file:<source-path>]`, while syntactically-valid but unapproved prefixes are kept as-is for display.
6. Strict mode (`--strict-findings`): if any malformed block exists, write `malformed_findings.jsonl`, abort with a non-zero exit, and do not overwrite the final changelog.
7. Strip the bracket prefix line from rendered markdown so the final changelog is player-facing.
8. Assign each finding block to a category (see mapping below).
9. Within each category, sub-group findings by entity.
10. If one entity fans in from multiple chunks/sub-parts, run a deterministic condenser before rendering: remove exact duplicates and prefer concrete bullets over overlapping speculative ones.
11. Sort entities alphabetically within each category.
12. Write changelog to `output/<V1>_to_<V2>_<model>.md.tmp`, atomic rename.

**No dedup.** All findings appear in the output. Related findings appear adjacent through entity-grouping.

**Resumability:** Pure function of the existing chunks and findings. Cheap to re-run.

#### Category mapping

First matching rule wins. Findings with no parseable entity prefix use file-path heuristics. Unmatched findings go to "Other" — never dropped.

- **Game Starts** — `gamestart:*` plus gamestart-tagged `station:*` / `plan:*`; file in `libraries/gamestarts.xml`, `md/setup_gamestarts.xml`, `md/gs_*`
- **Weapons & Equipment** — `ware:weapon_*`, `ware:turret_*`, `ware:missile_*`, `ware:shield_*`, `ware:engine_*`, `ware:thruster_*`, `effect:*`, `influencelist:*`; file in engine/shield/weapon asset paths
- **Ships, Stations & Modules** — `ship:*`, `module:*`, `station:*`, `plan:*`; `macro:ship_*`, `macro:struct_*`; file in `assets/units/ships/`, `assets/structures/`
- **Jobs & Spawns** — `job:*`; file in `libraries/jobs.xml`
- **Wares & Economy** — `ware:*` not matching Weapons & Equipment
- **Factions & Diplomacy** — `faction:*`, `race:*`, `people:*`; file in `libraries/factions.xml`, `libraries/diplomacy.xml`
- **Map & Sectors** — `region:*`, `definition:*`, `dataset:*`, `group:*`; file in `maps/`, `libraries/mapdefaults.xml`, `libraries/region*.xml`
- **Missions & Scripts** — `aiscript:*`; file in `md/`, `aiscripts/`, `libraries/aicompat.xml`
- **UI** — file in `ui/`, `t/0001-l044.xml`
- **Other** — everything else

#### Changelog format

```markdown
# Changelog: <V1> → <V2>

Generated by <model_name> on <date>.

## Weapons & Equipment

### Turret Arg L Beam MK1
- Damage reduced from 50 to 40.
- (Split DLC) Closedloop production recipe added.
- (Pirate DLC) Production costs adjusted.

### Generic Dumbfire S MK1
- Now produced by Split, Terran, Pirate, Boron factions.

## Wares & Economy

### Medical Supplies
- Production time reduced from 60s to 45s.

## Map & Sectors
...

## Other
- (libraries/effects.xml) New explosion effect added.

---
<model_name> | <chunk_count> chunks | <finding_count> findings | <malformed_count> malformed findings tolerated | <failed_count> failed chunks
```

**Outputs:** `output/<V1>_to_<V2>_<model>.md`, `artifacts/<run>/05_assemble/malformed_findings.jsonl`.
