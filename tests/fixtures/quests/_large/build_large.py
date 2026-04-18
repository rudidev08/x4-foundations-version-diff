"""Generate the large truncation fixture.

Produces `old/md/gm_huge.xml` and `new/md/gm_huge.xml` such that their
unified diff crosses 100 KB and ends up truncated. Includes a multibyte
UTF-8 character near the truncation boundary so the line-slice truncator
has to preserve codepoints.

Run manually if the fixture needs regeneration:

    python3 tests/fixtures/quests/_large/build_large.py

Files are checked in — this script is here so the fixture stays
reproducible.
"""
from pathlib import Path

HERE = Path(__file__).resolve().parent


def build():
    lines_old = ['<?xml version="1.0" encoding="utf-8"?>\n',
                 '<mdscript name="GM_Huge" xmlns:xsi='
                 '"http://www.w3.org/2001/XMLSchema-instance" '
                 'xsi:noNamespaceSchemaLocation="md.xsd">\n',
                 '  <cues>\n']
    lines_new = list(lines_old)
    # 6000 lines of differing content. Multibyte characters (日本語, δ, emoji)
    # sprinkled every ~250 lines so head and tail both hit them.
    multibytes = ['日本語', 'δ', '漢字', 'é', 'ü']
    for i in range(6000):
        mb = multibytes[i % len(multibytes)] if i % 250 == 0 else ''
        lines_old.append(
            f'    <cue name="Cue_{i:05d}_{mb}" instantiate="false">\n'
            f'      <actions><debug_text text="\'old line {i} {mb}\'"/></actions>\n'
            f'    </cue>\n'
        )
        lines_new.append(
            f'    <cue name="Cue_{i:05d}_{mb}" instantiate="true">\n'
            f'      <actions><debug_text text="\'new line {i} {mb}\'"/></actions>\n'
            f'    </cue>\n'
        )
    lines_old.append('  </cues>\n</mdscript>\n')
    lines_new.append('  </cues>\n</mdscript>\n')
    (HERE / 'old' / 'md').mkdir(parents=True, exist_ok=True)
    (HERE / 'new' / 'md').mkdir(parents=True, exist_ok=True)
    (HERE / 'old' / 'md' / 'gm_huge.xml').write_text(
        ''.join(lines_old), encoding='utf-8',
    )
    (HERE / 'new' / 'md' / 'gm_huge.xml').write_text(
        ''.join(lines_new), encoding='utf-8',
    )


if __name__ == '__main__':
    build()
    print(f'wrote {HERE}/old/md/gm_huge.xml')
    print(f'wrote {HERE}/new/md/gm_huge.xml')
