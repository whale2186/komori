"""Front-page contracts: terminal boundaries mocked, library/cover files real."""
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from test_komori import k


class FrontpageTests(unittest.TestCase):
    def screen(self, height=28, width=110):
        screen = MagicMock()
        screen.getmaxyx.return_value = (height, width)
        return screen

    def test_frontpage_places_selected_art_next_to_metadata(self):
        self.assertTrue(hasattr(k, 'draw_library_frontpage'), 'frontpage art renderer missing')
        with tempfile.TemporaryDirectory() as d:
            manga = {'id': 'a', 'title': 'First', 'dir': d, 'description': 'An actual synopsis.', 'status': 'Unknown'}
            screen = self.screen()
            covers = MagicMock()
            covers.lookup.return_value = '/local/cover.jpg'
            art = MagicMock()
            k.draw_library_frontpage(screen, [manga], 0, covers, art)
            args = art.draw.call_args.args
            self.assertEqual(args[1], '/local/cover.jpg')
            x, y, width, height = args[2]
            self.assertGreater(x, 0)
            self.assertLess(x + width, 110)
            self.assertLess(y + height, 27)
            rendered = ' '.join(str(c.args) for c in screen.addstr.call_args_list)
            for text in ('First', 'Synopsis', 'actual synopsis', '0 downloaded', 'Not started', 'Unknown'):
                self.assertIn(text, rendered)
    def test_navigation_switches_cover_and_clears_on_quit(self):
        screen = self.screen()
        screen.getch.side_effect = [ord('j'), ord('q')]
        mangas = [{'id': 'a', 'title': 'First', 'dir': '/nonexistent'}, {'id': 'b', 'title': 'Second', 'dir': '/nonexistent'}]
        covers, art = MagicMock(), MagicMock()
        covers.lookup.side_effect = lambda m: m['id'] + '.jpg'
        with patch.object(k, 'FrontpageArt', return_value=art, create=True), patch.object(k, 'FrontpageCovers', return_value=covers, create=True), patch.object(k, 'load_library', return_value={'mangas': mangas}), patch.object(k.curses, 'curs_set'), patch.object(k.curses, 'color_pair', return_value=0), patch.object(k.curses, 'init_pair'):
            k.main_library_menu(screen)
        self.assertEqual([c.args[1] for c in art.draw.call_args_list], ['a.jpg', 'b.jpg'])
        self.assertGreaterEqual(art.clear.call_count, 3)
        covers.close.assert_called_once()
    def test_missing_art_placeholder_without_unsupported_graphics(self):
        self.assertTrue(hasattr(k, 'FrontpageArt'), 'terminal art fallback missing')
        screen = self.screen()
        with patch.object(k.shutil, 'which', return_value='/bin/kitten'), patch.dict(k.os.environ, {'TERM': 'xterm-256color', 'KITTY_WINDOW_ID': ''}), patch.object(k.subprocess, 'run') as run:
            art = k.FrontpageArt()
            art.draw(screen, None, (1, 3, 20, 12))
            art.clear()
        self.assertFalse(run.called)
        self.assertIn('Cover unavailable', ' '.join(str(c.args) for c in screen.addstr.call_args_list))
    def test_cover_worker_returns_immediately_and_attempts_missing_cover_once(self):
        self.assertTrue(hasattr(k, 'FrontpageCovers'), 'nonblocking cover cache missing')
        import threading
        released, entered = threading.Event(), threading.Event()
        def offline(*args, **kwargs):
            entered.set()
            released.wait(2)
            raise OSError('offline')
        with tempfile.TemporaryDirectory() as d, patch.object(k, 'COVERS_CACHE_DIR', d), patch.object(k.urllib.request, 'urlopen', side_effect=offline) as request:
            covers = k.FrontpageCovers()
            manga = {'id': 'a', 'title': 'First', 'cover_url': 'https://example.test/cover.jpg'}
            self.assertIsNone(covers.lookup(manga))
            self.assertTrue(entered.wait(1))
            for _ in range(10):
                self.assertIsNone(covers.lookup(manga))
            released.set()
            covers.queue.join()
            self.assertIsNone(covers.lookup(manga))
            self.assertEqual(request.call_count, 1)
            covers.close()
    def test_narrow_resize_bounds_and_scroll_keeps_selection_visible(self):
        mangas = [{'id': str(i), 'title': f'Title {i}', 'dir': '/nonexistent'} for i in range(50)]
        for h, w in ((28, 110), (24, 80), (18, 44), (10, 20), (3, 5), (1, 1)):
            with self.subTest(size=(h, w)):
                screen = self.screen(h, w)
                k.draw_library_frontpage(screen, mangas, 49, MagicMock(), MagicMock())
                for call in screen.addstr.call_args_list:
                    y, x, text = call.args[:3]
                    self.assertTrue(0 <= y < h)
                    self.assertTrue(0 <= x < w)
                    self.assertLessEqual(len(text), max(0, w - x - 1))
                if h >= 10 and w >= 20:
                    self.assertIn('Title 49', ' '.join(str(c.args) for c in screen.addstr.call_args_list))

    def test_kitty_art_uses_explicit_size_and_clears_rendered_image(self):
        self.assertTrue(hasattr(k, 'FrontpageArt'))
        screen = self.screen()
        with patch.object(k.shutil, 'which', return_value='/bin/kitten'), patch.object(k.sys.stdout, 'isatty', return_value=True), patch.dict(k.os.environ, {'TERM': 'xterm-kitty', 'TMUX': ''}), patch.object(k.subprocess, 'run', return_value=__import__('subprocess').CompletedProcess([], 0)) as run:
            art = k.FrontpageArt()
            art.draw(screen, '/real/cover.jpg', (34, 3, 20, 12))
            args = run.call_args.args[0]
            self.assertIn('--use-window-size', args)
            self.assertIn('20x12@34x3', args)
            art.clear()
            self.assertIn('--clear', run.call_args.args[0])
    def test_details_i_never_spawns_desktop_viewer(self):
        screen = self.screen()
        screen.getch.side_effect = [ord('i'), ord('q')]
        with patch.object(k.curses, 'curs_set'), patch.object(k, 'cached_cover', return_value='/real/cover.jpg'), patch.object(k.shutil, 'which', return_value='/bin/viewer'), patch.object(k.subprocess, 'run'), patch.object(k.subprocess, 'Popen') as viewer:
            k.show_cover_art_screen(screen, {'id': 'a', 'title': 'First'})
        viewer.assert_not_called()
    def test_cover_completion_during_redraw_is_not_lost(self):
        screen = self.screen()
        screen.getch.side_effect = [-1, ord('q')]
        covers, art = MagicMock(), MagicMock()
        covers.revision = 0
        def completing(manga):
            covers.revision = 1
            return None
        covers.lookup.side_effect = completing
        with patch.object(k, 'FrontpageCovers', return_value=covers), patch.object(k, 'FrontpageArt', return_value=art), patch.object(k, 'load_library', return_value={'mangas': [{'id': 'a', 'title': 'First', 'dir': '/nonexistent'}]}), patch.object(k.curses, 'curs_set'), patch.object(k.curses, 'init_pair'):
            k.main_library_menu(screen)
        self.assertEqual(art.draw.call_count, 2)
    def test_unsupported_terminal_does_not_emit_legacy_kitty_clear(self):
        with patch.object(k.shutil, 'which', return_value='/bin/kitten'), patch.dict(k.os.environ, {'TERM': 'xterm-256color', 'KITTY_WINDOW_ID': ''}), patch.object(k, '_subprocess_run') as run:
            k.safe_kitten_run(['kitten', 'icat', '--clear'])
        run.assert_not_called()
    def test_ascii_cover_compensates_for_tall_terminal_cells(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest('Optional Pillow not installed')
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'cover.png'
            Image.new('RGB', (40, 60), 'white').save(path)
            screen = self.screen()
            art = k.FrontpageArt()
            art.graphics = False
            art.draw(screen, str(path), (1, 3, 20, 12))
            ascii_rows = [c.args[2] for c in screen.addstr.call_args_list if set(c.args[2]) == {'@'}]
            self.assertEqual(len(ascii_rows), 10)
            self.assertGreaterEqual(len(ascii_rows[0]), 12)
    def test_resize_write_race_is_tolerated(self):
        screen = self.screen(18, 44)
        screen.addstr.side_effect = k.curses.error('terminal resized during write')
        k.SafeScreen(screen).addstr(2, 1, 'Title on a resizing terminal')


if __name__ == '__main__':
    unittest.main()
