#!/usr/bin/env python3
"""
Test 03_chunk — pack diffs into LLM-ready chunks.

Run:
    python3 src/03_chunk.test.py

CLI shape exercised:
    python3 src/03_chunk.py --v1 DIR --v2 DIR --out DIR --chunk-kb N
        [--force-split] [--schema-map PATH]

Input:
    <out>/02_diff/diffs/*.{diff,added,deleted}
    V1/V2 source trees (so the level-1 splitter can walk V2).

Output:
    <out>/03_chunk/chunks/<chunk_id>.txt
"""
import difflib
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
SCRIPT = HERE / "03_chunk.py"

_DIFF_HEADER = (
    "# Source: {rel}\n"
    "# Status: modified\n"
    "# V1 bytes: {v1} | V2 bytes: {v2}\n"
    "# ─────────────────────────────────────\n"
)


def make_diff_artifact(out: Path, rel: str, ext: str, body: str) -> Path:
    """Plant a diff artifact under <out>/02_diff/diffs/<rel><ext>."""
    path = out / "02_diff" / "diffs" / f"{rel}{ext}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def make_schema_map(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "last_scanned_at": None,
        "scanned_sources": [],
        "entries": entries,
    }))


def write_source(root: Path, rel: str, text: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def run_chunk(
    out: Path,
    v1: Path,
    v2: Path,
    chunk_kb: int = 10,
    force_split: bool = False,
    schema_map: Path | None = None,
):
    cmd = [
        sys.executable, str(SCRIPT),
        "--v1", str(v1), "--v2", str(v2),
        "--out", str(out),
        "--chunk-kb", str(chunk_kb),
    ]
    if force_split:
        cmd.append("--force-split")
    if schema_map is not None:
        cmd.extend(["--schema-map", str(schema_map)])
    return subprocess.run(cmd, capture_output=True, text=True)


def empty_sources(tmp: Path) -> tuple[Path, Path]:
    v1 = tmp / "v1"; v1.mkdir(parents=True, exist_ok=True)
    v2 = tmp / "v2"; v2.mkdir(parents=True, exist_ok=True)
    return v1, v2


def unified_diff(rel: str, v1_text: str, v2_text: str) -> str:
    v1_lines = v1_text.splitlines(keepends=True)
    v2_lines = v2_text.splitlines(keepends=True)
    body = "".join(difflib.unified_diff(v1_lines, v2_lines, fromfile=rel, tofile=rel, n=3))
    header = _DIFF_HEADER.format(rel=rel, v1=len(v1_text), v2=len(v2_text))
    return header + body


def load_chunk_module():
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    spec = importlib.util.spec_from_file_location("chunk03_module", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class SingleChunkTest(unittest.TestCase):
    def _assert_singleton_macro_chunk(self, rel: str, body: str, expected_prefix: str):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            v1, v2 = empty_sources(tmp)
            make_diff_artifact(out, rel, ".added", body)
            sm = tmp / "x4_schema_map.generated.json"
            make_schema_map(sm, [])

            r = run_chunk(out, v1, v2, chunk_kb=10, schema_map=sm)
            self.assertEqual(r.returncode, 0, r.stderr)

            chunks = list((out / "03_chunk" / "chunks").glob("*.txt"))
            self.assertEqual(len(chunks), 1)
            text = chunks[0].read_text()
            self.assertIn(f"# Entities (1): {expected_prefix}", text)
            self.assertIn(
                f'# Allowed prefixes JSON: ["{expected_prefix}", "file:{rel}"]',
                text,
            )

    def test_small_diff_emits_one_chunk(self):
        """A diff well under CHUNK_KB produces a single part 1/1 chunk."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            v1, v2 = empty_sources(tmp)
            make_diff_artifact(out, "libraries/wares.xml", ".diff",
                               "# Source: libraries/wares.xml\n# Status: modified\n...\n")
            sm = tmp / "x4_schema_map.generated.json"
            make_schema_map(sm, [])
            r = run_chunk(out, v1, v2, chunk_kb=10, schema_map=sm)
            self.assertEqual(r.returncode, 0, r.stderr)

            chunks = list((out / "03_chunk" / "chunks").glob("*.txt"))
            self.assertEqual(len(chunks), 1)
            self.assertEqual(chunks[0].name, "libraries__wares.xml__part1of1.txt")

            text = chunks[0].read_text()
            self.assertIn("# Chunk: libraries/wares.xml part 1/1", text)
            self.assertIn("# Entities: entire file", text)
            self.assertIn('# Allowed prefixes JSON: ["file:libraries/wares.xml"]', text)
            self.assertIn("# Source: libraries/wares.xml", text)  # body carried through

    def test_added_and_deleted_files_produce_chunks(self):
        """Both .added and .deleted artifacts get their own chunk files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            v1, v2 = empty_sources(tmp)
            make_diff_artifact(out, "libraries/factions.xml", ".added", "raw V2 content\n")
            make_diff_artifact(out, "libraries/ships.xml", ".deleted", "raw V1 content\n")
            sm = tmp / "x4_schema_map.generated.json"
            make_schema_map(sm, [])
            r = run_chunk(out, v1, v2, chunk_kb=10, schema_map=sm)
            self.assertEqual(r.returncode, 0, r.stderr)

            names = sorted(p.name for p in (out / "03_chunk" / "chunks").glob("*.txt"))
            self.assertEqual(names, [
                "libraries__factions.xml__part1of1.txt",
                "libraries__ships.xml__part1of1.txt",
            ])

    def test_resumable_skips_existing_chunks(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            v1, v2 = empty_sources(tmp)
            make_diff_artifact(out, "libraries/wares.xml", ".diff", "tiny\n")
            sm = tmp / "x4_schema_map.generated.json"
            make_schema_map(sm, [])
            target = out / "03_chunk" / "chunks" / "libraries__wares.xml__part1of1.txt"
            target.parent.mkdir(parents=True)
            target.write_text("SENTINEL")
            run_chunk(out, v1, v2, chunk_kb=10, schema_map=sm)
            self.assertEqual(target.read_text(), "SENTINEL")

    def test_small_diff_uses_touched_entity_labels_when_schema_is_known(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            v1, v2 = empty_sources(tmp)
            rel = "libraries/wares.xml"
            v1_text = (
                "<wares>\n"
                "  <ware id='weapon_arg_l_beam_01'><damage value='50'/></ware>\n"
                "  <ware id='weapon_arg_l_beam_02'><damage value='40'/></ware>\n"
                "</wares>\n"
            )
            v2_text = (
                "<wares>\n"
                "  <ware id='weapon_arg_l_beam_01'><damage value='45'/></ware>\n"
                "  <ware id='weapon_arg_l_beam_02'><damage value='40'/></ware>\n"
                "</wares>\n"
            )
            write_source(v1, rel, v1_text)
            write_source(v2, rel, v2_text)
            make_diff_artifact(out, rel, ".diff", unified_diff(rel, v1_text, v2_text))
            sm = tmp / "x4_schema_map.generated.json"
            make_schema_map(sm, [{"file": rel, "entity_tag": "ware", "id_attribute": "id"}])

            r = run_chunk(out, v1, v2, chunk_kb=10, schema_map=sm)
            self.assertEqual(r.returncode, 0, r.stderr)

            text = (out / "03_chunk" / "chunks" / "libraries__wares.xml__part1of1.txt").read_text()
            self.assertIn("# Entities (1): ware:weapon_arg_l_beam_01", text)
            self.assertIn(
                '# Allowed prefixes JSON: ["ware:weapon_arg_l_beam_01", "file:libraries/wares.xml"]',
                text,
            )

    def test_small_diff_adds_deleted_side_entity_labels_when_schema_is_known(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            v1, v2 = empty_sources(tmp)
            rel = "libraries/regionyields.xml"
            v1_text = (
                "<definitions>\n"
                "  <definition id='sphere_tiny_helium_low'><yield value='5000'/></definition>\n"
                "  <definition id='sphere_tiny_helium_verylow'><yield value='6000'/></definition>\n"
                "</definitions>\n"
            )
            v2_text = (
                "<definitions>\n"
                "  <definition id='sphere_tiny_helium_verylow'><yield value='10000'/></definition>\n"
                "  <definition id='sphere_tiny_hydrogen_veryhigh'><yield value='25000'/></definition>\n"
                "</definitions>\n"
            )
            write_source(v1, rel, v1_text)
            write_source(v2, rel, v2_text)
            make_diff_artifact(out, rel, ".diff", unified_diff(rel, v1_text, v2_text))
            sm = tmp / "x4_schema_map.generated.json"
            make_schema_map(sm, [{"file": rel, "entity_tag": "definition", "id_attribute": "id"}])

            r = run_chunk(out, v1, v2, chunk_kb=10, schema_map=sm)
            self.assertEqual(r.returncode, 0, r.stderr)

            text = (out / "03_chunk" / "chunks" / "libraries__regionyields.xml__part1of1.txt").read_text()
            self.assertIn("definition:sphere_tiny_helium_low", text)
            self.assertIn("definition:sphere_tiny_helium_verylow", text)
            self.assertIn("definition:sphere_tiny_hydrogen_veryhigh", text)
            self.assertIn('"definition:sphere_tiny_helium_low"', text)

    def test_small_aiscript_xml_uses_path_based_semantic_prefix(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            v1, v2 = empty_sources(tmp)
            rel = "aiscripts/order.fight.escort.xml"
            v1_text = (
                "<aiscript>\n"
                "  <escort canattack=\"false\"/>\n"
                "</aiscript>\n"
            )
            v2_text = (
                "<aiscript>\n"
                "  <escort canattack=\"true\"/>\n"
                "</aiscript>\n"
            )
            write_source(v1, rel, v1_text)
            write_source(v2, rel, v2_text)
            make_diff_artifact(out, rel, ".diff", unified_diff(rel, v1_text, v2_text))
            sm = tmp / "x4_schema_map.generated.json"
            make_schema_map(sm, [])

            r = run_chunk(out, v1, v2, chunk_kb=10, schema_map=sm)
            self.assertEqual(r.returncode, 0, r.stderr)

            text = (out / "03_chunk" / "chunks" / "aiscripts__order.fight.escort.xml__part1of1.txt").read_text()
            self.assertIn("# Entities (1): aiscript:order.fight.escort", text)
            self.assertIn(
                '# Allowed prefixes JSON: ["aiscript:order.fight.escort", "file:aiscripts/order.fight.escort.xml"]',
                text,
            )

    def test_small_lua_diff_uses_v2_function_boundaries_for_mixed_hunk(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            v1, v2 = empty_sources(tmp)
            rel = "ui/menu.lua"
            v1_text = (
                "local menu = {}\n\n"
                "function menu.formatRange(range)\n"
                "  return ConvertIntegerString(range / 1000, true, 0, true)\n"
                "end\n\n"
                "function menu.checkStatCondition(statconditions, statcondition)\n"
                "  return statconditions[statcondition] == true\n"
                "end\n"
            )
            v2_text = (
                "local menu = {}\n\n"
                "function menu.formatRange(range)\n"
                "  return string.format(\"%.1f\", range / 1000)\n"
                "end\n\n"
                "function menu.checkStatCondition(statconditions, statcondition)\n"
                "  return statconditions[statcondition] == true\n"
                "end\n"
            )
            write_source(v1, rel, v1_text)
            write_source(v2, rel, v2_text)
            make_diff_artifact(out, rel, ".diff", unified_diff(rel, v1_text, v2_text))
            sm = tmp / "x4_schema_map.generated.json"
            make_schema_map(sm, [])

            r = run_chunk(out, v1, v2, chunk_kb=10, schema_map=sm)
            self.assertEqual(r.returncode, 0, r.stderr)

            text = (out / "03_chunk" / "chunks" / "ui__menu.lua__part1of1.txt").read_text()
            self.assertIn("# Entities (1): lua:menu.formatRange", text)
            self.assertIn(
                '# Allowed prefixes JSON: ["lua:menu.formatRange", "file:ui/menu.lua"]',
                text,
            )
            self.assertNotIn("lua:menu.checkStatCondition", text.split("# ─", 1)[0])

    def test_small_lua_diff_labels_deleted_function_from_v1(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            v1, v2 = empty_sources(tmp)
            rel = "ui/menu.lua"
            v1_text = (
                "local menu = {}\n\n"
                "function obsolete()\n"
                "  return 1\n"
                "end\n\n"
                "function keep()\n"
                "  return 2\n"
                "end\n"
            )
            v2_text = (
                "local menu = {}\n\n"
                "function keep()\n"
                "  return 2\n"
                "end\n"
            )
            write_source(v1, rel, v1_text)
            write_source(v2, rel, v2_text)
            make_diff_artifact(out, rel, ".diff", unified_diff(rel, v1_text, v2_text))
            sm = tmp / "x4_schema_map.generated.json"
            make_schema_map(sm, [])

            r = run_chunk(out, v1, v2, chunk_kb=10, schema_map=sm)
            self.assertEqual(r.returncode, 0, r.stderr)

            text = (out / "03_chunk" / "chunks" / "ui__menu.lua__part1of1.txt").read_text()
            self.assertIn("# Entities (1): lua:obsolete", text)
            self.assertIn(
                '# Allowed prefixes JSON: ["lua:obsolete", "file:ui/menu.lua"]',
                text,
            )
            self.assertNotIn("lua:keep", text.split("# ─", 1)[0])

    def test_small_singleton_ship_macro_promotes_to_ship_prefix(self):
        self._assert_singleton_macro_chunk(
            "assets/units/size_l/macros/ship_kha_l_destroyer_01_a_macro.xml",
            (
                "<?xml version='1.0' encoding='utf-8'?>\n"
                "<macros>\n"
                "  <macro name='ship_kha_l_destroyer_01_a_macro' class='ship_l'>\n"
                "    <properties><hull max='10000'/></properties>\n"
                "  </macro>\n"
                "</macros>\n"
            ),
            "ship:ship_kha_l_destroyer_01_a",
        )

    def test_small_singleton_engine_macro_promotes_to_ware_prefix(self):
        self._assert_singleton_macro_chunk(
            "extensions/ego_dlc_split/assets/props/Engines/macros/engine_spl_s_combat_01_mk3_macro.xml",
            (
                "<?xml version='1.0' encoding='utf-8'?>\n"
                "<macros>\n"
                "  <macro name='engine_spl_s_combat_01_mk3_macro' class='engine'>\n"
                "    <properties><thrust forward='784.89'/></properties>\n"
                "  </macro>\n"
                "</macros>\n"
            ),
            "ware:engine_spl_s_combat_01_mk3",
        )

    def test_small_singleton_shield_macro_promotes_to_ware_prefix(self):
        self._assert_singleton_macro_chunk(
            "assets/props/SurfaceElements/macros/shield_tel_xl_standard_01_mk1_macro.xml",
            (
                "<macros>\n"
                "  <macro name='shield_tel_xl_standard_01_mk1_macro' class='shieldgenerator'>\n"
                "    <properties><recharge max='120'/></properties>\n"
                "  </macro>\n"
                "</macros>\n"
            ),
            "ware:shield_tel_xl_standard_01_mk1",
        )

    def test_small_modified_singleton_macro_keeps_promoted_prefix_across_shared_hunk(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            v1, v2 = empty_sources(tmp)

            rel = "assets/props/StorageModules/macros/storage_par_l_miner_liquid_02_a_macro.xml"
            v1_text = (
                "<?xml version='1.0' encoding='utf-8'?>\n"
                "<!--Exported by: nick at old-time-->\n"
                "<macros>\n"
                "  <macro name='storage_par_l_miner_liquid_02_a_macro' class='storage'>\n"
                "    <properties>\n"
                "      <cargo max='35200' tags='liquid'/>\n"
                "    </properties>\n"
                "  </macro>\n"
                "</macros>\n"
            )
            v2_text = (
                "<?xml version='1.0' encoding='utf-8'?>\n"
                "<!--Exported by: Ketraar at new-time-->\n"
                "<macros>\n"
                "  <macro name='storage_par_l_miner_liquid_02_a_macro' class='storage'>\n"
                "    <properties>\n"
                "      <cargo max='45100' tags='liquid'/>\n"
                "    </properties>\n"
                "  </macro>\n"
                "</macros>\n"
            )
            write_source(v1, rel, v1_text)
            write_source(v2, rel, v2_text)
            make_diff_artifact(out, rel, ".diff", unified_diff(rel, v1_text, v2_text))

            sm = tmp / "x4_schema_map.generated.json"
            make_schema_map(sm, [])

            r = run_chunk(out, v1, v2, chunk_kb=10, schema_map=sm)
            self.assertEqual(r.returncode, 0, r.stderr or r.stdout)

            chunks = list((out / "03_chunk" / "chunks").glob("*.txt"))
            self.assertEqual(len(chunks), 1)
            text = chunks[0].read_text()
            self.assertIn("# Entities (1): module:storage_par_l_miner_liquid_02_a", text)
            self.assertIn(
                '# Allowed prefixes JSON: ["module:storage_par_l_miner_liquid_02_a", '
                f'"file:{rel}"]',
                text,
            )

    def test_safe_storage_macro_promotes_to_module_prefix(self):
        self._assert_singleton_macro_chunk(
            "assets/props/StorageModules/macros/storage_par_l_miner_liquid_02_a_macro.xml",
            (
                "<macros>\n"
                "  <macro name='storage_par_l_miner_liquid_02_a_macro' class='storage'>\n"
                "    <properties><cargo max='1000'/></properties>\n"
                "  </macro>\n"
                "</macros>\n"
            ),
            "module:storage_par_l_miner_liquid_02_a",
        )

    def test_unit_storage_macro_stays_macro_prefix(self):
        self._assert_singleton_macro_chunk(
            "extensions/ego_dlc_terran/assets/units/size_l/macros/storage_ter_l_miner_liquid_01_a_macro.xml",
            (
                "<macros>\n"
                "  <macro name='storage_ter_l_miner_liquid_01_a_macro' class='storage'>\n"
                "    <properties><cargo max='1000'/></properties>\n"
                "  </macro>\n"
                "</macros>\n"
            ),
            "macro:storage_ter_l_miner_liquid_01_a_macro",
        )

    def test_small_diff_in_lib_generic_uses_library_prefix(self):
        """md/lib_generic.xml <library name=...> blocks are labeled library:<name>."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            v1, v2 = empty_sources(tmp)
            rel = "md/lib_generic.xml"
            v1_text = (
                "<mdscript>\n"
                "  <cues>\n"
                "    <library name='ApproachObject_Handler' version='5'>\n"
                "      <params><param name='Foo' default='null'/></params>\n"
                "    </library>\n"
                "    <library name='BuildStation_Handler' version='3'>\n"
                "      <params><param name='Bar' default='null'/></params>\n"
                "    </library>\n"
                "  </cues>\n"
                "</mdscript>\n"
            )
            v2_text = v1_text.replace("version='5'", "version='6'").replace(
                "<param name='Foo' default='null'/>",
                "<param name='Foo' default='null'/><param name='ApproachOffset' default='null'/>",
            )
            write_source(v1, rel, v1_text)
            write_source(v2, rel, v2_text)
            make_diff_artifact(out, rel, ".diff", unified_diff(rel, v1_text, v2_text))
            sm = tmp / "x4_schema_map.generated.json"
            make_schema_map(sm, [])

            r = run_chunk(out, v1, v2, chunk_kb=10, schema_map=sm)
            self.assertEqual(r.returncode, 0, r.stderr)

            chunks = list((out / "03_chunk" / "chunks").glob("*.txt"))
            self.assertEqual(len(chunks), 1)
            text = chunks[0].read_text()
            self.assertIn("library:ApproachObject_Handler", text)
            self.assertIn('"library:ApproachObject_Handler"', text)

    def test_small_diff_in_rml_barterwares_uses_cue_prefix(self):
        """md/rml_barterwares.xml top-level <cue> entries are labeled cue:<name>."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            v1, v2 = empty_sources(tmp)
            rel = "md/rml_barterwares.xml"
            v1_text = (
                "<mdscript>\n"
                "  <cues>\n"
                "    <library name='BarterWares' version='3'>\n"
                "      <cue name='StartMission' version='3'>\n"
                "        <actions><debug_text text='foo'/></actions>\n"
                "      </cue>\n"
                "    </library>\n"
                "    <cue name='AwaitConfirmTransferWares' version='4'>\n"
                "      <actions><debug_text text='bar'/></actions>\n"
                "    </cue>\n"
                "  </cues>\n"
                "</mdscript>\n"
            )
            v2_text = v1_text.replace("version='4'", "version='5'").replace(
                "text='bar'", "text='baz'",
            )
            write_source(v1, rel, v1_text)
            write_source(v2, rel, v2_text)
            make_diff_artifact(out, rel, ".diff", unified_diff(rel, v1_text, v2_text))
            sm = tmp / "x4_schema_map.generated.json"
            make_schema_map(sm, [])

            r = run_chunk(out, v1, v2, chunk_kb=10, schema_map=sm)
            self.assertEqual(r.returncode, 0, r.stderr)

            chunks = list((out / "03_chunk" / "chunks").glob("*.txt"))
            self.assertEqual(len(chunks), 1)
            text = chunks[0].read_text()
            self.assertIn("cue:AwaitConfirmTransferWares", text)
            self.assertIn('"cue:AwaitConfirmTransferWares"', text)

    def test_small_diff_in_setup_gamestarts_uses_gamestart_prefix(self):
        """md/setup_gamestarts.xml cues are labeled gamestart:<name>, not cue:<name>."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            v1, v2 = empty_sources(tmp)
            rel = "md/setup_gamestarts.xml"
            v1_text = (
                "<mdscript>\n"
                "  <cues>\n"
                "    <cue name='Test_Mining' module='test_mining'>\n"
                "      <actions>\n"
                "        <create_ship><loadout><level exact='0.5'/></loadout></create_ship>\n"
                "      </actions>\n"
                "    </cue>\n"
                "    <cue name='Test_Battle' module='test_battle'>\n"
                "      <actions>\n"
                "        <create_ship><loadout><level exact='0.5'/></loadout></create_ship>\n"
                "      </actions>\n"
                "    </cue>\n"
                "  </cues>\n"
                "</mdscript>\n"
            )
            v2_text = v1_text.replace(
                "<cue name='Test_Mining' module='test_mining'>\n"
                "      <actions>\n"
                "        <create_ship><loadout><level exact='0.5'/></loadout></create_ship>",
                "<cue name='Test_Mining' module='test_mining'>\n"
                "      <actions>\n"
                "        <create_ship><loadout><level exact='1.0'/></loadout></create_ship>",
            )
            write_source(v1, rel, v1_text)
            write_source(v2, rel, v2_text)
            make_diff_artifact(out, rel, ".diff", unified_diff(rel, v1_text, v2_text))
            sm = tmp / "x4_schema_map.generated.json"
            make_schema_map(sm, [])  # Override table should kick in even without schema entry.

            r = run_chunk(out, v1, v2, chunk_kb=10, schema_map=sm)
            self.assertEqual(r.returncode, 0, r.stderr)

            chunks = list((out / "03_chunk" / "chunks").glob("*.txt"))
            self.assertEqual(len(chunks), 1)
            text = chunks[0].read_text()
            self.assertIn("gamestart:Test_Mining", text)
            self.assertNotIn("cue:Test_Mining", text)
            self.assertIn('"gamestart:Test_Mining"', text)


class Level2GenericXmlTest(unittest.TestCase):
    def test_diff_visible_rows_seed_depth_from_full_v2_context(self):
        chunk = load_chunk_module()
        rel = "aiscripts/test.xml"

        def build(mark: str) -> str:
            rows = ["<root>"]
            for i in range(6):
                rows.extend([
                    f"  <cue id='{i}'>",
                    "    <conditions>",
                    '      <check value="same"/>',
                    "    </conditions>",
                    "    <actions>",
                    "      <set_value>",
                    f"        {mark}_{i}_" + ("x" * 120),
                    "      </set_value>",
                    "    </actions>",
                    "  </cue>",
                ])
            rows.append("</root>")
            return "\n".join(rows) + "\n"

        v1_text = build("old")
        v2_text = build("new")
        body = unified_diff(rel, v1_text, v2_text)
        _header, _file_lines, _diff_lines, hunks = chunk.split_unified_diff(body)

        old_visible_rows: list[tuple[int, int, set[int]]] = []
        depth = 0
        for _, _, htext in hunks:
            hunk_rows = htext.splitlines(keepends=True)
            match = chunk._HUNK_RE.match(hunk_rows[0].rstrip("\n"))
            self.assertIsNotNone(match)
            v2 = int(match.group(3))
            for row in hunk_rows[1:]:
                if row.startswith(("+", " ")):
                    depth, returned_depths = chunk._xml_line_scan(row[1:], depth)
                    old_visible_rows.append((v2, depth, returned_depths))
                    v2 += 1

        seeded_visible_rows = chunk._xml_visible_rows_for_diff(hunks, v2_text)
        raw_cut_lines, _raw_boundary = chunk._xml_cut_lines_raw(v2_text)

        self.assertLess(chunk._xml_boundary_depth(old_visible_rows), 0)
        self.assertEqual(chunk._xml_boundary_depth(seeded_visible_rows), 1)
        self.assertNotEqual(chunk._xml_cut_boundaries(old_visible_rows), raw_cut_lines)
        self.assertEqual(chunk._xml_cut_boundaries(seeded_visible_rows), raw_cut_lines)

    def test_oversize_raw_added_xml_with_no_schema_splits_via_level2(self):
        """Unknown basename + oversize .added XML → level-2 generic split (≥2 chunks)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            v1, v2 = empty_sources(tmp)
            # Build a new file with many top-level <widget> children — structural
            # depth returns to 1 after each </widget>. Level-2 cuts there.
            rows = ["<root>"]
            for i in range(200):
                rows.append(f"  <widget id='{i}'>")
                rows.append(f"    <prop name='p1' value='{'x' * 60}'/>")
                rows.append(f"    <prop name='p2' value='{'y' * 60}'/>")
                rows.append("  </widget>")
            rows.append("</root>")
            raw = "\n".join(rows) + "\n"
            make_diff_artifact(out, "libraries/unknown.xml", ".added", raw)
            sm = tmp / "x4_schema_map.generated.json"
            make_schema_map(sm, [])  # no entry — forces level-2
            r = run_chunk(out, v1, v2, chunk_kb=5, schema_map=sm)
            self.assertEqual(r.returncode, 0, r.stderr or r.stdout)
            chunks = sorted((out / "03_chunk" / "chunks").glob("*.txt"))
            self.assertGreaterEqual(len(chunks), 2,
                                    f"expected split: got {[c.name for c in chunks]}")
            # Each chunk header should use the generic line-range labels.
            self.assertIn("lines:", chunks[0].read_text())

    def test_oversize_raw_added_one_line_children_split_structurally(self):
        """One-line sibling elements must still create level-2 cut points."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            v1, v2 = empty_sources(tmp)
            rows = ["<?xml version='1.0'?>", "<mdscript>"]
            for i in range(200):
                rows.append(
                    "  "
                    f"<cue id='cue_{i}'><actions><set_value name='value' exact='"
                    f"{'x' * 120}_{i}'/></actions></cue>"
                )
            rows.append("</mdscript>")
            raw = "\n".join(rows) + "\n"
            make_diff_artifact(out, "md/setup.xml", ".added", raw)
            sm = tmp / "x4_schema_map.generated.json"
            make_schema_map(sm, [])

            r = run_chunk(out, v1, v2, chunk_kb=5, schema_map=sm)
            self.assertEqual(r.returncode, 0, r.stderr or r.stdout)

            chunks = sorted((out / "03_chunk" / "chunks").glob("*.txt"))
            self.assertGreaterEqual(len(chunks), 2)
            self.assertTrue(all("WARNING: force-split" not in chunk.read_text() for chunk in chunks))
            self.assertIn("lines:", chunks[0].read_text())

    def test_oversize_diff_xml_with_no_schema_splits_via_level2(self):
        """Unknown basename + oversize .diff XML → level-2 (linearly-packed hunks)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            v1, v2 = empty_sources(tmp)
            rel = "libraries/unknown.xml"
            # Every 5th widget has one big prop tweaked. Unchanged widgets
            # carry heavy filler so difflib's n=3 context doesn't merge
            # neighbouring changes into one giant hunk, AND the diff body
            # exceeds CHUNK_KB=5.
            filler = "z" * 400
            def build(n: int, mark: str) -> str:
                rows = ["<root>"]
                for i in range(n):
                    rows.append(f"  <widget id='{i}'>")
                    val = f"{mark}_{filler}" if i % 5 == 0 else f"keep_{filler}"
                    rows.append(f"    <prop v='{val}'/>")
                    rows.append("  </widget>")
                rows.append("</root>")
                return "\n".join(rows) + "\n"
            write_source(v1, rel, build(40, "old"))
            write_source(v2, rel, build(40, "new"))
            diff_body = unified_diff(rel, build(40, "old"), build(40, "new"))
            make_diff_artifact(out, rel, ".diff", diff_body)
            sm = tmp / "x4_schema_map.generated.json"
            make_schema_map(sm, [])
            r = run_chunk(out, v1, v2, chunk_kb=5, schema_map=sm)
            self.assertEqual(r.returncode, 0, r.stderr or r.stdout)
            chunks = sorted((out / "03_chunk" / "chunks").glob("*.txt"))
            self.assertGreaterEqual(len(chunks), 2)

    def test_oversize_diff_xml_with_one_line_self_closing_siblings_splits_via_level2(self):
        """One-line self-closing siblings must split oversized generic XML diffs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            v1, v2 = empty_sources(tmp)
            rel = "md/setup.xml"
            filler = "z" * 240

            def build(mark: str) -> str:
                rows = ["<root>"]
                for i in range(160):
                    value = f"{mark}_{filler}_{i}" if i % 5 == 0 else f"keep_{filler}_{i}"
                    rows.append(f"  <row id='{i}' value='{value}'/>")
                rows.append("</root>")
                return "\n".join(rows) + "\n"

            v1_text = build("old")
            v2_text = build("new")
            write_source(v1, rel, v1_text)
            write_source(v2, rel, v2_text)
            make_diff_artifact(out, rel, ".diff", unified_diff(rel, v1_text, v2_text))
            sm = tmp / "x4_schema_map.generated.json"
            make_schema_map(sm, [])

            r = run_chunk(out, v1, v2, chunk_kb=5, schema_map=sm)
            self.assertEqual(r.returncode, 0, r.stderr or r.stdout)

            chunks = sorted((out / "03_chunk" / "chunks").glob("*.txt"))
            self.assertGreaterEqual(len(chunks), 2)
            self.assertTrue(all("WARNING: force-split" not in chunk.read_text() for chunk in chunks))
            self.assertIn("lines:", chunks[0].read_text())

    def test_oversize_aiscript_diff_keeps_semantic_prefix_without_schema(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            v1, v2 = empty_sources(tmp)
            rel = "aiscripts/order.fight.escort.xml"
            filler = "z" * 400

            def build(mark: str) -> str:
                rows = ["<aiscript>"]
                for i in range(40):
                    rows.append(f"  <cue id='cue_{i}'>")
                    relation = mark if i % 5 == 0 else "keep"
                    rows.append(f"    <set_value relation='{relation}_{filler}'/>")
                    rows.append("  </cue>")
                rows.append("</aiscript>")
                return "\n".join(rows) + "\n"

            v1_text = build("old")
            v2_text = build("new")
            write_source(v1, rel, v1_text)
            write_source(v2, rel, v2_text)
            make_diff_artifact(out, rel, ".diff", unified_diff(rel, v1_text, v2_text))
            sm = tmp / "x4_schema_map.generated.json"
            make_schema_map(sm, [])

            r = run_chunk(out, v1, v2, chunk_kb=5, schema_map=sm)
            self.assertEqual(r.returncode, 0, r.stderr or r.stdout)
            chunks = sorted((out / "03_chunk" / "chunks").glob("*.txt"))
            self.assertGreaterEqual(len(chunks), 2)

            header = chunks[0].read_text().split("# ─", 1)[0]
            self.assertIn("# Entities (1): aiscript:order.fight.escort", header)
            self.assertIn(
                '# Allowed prefixes JSON: ["aiscript:order.fight.escort", "file:aiscripts/order.fight.escort.xml"]',
                header,
            )


class Level3LuaTest(unittest.TestCase):
    def test_oversize_raw_lua_splits_at_functions(self):
        """.added .lua oversize → level-3 cuts at function defs; entity keys are function names."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            v1, v2 = empty_sources(tmp)
            body_lines = ["local M = {}\n"]
            for i in range(20):
                body_lines.append(f"local function helper_{i}()\n")
                body_lines.append(f"  return '{'x' * 400}'\n")
                body_lines.append("end\n\n")
            body_lines.append("return M\n")
            body = "".join(body_lines)
            make_diff_artifact(out, "aiscripts/lib_big.lua", ".added", body)
            sm = tmp / "x4_schema_map.generated.json"
            make_schema_map(sm, [])
            r = run_chunk(out, v1, v2, chunk_kb=4, schema_map=sm)
            self.assertEqual(r.returncode, 0, r.stderr or r.stdout)
            chunks = sorted((out / "03_chunk" / "chunks").glob("*.txt"))
            self.assertGreaterEqual(len(chunks), 2)
            header_text = chunks[0].read_text()
            # At least one lua:helper_* entity key should appear.
            m = re.search(r"lua:helper_\d+", header_text)
            self.assertIsNotNone(m, f"expected lua:helper_ key in {header_text[:200]}")

    def test_oversize_diff_lua_uses_v2_function_boundaries_for_mixed_hunk(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            v1, v2 = empty_sources(tmp)
            rel = "ui/menu.lua"
            filler_old = "old_" + ("x" * 900)
            filler_new = "new_" + ("x" * 900)
            v1_text = (
                "local menu = {}\n\n"
                "function menu.formatRange(range)\n"
                f"  return \"{filler_old}\"\n"
                "end\n\n"
                "function menu.checkStatCondition(statconditions, statcondition)\n"
                "  return statconditions[statcondition] == true\n"
                "end\n"
            )
            v2_text = (
                "local menu = {}\n\n"
                "function menu.formatRange(range)\n"
                f"  return \"{filler_new}\"\n"
                "end\n\n"
                "function menu.checkStatCondition(statconditions, statcondition)\n"
                "  return statconditions[statcondition] == true\n"
                "end\n"
            )
            write_source(v1, rel, v1_text)
            write_source(v2, rel, v2_text)
            make_diff_artifact(out, rel, ".diff", unified_diff(rel, v1_text, v2_text))
            sm = tmp / "x4_schema_map.generated.json"
            make_schema_map(sm, [])

            r = run_chunk(out, v1, v2, chunk_kb=4, schema_map=sm)
            self.assertEqual(r.returncode, 0, r.stderr or r.stdout)

            chunks = sorted((out / "03_chunk" / "chunks").glob("*.txt"))
            self.assertGreaterEqual(len(chunks), 1)
            header = chunks[0].read_text().split("# ─", 1)[0]
            self.assertIn("lua:menu.formatRange", header)
            self.assertNotIn("lua:menu.checkStatCondition", header)

    def test_oversize_diff_lua_labels_deleted_function_from_v1(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            v1, v2 = empty_sources(tmp)
            rel = "ui/menu.lua"
            deleted_payload = "delete_" + ("x" * 420)
            tweak_old = "old_" + ("y" * 320)
            tweak_new = "new_" + ("z" * 320)
            v1_text = (
                "local menu = {}\n\n"
                "function obsolete()\n"
                f"  return \"{deleted_payload}\"\n"
                "end\n\n"
                "function tweak()\n"
                f"  return \"{tweak_old}\"\n"
                "end\n\n"
                "function keep()\n"
                "  return \"keep\"\n"
                "end\n"
            )
            v2_text = (
                "local menu = {}\n\n"
                "function tweak()\n"
                f"  return \"{tweak_new}\"\n"
                "end\n\n"
                "function keep()\n"
                "  return \"keep\"\n"
                "end\n"
            )
            write_source(v1, rel, v1_text)
            write_source(v2, rel, v2_text)
            make_diff_artifact(out, rel, ".diff", unified_diff(rel, v1_text, v2_text))
            sm = tmp / "x4_schema_map.generated.json"
            make_schema_map(sm, [])

            r = run_chunk(out, v1, v2, chunk_kb=1, schema_map=sm)
            self.assertEqual(r.returncode, 0, r.stderr or r.stdout)

            chunks = sorted((out / "03_chunk" / "chunks").glob("*.txt"))
            self.assertGreaterEqual(len(chunks), 2)
            header = chunks[0].read_text().split("# ─", 1)[0]
            self.assertIn("lua:obsolete", header)
            self.assertNotIn("lua:keep", header)


class Level4ForceSplitTest(unittest.TestCase):
    def test_force_split_raw_cuts_at_blank_or_close_tag(self):
        """--force-split on a non-XML/non-Lua oversize raw produces warning chunks."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            v1, v2 = empty_sources(tmp)
            body = "".join(f"line {i}\n" + ("\n" if i % 50 == 49 else "")
                           for i in range(400))
            make_diff_artifact(out, "libraries/oddball.txt", ".added", body)
            sm = tmp / "x4_schema_map.generated.json"
            make_schema_map(sm, [])
            r = run_chunk(out, v1, v2, chunk_kb=2, schema_map=sm, force_split=True)
            self.assertEqual(r.returncode, 0, r.stderr or r.stdout)
            chunks = sorted((out / "03_chunk" / "chunks").glob("*.txt"))
            self.assertGreaterEqual(len(chunks), 2)
            self.assertIn("WARNING: force-split", chunks[0].read_text())

    def test_force_split_diff_retries_after_structural_overflow(self):
        """Schema-less XML diffs retry with level 4 when a structural chunk is too dense."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            v1, v2 = empty_sources(tmp)
            rel = "aiscripts/force_split_retry.xml"

            def build(version: str) -> str:
                lines = ["<root>"]
                payload = "x" * 80
                for i in range(160):
                    lines.append(f'  <item id="{i}" value="{version}_{payload}_{i}"/>')
                lines.append("</root>")
                return "\n".join(lines) + "\n"

            v1_text = build("old")
            v2_text = build("new")
            write_source(v1, rel, v1_text)
            write_source(v2, rel, v2_text)
            make_diff_artifact(out, rel, ".diff", unified_diff(rel, v1_text, v2_text))

            sm = tmp / "x4_schema_map.generated.json"
            make_schema_map(sm, [])

            r = run_chunk(out, v1, v2, chunk_kb=5, schema_map=sm, force_split=True)
            self.assertEqual(r.returncode, 0, r.stderr or r.stdout)
            self.assertIn("force-split fallback", r.stdout)

            chunks = sorted((out / "03_chunk" / "chunks").glob("*.txt"))
            self.assertGreaterEqual(len(chunks), 2)
            self.assertTrue(all("WARNING: force-split" in chunk.read_text() for chunk in chunks))


class Level5HardFailTest(unittest.TestCase):
    def test_non_xml_non_lua_oversize_without_force_flag_exits(self):
        """Unknown extension + oversize + no --force-split → SystemExit with options."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            v1, v2 = empty_sources(tmp)
            body = "x" * (20 * 1024)
            make_diff_artifact(out, "libraries/weird.txt", ".added", body)
            sm = tmp / "x4_schema_map.generated.json"
            make_schema_map(sm, [])
            r = run_chunk(out, v1, v2, chunk_kb=5, schema_map=sm, force_split=False)
            self.assertNotEqual(r.returncode, 0)
            blob = r.stderr + r.stdout
            self.assertIn("no splitter could handle it", blob)
            self.assertIn("--force-split", blob)


class Level1ModifiedTest(unittest.TestCase):
    def _build_wares(self, n_wares: int, lines_per_ware: int, tweak: int = 0) -> str:
        # Each ware: <ware id="weapon_N">\n  <attr ...\n  ...\n</ware>. `tweak`
        # changes every Nth ware's body so difflib produces per-entity hunks
        # instead of one giant top-to-bottom hunk.
        out = ["<wares>"]
        for i in range(n_wares):
            out.append(f'  <ware id="weapon_{i:03d}">')
            for j in range(lines_per_ware - 2):
                marker = "changed" if tweak and i % 2 == 0 else "original"
                out.append(
                    f'    <attr value="{i}_{j}_{marker}_padding_text_to_bulk_up_the_file"/>'
                )
            out.append("  </ware>")
        out.append("</wares>")
        return "\n".join(out) + "\n"

    def test_modified_xml_splits_across_parts(self):
        """A modified libraries/wares.xml bigger than CHUNK_KB splits into ≥2 parts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            v1, v2 = empty_sources(tmp)

            # 6 wares with full body on both sides; only even-indexed wares
            # differ. difflib then produces ≥3 hunks located within specific
            # <ware> intervals — the right shape for level-1 splitting.
            v1_text = self._build_wares(n_wares=8, lines_per_ware=24, tweak=0)
            v2_text = self._build_wares(n_wares=8, lines_per_ware=24, tweak=1)
            rel = "libraries/wares.xml"
            write_source(v1, rel, v1_text)
            write_source(v2, rel, v2_text)

            diff_body = unified_diff(rel, v1_text, v2_text)
            make_diff_artifact(out, rel, ".diff", diff_body)

            sm = tmp / "x4_schema_map.generated.json"
            make_schema_map(sm, [
                {"file": "libraries/wares.xml", "entity_tag": "ware", "id_attribute": "id"},
            ])

            # Use 5 KB so each entity mini-diff still fits, but the file needs
            # multiple packed parts overall.
            r = run_chunk(out, v1, v2, chunk_kb=5, schema_map=sm)
            self.assertEqual(r.returncode, 0, r.stderr or r.stdout)

            chunks = sorted((out / "03_chunk" / "chunks").glob("*.txt"))
            self.assertGreaterEqual(len(chunks), 2, f"expected split: got {[c.name for c in chunks]}")

            # Header correctness: each chunk names part K of N matching the count.
            total = len(chunks)
            seen_parts = set()
            all_plus_lines: list[str] = []
            for ch in chunks:
                text = ch.read_text()
                # Header line starts with "# Chunk: libraries/wares.xml part K/N".
                first = text.splitlines()[0]
                m = re.match(r"^# Chunk: libraries/wares\.xml part (\d+)/(\d+)$", first)
                self.assertIsNotNone(m, f"bad header line: {first!r}")
                k, n = int(m.group(1)), int(m.group(2))
                self.assertEqual(n, total)
                seen_parts.add(k)
                # Entity list includes at least one ware:weapon_* key.
                header_block = text.split("# ─")[0]
                self.assertRegex(header_block, r"ware:weapon_\d{3}")
                # Collect all `+` change lines from the body for a coverage check.
                body_section = text.split("# ─", 1)[1].split("\n", 1)[1]
                for line in body_section.splitlines():
                    if line.startswith("+") and not line.startswith("+++"):
                        all_plus_lines.append(line)
            self.assertEqual(seen_parts, set(range(1, total + 1)))

            # Union of `+` lines across all chunks must equal the original diff's.
            original_plus = [
                ln for ln in diff_body.splitlines()
                if ln.startswith("+") and not ln.startswith("+++")
            ]
            self.assertEqual(sorted(all_plus_lines), sorted(original_plus))

    def test_deleted_side_entities_split_into_multiple_modified_chunks(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            v1, v2 = empty_sources(tmp)
            rel = "libraries/jobs.xml"

            def build_job(job_id: str, base: int) -> list[str]:
                lines = [f'  <job id="{job_id}">']
                for i in range(20):
                    lines.append(
                        f'    <quota zone="{i}" amount="{base + i}" note="{"x" * 60}"/>'
                    )
                lines.append("  </job>")
                return lines

            v1_lines = ["<jobs>"]
            for i in range(6):
                v1_lines.extend(build_job(f"job_{i:02d}", i * 100))
            v1_lines.append("</jobs>")
            v1_text = "\n".join(v1_lines) + "\n"

            v2_lines = ["<jobs>"]
            v2_lines.extend(build_job("job_05", 500))
            v2_lines.append("</jobs>")
            v2_text = "\n".join(v2_lines) + "\n"

            write_source(v1, rel, v1_text)
            write_source(v2, rel, v2_text)
            make_diff_artifact(out, rel, ".diff", unified_diff(rel, v1_text, v2_text))
            sm = tmp / "x4_schema_map.generated.json"
            make_schema_map(sm, [{"file": rel, "entity_tag": "job", "id_attribute": "id"}])

            r = run_chunk(out, v1, v2, chunk_kb=3, schema_map=sm)
            self.assertEqual(r.returncode, 0, r.stderr or r.stdout)

            chunks = sorted((out / "03_chunk" / "chunks").glob("*.txt"))
            self.assertGreaterEqual(len(chunks), 2, f"expected deleted-side split: got {[c.name for c in chunks]}")

            headers = [chunk.read_text().split("# ─", 1)[0] for chunk in chunks]
            all_headers = "\n".join(headers)
            self.assertIn("job:job_00", all_headers)
            self.assertIn("job:job_04", all_headers)
            self.assertTrue(
                all("# Entities: entire file" not in header for header in headers),
                headers,
            )

    def test_recursion_produces_subpart_header(self):
        """A single entity whose mini-diff exceeds CHUNK_KB triggers Sub-part split."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            v1, v2 = empty_sources(tmp)

            # One big <plan id="huge"> with many <entry/> children.
            v1_text = "<plans>\n  <plan id=\"huge\">\n  </plan>\n</plans>\n"
            entries = [f'    <entry ref="e_{i:04d}" amount="{i}"/>' for i in range(200)]
            v2_text = "<plans>\n  <plan id=\"huge\">\n" + "\n".join(entries) + "\n  </plan>\n</plans>\n"
            rel = "libraries/constructionplans.xml"
            write_source(v1, rel, v1_text)
            write_source(v2, rel, v2_text)

            diff_body = unified_diff(rel, v1_text, v2_text)
            make_diff_artifact(out, rel, ".diff", diff_body)

            sm = tmp / "x4_schema_map.generated.json"
            make_schema_map(sm, [
                {"file": "libraries/constructionplans.xml", "entity_tag": "plan", "id_attribute": "id"},
            ])

            r = run_chunk(out, v1, v2, chunk_kb=2, schema_map=sm)
            self.assertEqual(r.returncode, 0, r.stderr or r.stdout)

            chunks = sorted((out / "03_chunk" / "chunks").glob("*.txt"))
            self.assertGreaterEqual(len(chunks), 2)
            sub_part_chunks = [c for c in chunks if "Sub-part:" in c.read_text()]
            self.assertGreaterEqual(
                len(sub_part_chunks), 1,
                f"expected at least one Sub-part chunk; got headers:\n"
                + "\n".join(c.read_text().split('# ─')[0] for c in chunks),
            )
            # Verify the sub-part label format.
            sample = sub_part_chunks[0].read_text()
            self.assertRegex(
                sample,
                r"# Sub-part: \d+/\d+ of plan:huge \(split at <entry> boundaries\)",
            )

    def test_complex_single_entity_splits_even_when_under_chunk_kb(self):
        """Complex single-entity diffs recurse on child boundaries before CHUNK_KB is hit."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            v1, v2 = empty_sources(tmp)

            v1_text = "<plans>\n  <plan id=\"huge\">\n  </plan>\n</plans>\n"
            entries = [f'    <entry ref="e_{i:04d}" amount="{i}"/>' for i in range(120)]
            v2_text = "<plans>\n  <plan id=\"huge\">\n" + "\n".join(entries) + "\n  </plan>\n</plans>\n"
            rel = "libraries/constructionplans.xml"
            write_source(v1, rel, v1_text)
            write_source(v2, rel, v2_text)

            diff_body = unified_diff(rel, v1_text, v2_text)
            self.assertLess(len(diff_body.encode("utf-8")), 50 * 1024)
            make_diff_artifact(out, rel, ".diff", diff_body)

            sm = tmp / "x4_schema_map.generated.json"
            make_schema_map(sm, [
                {"file": rel, "entity_tag": "plan", "id_attribute": "id"},
            ])

            r = run_chunk(out, v1, v2, chunk_kb=50, schema_map=sm)
            self.assertEqual(r.returncode, 0, r.stderr or r.stdout)

            chunks = sorted((out / "03_chunk" / "chunks").glob("*.txt"))
            self.assertGreaterEqual(len(chunks), 2)
            self.assertTrue(
                any("Sub-part:" in c.read_text() for c in chunks),
                f"expected recursive split under 50 KB, got {[c.name for c in chunks]}",
            )

    def test_small_dense_multi_entity_diff_splits_before_chunk_kb(self):
        """Small but dense multi-entity diffs split so weak local models see fewer entities."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            v1, v2 = empty_sources(tmp)

            rel = "libraries/mapdefaults.xml"

            def build(version: int) -> str:
                lines = ["<datasets>"]
                for i in range(4):
                    lines.append(f'  <dataset macro="cluster_{i:03d}">')
                    for j in range(12):
                        lines.append(f'    <resource idx="{j}" amount="{version + i + j}"/>')
                    lines.append("  </dataset>")
                lines.append("</datasets>")
                return "\n".join(lines) + "\n"

            v1_text = build(1)
            v2_text = build(2)
            write_source(v1, rel, v1_text)
            write_source(v2, rel, v2_text)

            diff_body = unified_diff(rel, v1_text, v2_text)
            self.assertLess(len(diff_body.encode("utf-8")), 20 * 1024)
            make_diff_artifact(out, rel, ".diff", diff_body)

            sm = tmp / "x4_schema_map.generated.json"
            make_schema_map(sm, [
                {"file": rel, "entity_tag": "dataset", "id_attribute": "macro"},
            ])

            r = run_chunk(out, v1, v2, chunk_kb=20, schema_map=sm)
            self.assertEqual(r.returncode, 0, r.stderr or r.stdout)

            chunks = sorted((out / "03_chunk" / "chunks").glob("*.txt"))
            self.assertGreaterEqual(len(chunks), 2, f"expected dense split: got {[c.name for c in chunks]}")
            headers = "\n".join(c.read_text().split("# ─")[0] for c in chunks)
            for i in range(4):
                self.assertIn(f"dataset:cluster_{i:03d}", headers)

    def test_modified_xml_split_headers_include_deleted_side_entities(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            v1, v2 = empty_sources(tmp)
            rel = "libraries/regionyields.xml"

            def build_definition(def_id: str, base: int) -> list[str]:
                lines = [f'  <definition id="{def_id}">']
                for i in range(24):
                    lines.append(f'    <yield idx="{i}" amount="{base + i}"/>')
                lines.append("  </definition>")
                return lines

            v1_lines = ["<definitions>"]
            for def_id, base in [
                ("sphere_tiny_helium_low", 1000),
                ("sphere_tiny_helium_medium", 2000),
                ("sphere_tiny_helium_verylow", 3000),
                ("sphere_tiny_hydrogen_low", 4000),
            ]:
                v1_lines.extend(build_definition(def_id, base))
            v1_lines.append("</definitions>")
            v1_text = "\n".join(v1_lines) + "\n"

            v2_lines = ["<definitions>"]
            for def_id, base in [
                ("sphere_tiny_helium_verylow", 3500),
                ("sphere_tiny_hydrogen_veryhigh", 5000),
                ("sphere_tiny_hydrogen_low", 4500),
            ]:
                v2_lines.extend(build_definition(def_id, base))
            v2_lines.append("</definitions>")
            v2_text = "\n".join(v2_lines) + "\n"

            write_source(v1, rel, v1_text)
            write_source(v2, rel, v2_text)
            make_diff_artifact(out, rel, ".diff", unified_diff(rel, v1_text, v2_text))
            sm = tmp / "x4_schema_map.generated.json"
            make_schema_map(sm, [{"file": rel, "entity_tag": "definition", "id_attribute": "id"}])

            r = run_chunk(out, v1, v2, chunk_kb=2, schema_map=sm)
            self.assertEqual(r.returncode, 0, r.stderr or r.stdout)

            chunks = sorted((out / "03_chunk" / "chunks").glob("*.txt"))
            self.assertGreaterEqual(len(chunks), 2)
            headers = [chunk.read_text().split("# ─", 1)[0] for chunk in chunks]
            self.assertTrue(
                any("definition:sphere_tiny_helium_low" in header for header in headers),
                headers,
            )
            self.assertTrue(
                any("definition:sphere_tiny_helium_medium" in header for header in headers),
                headers,
            )


class Level1RawTest(unittest.TestCase):
    def test_oversize_added_splits_into_per_entity_chunks(self):
        """An oversize .added file splits into chunks whose Entities list per-entity keys."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            v1, v2 = empty_sources(tmp)

            # Build a raw V2 file that's way larger than chunk_kb=2.
            lines = ["<wares>"]
            for i in range(6):
                lines.append(f'  <ware id="ware_{i:02d}">')
                for j in range(50):
                    lines.append(
                        f'    <attr value="bulk_padding_attribute_{i}_{j}_xxxxxxxxxxxxxxxxxxx"/>'
                    )
                lines.append("  </ware>")
            lines.append("</wares>")
            raw = "\n".join(lines) + "\n"
            make_diff_artifact(out, "libraries/wares.xml", ".added", raw)

            sm = tmp / "x4_schema_map.generated.json"
            make_schema_map(sm, [
                {"file": "libraries/wares.xml", "entity_tag": "ware", "id_attribute": "id"},
            ])

            r = run_chunk(out, v1, v2, chunk_kb=2, schema_map=sm)
            self.assertEqual(r.returncode, 0, r.stderr or r.stdout)

            chunks = sorted((out / "03_chunk" / "chunks").glob("*.txt"))
            self.assertGreaterEqual(len(chunks), 2)
            # Every chunk mentions ≥ 1 ware:ware_* entity.
            ware_ids_seen: set[str] = set()
            for ch in chunks:
                text = ch.read_text()
                header = text.split("# ─")[0]
                self.assertRegex(header, r"# Chunk: libraries/wares\.xml part \d+/\d+")
                for m in re.finditer(r"ware:ware_\d{2}", header):
                    ware_ids_seen.add(m.group(0))
                # Body must NOT have + / - prefixes (raw, per spec).
                body = text.split("# ─", 1)[1].split("\n", 1)[1]
                for line in body.splitlines():
                    self.assertFalse(
                        line.startswith(("+", "-")) and not line.startswith(("+++", "---")),
                        f"raw .added chunk should not have +/- prefix lines: {line!r}",
                    )
            # All six wares should be accounted for across the chunks.
            self.assertEqual(
                ware_ids_seen,
                {f"ware:ware_{i:02d}" for i in range(6)},
            )

    def test_huge_first_entity_recurses_at_child_boundaries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            v1, v2 = empty_sources(tmp)

            lines = ["<plans>", '  <plan id="mega_plan">']
            for i in range(24):
                lines.append(f'    <module macro="module_{i:02d}">')
                lines.append(f'      <connection ref="c{i}" value="{"x" * 120}"/>')
                lines.append("    </module>")
            lines.append("  </plan>")
            lines.append('  <plan id="small_plan"><module macro="small"/></plan>')
            lines.append("</plans>")
            raw = "\n".join(lines) + "\n"
            make_diff_artifact(out, "libraries/constructionplans.xml", ".added", raw)

            sm = tmp / "x4_schema_map.generated.json"
            make_schema_map(sm, [
                {
                    "file": "libraries/constructionplans.xml",
                    "entity_tag": "plan",
                    "id_attribute": "id",
                },
            ])

            r = run_chunk(out, v1, v2, chunk_kb=2, schema_map=sm)
            self.assertEqual(r.returncode, 0, r.stderr or r.stdout)

            chunks = sorted((out / "03_chunk" / "chunks").glob("*.txt"))
            self.assertGreaterEqual(len(chunks), 2)
            headers = [chunk.read_text().split("# ─", 1)[0] for chunk in chunks]
            self.assertTrue(any("# Sub-part:" in header for header in headers), headers)
            self.assertTrue(any("plan:mega_plan" in header for header in headers), headers)

    def test_byte_overflow_single_entity_hard_fails_with_explicit_message(self):
        """Byte-cap overflow is a hard LLM constraint, so the pipeline still fails.

        The error message must identify this as a byte-budget problem so the user
        knows to raise --chunk-kb or add a custom splitter. Complexity-cap hits
        go through a separate softer path (see
        test_complexity_overflow_emits_chunk_with_warning_header).
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            v1, v2 = empty_sources(tmp)

            raw = (
                "<plans>\n"
                '  <plan id="mega_plan">\n'
                f'    <blob text="{"x" * 5000}"/>\n'
                "  </plan>\n"
                "</plans>\n"
            )
            make_diff_artifact(out, "libraries/constructionplans.xml", ".added", raw)

            sm = tmp / "x4_schema_map.generated.json"
            make_schema_map(sm, [
                {
                    "file": "libraries/constructionplans.xml",
                    "entity_tag": "plan",
                    "id_attribute": "id",
                },
            ])

            r = run_chunk(out, v1, v2, chunk_kb=2, schema_map=sm)
            self.assertNotEqual(r.returncode, 0)
            blob = r.stderr + r.stdout
            self.assertIn("won't fit the LLM context", blob)
            self.assertIn("byte budget", blob)
            self.assertFalse(list((out / "03_chunk" / "chunks").glob("*.txt")))


class DlcDiffPatchTest(unittest.TestCase):
    def test_dlc_diff_patch_extracts_keys_from_sel(self):
        """DLC <diff> patch files extract entity keys from sel XPaths."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            v1, v2 = empty_sources(tmp)

            # V2 source is a <diff> patch file. Small is fine here; this test
            # is about sel-derived entity labels, not oversize splitting.
            lines = ['<diff>']
            # Two add ops with sel ids, plus padding.
            lines.append('  <add sel="/wares/ware[@id=\'weapon_arg_l_beam_01\']/production">')
            for j in range(8):
                lines.append(
                    f'    <padding value="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx_{j}"/>'
                )
            lines.append('  </add>')
            lines.append('  <replace sel="//ware[@id=\'shield_gen_m_mk1\']/amount">100</replace>')
            for j in range(8):
                lines.append(
                    f'  <add sel="//other[@id=\'padding_{j}\']"><x/></add>'
                )
            lines.append('</diff>')
            raw = "\n".join(lines) + "\n"

            rel = "extensions/ego_dlc_split/libraries/wares.xml"
            v1_text = "<diff>\n</diff>\n"
            v2_text = raw
            write_source(v1, rel, v1_text)
            write_source(v2, rel, v2_text)

            diff_body = unified_diff(rel, v1_text, v2_text)
            make_diff_artifact(out, rel, ".diff", diff_body)

            sm = tmp / "x4_schema_map.generated.json"
            make_schema_map(sm, [])  # No entry for DLC patch — path should still work.

            r = run_chunk(out, v1, v2, chunk_kb=10, schema_map=sm)
            self.assertEqual(r.returncode, 0, r.stderr or r.stdout)

            chunks = sorted((out / "03_chunk" / "chunks").glob("*.txt"))
            self.assertGreaterEqual(len(chunks), 1)
            headers = "\n".join(c.read_text().split("# ─")[0] for c in chunks)
            self.assertIn("ware:weapon_arg_l_beam_01", headers)
            self.assertIn("ware:shield_gen_m_mk1", headers)


class ChunkProfileRulesTest(unittest.TestCase):
    def test_universal_hunk_cap_splits_even_when_other_limits_fit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            v1, v2 = empty_sources(tmp)
            rel = "libraries/wares.xml"

            def build(mark: str) -> str:
                lines = ["<wares>"]
                for i in range(4):
                    lines.append(f'  <ware id="ware_{i:02d}">')
                    for j in range(10):
                        value = f"{mark}_{i}_{j}" if j == 5 else f"keep_{i}_{j}"
                        lines.append(f'    <price index="{j}" min="{value}"/>')
                    lines.append("  </ware>")
                lines.append("</wares>")
                return "\n".join(lines) + "\n"

            v1_text = build("old")
            v2_text = build("new")
            write_source(v1, rel, v1_text)
            write_source(v2, rel, v2_text)
            diff_body = unified_diff(rel, v1_text, v2_text)
            changed_lines = [
                line for line in diff_body.splitlines()
                if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
            ]
            self.assertLess(len(changed_lines), 30)
            make_diff_artifact(out, rel, ".diff", diff_body)

            sm = tmp / "x4_schema_map.generated.json"
            make_schema_map(sm, [
                {"file": rel, "entity_tag": "ware", "id_attribute": "id"},
            ])

            r = run_chunk(out, v1, v2, chunk_kb=50, schema_map=sm)
            self.assertEqual(r.returncode, 0, r.stderr or r.stdout)
            chunks = sorted((out / "03_chunk" / "chunks").glob("*.txt"))
            self.assertGreaterEqual(len(chunks), 2, f"expected hunk-cap split: got {[c.name for c in chunks]}")

    def test_complexity_overflow_emits_chunk_with_warning_header(self):
        """Chunks that fit the byte budget but exceed soft complexity caps are
        emitted with a '# WARNING:' header instead of hard-failing. Dense diffs
        inside a single nested scope can't always be split structurally, and
        losing the whole pipeline to protect weak-model quality on one chunk
        violates the project's stated priorities (Resumable > Low data loss).
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            v1, v2 = empty_sources(tmp)
            rel = "aiscripts/dense.xml"

            v1_lines = ["<root>", "  <deep>"]
            v1_lines.extend(f"    old_value_line_{i:03d}" for i in range(40))
            v1_lines.extend(["  </deep>", "</root>", ""])
            v2_lines = ["<root>", "  <deep>"]
            v2_lines.extend(f"    new_value_line_{i:03d}" for i in range(40))
            v2_lines.extend(["  </deep>", "</root>", ""])
            v1_text = "\n".join(v1_lines)
            v2_text = "\n".join(v2_lines)
            write_source(v1, rel, v1_text)
            write_source(v2, rel, v2_text)
            make_diff_artifact(out, rel, ".diff", unified_diff(rel, v1_text, v2_text))

            r = run_chunk(out, v1, v2, chunk_kb=50)
            self.assertEqual(r.returncode, 0, r.stderr or r.stdout)
            chunks = sorted((out / "03_chunk" / "chunks").glob("*.txt"))
            self.assertTrue(chunks, "expected chunk emitted despite complexity overflow")
            headers = [c.read_text().split("# ─", 1)[0] for c in chunks]
            self.assertTrue(
                any("too dense for weaker models" in h for h in headers),
                f"expected complexity warning in headers: {headers}",
            )

    def test_jobs_profile_override_splits_small_dlc_added_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            v1, v2 = empty_sources(tmp)
            rel = "extensions/ego_dlc_terran/libraries/jobs.xml"

            lines = ["<jobs>"]
            for i in range(5):
                lines.append(f'  <job id="job_{i:02d}">')
                lines.append("    <ship>")
                lines.append(f'      <select faction="test_{i}" size="ship_s"/>')
                lines.append("    </ship>")
                lines.append("  </job>")
            lines.append("</jobs>")
            raw = "\n".join(lines) + "\n"
            make_diff_artifact(out, rel, ".added", raw)

            sm = tmp / "x4_schema_map.generated.json"
            make_schema_map(sm, [
                {"file": "libraries/jobs.xml", "entity_tag": "job", "id_attribute": "id"},
            ])

            r = run_chunk(out, v1, v2, chunk_kb=50, schema_map=sm)
            self.assertEqual(r.returncode, 0, r.stderr or r.stdout)
            chunks = sorted((out / "03_chunk" / "chunks").glob("*.txt"))
            self.assertGreaterEqual(len(chunks), 2, f"expected jobs override split: got {[c.name for c in chunks]}")
            for chunk in chunks:
                header = chunk.read_text().split("# ─")[0]
                m = re.search(r"# Entities \((\d+)\):", header)
                self.assertIsNotNone(m, f"missing entity count in {chunk.name}")
                self.assertLessEqual(int(m.group(1)), 3, header)


class CliFlagsTest(unittest.TestCase):
    def test_schema_map_cli_flag_accepts_fixture_path(self):
        """--schema-map points the chunker at a fixture schema without touching the default generated map."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            v1, v2 = empty_sources(tmp)

            # Small artifact that fits in one chunk — the run should just succeed.
            make_diff_artifact(out, "libraries/wares.xml", ".diff", "tiny body\n")
            fixture = tmp / "fixture_x4_schema_map.generated.json"
            make_schema_map(fixture, [
                {"file": "libraries/wares.xml", "entity_tag": "ware", "id_attribute": "id"},
            ])
            r = run_chunk(out, v1, v2, chunk_kb=10, schema_map=fixture)
            self.assertEqual(r.returncode, 0, r.stderr)
            chunks = list((out / "03_chunk" / "chunks").glob("*.txt"))
            self.assertEqual(len(chunks), 1)


if __name__ == "__main__":
    unittest.main()
