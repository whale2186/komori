"""Komori adaptations; executed in the baseline namespace to preserve its TUI."""
import hashlib
import builtins
import html as html_module
import textwrap
from decimal import Decimal, InvalidOperation

CONFIG_DIR = os.path.join(os.environ.get('XDG_CONFIG_HOME', os.path.expanduser('~/.config')), 'komori')
CACHE_DIR = os.path.join(os.environ.get('XDG_CACHE_HOME', os.path.expanduser('~/.cache')), 'komori')
COVERS_CACHE_DIR = os.path.join(CACHE_DIR, 'covers')
LIBRARY_FILE = os.path.join(CONFIG_DIR, 'library.json')
OLD_LIBRARY_FILE = os.path.expanduser('~/.config/opm/library.json')
MANGA_DIR = os.path.expanduser('~/Downloads/Komori')

def load_library():
    init_directories()
    if os.path.exists(LIBRARY_FILE):
        with open(LIBRARY_FILE) as f:
            lib = json.load(f)
        if not isinstance(lib, dict) or not isinstance(lib.get('mangas'), list):
            raise ValueError('Invalid Komori library; file preserved')
        return lib
    lib = {'active_manga_id': None, 'mangas': []}
    if os.path.exists(OLD_LIBRARY_FILE):
        try:
            with open(OLD_LIBRARY_FILE) as f:
                old = json.load(f)
            if isinstance(old, dict) and isinstance(old.get('mangas'), list):
                lib = old
                for manga in lib['mangas']:
                    manga['read_only_import'] = True
                    # The previous scraper invented this status.
                    if manga.get('source') != 'mangadex':
                        manga['status'] = 'Unknown'
        except (OSError, ValueError):
            print('Previous library could not be imported; original left intact.')
    save_library(lib)
    return lib

_search_candidates = search_online_manga


def hydrate_metadata(manga):
    manga = dict(manga)
    manga['title'] = html_module.unescape(manga.get('title', 'Untitled'))
    if manga.get('description') or manga.get('source') != 'weebcentral' or not manga.get('url'):
        return manga
    try:
        with urllib.request.urlopen(urllib.request.Request(manga['url'], headers=HEADERS), timeout=12) as response:
            content = response.read().decode('utf-8', errors='replace')
        content = re.sub(r'<!--.*?-->', '', content, flags=re.S)
        match = re.search(r'<strong>\s*Description\s*</strong>\s*<p\b[^>]*>(.*?)</p>', content, re.S | re.I)
        if match:
            text = html_module.unescape(match.group(1))
            text = re.sub(r'<br\s*/?>', '\n', text, flags=re.I)
            manga['description'] = re.sub(r'<[^>]+>', '', text).strip()
    except Exception:
        pass
    return manga


def search_online_manga(query):
    results = []
    for candidate in _search_candidates(query):
        mapping = get_online_chapter_mapping(candidate)
        if not mapping:
            continue
        candidate = dict(candidate)
        if candidate.get('source') != 'mangadex':
            candidate['status'] = 'Unknown'
        candidate.setdefault('status', 'Unknown')
        candidate['chapter_count'] = len(mapping)
        candidate['availability'] = 'remotely listed; pages not checked'
        results.append(hydrate_metadata(candidate))
    return results


def normalize_chapter(value):
    value = str(value).strip()
    if not re.fullmatch(r'\d+(?:\.\d+)?', value):
        raise ValueError('Invalid chapter number')
    number = Decimal(value)
    return format(number, 'f').rstrip('0').rstrip('.') if '.' in value else str(int(number))


def select_chapters(spec, listing):
    """Select from a real listing, never synthesize gaps in a range."""
    keys = sorted(listing, key=Decimal)
    selected = []
    for part in spec.split(','):
        part = part.strip()
        match = re.fullmatch(r'(\d+(?:\.\d+)?)\s*(?:-|to|\.\.)\s*(\d+(?:\.\d+)?)', part, re.I)
        if match:
            first, last = map(Decimal, match.groups())
            matches = [key for key in keys if min(first, last) <= Decimal(key) <= max(first, last)]
            if first > last:
                matches.reverse()
            if not matches:
                raise ValueError('No remotely listed chapters in that range')
        else:
            key = normalize_chapter(part)
            if key not in listing:
                raise ValueError(f'Chapter {key} is not remotely listed')
            matches = [key]
        selected.extend(key for key in matches if key not in selected)
    return selected


