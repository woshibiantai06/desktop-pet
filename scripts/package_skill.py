"""Package the portable Skill and a single readable workflow document."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'skills/windows-desktop-pet'


def normalized(path):
    return path.read_text(encoding='utf-8').replace('\r\r\n', '\n').replace('\r\n', '\n')


def package(output):
    output.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in SKILL.rglob('*') if p.is_file() and '__pycache__' not in p.parts)
    archive = output / 'windows-desktop-pet-skill.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as handle:
        for path in files:
            info = zipfile.ZipInfo('windows-desktop-pet/' + path.relative_to(SKILL).as_posix(), (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            handle.writestr(info, normalized(path).encode('utf-8'))
        info = zipfile.ZipInfo('windows-desktop-pet/LICENSE', (2026, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        handle.writestr(info, normalized(ROOT / 'LICENSE').encode('utf-8'))
    with zipfile.ZipFile(archive) as handle:
        if handle.testzip():
            raise ValueError('Corrupt archive')
    body = normalized(SKILL / 'SKILL.md').split('---', 2)[2].strip()
    document = '# 桌宠制作流程：交给 Agent 的完整说明\n\n先阅读全文，再根据用户提供的照片与要求规划；未生成的图片和视频只能标为计划，不能报告通过。\n\n' + body
    for path in sorted((SKILL / 'references').glob('*.md')):
        document += '\n\n---\n\n## 附录：references/' + path.name + '\n\n' + normalized(path)
    document = re.sub(r'\[([^\]]+)\]\((references/[^)]+)\)', lambda m: '**' + m[1] + '（见附录：' + m[2].split('#')[0] + '）**', document)
    (output / 'pet-workflow.md').write_text(document + '\n', encoding='utf-8', newline='\n')
    return archive


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    result = package(args.output.resolve())
    print(result.name)
