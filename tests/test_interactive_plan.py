"""Unit tests for scripts/interactive_plan.py (stdlib unittest; no pytest).

These cover the engine's deterministic logic on synthetic fixtures. They do
NOT validate end-to-end note QUALITY — the skill's output is non-deterministic
subagent text. To validate a real run: run /x4-diff on an extracted pair and
READ output/<old>-<new>-<tag>.md, comparing the kind and density of detail
(quoted before/after numbers, "added but disabled" flags, modding callouts) to
a run.sh batch output for a comparable pair. "Works" = read and confirmed, not
a green exit.
"""
import json
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts import interactive_plan as ip  # noqa: E402


class SlugifyTests(unittest.TestCase):
    def test_brackets_become_dashes(self):
        self.assertEqual(ip.slugify_tag('claude-opus-4-8[1m]'), 'claude-opus-4-8-1m')

    def test_spaces_and_parens(self):
        self.assertEqual(ip.slugify_tag('Sonnet 4.6 (low)'), 'sonnet-4.6-low')

    def test_empty_falls_back(self):
        self.assertEqual(ip.slugify_tag('***'), 'claude-session')

    def test_tag_cli_emits_json_only(self):
        r = subprocess.run(
            [sys.executable, str(ROOT / 'scripts' / 'interactive_plan.py'),
             'tag', 'claude-opus-4-8[1m]'],
            capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(json.loads(r.stdout)['tag'], 'claude-opus-4-8-1m')
        self.assertEqual(r.stderr, '')


def _summary(d: Path, old='8.00h4', new='9.00rc4'):
    (d / 'summary.json').write_text(json.dumps(
        {'old_version': old, 'new_version': new, 'changed_files': 1, 'rules': {}}))


def _rule_json(d: Path, rule: str, records: list):
    (d / f'{rule}.json').write_text(json.dumps(records))


def _rec(text, kind=None):
    return {'tag': 'x', 'text': text, 'extras': ({'kind': kind} if kind else {})}


class PrepRuleTests(unittest.TestCase):
    def test_single_chunk_naming(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t); _summary(d); _rule_json(d, 'missiles', [_rec('a small change')])
            out = ip.prep_rule(d, 'missiles', budget=50000)
            self.assertEqual(len(out['chunks']), 1)
            self.assertTrue(out['chunks'][0]['out_path'].endswith('llm_missiles.md'))
            self.assertFalse(out['empty_after_diagnostic'])
            self.assertTrue(Path(out['chunks'][0]['prompt_path']).exists())

    def test_multi_chunk_naming(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t); _summary(d)
            _rule_json(d, 'weapons', [_rec('x' * 8000) for _ in range(4)])
            out = ip.prep_rule(d, 'weapons', budget=3000)
            n = len(out['chunks'])
            self.assertGreater(n, 1)
            self.assertTrue(out['chunks'][0]['out_path'].endswith(f'_chunk1of{n}.md'))

    def test_oversized_flag(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t); _summary(d)
            _rule_json(d, 'quests', [_rec('x' * 40000)])
            out = ip.prep_rule(d, 'quests', budget=5000)
            self.assertTrue(any(c['oversized'] for c in out['chunks']))

    def test_empty_after_diagnostic(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t); _summary(d)
            _rule_json(d, 'jobs', [_rec('w', 'warning'), _rec('i', 'incomplete')])
            out = ip.prep_rule(d, 'jobs', budget=50000)
            self.assertTrue(out['empty_after_diagnostic'])
            self.assertEqual(out['dropped_diagnostic'], 2)
            self.assertEqual(out['chunks'], [])

    def test_skip_existing(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t); _summary(d); _rule_json(d, 'missiles', [_rec('a change')])
            (d / 'llm_missiles.md').write_text('already done')
            out = ip.prep_rule(d, 'missiles', budget=50000)
            self.assertTrue(out['chunks'][0]['done'])
            self.assertIsNone(out['chunks'][0]['prompt_path'])


class PlanRoundBasicTests(unittest.TestCase):
    def test_single_chunk_rule_is_done(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t); _summary(d)
            (d / 'llm_missiles.md').write_text('the notes')
            out = ip.plan_round('rule:missiles', d, budget=50000)
            self.assertEqual(out['phase'], 'done')
            self.assertTrue(out['final_path'].endswith('llm_missiles.md'))
            self.assertEqual(out['tasks'], [])

    def test_two_small_chunks_make_one_final_then_done(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t); _summary(d)
            (d / 'llm_weapons_chunk1of2.md').write_text('a' * 800)
            (d / 'llm_weapons_chunk2of2.md').write_text('b' * 800)
            out = ip.plan_round('rule:weapons', d, budget=50000)
            self.assertEqual(out['phase'], 'final')
            self.assertEqual(len(out['tasks']), 1)
            self.assertTrue(out['tasks'][0]['out_path'].endswith('llm_weapons_aggregated.md'))
            Path(out['tasks'][0]['out_path']).write_text('merged section')
            out2 = ip.plan_round('rule:weapons', d, budget=50000)
            self.assertEqual(out2['phase'], 'done')


class PlanRoundReduceTests(unittest.TestCase):
    def _three_big_chunks(self, d):
        for i in (1, 2, 3):
            (d / f'llm_weapons_chunk{i}of3.md').write_text('x' * 16000)  # ~4000 tok each

    def test_big_chunks_split_into_subbatch_round(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t); _summary(d); self._three_big_chunks(d)
            out = ip.plan_round('rule:weapons', d, budget=6000)
            self.assertEqual(out['phase'], 'merge')
            self.assertGreaterEqual(len(out['tasks']), 2)
            self.assertEqual(out['tasks'][0]['template'], 'subbatch')

    def test_resume_returns_only_pending_tasks(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t); _summary(d); self._three_big_chunks(d)
            out = ip.plan_round('rule:weapons', d, budget=6000)
            Path(out['tasks'][0]['out_path']).write_text('done one')  # finish 1 of N
            again = ip.plan_round('rule:weapons', d, budget=6000)
            self.assertEqual(again['phase'], 'merge')
            self.assertEqual(len(again['tasks']), len(out['tasks']) - 1)

    def test_no_shrinkage_guard_finalizes(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t); _summary(d); self._three_big_chunks(d)
            out = ip.plan_round('rule:weapons', d, budget=6000)
            for task in out['tasks']:                       # outputs as big as inputs
                Path(task['out_path']).write_text('y' * 16000)
            out2 = ip.plan_round('rule:weapons', d, budget=6000)
            self.assertEqual(out2['phase'], 'final')
            self.assertTrue(out2['fallback'])
            self.assertTrue(out2['tasks'][0]['out_path'].endswith('llm_weapons_aggregated.md'))


class CliTests(unittest.TestCase):
    def _run(self, *argv):
        return subprocess.run(
            [sys.executable, str(ROOT / 'scripts' / 'interactive_plan.py'), *argv],
            capture_output=True, text=True)

    def test_prep_rule_cli_json_stdout_clean_stderr(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t); _summary(d); _rule_json(d, 'missiles', [_rec('a change')])
            r = self._run('prep-rule', str(d), 'missiles', '--max-tokens', '50000')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads(r.stdout)['rule'], 'missiles')
            self.assertEqual(r.stderr, '')

    def test_plan_round_cli(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t); _summary(d)
            (d / 'llm_missiles.md').write_text('notes')
            r = self._run('plan-round', 'rule:missiles', str(d), '--max-tokens', '50000')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(json.loads(r.stdout)['phase'], 'done')

    def test_top_requires_out(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t); _summary(d)
            r = self._run('plan-round', 'top', str(d), '--max-tokens', '50000')
            self.assertNotEqual(r.returncode, 0)


