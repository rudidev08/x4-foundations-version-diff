#!/usr/bin/env python3
"""
Integration tests for run.sh.

Run:
    python3 src/run.test.py
"""
import os
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "run.sh"
MODEL_NAME = "cap-test-model"
MODEL_PREFIX = "CAP_TEST"


def write_file(root: Path, rel_path: str, text: str) -> None:
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class RunScriptTest(unittest.TestCase):
    def test_llm_calls_cap_stops_before_assembly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            v1 = tmp / "v1"
            v2 = tmp / "v2"

            for name, old, new in [
                ("md/a.xml", "<root>old-a</root>\n", "<root>new-alpha</root>\n"),
                ("md/b.xml", "<root>old-b</root>\n", "<root>new-bravo</root>\n"),
                ("md/c.xml", "<root>old-c</root>\n", "<root>new-charlie</root>\n"),
            ]:
                write_file(v1, name, old)
                write_file(v2, name, new)

            art = ROOT / "artifacts" / f"{v1.name}_to_{v2.name}_{MODEL_NAME}"
            out = ROOT / "output" / f"{v1.name}_to_{v2.name}_{MODEL_NAME}.md"

            env = os.environ.copy()
            env.update(
                {
                    f"{MODEL_PREFIX}_MODEL_NAME": MODEL_NAME,
                    f"{MODEL_PREFIX}_LLM_CMD": "cat",
                    f"{MODEL_PREFIX}_CHUNK_KB": "30",
                }
            )

            try:
                result = subprocess.run(
                    ["bash", str(RUNNER), "--v1", str(v1), "--v2", str(v2), "--model", MODEL_NAME, "--llm-calls", "2"],
                    cwd=ROOT,
                    env=env,
                    input="y\n",
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("Paused after step 4", result.stdout)

                findings = sorted((art / "04_llm" / "findings").glob("*.md"))
                self.assertEqual(len(findings), 2)
                self.assertFalse(out.exists(), "capped run should not assemble final output")
            finally:
                shutil.rmtree(art, ignore_errors=True)
                out.unlink(missing_ok=True)

    def test_workers_flag_is_forwarded_to_step4(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            v1 = tmp / "v1"
            v2 = tmp / "v2"

            write_file(v1, "md/a.xml", "<root>old-a</root>\n")
            write_file(v2, "md/a.xml", "<root>new-a</root>\n")

            art = ROOT / "artifacts" / f"{v1.name}_to_{v2.name}_{MODEL_NAME}"
            out = ROOT / "output" / f"{v1.name}_to_{v2.name}_{MODEL_NAME}.md"

            env = os.environ.copy()
            env.update(
                {
                    f"{MODEL_PREFIX}_MODEL_NAME": MODEL_NAME,
                    f"{MODEL_PREFIX}_LLM_CMD": "cat",
                    f"{MODEL_PREFIX}_CHUNK_KB": "30",
                }
            )

            try:
                result = subprocess.run(
                    ["bash", str(RUNNER), "--v1", str(v1), "--v2", str(v2), "--model", MODEL_NAME, "--workers", "not-a-number"],
                    cwd=ROOT,
                    env=env,
                    input="y\n",
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("argument --workers: invalid int value", result.stderr)
                self.assertFalse(out.exists())
            finally:
                shutil.rmtree(art, ignore_errors=True)
                out.unlink(missing_ok=True)

    def test_relative_llm_cmd_is_resolved_from_repo_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            v1 = tmp / "v1"
            v2 = tmp / "v2"
            bin_dir = tmp / "bin"
            bin_dir.mkdir()

            codex = bin_dir / "codex"
            codex.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "out=''\n"
                "while [[ $# -gt 0 ]]; do\n"
                "  if [[ \"$1\" == \"--output-last-message\" ]]; then\n"
                "    out=\"$2\"\n"
                "    shift 2\n"
                "    continue\n"
                "  fi\n"
                "  shift\n"
                "done\n"
                "printf '[none]\\n' > \"$out\"\n",
                encoding="utf-8",
            )
            codex.chmod(0o755)

            for name, old, new in [
                ("md/a.xml", "<root>old-a</root>\n", "<root>new-alpha</root>\n"),
                ("md/b.xml", "<root>old-b</root>\n", "<root>new-bravo</root>\n"),
                ("md/c.xml", "<root>old-c</root>\n", "<root>new-charlie</root>\n"),
            ]:
                write_file(v1, name, old)
                write_file(v2, name, new)

            art = ROOT / "artifacts" / f"{v1.name}_to_{v2.name}_{MODEL_NAME}"
            out = ROOT / "output" / f"{v1.name}_to_{v2.name}_{MODEL_NAME}.md"

            env = os.environ.copy()
            env.update(
                {
                    f"{MODEL_PREFIX}_MODEL_NAME": MODEL_NAME,
                    f"{MODEL_PREFIX}_LLM_CMD": "src/codex-wrap.sh --color never",
                    f"{MODEL_PREFIX}_CHUNK_KB": "30",
                    "PATH": f"{bin_dir}{os.pathsep}{env.get('PATH', '')}",
                }
            )

            try:
                result = subprocess.run(
                    ["bash", str(RUNNER), "--v1", str(v1), "--v2", str(v2), "--model", MODEL_NAME, "--llm-calls", "1"],
                    cwd=tmp,
                    env=env,
                    input="y\n",
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("Paused after step 4", result.stdout)

                settings = json.loads((art / "settings.json").read_text())
                self.assertTrue(settings["llm_cmd"].startswith(str(ROOT / "src" / "codex-wrap.sh")))
            finally:
                shutil.rmtree(art, ignore_errors=True)
                out.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
