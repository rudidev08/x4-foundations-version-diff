# scripts/inventory_xpath_ops.py
"""Scan all x4-data/<version>/extensions/*/.../*.xml and .../maps/*.xml for
<diff>-wrapped patch ops. Tabulate distinct shapes:
- op tag: add/replace/remove
- attrs present: sel, if, pos, silent, sel_has_attr_terminator
- pos values seen
- XPath axis tokens: //, /, predicates, not(), @attr
Output a human-readable table to tests/fixtures/_entity_diff_golden/xpath_inventory.txt.
"""
from collections import Counter, defaultdict
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path('x4-data')
OUT = Path('tests/fixtures/_entity_diff_golden/xpath_inventory.txt')


def main():
    counts = Counter()
    sel_patterns = Counter()
    if_patterns = Counter()
    pos_values = Counter()
    silent_values = Counter()
    file_root_tags = Counter()
    by_version = defaultdict(int)
    for ver in sorted(ROOT.glob('*')):
        if not ver.is_dir():
            continue
        for xml in ver.glob('extensions/*/**/*.xml'):
            try:
                root = ET.parse(xml).getroot()
            except ET.ParseError:
                continue
            file_root_tags[root.tag] += 1
            if root.tag != 'diff':
                continue
            for op in root:
                counts[op.tag] += 1
                by_version[ver.name] += 1
                sel = op.get('sel')
                if sel:
                    sel_patterns[_shape(sel)] += 1
                gate = op.get('if')
                if gate:
                    if_patterns[_shape(gate)] += 1
                if op.get('pos'):
                    pos_values[op.get('pos')] += 1
                if op.get('silent'):
                    silent_values[op.get('silent')] += 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open('w') as f:
        f.write('# XPath inventory\n\n')
        f.write('## op counts\n')
        for k, v in counts.most_common(): f.write(f'  {k}: {v}\n')
        f.write('\n## by version\n')
        for k, v in sorted(by_version.items()): f.write(f'  {k}: {v}\n')
        f.write('\n## file root tags (for fragment vs diff detection)\n')
        for k, v in file_root_tags.most_common(30): f.write(f'  {k}: {v}\n')
        f.write('\n## pos values\n')
        for k, v in pos_values.most_common(): f.write(f'  {k}: {v}\n')
        f.write('\n## silent values\n')
        for k, v in silent_values.most_common(): f.write(f'  {k}: {v}\n')
        f.write('\n## sel shapes (top 40)\n')
        for k, v in sel_patterns.most_common(40): f.write(f'  {k}: {v}\n')
        f.write('\n## if shapes (top 40)\n')
        for k, v in if_patterns.most_common(40): f.write(f'  {k}: {v}\n')
    print(f'wrote {OUT}')


def _shape(xp: str) -> str:
    """Normalize XPath into a shape signature: replace ids/names with placeholders."""
    import re
    s = re.sub(r"='[^']*'", "='$V'", xp)
    s = re.sub(r'="[^"]*"', '="$V"', s)
    return s


if __name__ == '__main__':
    main()
