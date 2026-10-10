"""Exercise installed CLI and real existing covers, without changing library files.

Kitty mode verifies emitted protocol in a PTY, not visual terminal rendering.
Only aggregate protocol counts are saved, not cover image payloads.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pty
import select
import signal
import struct
import subprocess
import termios
import time

ROOT = Path(__file__).resolve().parent
CLI = Path.home() / '.local/bin/komori'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def exercise(term):
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 28, 110, 880, 448))
    env = dict(os.environ, TERM=term, KITTY_WINDOW_ID='', TMUX='')
    process = subprocess.Popen([str(CLI)], stdin=slave, stdout=slave, stderr=slave, env=env)
    os.close(slave)
    output = bytearray()
    stage = 0
    deadline = time.monotonic() + 12
    stage_started = time.monotonic()
    try:
        while time.monotonic() < deadline:
            if select.select([master], [], [], .05)[0]:
                try:
                    block = os.read(master, 65536)
                except OSError:
                    break
                output.extend(block)
            now = time.monotonic()
            # Allow each real render to complete, including its graphics payload.
            if stage == 0 and b'YOUR SHELF' in output and now - stage_started > .5:
                os.write(master, b'j')
                stage, stage_started = 1, now
            elif stage == 1 and now - stage_started > .5:
                fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack('HHHH', 18, 44, 352, 288))
                process.send_signal(signal.SIGWINCH)
                stage, stage_started = 2, now
            elif stage == 2 and now - stage_started > .5:
                os.write(master, b'q')
                stage = 3
            if process.poll() is not None:
                # Drain pending final clear/alternate-screen cleanup.
                while select.select([master], [], [], .05)[0]:
                    try:
                        block = os.read(master, 65536)
                    except OSError:
                        break
                    if not block:
                        break
                    output.extend(block)
                break
        if process.poll() is None:
            process.kill()
        code = process.wait(timeout=3)
        data = bytes(output)
        return {'term': term, 'exit_code': code, 'navigation_resize_quit_sent': stage == 3,
                'bytes_captured': len(data), 'graphics_packets': data.count(b'\x1b_G'),
                'image_transmissions': data.count(b'a=T'), 'clear_packets': data.count(b'a=d'),
                'synopsis_visible': b'Synopsis' in data, 'traceback': b'Traceback' in data,
                'source_leaked': any(s in data.lower() for s in (b'mangadex', b'weebcentral', b'https://'))}
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        os.close(master)


def main():
    protected = [Path.home()/'.local/bin/opm', Path.home()/'.config/opm/library.json',
                 Path.home()/'.config/komori/library.json']
    lib = json.loads(protected[-1].read_text())
    covers = [Path(m['cover_path']) for m in lib['mangas'] if m.get('cover_path')]
    protected.extend(covers)
    before = {str(path): digest(path) for path in protected}
    commands = []
    for args in (['--help'], ['list']):
        result = subprocess.run([str(CLI), *args], text=True, capture_output=True, timeout=10)
        commands.append({'argv': [str(CLI), *args], 'exit_code': result.returncode,
                         'stdout': result.stdout, 'stderr': result.stderr})
    modes = [exercise('xterm-256color'), exercise('xterm-kitty')]
    after = {str(path): digest(path) for path in protected}
    result = {'installed_target': str(CLI.resolve()), 'commands': commands, 'pty': modes,
              'real_cover_files': [str(p) for p in covers if p.is_file()],
              'protected_hashes_before': before, 'protected_hashes_after': after,
              'protected_files_unchanged': before == after,
              'visual_graphics_verified': False,
              'note': 'Real kitten decoded existing local covers and emitted Kitty protocol into a PTY. No graphical terminal screenshot was inspected.'}
    (ROOT/'frontpage-verification-results.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    assert before == after
    assert all(c['exit_code'] == 0 for c in commands)
    assert all(m['exit_code'] == 0 and m['navigation_resize_quit_sent'] and not m['traceback'] and not m['source_leaked'] for m in modes)
    assert modes[0]['graphics_packets'] == 0
    assert modes[1]['image_transmissions'] >= 2 and modes[1]['clear_packets'] >= 2


if __name__ == '__main__':
    main()