_baseline_weebcentral_chapters = get_weebcentral_chapters
_baseline_mangadex_chapters = get_mangadex_chapters

def get_weebcentral_chapters(series_id):
    raw = _baseline_weebcentral_chapters(series_id)
    return {normalize_chapter('0' + k if k.startswith('.') else k): v for k, v in raw.items()}


def get_mangadex_chapters(manga_id):
    raw = _baseline_mangadex_chapters(manga_id)
    return {normalize_chapter('0' + k if k.startswith('.') else k): v for k, v in raw.items() if re.fullmatch(r'\d*(?:\.\d+)?', k)}


def get_online_chapter_mapping(manga):
    # Legacy special-case albums/RSS are not availability listings.
    series_id = manga.get('series_id') or manga.get('id')
    if manga.get('source', 'weebcentral') == 'mangadex':
        return get_mangadex_chapters(series_id)
    return get_weebcentral_chapters(series_id)


def get_chapter_page_urls(manga, ref):
    if manga.get('source') == 'mangadex':
        url = f'https://api.mangadex.org/at-home/server/{ref}'
        with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=20) as response:
            data = json.loads(response.read().decode())
        chapter = data.get('chapter', {})
        return [f"{data['baseUrl']}/data/{chapter['hash']}/{name}" for name in chapter.get('data', [])]
    url = f'{ref}/images?reading_style=long_strip'
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=20) as response:
        content = response.read().decode('utf-8', errors='replace')
    urls = re.findall(r'<img\b[^>]*\bsrc=["\'](https://[^"\']+)["\']', content, re.I)
    return list(dict.fromkeys(html_module.unescape(u) for u in urls if re.search(r'\.(?:png|jpe?g|webp)(?:\?|$)', u, re.I) and not re.search(r'badge|avatar|icon', u, re.I)))


def valid_image(data):
    return bool(data.startswith(b'\x89PNG\r\n\x1a\n') or data.startswith(b'\xff\xd8\xff') or (data.startswith(b'RIFF') and data[8:12] == b'WEBP'))


def download_pages(out_dir, page_urls):
    if not page_urls:
        raise ValueError('No image pages listed')
    os.makedirs(out_dir, exist_ok=True)
    def fetch(item):
        index, url = item
        ext = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
        if ext not in ('.jpg', '.jpeg', '.png', '.webp'):
            ext = '.jpg'
        path = os.path.join(out_dir, f'{index + 1:03d}{ext}')
        if os.path.exists(path):
            with open(path, 'rb') as f:
                if valid_image(f.read(16)):
                    return path
        temp = path + '.tmp'
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=30) as response:
                data = response.read()
            if not valid_image(data):
                raise ValueError('Image page is empty or invalid')
            with open(temp, 'wb') as f:
                f.write(data)
            os.replace(temp, path)
            return path
        finally:
            if os.path.exists(temp):
                os.remove(temp)
    with ThreadPoolExecutor(max_workers=6) as executor:
        return list(executor.map(fetch, enumerate(page_urls)))


def download_manga_chapter(manga, ch_str, online_mapping=None):
    ch = normalize_chapter(ch_str)
    mapping = get_online_chapter_mapping(manga) if online_mapping is None else online_mapping
    if ch not in mapping:
        print(f'Chapter {ch} is not remotely listed.')
        return False
    if manga.get('read_only_import'):
        print('Imported library files are read-only. Search and add this title to create a Komori download copy.')
        return False
    out_dir = os.path.join(manga['dir'], f'Chapter_{ch}')
    try:
        urls = get_chapter_page_urls(manga, mapping[ch])
        if not urls:
            print(f'Chapter {ch} unavailable: no image pages listed.')
            return False
        print(f'Downloading Chapter {ch} ({len(urls)} listed pages)...')
        os.makedirs(out_dir, exist_ok=True)
        marker = os.path.join(out_dir, '.komori_incomplete')
        Path(marker).touch()
        paths = download_pages(out_dir, urls)
        with open(os.path.join(out_dir, '.komori_complete.json'), 'w') as f:
            json.dump({'pages': [os.path.basename(p) for p in paths], 'count': len(paths)}, f)
        os.remove(marker)
        print(f'Chapter {ch}: {len(paths)} image pages downloaded.')
        return True
    except Exception:
        print(f'Chapter {ch} failed: unable to retrieve all image pages; retry later.')
        return False