class TopGuardTests(unittest.TestCase):
    def test_top_zero_parts_raises(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t); _summary(d)  # summary.json only, no llm_* files
            with self.assertRaises(SystemExit):
                ip.plan_round('top', d, budget=50000, final_out=str(d / 'out.md'))

    def test_top_unaggregated_multichunk_rule_raises(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t); _summary(d)
            (d / 'llm_weapons_chunk1of2.md').write_text('a')
            (d / 'llm_weapons_chunk2of2.md').write_text('b')  # 2 chunks, no aggregate
            with self.assertRaises(SystemExit):
                ip.plan_round('top', d, budget=50000, final_out=str(d / 'out.md'))


def _summary_rules(d: Path, rules: dict, old='8.00h4', new='9.00rc4'):
    (d / 'summary.json').write_text(json.dumps(
        {'old_version': old, 'new_version': new, 'changed_files': 1, 'rules': rules}))


class ChunkCompletenessTests(unittest.TestCase):
    def test_skipped_chunk_raises(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t); _summary(d)
            (d / 'llm_weapons_chunk1of3.md').write_text('a')
            (d / 'llm_weapons_chunk3of3.md').write_text('c')  # 2 of 3 present
            with self.assertRaises(SystemExit):
                ip.plan_round('rule:weapons', d, budget=50000)

    def test_mixed_chunk_counts_raise(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t); _summary(d)
            for i in (1, 2):
                (d / f'llm_weapons_chunk{i}of2.md').write_text('x')
            for i in (1, 2, 3):
                (d / f'llm_weapons_chunk{i}of3.md').write_text('y')  # two chunkings
            with self.assertRaises(SystemExit):
                ip.plan_round('rule:weapons', d, budget=50000)


