"""Local synthetic fixtures only; no chapter content or live calls."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('komori', Path(__file__).resolve().parents[1] / 'src/komori.py')
k = importlib.util.module_from_spec(spec)
spec.loader.exec_module(k)

class LibraryTests(unittest.TestCase):
    def test_empty_library_stays_empty(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            libfile = root / 'library.json'
            libfile.write_text('{"mangas": [], "active_manga_id": null}')
            with patch.multiple(k, CONFIG_DIR=d, CACHE_DIR=d, COVERS_CACHE_DIR=d, MANGA_DIR=str(root/'downloads'), LIBRARY_FILE=str(libfile)):
                self.assertEqual(k.load_library()['mangas'], [])

class AvailabilityTests(unittest.TestCase):
    def test_search_excludes_empty_listing(self):
        candidates = [{'id': 'a', 'title': 'Available', 'source': 'weebcentral'}, {'id': 'b', 'title': 'Empty', 'source': 'weebcentral'}]
        with patch.object(k, '_search_candidates', return_value=candidates, create=True), patch.object(k, 'get_online_chapter_mapping', side_effect=[{'0.5': 'https://example.test/chapters/x'}, {}]), patch.object(k.urllib.request, 'urlopen', side_effect=OSError('offline')):
            result = k.search_online_manga('fixture')
        self.assertEqual([r['id'] for r in result], ['a'])
        self.assertEqual(result[0]['availability'], 'remotely listed; pages not checked')
        self.assertEqual(result[0]['status'], 'Unknown')

    def test_ranges_select_only_actual_listings_including_fractions(self):
        self.assertTrue(hasattr(k, 'select_chapters'), 'listing-based selection is missing')
        listing = {'1': 'a', '1.5': 'b', '3': 'c', '0.5': 'd'}
        self.assertEqual(k.select_chapters('1-3', listing), ['1', '1.5', '3'])
        self.assertEqual(k.select_chapters('3..1', listing), ['3', '1.5', '1'])
        self.assertEqual(k.select_chapters('0.5', listing), ['0.5'])
        with self.assertRaises(ValueError):
            k.select_chapters('2', listing)

    def test_fraction_normalization_never_drops_zero_before_decimal(self):
        payload = '<a href="/chapters/abc"><span class="">Chapter 00.5</span></a>'
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = payload.encode()
        with patch.object(k.urllib.request, 'urlopen', return_value=response):
            self.assertEqual(list(k.get_weebcentral_chapters('fixture')), ['0.5'])

    def test_remote_synopsis_comes_from_series_detail_not_meta_advertising(self):
        self.assertTrue(hasattr(k, 'hydrate_metadata'), 'series synopsis extraction is missing')
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'<!-- <strong>Description</strong><p>Hidden old text</p> --><strong>Description</strong><p class="whitespace-pre-wrap">Actual &amp; detailed <b>synopsis</b>.</p>'
        with patch.object(k.urllib.request, 'urlopen', return_value=response):
            result = k.hydrate_metadata({'source': 'weebcentral', 'url': 'https://example.test/series/fixture', 'title': 'Fixture'})
        self.assertEqual(result['description'], 'Actual & detailed synopsis.')

class DownloadTests(unittest.TestCase):
    def test_summary_counts_failed_chapters(self):
        with patch.object(k, 'get_online_chapter_mapping', return_value={'1': 'x', '1.5': 'y', '3': 'z'}), patch.object(k, 'download_manga_chapter', side_effect=[True, False, True]), patch('sys.stdout', new_callable=__import__('io').StringIO) as out:
            result = k.download_range({'title': 'Fixture', 'dir': '/nonexistent'}, '1-3')
        self.assertEqual(result, {'requested': 3, 'downloaded': 2, 'failed': 1})
        self.assertIn('2 downloaded; 1 failed', out.getvalue())
        self.assertNotIn('All downloads finished', out.getvalue())

    def test_empty_image_manifest_never_success(self):
        with tempfile.TemporaryDirectory() as d, patch.object(k, 'get_chapter_page_urls', return_value=[], create=True), patch.object(k.urllib.request, 'urlopen', side_effect=OSError('https://weebcentral.com failure')):
            self.assertFalse(k.download_manga_chapter({'title': 'Fixture', 'dir': d}, '1', {'1': 'https://example.test/chapters/x'}))
            self.assertFalse((Path(d) / 'Chapter_1').exists())

    def test_empty_page_bytes_are_failure(self):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b''
        with tempfile.TemporaryDirectory() as d, patch.object(k.urllib.request, 'urlopen', return_value=response):
            with self.assertRaises(ValueError):
                k.download_pages(d, ['https://example.test/001.png'])
            self.assertFalse((Path(d) / '001.png').exists())

    def test_failed_page_hides_chapter_and_redacts_error(self):
        with tempfile.TemporaryDirectory() as d, patch.object(k, 'get_chapter_page_urls', return_value=['https://example.test/001.png']), patch.object(k, 'download_pages', side_effect=OSError('MangaDex https://api.mangadex.org secret')), patch('sys.stdout', new_callable=__import__('io').StringIO) as out:
            success = k.download_manga_chapter({'title': 'Fixture', 'dir': d}, '0.5', {'0.5': 'ref'})
            self.assertFalse(success)
            self.assertEqual(k.get_local_chapters(d), [])
            self.assertTrue((Path(d)/'Chapter_0.5/.komori_incomplete').exists())
            self.assertNotIn('mangadex', out.getvalue().lower())
            self.assertNotIn('https://', out.getvalue())

    def test_success_records_all_fixture_pages(self):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'\x89PNG\r\n\x1a\nfixture-image-header'
        with tempfile.TemporaryDirectory() as d, patch.object(k, 'get_chapter_page_urls', return_value=['https://example.test/001.png', 'https://example.test/002.png']), patch.object(k.urllib.request, 'urlopen', return_value=response), patch('sys.stdout', new_callable=__import__('io').StringIO):
            self.assertTrue(k.download_manga_chapter({'title': 'Fixture', 'dir': d}, '0.5', {'0.5': 'ref'}))
            manifest = json.loads((Path(d)/'Chapter_0.5/.komori_complete.json').read_text())
            self.assertEqual(manifest['count'], 2)
            self.assertEqual(len(k.get_local_chapters(d)[0][2]), 2)
            self.assertFalse((Path(d)/'Chapter_0.5/.komori_incomplete').exists())

class SafetyTests(unittest.TestCase):
    def test_console_redacts_provider_names_urls_and_escape_sequences(self):
        self.assertTrue(hasattr(k, 'safe_text'), 'output sanitization is missing')
        text = k.safe_text('WeebCentral MangaDex Imgur Cubari https://api.mangadex.org/foo \x1b[31mHello')
        for forbidden in ('weebcentral', 'mangadex', 'imgur', 'cubari', 'https://', '\x1b'):
            self.assertNotIn(forbidden, text.lower())
        self.assertIn('Hello', text)

    def test_import_snapshot_does_not_modify_original(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            old = root/'old.json'
            before = json.dumps({'mangas': [{'id': 'fixture', 'title': 'Fixture', 'dir': str(root/'legacy'), 'status': 'Ongoing'}]})
            old.write_text(before)
            with patch.multiple(k, CONFIG_DIR=str(root/'config'), CACHE_DIR=str(root/'cache'), COVERS_CACHE_DIR=str(root/'covers'), MANGA_DIR=str(root/'downloads'), LIBRARY_FILE=str(root/'config/library.json'), OLD_LIBRARY_FILE=str(old)):
                lib = k.load_library()
                self.assertTrue(lib['mangas'][0]['read_only_import'])
                self.assertEqual(lib['mangas'][0]['dir'], str(root/'legacy'))
                self.assertFalse(k.delete_chapter(str(root/'legacy'), '1'))
                self.assertNotEqual(k.get_state_file(str(root/'legacy')), str(root/'legacy/.last_read.json'))
                self.assertEqual(old.read_text(), before)

    def test_partial_chapter_hidden_from_reader(self):
        with tempfile.TemporaryDirectory() as d:
            chapter = Path(d)/'Chapter_1'
            chapter.mkdir()
            (chapter/'001.png').write_bytes(b'\x89PNG\r\n\x1a\nfixture')
            (chapter/'.komori_incomplete').touch()
            self.assertEqual(k.get_local_chapters(d), [])

    def test_missing_kitten_does_not_crash(self):
        with patch.object(k.shutil, 'which', return_value=None):
            result = k.subprocess.run(['kitten', 'icat', '--clear'], check=False)
            self.assertNotEqual(result.returncode, 0)

class UiTests(unittest.TestCase):
    def test_narrow_hero_has_synopsis_without_kitten(self):
        screen = unittest.mock.MagicMock()
        screen.getmaxyx.return_value = (18, 44)
        screen.getch.return_value = ord('q')
        with patch.object(k.curses, 'curs_set'), patch.object(k.curses, 'init_pair'), patch.object(k.curses, 'color_pair', return_value=0), patch.object(k.shutil, 'which', return_value=None):
            k.show_cover_art_screen(screen, {'id': 'fixture', 'title': 'Fixture', 'description': 'A real synopsis remains visible.', 'status': 'Unknown'})
        rendered = ' '.join(str(call.args) for call in screen.addstr.call_args_list)
        self.assertIn('Synopsis', rendered)
        self.assertIn('real synopsis', rendered)

class CliTests(unittest.TestCase):
    def test_help_brand_and_chapters_command(self):
        with patch.object(k.sys, 'argv', ['komori', '--help']), patch.object(k, 'load_library', return_value={'mangas': []}), patch('sys.stdout', new_callable=__import__('io').StringIO) as out:
            k.main()
        self.assertIn('Komori', out.getvalue())
        self.assertIn('komori chapters', out.getvalue())
        self.assertNotIn('opm', out.getvalue())

    def test_chapter_command_lists_only_remote_keys(self):
        manga = {'id': 'fixture', 'title': 'Fixture'}
        with patch.object(k.curses, 'wrapper'), patch.object(k.sys, 'argv', ['komori', 'chapters', 'Fixture']), patch.object(k, 'load_library', return_value={'mangas': [manga]}), patch.object(k, 'get_online_chapter_mapping', return_value={'0.5': 'ref', '3': 'ref3'}), patch('sys.stdout', new_callable=__import__('io').StringIO) as out:
            k.main()
        text = out.getvalue()
        self.assertIn('Chapter 0.5', text)
        self.assertIn('Chapter 3', text)
        self.assertIn('pages not checked', text)
        self.assertNotIn('Chapter 2', text)

if __name__ == '__main__':
    unittest.main()