def download_range(manga, spec_str):
    mapping = get_online_chapter_mapping(manga)
    try:
        chapters = select_chapters(spec_str, mapping)
    except ValueError as error:
        print(str(error))
        print('Download summary: 0 downloaded; selection unavailable.')
        return {'requested': 0, 'downloaded': 0, 'failed': 0, 'selection_error': True}
    print(f"Komori: {manga['title']} — {len(chapters)} remotely listed chapter(s)")
    count = sum(bool(download_manga_chapter(manga, chapter, mapping)) for chapter in chapters)
    result = {'requested': len(chapters), 'downloaded': count, 'failed': len(chapters) - count}
    print(f"Download summary: {count} downloaded; {result['failed']} failed; {len(chapters)} requested.")
    return result


def safe_text(value):
    text = html_module.unescape(str(value))
    text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text)
    text = re.sub(r'https?://\S+', '[remote link]', text, flags=re.I)
    text = re.sub(r'weeb\s*central|manga\s*dex|imgur|cubari|compsci88|planeptune|opm[-_ ]rss|davidcricardo', 'remote', text, flags=re.I)
    text = re.sub(r'\bopm\b', 'komori', text, flags=re.I)
    text = text.replace('MANGA TERMINAL READER & LIBRARY', 'KOMORI — MANGA LIBRARY').replace('MANGA LIBRARY', 'KOMORI LIBRARY')
    return ''.join(c for c in text if c in '\n\t' or ord(c) >= 32 and ord(c) != 127)


def print(*values, **kwargs):
    builtins.print(*(safe_text(value) for value in values), **kwargs)


_subprocess_run = subprocess.run

def safe_kitten_run(args, **kwargs):
    if args and args[0] == 'kitten':
        if not (shutil.which('kitten') and sys.stdout.isatty()
                and (os.environ.get('KITTY_WINDOW_ID') or os.environ.get('TERM') == 'xterm-kitty')
                and not os.environ.get('TMUX')):
            return subprocess.CompletedProcess(args, 127)
        kwargs['stderr'] = subprocess.DEVNULL
        try:
            return _subprocess_run(args, **kwargs)
        except OSError:
            return subprocess.CompletedProcess(args, 127)
    return _subprocess_run(args, **kwargs)

subprocess.run = safe_kitten_run


def get_state_file(manga_dir):
    digest = hashlib.sha256(os.path.realpath(manga_dir).encode()).hexdigest()
    return os.path.join(CONFIG_DIR, 'progress', digest + '.json')


def load_last_read(manga_dir):
    for path in (get_state_file(manga_dir), os.path.join(manga_dir, '.last_read.json')):
        try:
            with open(path) as f:
                return json.load(f)
        except (OSError, ValueError):
            continue
    return {}


def save_last_read(manga_dir, ch_num, page_idx):
    path = get_state_file(manga_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + '.tmp', 'w') as f:
        json.dump({'chapter': str(ch_num), 'page_idx': max(0, int(page_idx))}, f)
    os.replace(path + '.tmp', path)


_baseline_local_chapters = get_local_chapters

def get_local_chapters(manga_dir):
    return [chapter for chapter in _baseline_local_chapters(manga_dir)
            if not os.path.exists(os.path.join(manga_dir, chapter[1], '.komori_incomplete'))]


_baseline_delete_chapter = delete_chapter

def delete_chapter(manga_dir, ch_num):
    if not Path(manga_dir).resolve().is_relative_to(Path(MANGA_DIR).resolve()):
        print('Imported library files are read-only; no files deleted.')
        return False
    normalize_chapter(ch_num)
    return _baseline_delete_chapter(manga_dir, ch_num)


_baseline_add_manga = add_manga_to_library

