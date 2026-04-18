import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.lib.entity_diff import apply_patch


GOLDEN = Path(__file__).resolve().parent / 'fixtures' / '_entity_diff_golden'


def normalize(xml_bytes: bytes) -> str:
    el = ET.fromstring(xml_bytes)
    _strip_whitespace(el)
    return ET.tostring(el, encoding='unicode')


def _strip_whitespace(el):
    el.text = (el.text or '').strip() or None
    el.tail = (el.tail or '').strip() or None
    for c in el:
        _strip_whitespace(c)


class PatchEngineGoldenTest(unittest.TestCase):
    pass


def _make(name):
    def test(self):
        d = GOLDEN / name
        core = d / 'core_input.xml'
        patch = d / 'dlc_patch.xml'
        expected = d / 'expected_effective.xml'
        if not core.exists() or not patch.exists() or not expected.exists():
            self.skipTest(f'fixture {name} missing')
        core_tree = ET.parse(core).getroot()
        failures, warnings = apply_patch(core_tree, ET.parse(patch).getroot())
        self.assertEqual(failures, [], f'unexpected failures: {failures}')
        # Silent-miss goldens are expected to produce a warning but no failure.
        if 'silent_' in name:
            self.assertTrue(warnings, f'{name} should produce a warning')
        self.assertEqual(
            normalize(ET.tostring(core_tree)),
            normalize(Path(expected).read_bytes()),
            f'golden mismatch for {name}',
        )
    test.__name__ = f'test_{name}'
    return test


for slug in ['add_plain', 'add_pos_after', 'add_pos_before', 'add_pos_prepend',
             'add_positional_literal',
             'replace_element', 'replace_attr', 'remove_element',
             'remove_silent_true', 'remove_silent_1',
             'if_not_gate', 'if_not_gate_blocks', 'native_fragment']:
    setattr(PatchEngineGoldenTest, f'test_{slug}', _make(slug))


class FailureCaseTest(unittest.TestCase):
    def test_unsupported_xpath_raises(self):
        core = ET.fromstring('<root><a/></root>')
        patch = ET.fromstring('<diff><add sel="//a[position()=1]">x</add></diff>')
        failures, warnings = apply_patch(core, patch)
        self.assertTrue(failures)
        self.assertEqual(failures[0][1].get('reason'), 'unsupported_xpath')

    def test_missing_target_without_silent_fails(self):
        core = ET.fromstring('<root/>')
        patch = ET.fromstring('<diff><remove sel="//absent"/></diff>')
        failures, warnings = apply_patch(core, patch)
        self.assertTrue(failures)

    def test_silent_miss_produces_warning_not_failure(self):
        core = ET.fromstring('<root/>')
        patch = ET.fromstring('<diff><remove sel="//absent" silent="true"/></diff>')
        failures, warnings = apply_patch(core, patch)
        self.assertEqual(failures, [])
        self.assertTrue(warnings)
        self.assertEqual(warnings[0][1].get('reason'), 'silent_remove_miss')

    def test_native_fragment_targets_file_root(self):
        core = ET.fromstring('<plans><plan id="existing"/></plans>')
        patch = ET.fromstring('<plans><plan id="new_from_dlc"/></plans>')
        failures, warnings = apply_patch(core, patch)
        self.assertEqual(failures, [])
        self.assertEqual([p.get('id') for p in core], ['existing', 'new_from_dlc'])


if __name__ == '__main__':
    unittest.main()
