"""PTY smoke tests using local synthetic metadata/pages only."""
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unittest

APP = Path(__file__).resolve().parents[1] / 'src/komori.py'


def smoke(argv, rows=24, columns=80, ready=b'KOMORI'):
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', rows, columns, 0, 0))
    home = tempfile.TemporaryDirectory()
    env = dict(os.environ, TERM='xterm-256color', PATH='/nonexistent', HOME=home.name,
               XDG_CONFIG_HOME=home.name + '/.config', XDG_CACHE_HOME=home.name + '/.cache',
               KITTY_WINDOW_ID='', TMUX='')
    process = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, env=env)
    os.close(slave)
    captured = bytearray()
    sent = False
    deadline = time.monotonic() + 12
    try:
        while time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                try:
                    block = os.read(master, 65536)
                except OSError:
                    break
                if not block:
                    break
                captured.extend(block)
                if ready.lower() in bytes(captured).lower() and not sent:
                    os.write(master, b'q')
                    sent = True
            if process.poll() is not None:
                break
        if process.poll() is None:
            process.kill()
        code = process.wait(timeout=3)
        return code, bytes(captured), sent
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        os.close(master)
        home.cleanup()


class PtyTests(unittest.TestCase):
    def test_library_quits_in_pty(self):
        code, output, sent = smoke([sys.executable, str(APP)])
        self.assertTrue(sent, output.decode(errors='replace'))
        self.assertEqual(code, 0, output.decode(errors='replace'))
        self.assertNotIn(b'Traceback', output)

    def test_narrow_hero_quits_without_kitten(self):
        code = f"""import importlib.util
s=importlib.util.spec_from_file_location('komori', {str(APP)!r})
k=importlib.util.module_from_spec(s); s.loader.exec_module(k)
k.curses.wrapper(lambda screen: k.show_cover_art_screen(screen, {{'id':'test-fixture','title':'Fixture','description':'Synthetic synopsis stays visible on narrow terminals.','status':'Unknown'}}))
"""
        result, output, sent = smoke([sys.executable, '-c', code], rows=18, columns=44, ready=b'Synopsis')
        self.assertTrue(sent, output.decode(errors='replace'))
        self.assertEqual(result, 0, output.decode(errors='replace'))
        self.assertIn(b'Synthetic synopsis', output)

    def test_resume_uses_isolated_progress_and_preserves_legacy_state(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            chapter = root/'legacy/Chapter_0.5'
            chapter.mkdir(parents=True)
            for name in ('001.png', '002.png'):
                (chapter/name).write_bytes(b'\x89PNG\r\n\x1a\nsynthetic-fixture')
            state = root/'legacy/.last_read.json'
            state.write_text('{"chapter": "0.5", "page_idx": 1}')
            old = state.read_bytes()
            config = root/'config'
            config.mkdir()
            (config/'library.json').write_text(json.dumps({'active_manga_id':'fixture', 'mangas':[{'id':'fixture','title':'Fixture','dir':str(root/'legacy'),'read_only_import':True}]}))
            code = f"""import importlib.util, sys
s=importlib.util.spec_from_file_location('komori', {str(APP)!r})
k=importlib.util.module_from_spec(s); s.loader.exec_module(k)
k.CONFIG_DIR={str(config)!r}; k.LIBRARY_FILE={str(config/'library.json')!r}
k.CACHE_DIR={str(root/'cache')!r}; k.COVERS_CACHE_DIR={str(root/'covers')!r}; k.MANGA_DIR={str(root/'new-downloads')!r}
sys.argv=['komori','resume','fixture']; sys.exit(k.main())
"""
            result, output, sent = smoke([sys.executable, '-c', code], ready=b'Page 2/2')
            self.assertTrue(sent, output.decode(errors='replace'))
            self.assertEqual(result, 0, output.decode(errors='replace'))
            self.assertEqual(state.read_bytes(), old)
            progress_files = list((config/'progress').glob('*.json'))
            self.assertEqual(len(progress_files), 1)
            self.assertEqual(json.loads(progress_files[0].read_text())['page_idx'], 1)

    def test_source_frontpage_navigates_resizes_and_quits(self):
        self._frontpage_smoke([sys.executable, str(APP)])

    @unittest.skipUnless(os.environ.get('KOMORI_TEST_INSTALLED') == '1',
                         'Set KOMORI_TEST_INSTALLED=1 for the installed-command smoke')
    def test_installed_frontpage_navigates_resizes_and_quits(self):
        installed = Path.home() / '.local/bin/komori'
        self.assertTrue(installed.exists(), 'Install the Komori command first')
        self._frontpage_smoke([str(installed)])

    def _frontpage_smoke(self, argv):
        home = tempfile.TemporaryDirectory()
        root = Path(home.name)
        config = root / '.config/komori'
        config.mkdir(parents=True)
        (config / 'library.json').write_text(json.dumps({'mangas': [
            {'id': 'first', 'title': 'First', 'dir': str(root / 'first')},
            {'id': 'second', 'title': 'Second', 'dir': str(root / 'second')},
        ]}))
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 28, 110, 880, 448))
        env = dict(os.environ, TERM='xterm-256color', KITTY_WINDOW_ID='', TMUX='',
                   HOME=home.name, XDG_CONFIG_HOME=str(root / '.config'),
                   XDG_CACHE_HOME=str(root / '.cache'))
        process = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, env=env)
        os.close(slave)
        captured = bytearray()
        stage = 0
        deadline = time.monotonic() + 10
        try:
            while time.monotonic() < deadline:
                if select.select([master], [], [], .1)[0]:
                    try:
                        block = os.read(master, 65536)
                    except OSError:
                        break
                    captured.extend(block)
                    if stage == 0 and b'YOUR SHELF' in captured:
                        os.write(master, b'j')
                        stage = 1
                    elif stage == 1:
                        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack('HHHH', 18, 44, 352, 288))
                        process.send_signal(__import__('signal').SIGWINCH)
                        stage = 2
                    elif stage == 2:
                        os.write(master, b'q')
                        stage = 3
                if process.poll() is not None:
                    break
            if process.poll() is None:
                process.kill()
            self.assertEqual(process.wait(timeout=3), 0, captured.decode(errors='replace'))
            self.assertEqual(stage, 3)
            self.assertNotIn(b'Traceback', captured)
            self.assertNotIn(b'\x1b_G', captured)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            os.close(master)
            home.cleanup()


if __name__ == '__main__':
    unittest.main()
