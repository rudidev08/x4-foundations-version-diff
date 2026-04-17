#!/usr/bin/env python3
"""
Test 04_llm — pipe each chunk to an LLM command, store findings.

Run:
    python3 src/04_llm.test.py

CLI shape exercised:
    python3 src/04_llm.py --out DIR --llm-cmd "<shell-command>"
        [--prompt PATH] [--workers N] [--no-approval]

Testing strategy:
    Real LLMs are non-deterministic and cost money, so these tests use
    `cat` (echoes stdin verbatim) or `printf` as deterministic stand-ins.
    Every test passes `--no-approval` to skip the interactive prompt.
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
SCRIPT = HERE / "04_llm.py"


def make_chunk(out: Path, chunk_id: str, body: str) -> Path:
    path = out / "03_chunk" / "chunks" / f"{chunk_id}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def make_prompt(tmp: Path, text: str = "SYSTEM_PROMPT\n") -> Path:
    p = tmp / "prompt.md"
    p.write_text(text)
    return p


def run_step(
    out: Path,
    llm_cmd: str,
    prompt_path: Path | None = None,
    *,
    workers: int = 1,
    no_approval: bool = True,
    llm_calls: int | None = None,
    timeout_sec: float | None = None,
):
    cmd = [sys.executable, str(SCRIPT), "--out", str(out), "--llm-cmd", llm_cmd]
    if prompt_path is not None:
        cmd.extend(["--prompt", str(prompt_path)])
    if workers != 1:
        cmd.extend(["--workers", str(workers)])
    if llm_calls is not None:
        cmd.extend(["--llm-calls", str(llm_calls)])
    if timeout_sec is not None:
        cmd.extend(["--timeout-sec", str(timeout_sec)])
    if no_approval:
        cmd.append("--no-approval")
    return subprocess.run(cmd, capture_output=True, text=True)


class LLMTest(unittest.TestCase):
    def test_finding_captures_llm_stdout(self):
        """Whatever the LLM command writes to stdout becomes the finding body."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            make_chunk(out, "libraries__wares.xml__part1of1", "chunk body here\n")
            r = run_step(out, "cat", prompt_path=make_prompt(tmp))
            self.assertEqual(r.returncode, 0, r.stderr)

            finding = out / "04_llm" / "findings" / "libraries__wares.xml__part1of1.md"
            self.assertTrue(finding.exists())
            text = finding.read_text()
            self.assertIn("SYSTEM_PROMPT", text)
            self.assertIn("chunk body here", text)
            self.assertLess(text.index("SYSTEM_PROMPT"), text.index("chunk body here"))

    def test_empty_stdout_aborts_with_full_stderr(self):
        """Non-zero exit / empty stdout → ABORT showing command, returncode, stderr."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            make_chunk(out, "chunk1", "body\n")
            # `sh -c 'echo oops >&2; exit 7'` — non-zero exit with recognizable stderr.
            fake_cmd = "sh -c 'echo oops_msg >&2; exit 7'"
            r = run_step(out, fake_cmd, prompt_path=make_prompt(tmp))
            self.assertNotEqual(r.returncode, 0)
            blob = r.stderr
            self.assertIn("ABORT", blob)
            self.assertIn("chunk1", blob)
            self.assertIn("returncode: 7", blob)
            self.assertIn("oops_msg", blob)
            self.assertFalse((out / "04_llm" / "findings" / "chunk1.md").exists())

    def test_timeout_aborts_cleanly_with_chunk_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            make_chunk(out, "chunk1", "body\n")
            r = run_step(out, "sh -c 'sleep 2'", prompt_path=make_prompt(tmp), timeout_sec=1)
            self.assertNotEqual(r.returncode, 0)
            blob = r.stderr
            self.assertIn("ABORT", blob)
            self.assertIn("chunk1", blob)
            self.assertIn("returncode: 124", blob)
            self.assertIn("timed out after 1 second", blob)
            self.assertNotIn("TimeoutExpired", blob)
            self.assertFalse((out / "04_llm" / "findings" / "chunk1.md").exists())

    def test_none_literal_is_saved_as_finding(self):
        """'[none]' is a valid finding body — must be persisted even though it's short."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            make_chunk(out, "chunk1", "body\n")
            r = run_step(out, "printf [none]", prompt_path=make_prompt(tmp))
            self.assertEqual(r.returncode, 0, r.stderr)
            finding = out / "04_llm" / "findings" / "chunk1.md"
            self.assertTrue(finding.exists())
            self.assertEqual(finding.read_text().strip(), "[none]")

    def test_small_entity_scoped_none_is_retried_once(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            chunk = (
                "# Chunk: libraries/jobs.xml part 1/1\n"
                "# Entities (1): job:test_job\n"
                "# Allowed prefixes JSON: [\"job:test_job\", \"file:libraries/jobs.xml\"]\n"
                "# ─────────────────────────────────────\n"
                "# Source: libraries/jobs.xml\n"
                "# Status: modified\n"
                "# V1 bytes: 100 | V2 bytes: 120\n"
                "# ─────────────────────────────────────\n"
                "--- libraries/jobs.xml\n"
                "+++ libraries/jobs.xml\n"
                "@@ -1,3 +1,5 @@\n"
                " <job id=\"test_job\">\n"
                "-  <quantity exact=\"1.0\"/>\n"
                "+  <quantity exact=\"1.0\">\n"
                "+    <variation exact=\"1.0\"/>\n"
                "+  </quantity>\n"
                " </job>\n"
            )
            make_chunk(out, "chunk1", chunk)
            retry_cmd = (
                "sh -c 'data=$(cat); "
                "case \"$data\" in "
                "*\"RECHECK CAREFULLY\"*) printf \"[job:test_job]\\n- Loadouts now vary.\\n\" ;; "
                "*) printf \"[none]\" ;; "
                "esac'"
            )
            r = run_step(out, retry_cmd, prompt_path=make_prompt(tmp))
            self.assertEqual(r.returncode, 0, r.stderr)
            finding = out / "04_llm" / "findings" / "chunk1.md"
            self.assertEqual(finding.read_text(), "[job:test_job]\n- Loadouts now vary.\n")
            self.assertIn("retried", r.stdout)

    def test_fully_file_scoped_fallback_is_retried_once(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            chunk = (
                "# Chunk: libraries/regionyields.xml part 20/65\n"
                "# Entities (3): definition:sphere_small_ore_veryhigh, definition:sphere_small_silicon_low, definition:sphere_small_silicon_medium\n"
                "# Allowed prefixes JSON: [\"definition:sphere_small_ore_veryhigh\", \"definition:sphere_small_silicon_low\", \"definition:sphere_small_silicon_medium\", \"file:libraries/regionyields.xml\"]\n"
                "# ─────────────────────────────────────\n"
                "# Source: libraries/regionyields.xml\n"
                "# Status: modified\n"
                "# V1 bytes: 100 | V2 bytes: 120\n"
                "# ─────────────────────────────────────\n"
                "--- libraries/regionyields.xml\n"
                "+++ libraries/regionyields.xml\n"
                "@@ -1,3 +1,4 @@\n"
                "+<definition id=\"sphere_small_ore_veryhigh\"/>\n"
                "-<definition id=\"sphere_small_silicon_low\"/>\n"
                "-<definition id=\"sphere_small_silicon_medium\"/>\n"
            )
            make_chunk(out, "chunk1", chunk)
            retry_cmd = (
                "sh -c 'data=$(cat); "
                "case \"$data\" in "
                "*\"PREFIX RECHECK CAREFULLY\"*) "
                "printf \"[definition:sphere_small_ore_veryhigh]\\n- Added a new very-high ore region.\\n\\n[definition:sphere_small_silicon_low]\\n- Removed.\\n\\n[definition:sphere_small_silicon_medium]\\n- Removed.\\n\" ;; "
                "*) "
                "printf \"[file:libraries/regionyields.xml]\\n- Added a new very-high ore region and removed the low and medium silicon definitions.\\n\" ;; "
                "esac'"
            )
            r = run_step(out, retry_cmd, prompt_path=make_prompt(tmp))
            self.assertEqual(r.returncode, 0, r.stderr)
            finding = out / "04_llm" / "findings" / "chunk1.md"
            text = finding.read_text()
            self.assertIn("[definition:sphere_small_silicon_low]", text)
            self.assertIn("[definition:sphere_small_silicon_medium]", text)
            self.assertNotIn("[file:libraries/regionyields.xml]", text)
            self.assertIn("retried", r.stdout)

    def test_mixed_file_and_entity_response_is_not_retried(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            chunk = (
                "# Chunk: libraries/regionyields.xml part 20/65\n"
                "# Entities (3): definition:sphere_small_ore_veryhigh, definition:sphere_small_silicon_low, definition:sphere_small_silicon_medium\n"
                "# Allowed prefixes JSON: [\"definition:sphere_small_ore_veryhigh\", \"definition:sphere_small_silicon_low\", \"definition:sphere_small_silicon_medium\", \"file:libraries/regionyields.xml\"]\n"
                "# ─────────────────────────────────────\n"
                "# Source: libraries/regionyields.xml\n"
                "# Status: modified\n"
                "# V1 bytes: 100 | V2 bytes: 120\n"
                "# ─────────────────────────────────────\n"
                "--- libraries/regionyields.xml\n"
                "+++ libraries/regionyields.xml\n"
                "@@ -1,3 +1,4 @@\n"
                "+<definition id=\"sphere_small_ore_veryhigh\"/>\n"
                "-<definition id=\"sphere_small_silicon_low\"/>\n"
                "-<definition id=\"sphere_small_silicon_medium\"/>\n"
            )
            make_chunk(out, "chunk1", chunk)
            retry_cmd = (
                "sh -c 'data=$(cat); "
                "case \"$data\" in "
                "*\"PREFIX RECHECK CAREFULLY\"*) "
                "printf \"[definition:sphere_small_ore_veryhigh]\\n- Retry path should not run.\\n\" ;; "
                "*) "
                "printf \"[definition:sphere_small_ore_veryhigh]\\n- Added a new very-high ore region.\\n\\n[file:libraries/regionyields.xml]\\n- Removed the low and medium silicon definitions.\\n\" ;; "
                "esac'"
            )
            r = run_step(out, retry_cmd, prompt_path=make_prompt(tmp))
            self.assertEqual(r.returncode, 0, r.stderr)
            finding = out / "04_llm" / "findings" / "chunk1.md"
            self.assertEqual(
                finding.read_text(),
                "[definition:sphere_small_ore_veryhigh]\n- Added a new very-high ore region.\n\n[file:libraries/regionyields.xml]\n- Removed the low and medium silicon definitions.\n",
            )
            self.assertNotIn("retried", r.stdout)

    def test_file_scoped_none_is_not_retried(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            chunk = (
                "# Chunk: libraries/parameters.xml part 1/1\n"
                "# Entities: entire file\n"
                "# Allowed prefixes JSON: [\"file:libraries/parameters.xml\"]\n"
                "# ─────────────────────────────────────\n"
                "# Source: libraries/parameters.xml\n"
                "# Status: modified\n"
                "# V1 bytes: 100 | V2 bytes: 120\n"
                "# ─────────────────────────────────────\n"
                "--- libraries/parameters.xml\n"
                "+++ libraries/parameters.xml\n"
                "@@ -1,1 +1,1 @@\n"
                "-<param value=\"1\"/>\n"
                "+<param value=\"2\"/>\n"
            )
            make_chunk(out, "chunk1", chunk)
            retry_cmd = (
                "sh -c 'data=$(cat); "
                "case \"$data\" in "
                "*\"RECHECK CAREFULLY\"*) printf \"[file:libraries/parameters.xml]\\n- Should only appear on retry.\\n\" ;; "
                "*) printf \"[none]\" ;; "
                "esac'"
            )
            r = run_step(out, retry_cmd, prompt_path=make_prompt(tmp))
            self.assertEqual(r.returncode, 0, r.stderr)
            finding = out / "04_llm" / "findings" / "chunk1.md"
            self.assertEqual(finding.read_text().strip(), "[none]")
            self.assertNotIn("retried", r.stdout)

    def test_file_fallback_is_not_retried_without_entity_prefixes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            chunk = (
                "# Chunk: libraries/parameters.xml part 1/1\n"
                "# Entities: entire file\n"
                "# Allowed prefixes JSON: [\"file:libraries/parameters.xml\"]\n"
                "# ─────────────────────────────────────\n"
                "# Source: libraries/parameters.xml\n"
                "# Status: modified\n"
                "# V1 bytes: 100 | V2 bytes: 120\n"
                "# ─────────────────────────────────────\n"
                "--- libraries/parameters.xml\n"
                "+++ libraries/parameters.xml\n"
                "@@ -1,1 +1,1 @@\n"
                "-<param value=\"1\"/>\n"
                "+<param value=\"2\"/>\n"
            )
            make_chunk(out, "chunk1", chunk)
            retry_cmd = (
                "sh -c 'data=$(cat); "
                "case \"$data\" in "
                "*\"PREFIX RECHECK CAREFULLY\"*) printf \"[file:libraries/parameters.xml]\\n- Should only appear on retry.\\n\" ;; "
                "*) printf \"[file:libraries/parameters.xml]\\n- Parameter changed.\\n\" ;; "
                "esac'"
            )
            r = run_step(out, retry_cmd, prompt_path=make_prompt(tmp))
            self.assertEqual(r.returncode, 0, r.stderr)
            finding = out / "04_llm" / "findings" / "chunk1.md"
            self.assertEqual(finding.read_text(), "[file:libraries/parameters.xml]\n- Parameter changed.\n")
            self.assertNotIn("retried", r.stdout)

    def test_resumable_skips_existing_findings(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            make_chunk(out, "chunk1", "body\n")
            finding = out / "04_llm" / "findings" / "chunk1.md"
            finding.parent.mkdir(parents=True)
            finding.write_text("PRIOR FINDING\n")
            r = run_step(out, "cat", prompt_path=make_prompt(tmp))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(finding.read_text(), "PRIOR FINDING\n")

    def test_prompt_file_is_prepended_before_chunk(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            make_chunk(out, "chunk1", "CHUNK_MARKER\n")
            prompt = make_prompt(tmp, "FIRST_PROMPT_LINE\nSECOND_PROMPT_LINE\n")
            r = run_step(out, "cat", prompt_path=prompt)
            self.assertEqual(r.returncode, 0, r.stderr)
            text = (out / "04_llm" / "findings" / "chunk1.md").read_text()
            self.assertIn("FIRST_PROMPT_LINE", text)
            self.assertIn("CHUNK_MARKER", text)
            self.assertLess(text.index("FIRST_PROMPT_LINE"), text.index("CHUNK_MARKER"))

    def test_missing_prompt_file_aborts_before_writing_findings(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            make_chunk(out, "chunk1", "body\n")
            missing = tmp / "does_not_exist.md"

            r = run_step(out, "cat", prompt_path=missing)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("cannot read prompt file", r.stderr)
            self.assertIn(str(missing), r.stderr)
            self.assertFalse((out / "04_llm" / "findings" / "chunk1.md").exists())

    def test_llm_calls_cap_stops_and_leaves_rest_pending(self):
        """--llm-calls N caps fresh calls; remaining chunks stay unprocessed for next run."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            for i in range(5):
                make_chunk(out, f"chunk{i}", f"body-{i}\n")
            r = run_step(out, "cat", prompt_path=make_prompt(tmp), llm_calls=2)
            self.assertEqual(r.returncode, 0, r.stderr)
            findings = sorted(p.name for p in (out / "04_llm" / "findings").glob("*.md"))
            # Exactly 2 findings written; 3 still pending for the next run.
            self.assertEqual(len(findings), 2)
            self.assertIn("paused 3", r.stdout)

    def test_llm_calls_cap_skips_cached_chunks(self):
        """Cached chunks don't count toward the cap — only fresh calls do."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            findings_dir = out / "04_llm" / "findings"
            findings_dir.mkdir(parents=True)
            for i in range(5):
                make_chunk(out, f"chunk{i}", f"body-{i}\n")
            # Pre-populate 3 findings — those should skip instantly.
            for i in range(3):
                (findings_dir / f"chunk{i}.md").write_text("cached\n")
            r = run_step(out, "cat", prompt_path=make_prompt(tmp), llm_calls=1)
            self.assertEqual(r.returncode, 0, r.stderr)
            # Cached 3 + 1 fresh = 4 findings total; 1 still pending.
            findings = sorted(p.name for p in findings_dir.glob("*.md"))
            self.assertEqual(len(findings), 4)
            self.assertIn("paused 1", r.stdout)

    def test_llm_calls_cap_counts_retry_invocations(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            calls = tmp / "calls.txt"
            chunk1 = (
                "# Chunk: libraries/jobs.xml part 1/1\n"
                "# Entities (1): job:test_job\n"
                "# Allowed prefixes JSON: [\"job:test_job\", \"file:libraries/jobs.xml\"]\n"
                "# ─────────────────────────────────────\n"
                "# Source: libraries/jobs.xml\n"
                "# Status: modified\n"
                "# V1 bytes: 100 | V2 bytes: 120\n"
                "# ─────────────────────────────────────\n"
                "--- libraries/jobs.xml\n"
                "+++ libraries/jobs.xml\n"
                "@@ -1,3 +1,5 @@\n"
                " <job id=\"test_job\">\n"
                "-  <quantity exact=\"1.0\"/>\n"
                "+  <quantity exact=\"1.0\">\n"
                "+    <variation exact=\"1.0\"/>\n"
                "+  </quantity>\n"
                " </job>\n"
            )
            make_chunk(out, "chunk1", chunk1)
            make_chunk(out, "chunk2", "plain second chunk\n")
            retry_cmd = (
                f"sh -c 'printf x >> {calls}; "
                "data=$(cat); "
                "case \"$data\" in "
                "*\"RECHECK CAREFULLY\"*) printf \"[job:test_job]\\n- Loadouts now vary.\\n\" ;; "
                "*) printf \"[none]\" ;; "
                "esac'"
            )
            r = run_step(out, retry_cmd, prompt_path=make_prompt(tmp), llm_calls=2)
            self.assertEqual(r.returncode, 0, r.stderr)

            self.assertEqual(calls.read_text(), "xx")
            self.assertTrue((out / "04_llm" / "findings" / "chunk1.md").exists())
            self.assertFalse((out / "04_llm" / "findings" / "chunk2.md").exists())
            self.assertIn("paused 1", r.stdout)

    def test_llm_calls_cap_leaves_retryable_first_chunk_pending_without_caching(self):
        """Intentional: a retry-needed first pass is not a done-marker on its own."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            calls = tmp / "calls.txt"
            chunk1 = (
                "# Chunk: libraries/jobs.xml part 1/1\n"
                "# Entities (1): job:test_job\n"
                "# Allowed prefixes JSON: [\"job:test_job\", \"file:libraries/jobs.xml\"]\n"
                "# ─────────────────────────────────────\n"
                "# Source: libraries/jobs.xml\n"
                "# Status: modified\n"
                "# V1 bytes: 100 | V2 bytes: 120\n"
                "# ─────────────────────────────────────\n"
                "--- libraries/jobs.xml\n"
                "+++ libraries/jobs.xml\n"
                "@@ -1,3 +1,5 @@\n"
                " <job id=\"test_job\">\n"
                "-  <quantity exact=\"1.0\"/>\n"
                "+  <quantity exact=\"1.0\">\n"
                "+    <variation exact=\"1.0\"/>\n"
                "+  </quantity>\n"
                " </job>\n"
            )
            make_chunk(out, "chunk1", chunk1)
            make_chunk(out, "chunk2", "plain second chunk\n")
            retry_cmd = (
                f"sh -c 'printf x >> {calls}; "
                "data=$(cat); "
                "case \"$data\" in "
                "*\"RECHECK CAREFULLY\"*) printf \"[job:test_job]\\n- Retry path should not run.\\n\" ;; "
                "*) printf \"[none]\" ;; "
                "esac'"
            )
            r = run_step(out, retry_cmd, prompt_path=make_prompt(tmp), llm_calls=1)
            self.assertEqual(r.returncode, 0, r.stderr)

            self.assertEqual(calls.read_text(), "x")
            self.assertFalse((out / "04_llm" / "findings" / "chunk1.md").exists())
            self.assertFalse((out / "04_llm" / "findings" / "chunk2.md").exists())
            self.assertIn("retry required; budget exhausted; chunk left pending", r.stdout)
            self.assertIn("paused 2", r.stdout)

    def test_concurrent_workers_process_all_chunks(self):
        """--workers > 1: every non-cached chunk still gets a finding."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            out = tmp / "art"
            for i in range(6):
                make_chunk(out, f"chunk{i}", f"body-{i}\n")
            r = run_step(out, "cat", prompt_path=make_prompt(tmp), workers=3)
            self.assertEqual(r.returncode, 0, r.stderr)
            names = sorted(p.name for p in (out / "04_llm" / "findings").glob("*.md"))
            self.assertEqual(names, [f"chunk{i}.md" for i in range(6)])


if __name__ == "__main__":
    unittest.main()