def add_manga_to_library(info):
    # Re-adding an imported title switches only its Komori record to a new directory.
    result = _baseline_add_manga(info)
    lib = load_library()
    for manga in lib['mangas']:
        if manga['id'] == result['id']:
            manga.pop('read_only_import', None)
    save_library(lib)
    return result


class SafeScreen:
    """One output boundary for all inherited curses screens."""
    def __init__(self, screen):
        self.screen = screen
    def __getattr__(self, name):
        return getattr(self.screen, name)
    def addstr(self, y, x, text, *attrs):
        height, width = self.screen.getmaxyx()
        if not 0 <= y < height or width < 2:
            return
        x = min(max(0, x), width - 2)
        try:
            self.screen.addstr(y, x, safe_text(text)[:max(0, width - x - 1)], *attrs)
        except curses.error:
            # A resize can land between measuring and writing; the next frame repairs it.
            pass


_curses_wrapper = curses.wrapper

def tui_wrapper(function, *args, **kwargs):
    return _curses_wrapper(lambda screen: function(SafeScreen(screen), *args, **kwargs))

curses.wrapper = tui_wrapper


def cached_cover(manga):
    path = manga.get('cover_path')
    if path and os.path.isfile(path):
        return path
    if not manga.get('cover_url'):
        return None
    init_directories()
    filename = hashlib.sha256(str(manga.get('id', manga['title'])).encode()).hexdigest() + '.jpg'
    path = os.path.join(COVERS_CACHE_DIR, filename)
    if not os.path.exists(path):
        try:
            download_file(manga['cover_url'], path)
        except Exception:
            return None
    return path


def show_cover_art_screen(stdscr, manga_info, is_preview=False):
    curses.curs_set(0)
    stdscr.keypad(True)
    covers, art = FrontpageCovers(), FrontpageArt()
    screen = SafeScreen(stdscr)
    screen.timeout(150)
    previous = None
    try:
        while True:
            height, width = screen.getmaxyx()
            path = covers.lookup(manga_info)
            state = (height, width, path, covers.revision)
            if state != previous:
                art.clear()
                screen.erase()
                screen.addstr(0, 1, f"Komori / {manga_info.get('title', 'Untitled')}", curses.A_BOLD)
                has_room = width >= 60 and height >= 12
                x = 24 if has_room else 1
                lines = [f"Status: {manga_info.get('status') or 'Unknown'}"]
                if manga_info.get('chapter_count'):
                    lines.append(f"{manga_info['chapter_count']} remotely listed chapters; pages not checked")
                lines.append('Synopsis:')
                lines.extend(textwrap.wrap(safe_text(manga_info.get('description') or 'No synopsis available.'), width=max(1, width - x - 2)))
                for y, line in enumerate(lines[:max(0, height - 4)], 2):
                    screen.addstr(y, x, line)
                screen.addstr(height - 1, 0, '[A] Add [D] Download [Q] Back' if is_preview else '[D] Download [Q] Back', curses.A_DIM)
                screen.refresh()
                if has_room:
                    art.draw(screen, path, (1, 2, 20, min(12, height - 4)))
                previous = state
            key = screen.getch()
            if key in (ord('q'), ord('Q'), 27, 10, 13, ord(' ')):
                return None
            if is_preview and key in (ord('a'), ord('A')):
                return 'add'
            if key in (ord('d'), ord('D')):
                return 'add_and_download' if is_preview else 'download'
            if key == curses.KEY_RESIZE:
                previous = None
    finally:
        art.clear()
        covers.close()
        screen.timeout(-1)


import threading
import queue


