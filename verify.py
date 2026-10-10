#!/usr/bin/env python3
"""Record real commands and read-only network metadata. Never download chapter images."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
COMMAND = Path.home() / '.local/bin/komori'
ORIGINALS = [Path.home() / '.local/bin/opm', Path.home() / '.config/opm/library.json']

def hashes():
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in ORIGINALS if p.exists()}

report = {'original_hashes_before': hashes(), 'commands': []}
commands = [
    [sys.executable, '-m', 'unittest', 'discover', '-s', str(ROOT/'tests'), '-v'],
    [sys.executable, '-m', 'py_compile', str(ROOT/'src/komori.py'), str(ROOT/'src/komori_runtime.py')],
    [str(COMMAND), '--help'],
    [str(COMMAND), 'list'],
    [str(COMMAND), 'search', 'Yotsuba'],
    [str(COMMAND), 'chapters', 'Yotsuba'],
]
for command in commands:
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=240)
    report['commands'].append({'argv': command, 'exit_code': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})
    print('COMMAND:', ' '.join(command), '\nEXIT:', result.returncode)
    if command[1:2] == ['chapters']:
        lines = result.stdout.splitlines()
        print('\n'.join(lines[:5]))
        print('chapter rows:', sum(line.startswith('  Chapter ') for line in lines))
    else:
        print(result.stdout + result.stderr)

spec = importlib.util.spec_from_file_location('komori_verification', ROOT/'src/komori.py')
k = importlib.util.module_from_spec(spec)
spec.loader.exec_module(k)
try:
    candidates = k.search_online_manga('Yotsuba')
    if not candidates:
        raise RuntimeError('No nonempty remote listing returned')
    manga = candidates[0]
    mapping = k.get_online_chapter_mapping(manga)
    chapter = sorted(mapping, key=k.Decimal)[0]
    urls = k.get_chapter_page_urls(manga, mapping[chapter])
    report['live_metadata'] = {'title': k.safe_text(manga['title']), 'chapter_count': len(mapping), 'chapter': chapter, 'image_url_count': len(urls), 'chapter_images_downloaded': False, 'image_bytes_verified': False}
    print('LIVE METADATA:', json.dumps(report['live_metadata']))
except Exception as error:
    report['live_metadata'] = {'error': k.safe_text(str(error)), 'chapter_images_downloaded': False}
    print('LIVE METADATA BLOCKER:', report['live_metadata'])
report['original_hashes_after'] = hashes()
report['originals_unchanged'] = report['original_hashes_before'] == report['original_hashes_after']
report['command_symlink_target'] = str(COMMAND.resolve())
(ROOT/'verification-results.json').write_text(json.dumps(report, indent=2))
print('ORIGINALS UNCHANGED:', report['originals_unchanged'])
print('REPORT:', ROOT/'verification-results.json')
sys.exit(0 if all(c['exit_code'] == 0 for c in report['commands']) and report['originals_unchanged'] and 'error' not in report['live_metadata'] else 1)