class CoverageGateTests(unittest.TestCase):
    def test_missing_expected_rule_raises(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            _summary_rules(d, {'missiles': {'count': 5, 'by_kind': {'modified': 5}},
                               'weapons': {'count': 3, 'by_kind': {'modified': 3}}})
            (d / 'llm_missiles.md').write_text('notes')  # weapons absent
            with self.assertRaises(SystemExit):
                ip.plan_round('top', d, budget=50000, final_out=str(d / 'out.md'))

    def test_all_expected_present_passes_gate(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            _summary_rules(d, {'missiles': {'count': 5, 'by_kind': {'modified': 5}},
                               'weapons': {'count': 3, 'by_kind': {'modified': 3}}})
            (d / 'llm_missiles.md').write_text('m notes')
            (d / 'llm_weapons.md').write_text('w notes')
            out = ip.plan_round('top', d, budget=50000, final_out=str(d / 'out.md'))
            self.assertIn(out['phase'], ('final', 'merge'))

    def test_all_diagnostic_rule_not_required(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            _summary_rules(d, {'missiles': {'count': 5, 'by_kind': {'modified': 5}},
                               'jobs': {'count': 2, 'by_kind': {'warning': 2}}})
            (d / 'llm_missiles.md').write_text('m notes')  # jobs is all-diagnostic
            out = ip.plan_round('top', d, budget=50000, final_out=str(d / 'out.md'))
            self.assertIn(out['phase'], ('final', 'merge'))

    def test_allow_missing_sanctions_skip(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            _summary_rules(d, {'missiles': {'count': 5, 'by_kind': {'modified': 5}},
                               'weapons': {'count': 3, 'by_kind': {'modified': 3}}})
            (d / 'llm_missiles.md').write_text('m notes')
            out = ip.plan_round('top', d, budget=50000, final_out=str(d / 'out.md'),
                                allow_missing=['weapons'])
            self.assertIn(out['phase'], ('final', 'merge'))


class CleanErrorTests(unittest.TestCase):
    def test_budget_too_small_clean_message(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t); _summary(d); _rule_json(d, 'missiles', [_rec('a change')])
            r = subprocess.run(
                [sys.executable, str(ROOT / 'scripts' / 'interactive_plan.py'),
                 'prep-rule', str(d), 'missiles', '--max-tokens', '500'],
                capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual(r.stdout, '')
            self.assertIn('error:', r.stderr)
            self.assertNotIn('Traceback', r.stderr)


class ResumeStalenessTests(unittest.TestCase):
    def test_rule_rebuilds_when_a_chunk_changes(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t); _summary(d)
            (d / 'llm_weapons_chunk1of2.md').write_text('a' * 800)
            (d / 'llm_weapons_chunk2of2.md').write_text('b' * 800)
            out = ip.plan_round('rule:weapons', d, budget=50000)   # one final merge task
            Path(out['tasks'][0]['out_path']).write_text('merged v1')
            self.assertEqual(
                ip.plan_round('rule:weapons', d, budget=50000)['phase'], 'done')
            (d / 'llm_weapons_chunk2of2.md').write_text('c' * 1600)  # regenerate a chunk
            out2 = ip.plan_round('rule:weapons', d, budget=50000)
            self.assertEqual(out2['phase'], 'final')                # rebuilt, not stale 'done'
            self.assertEqual(len(out2['tasks']), 1)

    def test_top_rebuilds_when_a_rule_regenerates(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            _summary_rules(d, {'missiles': {'count': 5, 'by_kind': {'modified': 5}},
                               'weapons': {'count': 3, 'by_kind': {'modified': 3}}})
            (d / 'llm_missiles.md').write_text('m')
            (d / 'llm_weapons.md').write_text('w')
            fin = str(d / 'out.md')
            out = ip.plan_round('top', d, budget=50000, final_out=fin)
            Path(out['tasks'][0]['out_path']).write_text('TOP v1')
            self.assertEqual(
                ip.plan_round('top', d, budget=50000, final_out=fin)['phase'], 'done')
            (d / 'llm_weapons.md').write_text('w much richer now')  # regenerate a leaf
            out2 = ip.plan_round('top', d, budget=50000, final_out=fin)
            self.assertEqual(out2['phase'], 'final')               # rebuilt, not stale 'done'

    def test_top_resume_gate_fires_when_allow_missing_dropped(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t)
            _summary_rules(d, {'missiles': {'count': 5, 'by_kind': {'modified': 5}},
                               'weapons': {'count': 3, 'by_kind': {'modified': 3}}})
            (d / 'llm_missiles.md').write_text('m')  # weapons deliberately absent
            fin = str(d / 'out.md')
            out = ip.plan_round('top', d, budget=50000, final_out=fin,
                                allow_missing=['weapons'])
            Path(out['tasks'][0]['out_path']).write_text('TOP only missiles')
            with self.assertRaises(SystemExit):  # re-run WITHOUT allow-missing -> gate fires
                ip.plan_round('top', d, budget=50000, final_out=fin)


class RuntimeErrorCleanTests(unittest.TestCase):
    def test_mixed_compact_noncompact_clean_error(self):
        with tempfile.TemporaryDirectory() as t:
            d = Path(t); _summary(d)
            (d / 'llm_weapons.md').write_text('plain')
            (d / 'llm_weapons_compact.md').write_text('compact')  # both flavors -> RuntimeError
            r = subprocess.run(
                [sys.executable, str(ROOT / 'scripts' / 'interactive_plan.py'),
                 'plan-round', 'rule:weapons', str(d), '--max-tokens', '50000'],
                capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn('error:', r.stderr)
            self.assertNotIn('Traceback', r.stderr)


if __name__ == '__main__':
    unittest.main()
