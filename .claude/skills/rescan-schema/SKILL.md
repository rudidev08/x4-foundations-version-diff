---
name: rescan-schema
description: Rescan an X4 source tree to regenerate src/schema_map.json — the table of {XML basename → (entity_tag, id_attribute)} that the 03_chunk level-1 splitter depends on. Use when a new X4 source version or DLC is added, or when the existing map misses a file the chunker is failing to split.
---

# Rescan the schema map

`src/schema_map.json` lists every XML file in `x4-data/` whose top level is a repeating id-bearing element the chunker can cut along. `03_chunk` uses it for structural splitting; missing entries fall back to generic XML split (lower quality) or force-split / hard-fail on oversize files.

The map is hand-curated today. This skill replaces it with a generated scan result and records when the scan ran and which source roots it saw.

## When to invoke

- A new X4 source version has been extracted into `x4-data/<VERSION>/`.
- A new DLC directory appeared under `x4-data/<VERSION>/extensions/ego_dlc_*/`.
- `03_chunk` is failing on an oversize file with no schema entry.
- The user says "rescan the schema" or "update schema_map.json".

## Inputs

- One or more source root paths to scan, e.g. `x4-data/9.00B5` (includes its DLC subtrees automatically).

The new scan fully replaces the old map — no merging. Pass every source root you want represented in one invocation.

## Process

1. Confirm with the user which source roots to scan. Show the current `scanned_sources` from `src/schema_map.json` for reference.
2. Run the scanner:

   ```
   python3 src/_scan_schema.py --source x4-data/<VERSION> --out src/schema_map.json
   ```

   Repeat `--source` for every root you want represented in one invocation (the new scan fully replaces the old map — no merging). The scanner walks `libraries/`, `md/`, `maps/`, and every `extensions/ego_dlc_*/{libraries,md}/` under each source, parses each XML file with `xml.parsers.expat`, and records files whose root holds a repeating id-bearing direct child. `<diff>`-rooted DLC patches are skipped, and anything `_lib.should_include` rejects (material/sound library noise, non-gameplay extensions) is filtered. See `src/_scan_schema.py` for the qualifying rule.
3. Show a diff summary against the previous map: files added, removed, or with changed entity tag / id attribute. Flag anything surprising for the user to review.

## Output

Overwrites `src/schema_map.json`. No other files touched.

## Failure modes

- Source root doesn't exist → scanner exits non-zero, `src/schema_map.json` untouched. Error includes the missing path.
- A single malformed XML file → scanner reports the parse-failure count to stderr and skips that file. The existing map is replaced with whatever qualified; re-run after the offending file is fixed if you need it back.
- A file's top level has no repeating id-bearing child, or is ambiguous (multiple qualifying child tags) → silently skipped (not an error, just not a splittable schema).
- `<diff>`-rooted DLC files → always skipped; 03_chunk handles those via the `sel` selector path, not repeating children.

## Verification

After the scan, spot-check by running the level-1 splitter on one of the known tail offenders (`libraries/constructionplans.xml`, `libraries/effects.xml`). If the generated chunks have sensible `Entities` lists with real ids, the scan is good.