class FrontpageCovers:
    """One daemon worker per library visit; failures are memoized until next launch."""
    def __init__(self):
        self.queue = queue.Queue()
        self.attempted = set()
        self.revision = 0
        self.closed = threading.Event()
        self.worker = threading.Thread(target=self._work, name='komori-covers', daemon=True)
        self.worker.start()

    def lookup(self, manga):
        identity = str(manga.get('id') or manga.get('title', 'Untitled'))
        cache = os.path.join(COVERS_CACHE_DIR, hashlib.sha256(identity.encode()).hexdigest() + '.jpg')
        paths = [manga.get('cover_path'), os.path.join(manga.get('dir', ''), 'cover.jpg'),
                 os.path.join(COVERS_CACHE_DIR, os.path.basename(identity) + '.jpg'), cache]
        for path in paths:
            if path and os.path.isfile(path):
                try:
                    with open(path, 'rb') as file:
                        if valid_image(file.read(16)):
                            return path
                except OSError:
                    pass
        url = manga.get('cover_url')
        if url and identity not in self.attempted and not self.closed.is_set():
            self.attempted.add(identity)
            self.queue.put((url, cache))
        return None

    def _work(self):
        while True:
            item = self.queue.get()
            if item is None:
                self.queue.task_done()
                return
            url, path = item
            temp = path + '.tmp'
            try:
                if self.closed.is_set():
                    continue
                if urllib.parse.urlparse(url).scheme not in ('https', 'http'):
                    continue
                with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=8) as response:
                    data = response.read(8 * 1024 * 1024 + 1)
                if len(data) > 8 * 1024 * 1024 or not valid_image(data) or self.closed.is_set():
                    continue
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(temp, 'wb') as file:
                    file.write(data)
                os.replace(temp, path)
            except Exception:
                pass  # Covers are optional; never leak URLs/exceptions into the TUI.
            finally:
                if os.path.exists(temp):
                    try:
                        os.remove(temp)
                    except OSError:
                        pass
                self.revision += 1
                self.queue.task_done()

    def close(self):
        self.closed.set()
        self.queue.put(None)


