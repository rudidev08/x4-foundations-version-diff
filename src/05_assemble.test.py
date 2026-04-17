#!/usr/bin/env python3
"""
Test 05_assemble — parse findings, bucket by category/entity, render markdown.

Run:
    python3 src/05_assemble.test.py

CLI shape exercised:
    python3 src/05_assemble.py --out DIR \\
        --v1-name 9.00B4 --v2-name 9.00B5 --model opus-max \\
        [--strict-findings] --changelog output/changelog.md
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
SCRIPT = HERE / "05_assemble.py"


def plant_chunk(
    out: Path,
    cid: str,
    source_path: str,
    entities: tuple[str, ...] = (),
    *,
    allowed_prefixes: tuple[str, ...] | None = None,
    truncate_display_after: int | None = None,
    part: int = 1,
    total: int = 1,
    sub_part: str | None = None,
):
    path = out / "03_chunk" / "chunks" / f"{cid}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    if entities:
        shown = entities
        suffix = ""
        if truncate_display_after is not None and len(entities) > truncate_display_after:
            shown = entities[:truncate_display_after]
            suffix = f", +{len(entities) - truncate_display_after} more"
        ent_line = f"# Entities ({len(entities)}): {', '.join(shown)}{suffix}\n"
    else:
        ent_line = "# Entities: entire file\n"
    if allowed_prefixes is None:
        fallback = (f"file:{source_path}",)
        allowed_prefixes = entities + tuple(p for p in fallback if p not in entities)
    allowed_line = f"# Allowed prefixes JSON: {json.dumps(list(allowed_prefixes))}\n"
    sub_part_line = f"# Sub-part: {sub_part}\n" if sub_part else ""
    path.write_text(
        f"# Chunk: {source_path} part {part}/{total}\n"
        f"{sub_part_line}"
        f"{ent_line}"
        f"{allowed_line}"
        "# ─────────────────────────────────────\n"
        "...\n"
    )


def plant_finding(out: Path, cid: str, body: str):
    path = out / "04_llm" / "findings" / f"{cid}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


def run_assemble(out: Path, changelog: Path, strict: bool = False):
    cmd = [
        sys.executable, str(SCRIPT),
        "--out", str(out),
        "--v1-name", "9.00B4", "--v2-name", "9.00B5",
        "--model", "opus-max",
        "--changelog", str(changelog),
    ]
    if strict:
        cmd.append("--strict-findings")
    return subprocess.run(cmd, capture_output=True, text=True)


def malformed_report(out: Path) -> Path:
    return out / "05_assemble" / "malformed_findings.jsonl"


class AssembleTest(unittest.TestCase):
    def test_categories_and_entities_grouped_without_prefix_leakage(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "art"
            changelog = Path(tmpdir) / "out.md"

            plant_chunk(
                out, "libraries__wares.xml__part1of1", "libraries/wares.xml",
                ("ware:weapon_arg_l_beam_01",),
            )
            plant_finding(
                out, "libraries__wares.xml__part1of1",
                "[ware:weapon_arg_l_beam_01]\n- Damage reduced from 50 to 40.\n",
            )

            plant_chunk(
                out, "libraries__ships.xml__part1of1", "libraries/ships.xml",
                ("ship:ship_arg_xl_carrier_01_a",),
            )
            plant_finding(
                out, "libraries__ships.xml__part1of1",
                "[ship:ship_arg_xl_carrier_01_a]\n- New carrier variant.\n",
            )

            r = run_assemble(out, changelog)
            self.assertEqual(r.returncode, 0, r.stderr)

            text = changelog.read_text()
            self.assertIn("# Changelog: 9.00B4 → 9.00B5", text)
            self.assertIn("## Weapons & Equipment", text)
            self.assertIn("### ware:weapon_arg_l_beam_01\n- Damage reduced from 50 to 40.", text)
            self.assertIn("## Ships, Stations & Modules", text)
            self.assertIn("### ship:ship_arg_xl_carrier_01_a\n- New carrier variant.", text)
            self.assertNotIn("[ware:weapon_arg_l_beam_01]", text)
            self.assertNotIn("[ship:ship_arg_xl_carrier_01_a]", text)
            self.assertLess(text.index("## Weapons"), text.index("## Ships"))
            self.assertIn(
                "opus-max | 2 chunks | 2 findings | 0 malformed findings tolerated | 0 failed chunks",
                text,
            )

    def test_multiple_findings_in_one_file_are_split_and_counted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "art"
            changelog = Path(tmpdir) / "out.md"

            plant_chunk(
                out, "libraries__regionyields.xml__part1of1", "libraries/regionyields.xml",
                ("definition:sphere_a", "definition:sphere_b"),
            )
            plant_finding(
                out, "libraries__regionyields.xml__part1of1",
                "[definition:sphere_a]\n- A changed.\n\n[definition:sphere_b]\n- B changed.\n",
            )

            r = run_assemble(out, changelog)
            self.assertEqual(r.returncode, 0, r.stderr)

            text = changelog.read_text()
            self.assertIn("### definition:sphere_a\n- A changed.", text)
            self.assertIn("### definition:sphere_b\n- B changed.", text)
            self.assertIn(
                "1 chunks | 2 findings | 0 malformed findings tolerated | 0 failed chunks",
                text,
            )

    def test_none_findings_skipped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "art"
            changelog = Path(tmpdir) / "out.md"
            plant_chunk(out, "c1", "libraries/wares.xml")
            plant_finding(out, "c1", "[none]")
            plant_chunk(out, "c2", "libraries/wares.xml", ("ware:weapon_x",))
            plant_finding(out, "c2", "[ware:weapon_x]\n- Kept.\n")

            r = run_assemble(out, changelog)
            self.assertEqual(r.returncode, 0, r.stderr)
            text = changelog.read_text()
            self.assertNotIn("[none]", text)
            self.assertNotIn("[ware:weapon_x]", text)
            self.assertIn("### ware:weapon_x\n- Kept.", text)
            self.assertIn(
                "2 chunks | 1 findings | 0 malformed findings tolerated | 0 failed chunks",
                text,
            )

    def test_failed_chunks_counted_in_footer(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "art"
            changelog = Path(tmpdir) / "out.md"
            plant_chunk(out, "c1", "libraries/wares.xml", ("ware:weapon_x",))
            plant_finding(out, "c1", "[ware:weapon_x]\n- ok\n")
            plant_chunk(out, "c2", "libraries/ships.xml")

            r = run_assemble(out, changelog)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn(
                "2 chunks | 1 findings | 0 malformed findings tolerated | 1 failed chunks",
                changelog.read_text(),
            )

    def test_module_prefix_renders_under_ships_stations_and_modules(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "art"
            changelog = Path(tmpdir) / "out.md"
            source_path = "assets/props/StorageModules/macros/storage_par_l_miner_liquid_02_a_macro.xml"

            plant_chunk(out, "storage__part1of1", source_path, ("module:storage_par_l_miner_liquid_02_a",))
            plant_finding(
                out,
                "storage__part1of1",
                "[module:storage_par_l_miner_liquid_02_a]\n- Cargo capacity increased.\n",
            )

            r = run_assemble(out, changelog, strict=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            text = changelog.read_text()
            self.assertIn("## Ships, Stations & Modules", text)
            self.assertIn("### module:storage_par_l_miner_liquid_02_a\n- Cargo capacity increased.", text)

    def test_macro_leftovers_still_fall_back_by_source_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "art"
            changelog = Path(tmpdir) / "out.md"
            source_path = "extensions/ego_dlc_terran/assets/units/size_l/macros/storage_ter_l_miner_liquid_01_a_macro.xml"

            plant_chunk(out, "ambiguous_storage__part1of1", source_path, ("macro:storage_ter_l_miner_liquid_01_a_macro",))
            plant_finding(
                out,
                "ambiguous_storage__part1of1",
                "[macro:storage_ter_l_miner_liquid_01_a_macro]\n- Internal storage values changed.\n",
            )

            r = run_assemble(out, changelog, strict=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            text = changelog.read_text()
            ships_idx = text.index("## Ships, Stations & Modules")
            heading_idx = text.index("### macro:storage_ter_l_miner_liquid_01_a_macro")
            self.assertLess(ships_idx, heading_idx)
            self.assertIn("- Internal storage values changed.", text)

    def test_tolerant_mode_normalizes_malformed_prefix_and_writes_report(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "art"
            changelog = Path(tmpdir) / "out.md"

            plant_chunk(out, "aiscripts__move.gate.xml__part1of1", "aiscripts/move.gate.xml")
            plant_finding(
                out, "aiscripts__move.gate.xml__part1of1",
                "[aiscripts/move.gate.xml]\n- Ships now avoid exiting too close to the gate.\n",
            )

            r = run_assemble(out, changelog)
            self.assertEqual(r.returncode, 0, r.stderr)

            text = changelog.read_text()
            self.assertIn("### file:aiscripts/move.gate.xml\n- Ships now avoid exiting too close to the gate.", text)
            self.assertNotIn("[aiscripts/move.gate.xml]", text)
            self.assertIn(
                "1 chunks | 1 findings | 1 malformed findings tolerated | 0 failed chunks",
                text,
            )

            entries = [
                json.loads(line)
                for line in malformed_report(out).read_text().splitlines()
                if line.strip()
            ]
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["got"], "[aiscripts/move.gate.xml]")
            self.assertEqual(entries[0]["rendered_as"], "file:aiscripts/move.gate.xml")
            self.assertEqual(entries[0]["finding_file"], "04_llm/findings/aiscripts__move.gate.xml__part1of1.md")

    def test_tolerant_mode_keeps_useful_unapproved_prefixes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "art"
            changelog = Path(tmpdir) / "out.md"

            plant_chunk(out, "shield__part1of1", "extensions/ego_dlc_boron/assets/props/surfaceelements/macros/shield_bor_xl_standard_01_mk1_macro.xml")
            plant_finding(
                out, "shield__part1of1",
                "[ware:shield_bor_xl_standard_01_mk1]\n- Recharge delay reduced.\n",
            )

            r = run_assemble(out, changelog)
            self.assertEqual(r.returncode, 0, r.stderr)

            text = changelog.read_text()
            self.assertIn("## Weapons & Equipment", text)
            self.assertIn("### ware:shield_bor_xl_standard_01_mk1\n- Recharge delay reduced.", text)
            self.assertIn("1 malformed findings tolerated", text)

            entries = [
                json.loads(line)
                for line in malformed_report(out).read_text().splitlines()
                if line.strip()
            ]
            self.assertEqual(entries[0]["rendered_as"], "ware:shield_bor_xl_standard_01_mk1")

    def test_fence_wrapped_findings_render_clean_with_no_malformed_entry(self):
        # Regression: opus sometimes wraps the whole response in ```. The leading
        # fence used to log a phantom malformed entry, and the trailing fence
        # leaked into the bullet body and out into the changelog as an orphan ```.
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "art"
            changelog = Path(tmpdir) / "out.md"

            plant_chunk(out, "shield__part1of1", "libraries/wares.xml", ("ware:shield_x",))
            plant_finding(
                out, "shield__part1of1",
                "```\n[ware:shield_x]\n- Recharge rate increased from 79 to 86.\n```\n",
            )

            plant_chunk(out, "lua__part1of1", "ui/foo.lua", ("lua:menu.bar",))
            plant_finding(
                out, "lua__part1of1",
                "```markdown\n[lua:menu.bar]\n- Filter title shifted left.\n```",
            )

            r = run_assemble(out, changelog)
            self.assertEqual(r.returncode, 0, r.stderr)

            text = changelog.read_text()
            self.assertIn("### ware:shield_x\n- Recharge rate increased from 79 to 86.", text)
            self.assertIn("### lua:menu.bar\n- Filter title shifted left.", text)
            self.assertNotIn("```", text)
            self.assertIn("0 malformed findings tolerated", text)
            self.assertEqual(malformed_report(out).read_text(), "")

    def test_strict_findings_reject_malformed_prefixes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "art"
            changelog = Path(tmpdir) / "out.md"

            plant_chunk(out, "libraries__jobs.xml__part1of1", "libraries/jobs.xml", ("file:libraries/jobs.xml",))
            plant_finding(
                out, "libraries__jobs.xml__part1of1",
                "[file:entire file]\n- Several jobs now allow loadout variation.\n",
            )

            r = run_assemble(out, changelog, strict=True)
            self.assertNotEqual(r.returncode, 0)
            self.assertFalse(changelog.exists())
            self.assertIn("05_assemble: ERROR: 1 malformed findings found; changelog not written.", r.stderr)
            self.assertIn("got:      [file:entire file]", r.stderr)
            self.assertIn("expected: file:libraries/jobs.xml", r.stderr)
            self.assertTrue(malformed_report(out).exists())

    def test_strict_findings_reject_prefix_not_in_entity_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "art"
            changelog = Path(tmpdir) / "out.md"

            plant_chunk(out, "ships__part1of1", "libraries/ships.xml", ("ship:ship_arg_xl_carrier_01_a",))
            plant_finding(out, "ships__part1of1", "[ship:ship_tel_xl_carrier_01_a]\n- Wrong ship.\n")

            r = run_assemble(out, changelog, strict=True)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("ship:ship_arg_xl_carrier_01_a", r.stderr)
            self.assertIn("file:libraries/ships.xml", r.stderr)

    def test_allowed_prefixes_json_accepts_entities_beyond_display_cap(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "art"
            changelog = Path(tmpdir) / "out.md"
            entities = tuple(f"definition:sphere_{i}" for i in range(12))

            plant_chunk(
                out,
                "regionyields__part1of1",
                "libraries/regionyields.xml",
                entities,
                truncate_display_after=10,
            )
            plant_finding(
                out,
                "regionyields__part1of1",
                "[definition:sphere_11]\n- Late-listed entity still accepted.\n",
            )

            r = run_assemble(out, changelog, strict=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            text = changelog.read_text()
            self.assertIn("### definition:sphere_11\n- Late-listed entity still accepted.", text)
            self.assertIn("0 malformed findings tolerated", text)

    def test_file_fallback_categorizes_dlc_asset_paths_after_normalization(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "art"
            changelog = Path(tmpdir) / "out.md"
            source_path = "extensions/ego_dlc_split/assets/props/Engines/macros/engine_spl_s_combat_01_mk3_macro.xml"

            plant_chunk(out, "engine__part1of1", source_path)
            plant_finding(
                out,
                "engine__part1of1",
                f"[file:{source_path}]\n- Engine tuning changed.\n",
            )

            r = run_assemble(out, changelog)
            self.assertEqual(r.returncode, 0, r.stderr)
            text = changelog.read_text()
            self.assertIn("## Weapons & Equipment", text)
            self.assertIn(f"### file:{source_path}\n- Engine tuning changed.", text)

    def test_split_entity_prefers_concrete_bullets_over_speculative_overlap(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "art"
            changelog = Path(tmpdir) / "out.md"
            source_path = "libraries/aicompat.xml"
            label = "aiscript:order.fight.escort"

            plant_chunk(
                out,
                "aicompat__part1of2",
                source_path,
                (label,),
                part=1,
                total=2,
                sub_part="1/2 of aiscript:order.fight.escort (split at <attention> boundaries)",
            )
            plant_finding(
                out,
                "aicompat__part1of2",
                f"[{label}]\n- Escort combat AI no longer defines a separate `unknown`-attention branch in this section, which suggests ships using this script may now handle off-screen escort fighting differently from before.\n",
            )

            plant_chunk(
                out,
                "aicompat__part2of2",
                source_path,
                (label,),
                part=2,
                total=2,
                sub_part="2/2 of aiscript:order.fight.escort (split at <attention> boundaries)",
            )
            plant_finding(
                out,
                "aicompat__part2of2",
                f"[{label}]\n- Escort combat behaviour now runs at `unknown` attention, so escorted ships can continue this fight/escort AI logic even when they are not actively simulated near the player.\n",
            )

            r = run_assemble(out, changelog)
            self.assertEqual(r.returncode, 0, r.stderr)
            text = changelog.read_text()
            self.assertIn("### aiscript:order.fight.escort", text)
            self.assertIn("escorted ships can continue this fight/escort AI logic", text)
            self.assertNotIn("which suggests ships using this script", text)

    def test_same_label_duplicate_bullets_are_condensed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "art"
            changelog = Path(tmpdir) / "out.md"
            label = "plan:arg_wharf"

            plant_chunk(
                out,
                "plans__part1of2",
                "libraries/constructionplans.xml",
                (label,),
                part=1,
                total=2,
                sub_part="1/2 of plan:arg_wharf (split at <entry> boundaries)",
            )
            plant_finding(
                out,
                "plans__part1of2",
                f"[{label}]\n- Added `buildmodule_gen_ships_xl_macro`, so this wharf plan now includes XL ship production.\n",
            )

            plant_chunk(
                out,
                "plans__part2of2",
                "libraries/constructionplans.xml",
                (label,),
                part=2,
                total=2,
                sub_part="2/2 of plan:arg_wharf (split at <entry> boundaries)",
            )
            plant_finding(
                out,
                "plans__part2of2",
                f"[{label}]\n- Added `buildmodule_gen_ships_xl_macro`, so this wharf plan now includes XL ship production.\n",
            )

            r = run_assemble(out, changelog)
            self.assertEqual(r.returncode, 0, r.stderr)
            text = changelog.read_text()
            self.assertEqual(text.count("### plan:arg_wharf"), 1)
            self.assertEqual(text.count("buildmodule_gen_ships_xl_macro"), 1)

    def test_same_label_near_paraphrase_bullets_across_chunks_are_condensed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "art"
            changelog = Path(tmpdir) / "out.md"
            source_path = "md/lib_generic.xml"
            label = f"file:{source_path}"

            plant_chunk(out, "libgeneric__part1of2", source_path, (), part=1, total=2)
            plant_finding(
                out,
                "libgeneric__part1of2",
                f"[{label}]\n- Approach/diverge logic now supports an optional `$ApproachOffset`, so scripts can measure distance to an offset position around the target instead of only the target itself.\n",
            )

            plant_chunk(out, "libgeneric__part2of2", source_path, (), part=2, total=2)
            plant_finding(
                out,
                "libgeneric__part2of2",
                f"[{label}]\n- Approach/diverge logic now supports an optional `$ApproachOffset`, so scripts can measure proximity against an offset position instead of only the target object itself.\n",
            )

            r = run_assemble(out, changelog)
            self.assertEqual(r.returncode, 0, r.stderr)
            text = changelog.read_text()
            self.assertEqual(text.count("supports an optional `$ApproachOffset`"), 1)


if __name__ == "__main__":
    unittest.main()
