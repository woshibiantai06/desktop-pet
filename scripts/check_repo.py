"""Read-only checks for portable Markdown links and accidental local material."""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
errors = []
for path in ROOT.rglob('*'):
    relative = path.relative_to(ROOT)
    if any(part in {'.git', '.venv', '__pycache__', 'dist', 'runtime', 'logs', 'qa', 'userdata', 'runtime_cache'} for part in relative.parts):
        continue
    if not path.is_file():
        continue
    if path.stat().st_size > 90 * 1024 * 1024:
        errors.append(f'{relative}: large file belongs in Release')
    if path.suffix not in {'.md', '.py', '.json', '.yaml', '.yml', '.txt'}:
        continue
    text = path.read_text(encoding='utf-8')
    if re.search(r'[CD]:[\\/]+Users[\\/]+\d', text, re.I):
        errors.append(f'{relative}: personal absolute path')
    if re.search(r'gh[pousr]_[A-Za-z0-9]{25,}|sk-proj-[A-Za-z0-9]{20,}', text):
        errors.append(f'{relative}: possible credential; inspect privately')
    if path.suffix == '.md':
        for target in re.findall(r'\]\(([^)]+)\)', text):
            if '://' in target or target.startswith(('#', 'mailto:')):
                continue
            target = target.split('#', 1)[0].split(' ', 1)[0]
            if target and not (path.parent / target).exists():
                errors.append(f'{relative}: missing local link {target}')
if errors:
    print('\n'.join(errors))
    sys.exit(1)
print('PASS: local references, file sizes and portable source checks')