class FrontpageArt:
    """Own inline images; never invoke a desktop viewer or probe an unknown TTY."""
    def __init__(self):
        self.graphics = bool(shutil.which('kitten') and sys.stdout.isatty()
                             and (os.environ.get('KITTY_WINDOW_ID') or os.environ.get('TERM') == 'xterm-kitty')
                             and not os.environ.get('TMUX'))
        self.visible = False

    def clear(self):
        if self.visible:
            try:
                subprocess.run(['kitten', 'icat', '--clear', '--silent'],
                               check=False, timeout=2, stdin=subprocess.DEVNULL)
            except (OSError, subprocess.TimeoutExpired):
                pass
            self.visible = False

    def draw(self, screen, path, box):
        x, y, width, height = box
        if path and self.graphics:
            try:
                # Explicit capability selection avoids kitten waiting for terminal replies.
                h, w = screen.getmaxyx()
                # Kernel pixel size when available; conservative cell metrics otherwise.
                pixel_w, pixel_h = w * 8, h * 16
                try:
                    import fcntl
                    import struct
                    import termios
                    _, _, actual_w, actual_h = struct.unpack('HHHH', fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, b'\0' * 8))
                    if actual_w and actual_h:
                        pixel_w, pixel_h = actual_w, actual_h
                except (OSError, ValueError, AttributeError):
                    pass
                result = subprocess.run(['kitten', 'icat', '--silent', '--stdin=no',
                                         '--transfer-mode=stream', '--align=left',
                                         '--use-window-size', f'{w},{h},{pixel_w},{pixel_h}',
                                         '--place', f'{width}x{height}@{x}x{y}', path],
                                        check=False, timeout=2, stdin=subprocess.DEVNULL)
                self.visible = True  # Also clear partial output on an unsuccessful render.
                if result.returncode == 0:
                    return
                self.clear()
            except (OSError, subprocess.TimeoutExpired):
                self.visible = True
                self.clear()
        # Pillow is optional. Without it retain an honest, framed text cover card.
        rows = None
        if path:
            try:
                from PIL import Image
                with Image.open(path) as image:
                    image = image.convert('L')
                    image.thumbnail((width - 2, (height - 2) * 2))
                    image = image.resize((image.width, max(1, image.height // 2)))
                    pixels = image.load()
                    ramp = ' .:-=+*#%@'
                    rows = [''.join(ramp[pixels[column, row] * (len(ramp) - 1) // 255]
                                    for column in range(image.width))
                            for row in range(image.height)]
            except (ImportError, OSError, ValueError):
                pass
        screen.addstr(y, x, '+' + '-' * (width - 2) + '+', curses.A_DIM)
        for row in range(1, height - 1):
            screen.addstr(y + row, x, '|' + ' ' * (width - 2) + '|', curses.A_DIM)
        screen.addstr(y + height - 1, x, '+' + '-' * (width - 2) + '+', curses.A_DIM)
        if rows:
            for row, text in enumerate(rows[:height - 2], y + 1):
                screen.addstr(row, x + 1, text[:width - 2])
        else:
            label = 'Cover unavailable' if not path else 'Cover cached'
            for row, text in enumerate(textwrap.wrap(label, width=max(1, width - 2)), y + 2):
                screen.addstr(row, x + 1, text)
        screen.refresh()


def draw_library_frontpage(stdscr, mangas, selected, covers, art):
    """Responsive selected-cover hero with an independently scrolling shelf."""
    screen = SafeScreen(stdscr)
    h, w = screen.getmaxyx()
    screen.erase()
    screen.addstr(0, 1, f'KOMORI / YOUR SHELF   {len(mangas)} titles', curses.A_BOLD)
    if not mangas:
        screen.addstr(3, 1, 'Your shelf is empty. Press S to search and add manga.')
        screen.refresh()
        return
    manga = mangas[selected]
    wide = w >= 100 and h >= 18
    compact = w < 45 or h < 15
    list_x = 1
    list_y = 3 if wide else (9 if compact else min(h - 6, 17))
    list_width = 30 if wide else w - 2
    art_x = 34 if wide else 1
    art_y = 3
    art_w = 20 if wide else min(18, max(6, w // 3))
    art_h = min(12, max(3, h - 10))
    info_x = art_x + art_w + 3
    info_y = 3
    if compact:
        info_x, info_y = 1, 2
    info_width = max(1, w - info_x - 2)
    chapters = get_local_chapters(manga.get('dir', ''))
    progress = load_last_read(manga.get('dir', ''))
    progress_text = (f"Ch {progress['chapter']} / page {int(progress.get('page_idx', 0)) + 1}"
                     if progress.get('chapter') is not None else 'Not started')
    lines = [manga.get('title', 'Untitled'),
             f"{len(chapters)} downloaded chapters | {manga.get('status') or 'Unknown'}",
             f'Reading: {progress_text}', 'Synopsis:']
    lines.extend(textwrap.wrap(safe_text(manga.get('description') or 'No synopsis available.'), width=info_width))
    for row, line in enumerate(lines[:5 if compact else art_h], info_y):
        screen.addstr(row, info_x, line, curses.A_BOLD if row == info_y else curses.A_NORMAL)
    screen.addstr(list_y - 1, list_x, 'SHELF / j k or arrows', curses.A_DIM)
    rows = max(1, h - list_y - 3)
    offset = max(0, selected - rows + 1)
    for row, index in enumerate(range(offset, min(len(mangas), offset + rows)), list_y):
        prefix = '> ' if index == selected else '  '
        screen.addstr(row, list_x, (prefix + mangas[index].get('title', 'Untitled'))[:list_width],
                      curses.A_REVERSE if index == selected else curses.A_NORMAL)
    screen.addstr(h - 2, 0, '[Enter] Open [R] Resume [D] Download [V] Details', curses.A_DIM)
    screen.addstr(h - 1, 0, '[S/+] Search [X] Remove [Q] Quit', curses.A_DIM)
    if compact:
        screen.addstr(7, 1, 'Cover: widen terminal for artwork', curses.A_DIM)
    screen.refresh()
    if not compact:
        art.draw(screen, covers.lookup(manga), (art_x, art_y, art_w, art_h))


def main_library_menu(stdscr):
    curses.curs_set(0)
    stdscr.keypad(True)
    for number, foreground in ((1, curses.COLOR_WHITE), (2, curses.COLOR_CYAN),
                               (3, curses.COLOR_YELLOW), (4, curses.COLOR_GREEN)):
        try:
            curses.init_pair(number, foreground, curses.COLOR_BLACK)
        except curses.error:
            pass
    covers, art = FrontpageCovers(), FrontpageArt()
    selected = 0
    lib = load_library()
    dirty = True
    last_revision = covers.revision
    last_size = None
    stdscr.timeout(150)
    try:
        while True:
            mangas = lib.get('mangas', [])
            selected = min(selected, max(0, len(mangas) - 1))
            size = stdscr.getmaxyx()
            if dirty or size != last_size or covers.revision != last_revision:
                revision = covers.revision
                art.clear()
                draw_library_frontpage(stdscr, mangas, selected, covers, art)
                last_size, last_revision = size, revision
                dirty = False
            key = stdscr.getch()
            if key == -1:
                continue
            if key in (ord('q'), ord('Q'), 27):
                break
            if key == curses.KEY_RESIZE:
                dirty = True
                continue
            if mangas and key in (curses.KEY_UP, ord('k'), curses.KEY_LEFT, ord('h'),
                                  curses.KEY_DOWN, ord('j'), curses.KEY_RIGHT, ord('l'),
                                  curses.KEY_NPAGE, curses.KEY_PPAGE, curses.KEY_HOME, curses.KEY_END):
                step = max(1, size[0] - 7) if key in (curses.KEY_NPAGE, curses.KEY_PPAGE) else 1
                if key == curses.KEY_HOME:
                    selected = 0
                elif key == curses.KEY_END:
                    selected = len(mangas) - 1
                else:
                    direction = -1 if key in (curses.KEY_UP, ord('k'), curses.KEY_LEFT, ord('h'), curses.KEY_PPAGE) else 1
                    selected = (selected + direction * step) % len(mangas)
                dirty = True
                continue
            # Clear terminal graphics before handing the screen to any other flow.
            art.clear()
            stdscr.timeout(-1)
            manga = mangas[selected] if mangas else None
            try:
                if key in (ord('s'), ord('S'), ord('+')) or (not mangas and key in (10, 13, curses.KEY_ENTER)):
                    new_manga = search_and_add_tui(stdscr)
                    if new_manga:
                        manga_hub_menu(stdscr, new_manga)
                elif manga and key in (10, 13, curses.KEY_ENTER):
                    set_active_manga(manga['id'])
                    manga_hub_menu(stdscr, manga)
                elif manga and key in (ord('v'), ord('V')):
                    if show_cover_art_screen(stdscr, manga) == 'download':
                        prompt_download(stdscr, manga)
                elif manga and key in (ord('d'), ord('D')):
                    prompt_download(stdscr, manga)
                elif manga and key in (ord('r'), ord('R')):
                    chapters = get_local_chapters(manga.get('dir', ''))
                    progress = load_last_read(manga.get('dir', ''))
                    if chapters:
                        index = next((i for i, chapter in enumerate(chapters) if chapter[0] == str(progress.get('chapter'))), 0)
                        read_chapters_flow(stdscr, index, chapters, manga['title'], manga['dir'],
                                           initial_page=progress.get('page_idx', 0))
                elif manga and key in (ord('x'), ord('X'), curses.KEY_DC):
                    screen = SafeScreen(stdscr)
                    screen.addstr(size[0] - 1, 0, f"Remove {manga['title']} record only? [y/N]", curses.A_REVERSE)
                    screen.refresh()
                    if stdscr.getch() in (ord('y'), ord('Y')):
                        remove_manga_from_library(manga['id'], delete_files=False)
                lib = load_library()
            finally:
                stdscr.timeout(150)
                dirty = True
    finally:
        art.clear()
        covers.close()
        stdscr.timeout(-1)


_baseline_main = main

def cli_search(query):
    print(f'Komori / Search: {query}')
    results = search_online_manga(query)
    if not results:
        print('No matching title with a nonempty remote chapter listing. The network may also be unavailable.')
        return 1
    for index, manga in enumerate(results, 1):
        print(f"[{index}] {manga['title']} — {manga['chapter_count']} remotely listed chapters; pages not checked")
        print(f"    Status: {manga['status']}")
        if manga.get('description'):
            print('    ' + manga['description'].replace('\n', ' ')[:160])
    if sys.stdin.isatty():
        choice = input('Add a numbered result to Komori, or Enter to cancel: ').strip()
        if choice.isdigit() and 1 <= int(choice) <= len(results):
            added = add_manga_to_library(results[int(choice) - 1])
            print(f"Added {added['title']} to Komori.")
    return 0


def cli_cover(manga_arg=None):
    manga = resolve_manga(load_library(), manga_arg)
    if not manga:
        print('Manga not found in Komori library.')
        return 1
    print(f"Komori / {manga['title']}")
    print(f"Status: {manga.get('status') or 'Unknown'}")
    print('Synopsis: ' + (manga.get('description') or 'No synopsis available.'))
    path = cached_cover(manga)
    if path and shutil.which('kitten'):
        subprocess.run(['kitten', 'icat', path], check=False)
    else:
        print('Cover image cannot be displayed here; use a graphics-capable terminal or external viewer.')
    return 0


def cli_chapters(manga, mapping=None):
    mapping = get_online_chapter_mapping(manga) if mapping is None else mapping
    print(f"Komori / {manga['title']} — {len(mapping)} remotely listed chapter(s); pages not checked")
    if not mapping:
        print('No downloadable chapter listing available, or network lookup failed.')
        return 1
    for chapter in sorted(mapping, key=Decimal):
        print(f'  Chapter {chapter} — remotely listed; pages not checked')
    return 0


def prompt_download(stdscr, manga):
    curses.endwin()
    mapping = get_online_chapter_mapping(manga)
    cli_chapters(manga, mapping)
    if mapping:
        spec = input('Choose listed chapters (e.g. 1,1.5 or 1-5), Enter to cancel: ').strip()
        if spec:
            # Re-check listing when starting the download.
            download_range(manga, spec)
    input('Press Enter to return to Komori...')
    stdscr.clear()


def main():
    args = sys.argv[1:]
    if args and args[0] in ('-h', '--help', 'help'):
        print('Komori — terminal manga library, reader and downloader')
        print('Usage:')
        print('  komori                         Interactive library (Q to quit)')
        print('  komori list                    Local library and reading progress')
        print('  komori search <query>          Search nonempty remote listings; add interactively')
        print('  komori chapters [manga/query]  Actual remote chapter listing, no image downloads')
        print('  komori cover [manga]           Cover/hero and synopsis')
        print('  komori resume [manga]          Resume local reading')
        print('  komori dl [manga] <selection>  Download only listed chapters, including fractions')
        print('  komori rm [manga] <selection>  Delete Komori chapters, never imported originals')
        print('Selections: 1,1.5,3 or 1-5 (gaps are not invented). Status is Unknown unless supplied.')
        print('Remote listing is not a guarantee that image pages are accessible.')
        return 0
    lib = load_library()
    if not args:
        if not sys.stdin.isatty():
            print('Komori TUI needs a terminal; use komori list or komori --help.')
            return 2
        try:
            curses.wrapper(main_library_menu)
        finally:
            subprocess.run(['kitten', 'icat', '--clear', '--silent'], check=False)
        return 0
    command = args[0]
    if command == 'list':
        cli_list()
        return 0
    if command == 'search':
        if len(args) < 2:
            print('Usage: komori search <query>')
            return 2
        return cli_search(' '.join(args[1:]))
    if command == 'chapters':
        query = ' '.join(args[1:]) or None
        manga = resolve_manga(lib, query)
        if not manga and query:
            candidates = search_online_manga(query)
            manga = candidates[0] if candidates else None
        if not manga:
            print('No matching manga with a nonempty remote listing.')
            return 1
        return cli_chapters(manga)
    if command == 'cover':
        return cli_cover(' '.join(args[1:]) or None)
    if command in ('dl', 'rm', 'delete'):
        if len(args) < 2:
            print(f'Usage: komori {command} [manga] <selection>')
            return 2
        manga = resolve_manga(lib, ' '.join(args[1:-1]) or None)
        if not manga:
            print('Manga not found in Komori library.')
            return 1
        if command == 'dl':
            result = download_range(manga, args[-1])
            return 1 if result.get('selection_error') or result['failed'] else 0
        if manga.get('read_only_import'):
            print('Imported library files are read-only; no files deleted.')
            return 1
        listing = {c[0]: c[1] for c in get_local_chapters(manga['dir'])}
        try:
            chapters = select_chapters(args[-1], listing)
        except ValueError:
            print('Selection is not available locally; nothing deleted.')
            return 1
        return 0 if all(delete_chapter(manga['dir'], ch) for ch in chapters) else 1
    if command == 'resume':
        _baseline_main()
        return 0
    print('Unknown Komori command. Run komori --help.')
    return 2
