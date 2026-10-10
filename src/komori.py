#!/usr/bin/env python3
import curses
import os
import sys
import glob
import subprocess
import shutil
import re
import json
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

CONFIG_DIR = os.path.expanduser("~/.config/opm")
CACHE_DIR = os.path.expanduser("~/.cache/opm")
COVERS_CACHE_DIR = os.path.join(CACHE_DIR, "covers")
LIBRARY_FILE = os.path.join(CONFIG_DIR, "library.json")
MANGA_DIR = os.path.expanduser("~/Downloads/Manga")
DEFAULT_OPM_DIR = os.path.expanduser("~/Downloads/OnePunchMan")

IMGUR_ALBUMS = {
    "164": "f8XgB2A",
    "165": "SnTDC4m",
    "166": "IS756yu",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://weebcentral.com/"
}

# --- LIBRARY MANAGEMENT ---
def init_directories():
    os.makedirs(CONFIG_DIR, exist_ok=True)
    os.makedirs(CACHE_DIR, exist_ok=True)
    os.makedirs(COVERS_CACHE_DIR, exist_ok=True)
    os.makedirs(MANGA_DIR, exist_ok=True)
    # Existing library directories are never created or modified on import.

def load_library():
    init_directories()
    if os.path.exists(LIBRARY_FILE):
        try:
            with open(LIBRARY_FILE, "r") as f:
                lib = json.load(f)
                if isinstance(lib, dict) and "mangas" in lib and len(lib["mangas"]) > 0:
                    # Validate paths
                    for m in lib["mangas"]:
                        if not m.get("cover_path") or not os.path.exists(m.get("cover_path")):
                            cached = os.path.join(m.get("dir", ""), "cover.jpg")
                            if os.path.exists(cached):
                                m["cover_path"] = cached
                    return lib
        except Exception:
            pass

    # Default initial library containing One-Punch Man
    opm_cover_path = os.path.join(DEFAULT_OPM_DIR, "cover.jpg")
    default_lib = {
        "active_manga_id": "01J76XY7KT7J224EBK6J816Y1Q",
        "mangas": [
            {
                "id": "01J76XY7KT7J224EBK6J816Y1Q",
                "title": "One-Punch Man",
                "dir": DEFAULT_OPM_DIR,
                "cover_url": "https://temp.compsci88.com/cover/fallback/01J76XY7KT7J224EBK6J816Y1Q.jpg",
                "cover_path": opm_cover_path,
                "source": "weebcentral",
                "series_id": "01J76XY7KT7J224EBK6J816Y1Q",
                "series_url": "https://weebcentral.com/series/01J76XY7KT7J224EBK6J816Y1Q/Onepunch-Man",
                "description": "The story of Saitama, a superhero who can defeat any opponent with a single punch but seeks to find a worthy opponent after growing bored by a lack of challenge.",
                "status": "Ongoing",
                "is_builtin_opm": True
            }
        ]
    }
    save_library(default_lib)
    return default_lib

def save_library(lib):
    init_directories()
    try:
        with open(LIBRARY_FILE, "w") as f:
            json.dump(lib, f, indent=2)
    except Exception as e:
        print(f"Error saving library: {e}", file=sys.stderr)

def get_active_manga(lib):
    active_id = lib.get("active_manga_id")
    for m in lib.get("mangas", []):
        if m.get("id") == active_id:
            return m
    if lib.get("mangas"):
        return lib["mangas"][0]
    return None

def set_active_manga(manga_id):
    lib = load_library()
    lib["active_manga_id"] = manga_id
    save_library(lib)

def resolve_manga(lib, arg=None):
    mangas = lib.get("mangas", [])
    if not mangas:
        return None
    if not arg:
        return get_active_manga(lib)
    arg_clean = arg.strip().lower()
    if arg_clean.isdigit():
        idx = int(arg_clean) - 1
        if 0 <= idx < len(mangas):
            return mangas[idx]
    for m in mangas:
        if m.get("id", "").lower() == arg_clean or m.get("series_id", "").lower() == arg_clean:
            return m
    for m in mangas:
        if arg_clean in m.get("title", "").lower():
            return m
    return None

def add_manga_to_library(manga_info):
    title = manga_info["title"]
    safe_title = "".join(c for c in title if c.isalnum() or c in (" ", "_", "-")).strip()
    manga_dir = os.path.join(MANGA_DIR, safe_title)
    os.makedirs(manga_dir, exist_ok=True)

    cover_path = os.path.join(manga_dir, "cover.jpg")
    cover_url = manga_info.get("cover_url")
    if cover_url:
        cached_cover = os.path.join(COVERS_CACHE_DIR, f"{manga_info['id']}.jpg")
        if os.path.exists(cached_cover):
            shutil.copyfile(cached_cover, cover_path)
        else:
            try:
                download_file(cover_url, cover_path)
                shutil.copyfile(cover_path, cached_cover)
            except Exception:
                pass

    entry = {
        "id": manga_info["id"],
        "title": title,
        "dir": manga_dir,
        "cover_url": cover_url,
        "cover_path": cover_path,
        "source": manga_info.get("source", "weebcentral"),
        "series_id": manga_info.get("id", ""),
        "series_url": manga_info.get("url", ""),
        "description": manga_info.get("description", ""),
        "status": manga_info.get("status", "Unknown"),
        "is_builtin_opm": bool(manga_info.get("is_builtin_opm", False))
    }

    lib = load_library()
    updated = False
    for idx, m in enumerate(lib["mangas"]):
        if m["id"] == entry["id"] or m["dir"] == entry["dir"]:
            lib["mangas"][idx].update(entry)
            updated = True
            break
    if not updated:
        lib["mangas"].append(entry)
    lib["active_manga_id"] = entry["id"]
    save_library(lib)
    return entry

def remove_manga_from_library(manga_id, delete_files=False):
    lib = load_library()
    manga = None
    new_mangas = []
    for m in lib.get("mangas", []):
        if m["id"] == manga_id:
            manga = m
        else:
            new_mangas.append(m)
    if manga:
        lib["mangas"] = new_mangas
        if lib.get("active_manga_id") == manga_id and new_mangas:
            lib["active_manga_id"] = new_mangas[0]["id"]
        save_library(lib)
        if delete_files and manga.get("dir") and os.path.exists(manga["dir"]):
            shutil.rmtree(manga["dir"], ignore_errors=True)
        return True
    return False

# --- STATE / LAST READ PER MANGA ---
def get_state_file(manga_dir):
    return os.path.join(manga_dir, ".last_read.json")

def load_last_read(manga_dir):
    sf = get_state_file(manga_dir)
    try:
        if os.path.exists(sf):
            with open(sf, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def save_last_read(manga_dir, ch_num, page_idx):
    sf = get_state_file(manga_dir)
    try:
        os.makedirs(manga_dir, exist_ok=True)
        with open(sf, "w") as f:
            json.dump({"chapter": str(ch_num), "page_idx": int(page_idx)}, f)
    except Exception:
        pass

def clear_last_read_if(manga_dir, ch_num):
    last_read = load_last_read(manga_dir)
    if str(last_read.get("chapter")) == str(ch_num):
        sf = get_state_file(manga_dir)
        try:
            if os.path.exists(sf):
                os.remove(sf)
        except Exception:
            pass

def delete_chapter(manga_dir, ch_num):
    ch_str = str(ch_num).strip()
    target_dir = os.path.join(manga_dir, f"Chapter_{ch_str}")
    if os.path.exists(target_dir):
        shutil.rmtree(target_dir, ignore_errors=True)
        clear_last_read_if(manga_dir, ch_str)
        return True
    return False

def confirm_delete_tui(stdscr, manga_dir, ch_num):
    h, w = stdscr.getmaxyx()
    msg = f"Delete Chapter {ch_num}? [y/N]: "
    try:
        stdscr.addstr(h - 1, 0, " " * (w - 1))
        stdscr.addstr(h - 1, max(0, (w - len(msg)) // 2), msg, curses.A_BOLD | curses.A_STANDOUT)
    except curses.error:
        pass
    stdscr.refresh()
    key = stdscr.getch()
    if key in (ord('y'), ord('Y')):
        delete_chapter(manga_dir, ch_num)
        return True
    return False

def parse_chapter_spec(spec_str):
    spec_str = spec_str.strip()
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(?:-|to|\.\.)\s*(\d+(?:\.\d+)?)$", spec_str, re.IGNORECASE)
    if m:
        start = float(m.group(1))
        end = float(m.group(2))
        step = 1 if start <= end else -1
        chapters = []
        cur = start
        while (cur <= end if step == 1 else cur >= end):
            chapters.append(str(int(cur)) if cur.is_integer() else str(cur))
            cur += step
        return chapters
    if "," in spec_str:
        return [p.strip() for p in spec_str.split(",") if p.strip()]
    return [spec_str]

def download_file(url, dest_path):
    temp_path = f"{dest_path}.tmp"
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=25) as resp, open(temp_path, "wb") as f:
        f.write(resp.read())
    os.replace(temp_path, dest_path)

def download_pages(out_dir, page_urls):
    os.makedirs(out_dir, exist_ok=True)

    def dl(item):
        idx, link = item
        ext = os.path.splitext(link.split("?")[0])[1] or ".png"
        path = os.path.join(out_dir, f"{idx+1:03d}{ext}")
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return
        temp_path = f"{path}.tmp"
        req = urllib.request.Request(link, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp, open(temp_path, "wb") as f:
                f.write(resp.read())
            os.replace(temp_path, path)
        except Exception:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
            raise

    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(dl, enumerate(page_urls)))

# --- ONLINE SEARCH ENGINE ---
def search_online_manga(query):
    results = []
    # 1. WeebCentral Search
    try:
        url = f"https://weebcentral.com/search/data?text={urllib.parse.quote(query)}"
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=12) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        articles = re.findall(r'<article class="bg-base-300.*?>(.*?)</article>', html, re.DOTALL)
        for art in articles:
            link_m = re.search(r'href="(https://weebcentral\.com/series/([a-zA-Z0-9]+)/([^"]+))"', art)
            if not link_m:
                continue
            series_url = link_m.group(1)
            series_id = link_m.group(2)
            slug = link_m.group(3)

            title_m = re.search(r'alt="([^"]+) cover"', art)
            title = title_m.group(1).strip() if title_m else slug.replace("-", " ")

            img_m = re.search(r'img src="([^"]+)"', art)
            cover_url = img_m.group(1) if img_m else f"https://temp.compsci88.com/cover/fallback/{series_id}.jpg"

            desc_m = re.search(r'<p class="line-clamp-3 text-sm">(.*?)</p>', art, re.DOTALL)
            desc = re.sub(r'<[^>]+>', '', desc_m.group(1)).strip() if desc_m else ""

            is_builtin_opm = bool("onepunch-man" in slug.lower() or "one-punch man" in title.lower())
            results.append({
                "id": series_id,
                "title": title,
                "url": series_url,
                "slug": slug,
                "cover_url": cover_url,
                "description": desc,
                "source": "weebcentral",
                "status": "Ongoing",
                "is_builtin_opm": is_builtin_opm
            })
    except Exception:
        pass

    # 2. MangaDex Search (supplementary)
    if len(results) < 8:
        try:
            md_url = f"https://api.mangadex.org/manga?title={urllib.parse.quote(query)}&limit=8&includes[]=cover_art"
            req2 = urllib.request.Request(md_url, headers={"User-Agent": "MangaReader/1.0"})
            with urllib.request.urlopen(req2, timeout=10) as resp2:
                data = json.loads(resp2.read().decode())
            for item in data.get("data", []):
                m_id = item["id"]
                attrs = item.get("attributes", {})
                title_dict = attrs.get("title", {})
                title = title_dict.get("en") or (list(title_dict.values())[0] if title_dict else "Unknown")
                if any(r["title"].lower() == title.lower() for r in results):
                    continue
                cover_file = None
                for rel in item.get("relationships", []):
                    if rel.get("type") == "cover_art":
                        cover_file = rel.get("attributes", {}).get("fileName")
                        break
                cover_url = f"https://uploads.mangadex.org/covers/{m_id}/{cover_file}.512.jpg" if cover_file else None
                desc_dict = attrs.get("description", {})
                desc = desc_dict.get("en") or (list(desc_dict.values())[0] if desc_dict else "")
                status = attrs.get("status", "Unknown").capitalize()
                year = attrs.get("year", "")

                results.append({
                    "id": m_id,
                    "title": title,
                    "url": f"https://mangadex.org/title/{m_id}",
                    "slug": re.sub(r'[^a-zA-Z0-9]+', '-', title).strip('-').lower(),
                    "cover_url": cover_url,
                    "description": desc,
                    "source": "mangadex",
                    "status": status,
                    "year": str(year) if year else "",
                    "is_builtin_opm": bool("one-punch man" in title.lower())
                })
        except Exception:
            pass

    return results

# --- CHAPTER SOURCES ENGINE ---
def get_opm_rss_chapters():
    feed_url = "https://raw.githubusercontent.com/DavidCRicardo/OPM-RSS_Feed/main/opm.rss"
    req = urllib.request.Request(feed_url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            xml = resp.read().decode('utf-8', errors='ignore')
    except Exception:
        return {}
    items = re.findall(r"<item>.*?</item>", xml, re.DOTALL)
    mapping = {}
    for it in items:
        t_m = re.search(r"<title>Ch\.\s*([^:]+):", it)
        l_m = re.search(r"<link>(.*?)</link>", it)
        if t_m and l_m:
            mapping[t_m.group(1).strip()] = l_m.group(1).strip()
    return mapping

def get_weebcentral_chapters(series_id):
    url = f"https://weebcentral.com/series/{series_id}/full-chapter-list"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        return {}

    all_items = re.findall(r'href="/chapters/([a-zA-Z0-9]+)".*?<span class="">\s*([^<]+)</span>', html, re.DOTALL)
    mapping = {}
    for ch_id, title in all_items:
        num_m = re.search(r'(\d+(?:\.\d+)?)', title)
        if num_m:
            ch_num = num_m.group(1).lstrip("0") or "0"
            if ch_num not in mapping:
                mapping[ch_num] = f"https://weebcentral.com/chapters/{ch_id}"
    return mapping

def get_mangadex_chapters(manga_id):
    url = f"https://api.mangadex.org/manga/{manga_id}/feed?limit=500&translatedLanguage[]=en&order[chapter]=asc"
    req = urllib.request.Request(url, headers={"User-Agent": "MangaReader/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception:
        return {}

    chapters = {}
    for item in data.get("data", []):
        attrs = item.get("attributes", {})
        ch = attrs.get("chapter")
        if ch and not attrs.get("externalUrl") and attrs.get("pages", 0) > 0:
            ch_clean = ch.lstrip("0") or "0"
            if ch_clean not in chapters:
                chapters[ch_clean] = item["id"]
    return chapters

def get_online_chapter_mapping(manga):
    source = manga.get("source", "weebcentral")
    series_id = manga.get("series_id", manga.get("id"))
    mapping = {}
    if manga.get("is_builtin_opm"):
        # Combine WeebCentral list and RSS feed
        mapping = get_weebcentral_chapters(series_id) if series_id else {}
        rss_map = get_opm_rss_chapters()
        for k, v in rss_map.items():
            k_clean = k.lstrip("0") or "0"
            if k_clean not in mapping:
                mapping[k_clean] = v
    elif source == "weebcentral":
        mapping = get_weebcentral_chapters(series_id)
    elif source == "mangadex":
        mapping = get_mangadex_chapters(series_id)
    return mapping

def download_manga_chapter(manga, ch_str, online_mapping=None):
    ch_str = str(ch_str).strip()
    ch_clean = ch_str.lstrip("0") or "0"
    manga_dir = manga["dir"]
    out_dir = os.path.join(manga_dir, f"Chapter_{ch_clean}")

    # Built-in OPM Imgur Albums
    client_id = os.environ.get("IMGUR_CLIENT_ID")
    if manga.get("is_builtin_opm") and ch_clean in IMGUR_ALBUMS and client_id:
        url = f"https://api.imgur.com/3/album/{IMGUR_ALBUMS[ch_clean]}"
        req = urllib.request.Request(url, headers={"Authorization": f"Client-ID {client_id}"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
            images = data.get("data", {}).get("images", [])
            if images:
                page_urls = [img["link"] for img in images]
                print(f"Downloading Chapter {ch_clean} ({len(page_urls)} pages) from Imgur...")
                download_pages(out_dir, page_urls)
                print(f"-> Downloaded Chapter {ch_clean} to: {out_dir}")
                return True
        except Exception:
            pass

    if online_mapping is None:
        online_mapping = get_online_chapter_mapping(manga)

    target_ref = online_mapping.get(ch_clean) or online_mapping.get(ch_str)
    if not target_ref:
        for k, v in online_mapping.items():
            if k.lstrip("0") == ch_clean:
                target_ref = v
                break

    if not target_ref:
        print(f"Chapter {ch_str} not found for {manga['title']}.")
        return False

    source = manga.get("source", "weebcentral")
    if source == "mangadex" and not manga.get("is_builtin_opm"):
        # Fetch from MangaDex at-home
        try:
            at_home_url = f"https://api.mangadex.org/at-home/server/{target_ref}"
            req = urllib.request.Request(at_home_url, headers={"User-Agent": "MangaReader/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                d = json.loads(resp.read().decode())
            base_url = d["baseUrl"]
            ch_hash = d["chapter"]["hash"]
            files = d["chapter"]["data"]
            page_urls = [f"{base_url}/data/{ch_hash}/{f}" for f in files]
            if page_urls:
                print(f"Downloading Chapter {ch_clean} ({len(page_urls)} pages)...")
                download_pages(out_dir, page_urls)
                print(f"-> Downloaded Chapter {ch_clean} to: {out_dir}")
                return True
        except Exception as e:
            print(f"Failed to fetch chapter {ch_str} via MangaDex: {e}")
            return False
    else:
        # WeebCentral chapter images
        images_url = f"{target_ref}/images?reading_style=long_strip"
        req = urllib.request.Request(images_url, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                html = resp.read().decode('utf-8', errors='ignore')
            urls = re.findall(r'src=["\'](https://[^"\']+\.(?:png|jpg|webp))["\']', html)
            if not urls:
                urls = re.findall(r'(https://[^"\']+\.(?:png|jpg|webp))', html)
            unique_urls = []
            for u in urls:
                if u not in unique_urls and ("/manga/" in u or "temp.compsci88" in u or "planeptune" in u):
                    unique_urls.append(u)
            if not unique_urls and urls:
                unique_urls = [u for u in urls if "badge" not in u and "avatar" not in u]

            if unique_urls:
                print(f"Downloading Chapter {ch_clean} ({len(unique_urls)} pages)...")
                download_pages(out_dir, unique_urls)
                print(f"-> Downloaded Chapter {ch_clean} to: {out_dir}")
                return True
        except Exception as e:
            print(f"Failed to fetch chapter {ch_str}: {e}")
            return False

    print(f"Chapter {ch_str} not found.")
    return False

def download_range(manga, spec_str):
    chapters = parse_chapter_spec(spec_str)
    print(f"\n[Target: {manga['title']}] Downloading {len(chapters)} chapter(s): {', '.join(chapters)}")
    online_mapping = get_online_chapter_mapping(manga)
    for ch in chapters:
        download_manga_chapter(manga, ch, online_mapping)
    print("\nAll downloads finished!")

# --- LOCAL CHAPTERS & VIEWER ---
def get_local_chapters(manga_dir):
    if not manga_dir or not os.path.exists(manga_dir):
        return []
    chapters = []
    for d in sorted(os.listdir(manga_dir)):
        full = os.path.join(manga_dir, d)
        if os.path.isdir(full) and d.startswith("Chapter_"):
            ch_num = d.replace("Chapter_", "")
            pages = sorted(glob.glob(os.path.join(full, "*.[jJ][pP][gG]")) +
                           glob.glob(os.path.join(full, "*.[pP][nN][gG]")) +
                           glob.glob(os.path.join(full, "*.[wW][eE][bB][pP]")))
            if pages:
                chapters.append((ch_num, d, pages))
    chapters.sort(key=lambda x: float(x[0]) if x[0].replace(".", "").isdigit() else 9999)
    return chapters

def render_kitty_image(img_path, stdscr, page_idx, total_pages, manga_title, ch_title):
    h, w = stdscr.getmaxyx()
    stdscr.erase()
    header = f" {manga_title} | {ch_title} | Page {page_idx + 1}/{total_pages} "
    footer = " [<- / -> / Space] Navigate | [I] External Viewer | [Q / Esc] Back to Menu "
    try:
        stdscr.addstr(0, max(0, (w - len(header)) // 2), header, curses.A_BOLD | curses.A_REVERSE)
        if h > 1:
            stdscr.addstr(h - 1, max(0, (w - len(footer)) // 2), footer, curses.A_DIM)
    except curses.error:
        pass
    stdscr.refresh()

    img_rows = max(1, h - 2)
    subprocess.run([
        "kitten", "icat",
        "--silent",
        "--clear",
        "--place", f"{w}x{img_rows}@0x1",
        "--scale-up",
        img_path
    ], stderr=subprocess.DEVNULL, check=False)

def read_chapters_flow(stdscr, initial_idx, chapters, manga_title, manga_dir, initial_page=0):
    curses.curs_set(0)
    stdscr.nodelay(False)
    stdscr.keypad(True)

    ch_idx = initial_idx
    page_idx = min(initial_page, len(chapters[ch_idx][2]) - 1) if ch_idx < len(chapters) and chapters[ch_idx][2] else 0

    while 0 <= ch_idx < len(chapters):
        ch_info = chapters[ch_idx]
        ch_title = f"Chapter {ch_info[0]}"
        pages = ch_info[2]
        total = len(pages)

        save_last_read(manga_dir, ch_info[0], page_idx)
        render_kitty_image(pages[page_idx], stdscr, page_idx, total, manga_title, ch_title)
        key = stdscr.getch()

        if key in (ord('q'), ord('Q'), 27):
            subprocess.run(["kitten", "icat", "--clear", "--silent"], stderr=subprocess.DEVNULL, check=False)
            break
        elif key in (curses.KEY_RIGHT, ord('l'), ord(' '), ord('n')):
            if page_idx < total - 1:
                page_idx += 1
            else:
                if ch_idx + 1 < len(chapters):
                    ch_idx += 1
                    page_idx = 0
                else:
                    subprocess.run(["kitten", "icat", "--clear", "--silent"], stderr=subprocess.DEVNULL, check=False)
                    stdscr.erase()
                    h, w = stdscr.getmaxyx()
                    msg1 = f"End of {ch_title} (No next chapter downloaded)."
                    msg2 = "Press [Space / Enter] to restart from Page 1, or [Q] to return to menu."
                    try:
                        stdscr.addstr(h // 2 - 1, max(0, (w - len(msg1)) // 2), msg1, curses.A_BOLD)
                        stdscr.addstr(h // 2 + 1, max(0, (w - len(msg2)) // 2), msg2, curses.A_DIM)
                    except curses.error:
                        pass
                    stdscr.refresh()
                    k = stdscr.getch()
                    if k in (ord('q'), ord('Q'), 27):
                        break
                    else:
                        page_idx = 0
        elif key in (curses.KEY_LEFT, ord('h'), ord('p'), curses.KEY_BACKSPACE, 127):
            if page_idx > 0:
                page_idx -= 1
            else:
                if ch_idx > 0:
                    ch_idx -= 1
                    page_idx = len(chapters[ch_idx][2]) - 1
        elif key in (ord('i'), ord('I')):
            if shutil.which("imv"):
                subprocess.Popen(["imv"] + pages, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                subprocess.Popen(["xdg-open", pages[page_idx]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# --- COVER ART VIEWER SCREEN ---
def show_cover_art_screen(stdscr, manga_info, is_preview=False):
    curses.curs_set(0)
    stdscr.keypad(True)
    curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_WHITE)
    curses.init_pair(2, curses.COLOR_CYAN, curses.COLOR_BLACK)
    curses.init_pair(3, curses.COLOR_YELLOW, curses.COLOR_BLACK)
    curses.init_pair(4, curses.COLOR_GREEN, curses.COLOR_BLACK)

    title = manga_info.get("title", "Unknown Manga")
    cover_path = manga_info.get("cover_path")

    # If preview or missing cover, try caching it
    if (not cover_path or not os.path.exists(cover_path)) and manga_info.get("cover_url"):
        cached = os.path.join(COVERS_CACHE_DIR, f"{manga_info['id']}.jpg")
        if not os.path.exists(cached):
            try:
                download_file(manga_info["cover_url"], cached)
            except Exception:
                pass
        if os.path.exists(cached):
            cover_path = cached

    h, w = stdscr.getmaxyx()
    stdscr.erase()

    tag = "SEARCH PREVIEW" if is_preview else "LIBRARY"
    hdr = f" === {title.upper()} [{tag}] - COVER ART === "
    try:
        stdscr.addstr(0, max(0, (w - len(hdr)) // 2), hdr, curses.A_BOLD | curses.color_pair(2))
    except curses.error:
        pass

    if is_preview:
        footer = " [A] Add to Library | [D] Add & Download | [I] External Viewer | [Q / Esc] Back "
    else:
        footer = " [D] Download Chapters | [I] External Viewer | [Q / Esc / Enter] Back "

    try:
        stdscr.addstr(h - 1, max(0, (w - len(footer)) // 2), footer, curses.A_DIM)
    except curses.error:
        pass

    # Layout: split if width >= 80
    if w >= 80:
        img_w = max(24, (w // 2) - 4)
        img_h = max(10, h - 3)
        img_x = 2
        img_y = 2

        rx = (w // 2) + 2
        ry = 2
        def add_detail(label, val, attr=curses.A_NORMAL):
            nonlocal ry
            if ry < h - 2 and val:
                line = f"{label}: {val}"
                try:
                    stdscr.addstr(ry, rx, line[:w - rx - 2], attr)
                except curses.error:
                    pass
                ry += 1

        add_detail("Title", title, curses.A_BOLD)
        add_detail("Status", manga_info.get("status"))
        add_detail("Source", manga_info.get("source", "").capitalize())
        if manga_info.get("year"):
            add_detail("Year", str(manga_info.get("year")))

        desc = manga_info.get("description", "").strip()
        if desc and ry < h - 4:
            ry += 1
            try:
                stdscr.addstr(ry, rx, "Synopsis:", curses.A_BOLD | curses.color_pair(3))
            except curses.error:
                pass
            ry += 1
            words = desc.replace("\n", " ").split()
            cur_line = ""
            max_w = w - rx - 3
            for word in words:
                if len(cur_line) + len(word) + 1 <= max_w:
                    cur_line += (" " if cur_line else "") + word
                else:
                    if ry < h - 3:
                        try:
                            stdscr.addstr(ry, rx, cur_line)
                        except curses.error:
                            pass
                        ry += 1
                    cur_line = word
                if ry >= h - 3:
                    break
            if cur_line and ry < h - 3:
                try:
                    stdscr.addstr(ry, rx, cur_line)
                except curses.error:
                    pass
    else:
        img_w = max(10, w - 4)
        img_h = max(5, h - 3)
        img_x = 2
        img_y = 2

    stdscr.refresh()

    if cover_path and os.path.exists(cover_path):
        subprocess.run([
            "kitten", "icat",
            "--silent",
            "--clear",
            "--place", f"{img_w}x{img_h}@{img_x}x{img_y}",
            "--scale-up",
            cover_path
        ], stderr=subprocess.DEVNULL, check=False)
    else:
        msg = "[No cover art image available]"
        try:
            stdscr.addstr(h // 2, max(0, (w - len(msg)) // 2), msg, curses.color_pair(3))
            stdscr.refresh()
        except curses.error:
            pass

    action = None
    while True:
        k = stdscr.getch()
        if k in (ord('i'), ord('I')):
            if cover_path and os.path.exists(cover_path):
                if shutil.which("imv"):
                    subprocess.Popen(["imv", cover_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    subprocess.Popen(["xdg-open", cover_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif is_preview and k in (ord('a'), ord('A')):
            action = "add"
            break
        elif is_preview and k in (ord('d'), ord('D')):
            action = "add_and_download"
            break
        elif not is_preview and k in (ord('d'), ord('D')):
            action = "download"
            break
        elif k in (ord('q'), ord('Q'), 27, 10, 13, ord(' ')):
            break

    subprocess.run(["kitten", "icat", "--clear", "--silent"], stderr=subprocess.DEVNULL, check=False)
    return action

# --- SEARCH & ADD TUI ---
def search_and_add_tui(stdscr):
    curses.echo()
    curses.curs_set(1)
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    prompt = "Enter Manga Title to Search: "
    try:
        stdscr.addstr(h // 2 - 1, max(0, (w - len(prompt)) // 2), prompt, curses.A_BOLD)
    except curses.error:
        pass
    stdscr.refresh()

    win = curses.newwin(1, max(20, w // 2), h // 2, max(0, (w - (w // 2)) // 2))
    query = win.getstr().decode('utf-8').strip()
    curses.noecho()
    curses.curs_set(0)

    if not query:
        return

    # Show searching status
    stdscr.erase()
    searching_msg = f"Searching for '{query}' across WeebCentral & MangaDex..."
    try:
        stdscr.addstr(h // 2, max(0, (w - len(searching_msg)) // 2), searching_msg, curses.A_BOLD)
    except curses.error:
        pass
    stdscr.refresh()

    results = search_online_manga(query)
    if not results:
        stdscr.erase()
        no_res = f"No manga found matching '{query}'. Press any key to return..."
        try:
            stdscr.addstr(h // 2, max(0, (w - len(no_res)) // 2), no_res, curses.A_BOLD)
        except curses.error:
            pass
        stdscr.refresh()
        stdscr.getch()
        return

    selected = 0
    while True:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        title_hdr = f"=== SEARCH RESULTS FOR: '{query}' ({len(results)} found) ==="
        try:
            stdscr.addstr(1, max(0, (w - len(title_hdr)) // 2), title_hdr, curses.A_BOLD | curses.color_pair(2))
        except curses.error:
            pass

        start_y = 3
        max_items = max(1, h - start_y - 3)
        offset = max(0, selected - max_items + 1)
        for idx in range(offset, min(len(results), offset + max_items)):
            r = results[idx]
            prefix = " > " if idx == selected else "   "
            line = f"{prefix}{idx+1}. {r['title']} — {r['chapter_count']} listed chapters"
            attr = curses.color_pair(1) | curses.A_BOLD if idx == selected else curses.A_NORMAL
            try:
                stdscr.addstr(start_y + idx - offset, 4, line[:w - 8], attr)
            except curses.error:
                pass

        help_bar = "[Up/Down] Navigate | [Enter] View Cover Art & Details | [A] Quick Add | [Q] Cancel"
        if h > 2:
            try:
                stdscr.addstr(h - 2, max(0, (w - len(help_bar)) // 2), help_bar, curses.A_DIM)
            except curses.error:
                pass

        stdscr.refresh()
        k = stdscr.getch()

        if k in (curses.KEY_UP, ord('k')):
            selected = (selected - 1) % len(results)
        elif k in (curses.KEY_DOWN, ord('j')):
            selected = (selected + 1) % len(results)
        elif k in (ord('a'), ord('A')):
            chosen = results[selected]
            added = add_manga_to_library(chosen)
            stdscr.erase()
            added_msg = f"Added '{added['title']}' to your Library!"
            try:
                stdscr.addstr(h // 2, max(0, (w - len(added_msg)) // 2), added_msg, curses.A_BOLD | curses.color_pair(4))
            except curses.error:
                pass
            stdscr.refresh()
            curses.napms(1000)
            return added
        elif k in (curses.KEY_ENTER, 10, 13, ord('v'), ord('V')):
            chosen = results[selected]
            action = show_cover_art_screen(stdscr, chosen, is_preview=True)
            if action in ("add", "add_and_download"):
                added = add_manga_to_library(chosen)
                if action == "add_and_download":
                    prompt_download(stdscr, added)
                return added
        elif k in (ord('q'), ord('Q'), 27):
            break

# --- DOWNLOAD PROMPT ---
def prompt_download(stdscr, manga):
    curses.echo()
    curses.curs_set(1)
    h, w = stdscr.getmaxyx()
    prompt = f"Enter Chapter or Range for '{manga['title']}' (e.g. 1 or 1-5): "
    stdscr.erase()
    try:
        stdscr.addstr(h // 2, max(0, (w - len(prompt)) // 2), prompt)
    except curses.error:
        pass
    stdscr.refresh()
    win = curses.newwin(1, 24, h // 2, min(w - 25, max(0, (w - len(prompt)) // 2) + len(prompt)))
    ch_str = win.getstr().decode('utf-8').strip()
    curses.noecho()
    curses.curs_set(0)

    if ch_str:
        curses.endwin()
        download_range(manga, ch_str)
        print("\nPress Enter to return to menu...")
        input()
        stdscr.clear()

# --- MANGA HUB MENU ---
def manga_hub_menu(stdscr, manga):
    curses.curs_set(0)
    stdscr.keypad(True)
    selected = 0

    while True:
        stdscr.erase()
        h, w = stdscr.getmaxyx()

        title_hdr = f"=== {manga['title'].upper()} ==="
        try:
            stdscr.addstr(1, max(0, (w - len(title_hdr)) // 2), title_hdr, curses.A_BOLD | curses.color_pair(2))
        except curses.error:
            pass

        manga_dir = manga["dir"]
        chapters = get_local_chapters(manga_dir)
        last_read = load_last_read(manga_dir)
        last_ch = last_read.get("chapter")
        last_page = last_read.get("page_idx", 0)

        resume_idx = None
        if last_ch is not None:
            for idx, c in enumerate(chapters):
                if c[0] == str(last_ch):
                    resume_idx = idx
                    break

        menu_items = []
        action_map = []

        if resume_idx is not None:
            menu_items.append(f"[R] Resume: Chapter {last_ch} (Page {last_page + 1})")
            action_map.append(("resume", resume_idx, last_page))

        menu_items.append("[V] View Cover Art & Details")
        action_map.append(("cover", None, None))

        for idx, c in enumerate(chapters):
            menu_items.append(f"Chapter {c[0]} ({len(c[2])} pages)")
            action_map.append(("chapter", idx, 0))

        menu_items.append("[+] Download Chapters (Single or Range)")
        action_map.append(("download", None, None))
        menu_items.append("[<] Back to Library")
        action_map.append(("back", None, None))

        if selected >= len(menu_items):
            selected = len(menu_items) - 1

        start_y = 3
        max_rows = max(1, h - start_y - 2)
        offset = max(0, selected - max_rows + 1)
        for idx in range(offset, min(len(menu_items), offset + max_rows)):
            item = menu_items[idx]
            prefix = " > " if idx == selected else "   "
            line = f"{prefix}{item} "
            attr = curses.color_pair(1) | curses.A_BOLD if idx == selected else curses.A_NORMAL
            try:
                stdscr.addstr(start_y + idx - offset, 4, line[:w - 6], attr)
            except curses.error:
                pass

        instructions = "[Up/Down] Select | [Enter] Open | [V] Cover Art | [R] Resume | [D] Download | [X] Delete | [Q] Back"
        if h > 2:
            try:
                stdscr.addstr(h - 2, max(0, (w - len(instructions)) // 2), instructions, curses.A_DIM)
            except curses.error:
                pass

        stdscr.refresh()
        key = stdscr.getch()

        if key in (curses.KEY_UP, ord('k')):
            selected = (selected - 1) % len(menu_items)
        elif key in (curses.KEY_DOWN, ord('j')):
            selected = (selected + 1) % len(menu_items)
        elif key in (ord('r'), ord('R')) and resume_idx is not None:
            read_chapters_flow(stdscr, resume_idx, chapters, manga["title"], manga_dir, initial_page=last_page)
        elif key in (ord('v'), ord('V')):
            info = {
                "source": manga.get("source"),
                "status": manga.get("status"),
                "description": manga.get("description"),
                "chapters_count": len(chapters)
            }
            act = show_cover_art_screen(stdscr, {**manga, **info}, is_preview=False)
            if act == "download":
                prompt_download(stdscr, manga)
        elif key in (ord('x'), ord('X'), curses.KEY_DC):
            action, target_idx, _ = action_map[selected]
            if action == "chapter":
                confirm_delete_tui(stdscr, manga_dir, chapters[target_idx][0])
            elif action == "resume":
                confirm_delete_tui(stdscr, manga_dir, chapters[target_idx][0])
        elif key in (ord('d'), ord('D')):
            prompt_download(stdscr, manga)
        elif key in (ord('q'), ord('Q'), 27):
            break
        elif key in (curses.KEY_ENTER, 10, 13):
            action, target_idx, page = action_map[selected]
            if action == "resume":
                read_chapters_flow(stdscr, target_idx, chapters, manga["title"], manga_dir, initial_page=page)
            elif action == "chapter":
                read_chapters_flow(stdscr, target_idx, chapters, manga["title"], manga_dir, initial_page=0)
            elif action == "cover":
                info = {
                    "source": manga.get("source"),
                    "status": manga.get("status"),
                    "description": manga.get("description"),
                    "chapters_count": len(chapters)
                }
                act = show_cover_art_screen(stdscr, {**manga, **info}, is_preview=False)
                if act == "download":
                    prompt_download(stdscr, manga)
            elif action == "download":
                prompt_download(stdscr, manga)
            elif action == "back":
                break

# --- MAIN LIBRARY MENU ---
def main_library_menu(stdscr):
    curses.curs_set(0)
    stdscr.keypad(True)
    curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_WHITE)
    curses.init_pair(2, curses.COLOR_CYAN, curses.COLOR_BLACK)
    curses.init_pair(3, curses.COLOR_YELLOW, curses.COLOR_BLACK)
    curses.init_pair(4, curses.COLOR_GREEN, curses.COLOR_BLACK)

    selected = 0

    while True:
        stdscr.erase()
        h, w = stdscr.getmaxyx()

        title = "=== MANGA TERMINAL READER & LIBRARY ==="
        try:
            stdscr.addstr(1, max(0, (w - len(title)) // 2), title, curses.A_BOLD | curses.color_pair(2))
        except curses.error:
            pass

        lib = load_library()
        mangas = lib.get("mangas", [])

        menu_items = []
        action_map = []

        for idx, m in enumerate(mangas):
            chs = get_local_chapters(m.get("dir", ""))
            lr = load_last_read(m.get("dir", ""))
            lr_str = f" | Last read: Ch {lr['chapter']}" if lr.get("chapter") else ""
            menu_items.append(f"{m['title']} ({len(chs)} chapters downloaded{lr_str})")
            action_map.append(("manga", m))

        menu_items.append("[+] Search & Add Manga to Library")
        action_map.append(("search", None))
        menu_items.append("[X] Quit")
        action_map.append(("quit", None))

        if selected >= len(menu_items):
            selected = len(menu_items) - 1

        start_y = 3
        max_rows = max(1, h - start_y - 2)
        offset = max(0, selected - max_rows + 1)
        for idx in range(offset, min(len(menu_items), offset + max_rows)):
            item = menu_items[idx]
            prefix = " > " if idx == selected else "   "
            line = f"{prefix}{item} "
            attr = curses.color_pair(1) | curses.A_BOLD if idx == selected else curses.A_NORMAL
            try:
                stdscr.addstr(start_y + idx - offset, 4, line[:w - 6], attr)
            except curses.error:
                pass

        help_bar = "[Up/Down] Select | [Enter] Open | [V] Cover Art | [S/+] Search & Add | [X] Remove Manga | [Q] Quit"
        if h > 2:
            try:
                stdscr.addstr(h - 2, max(0, (w - len(help_bar)) // 2), help_bar, curses.A_DIM)
            except curses.error:
                pass

        stdscr.refresh()
        key = stdscr.getch()

        if key in (curses.KEY_UP, ord('k')):
            selected = (selected - 1) % len(menu_items)
        elif key in (curses.KEY_DOWN, ord('j')):
            selected = (selected + 1) % len(menu_items)
        elif key in (ord('s'), ord('S'), ord('+')):
            new_m = search_and_add_tui(stdscr)
            if new_m:
                manga_hub_menu(stdscr, new_m)
        elif key in (ord('v'), ord('V')):
            action, m = action_map[selected]
            if action == "manga":
                chs = get_local_chapters(m.get("dir", ""))
                info = {
                    "source": m.get("source"),
                    "status": m.get("status"),
                    "description": m.get("description"),
                    "chapters_count": len(chs)
                }
                act = show_cover_art_screen(stdscr, {**m, **info}, is_preview=False)
                if act == "download":
                    prompt_download(stdscr, m)
        elif key in (ord('x'), ord('X'), curses.KEY_DC):
            action, m = action_map[selected]
            if action == "manga":
                # Confirm removing manga
                confirm_msg = f"Remove '{m['title']}' from Library? [y/N]: "
                try:
                    stdscr.addstr(h - 1, 0, " " * (w - 1))
                    stdscr.addstr(h - 1, max(0, (w - len(confirm_msg)) // 2), confirm_msg, curses.A_BOLD | curses.A_STANDOUT)
                except curses.error:
                    pass
                stdscr.refresh()
                k_conf = stdscr.getch()
                if k_conf in (ord('y'), ord('Y')):
                    remove_manga_from_library(m["id"], delete_files=False)
        elif key in (ord('q'), ord('Q'), 27):
            break
        elif key in (curses.KEY_ENTER, 10, 13):
            action, target = action_map[selected]
            if action == "manga":
                set_active_manga(target["id"])
                manga_hub_menu(stdscr, target)
            elif action == "search":
                new_m = search_and_add_tui(stdscr)
                if new_m:
                    manga_hub_menu(stdscr, new_m)
            elif action == "quit":
                break

# --- CLI ENTRYPOINT ---
def cli_search(query):
    print(f"Searching for '{query}'...")
    results = search_online_manga(query)
    if not results:
        print(f"No manga found matching '{query}'.")
        return
    print(f"\nFound {len(results)} manga result(s):")
    for idx, r in enumerate(results[:10]):
        print(f"  [{idx+1}] {r['title']} ({r['source']})")
        if r.get("description"):
            desc_snip = r["description"][:100].replace("\n", " ")
            print(f"      {desc_snip}...")

    choice = input("\nEnter number to view Cover Art & Add to Library (or Enter to cancel): ").strip()
    if choice.isdigit():
        c_idx = int(choice) - 1
        if 0 <= c_idx < len(results):
            chosen = results[c_idx]
            # Download cover if needed
            if chosen.get("cover_url"):
                cached = os.path.join(COVERS_CACHE_DIR, f"{chosen['id']}.jpg")
                if not os.path.exists(cached):
                    try:
                        download_file(chosen["cover_url"], cached)
                    except Exception:
                        pass
                if os.path.exists(cached):
                    subprocess.run(["kitten", "icat", cached], stderr=subprocess.DEVNULL, check=False)
            add_resp = input(f"\nAdd '{chosen['title']}' to your Library? [Y/n]: ").strip().lower()
            if add_resp in ("", "y", "yes"):
                added = add_manga_to_library(chosen)
                print(f"Successfully added '{added['title']}' to your Library at {added['dir']}!")
                dl_resp = input("Download chapters now? (e.g. 1-5 or Enter to skip): ").strip()
                if dl_resp:
                    download_range(added, dl_resp)

def cli_cover(manga_arg=None):
    lib = load_library()
    manga = resolve_manga(lib, manga_arg)
    if not manga:
        print(f"Manga '{manga_arg}' not found in library.")
        return
    cover_path = manga.get("cover_path")
    if (not cover_path or not os.path.exists(cover_path)) and manga.get("cover_url"):
        cover_path = os.path.join(manga["dir"], "cover.jpg")
        try:
            download_file(manga["cover_url"], cover_path)
            manga["cover_path"] = cover_path
            save_library(lib)
        except Exception:
            pass

    if cover_path and os.path.exists(cover_path):
        print(f"=== Cover Art: {manga['title']} ===")
        subprocess.run(["kitten", "icat", cover_path], stderr=subprocess.DEVNULL, check=False)
        print(f"Cover Path: {cover_path}")
    else:
        print(f"No cover art available for {manga['title']}.")

def cli_list():
    lib = load_library()
    mangas = lib.get("mangas", [])
    print(f"=== MANGA LIBRARY ({len(mangas)} title(s)) ===\n")
    for idx, m in enumerate(mangas):
        chs = get_local_chapters(m.get("dir", ""))
        lr = load_last_read(m.get("dir", ""))
        active_mark = " [*]" if m.get("id") == lib.get("active_manga_id") else ""
        print(f"[{idx+1}]{active_mark} {m['title']}")
        print(f"    Path:     {m.get('dir')}")
        print(f"    Chapters: {len(chs)} downloaded")
        if lr.get("chapter"):
            print(f"    Progress: Chapter {lr['chapter']} (Page {lr.get('page_idx', 0) + 1})")
        print()

def main():
    lib = load_library()
    if len(sys.argv) > 1:
        arg = sys.argv[1].strip()
        if arg in ("-h", "--help", "help"):
            print("Manga Terminal Reader & Range Downloader")
            print("Usage:")
            print("  opm                                  Interactive Library TUI")
            print("  opm list                             List library manga & chapters")
            print("  opm search <query>                   Search & add manga to library")
            print("  opm cover [manga]                    View cover art of manga")
            print("  opm resume [manga]                   Resume reading where you left off")
            print("  opm dl [manga] <range>               Download chapters (e.g. opm dl 1-5)")
            print("  opm rm [manga] <range>               Delete chapters (e.g. opm rm 1-5)")
            return
        elif arg == "list":
            cli_list()
            return
        elif arg == "search":
            if len(sys.argv) < 3:
                print("Usage: opm search <manga title>")
                return
            query = " ".join(sys.argv[2:])
            cli_search(query)
            return
        elif arg == "cover":
            manga_arg = sys.argv[2] if len(sys.argv) > 2 else None
            cli_cover(manga_arg)
            return
        elif arg in ("rm", "delete"):
            if len(sys.argv) < 3:
                print("Usage: opm rm [manga] <range>")
                return
            if len(sys.argv) >= 4:
                manga = resolve_manga(lib, sys.argv[2])
                range_arg = sys.argv[3]
            else:
                # Check if arg2 is a range or a manga
                if any(c in sys.argv[2] for c in ("-", "..", ",")) or sys.argv[2].replace(".", "").isdigit():
                    manga = get_active_manga(lib)
                    range_arg = sys.argv[2]
                else:
                    manga = resolve_manga(lib, sys.argv[2])
                    range_arg = None
            if not manga or not range_arg:
                print("Specify chapter or range to delete: opm rm [manga] <range>")
                return
            chapters_to_del = parse_chapter_spec(range_arg)
            for ch in chapters_to_del:
                if delete_chapter(manga["dir"], ch):
                    print(f"Deleted Chapter {ch} for {manga['title']}")
                else:
                    print(f"Chapter {ch} not found for {manga['title']}.")
            return
        elif arg == "resume":
            manga_arg = sys.argv[2] if len(sys.argv) > 2 else None
            manga = resolve_manga(lib, manga_arg)
            if not manga:
                print("Manga not found.")
                return
            last_read = load_last_read(manga["dir"])
            last_ch = last_read.get("chapter")
            last_page = last_read.get("page_idx", 0)
            if not last_ch:
                print(f"No previous reading progress saved for {manga['title']}.")
                return
            chapters = get_local_chapters(manga["dir"])
            target_idx = None
            for idx, c in enumerate(chapters):
                if c[0] == str(last_ch):
                    target_idx = idx
                    break
            if target_idx is None:
                print(f"Chapter {last_ch} is not available locally for {manga['title']}.")
                return
            try:
                curses.wrapper(lambda stdscr: read_chapters_flow(
                    stdscr, target_idx, chapters, manga["title"], manga["dir"], initial_page=last_page
                ))
            finally:
                subprocess.run(["kitten", "icat", "--clear", "--silent"], stderr=subprocess.DEVNULL, check=False)
            return
        elif arg == "dl":
            if len(sys.argv) < 3:
                print("Usage: opm dl [manga] <range>")
                return
            if len(sys.argv) >= 4:
                manga = resolve_manga(lib, sys.argv[2])
                range_arg = sys.argv[3]
            else:
                if any(c in sys.argv[2] for c in ("-", "..", ",")) or sys.argv[2].replace(".", "").isdigit():
                    manga = get_active_manga(lib)
                    range_arg = sys.argv[2]
                else:
                    manga = resolve_manga(lib, sys.argv[2])
                    range_arg = None
            if not manga or not range_arg:
                print("Specify range: opm dl [manga] <start>-<end>")
                return
            download_range(manga, range_arg)
            return
        elif any(c in arg for c in ("-", "..", ",")) or arg.replace(".", "").isdigit():
            manga = get_active_manga(lib)
            if manga:
                download_range(manga, arg)
            return

    try:
        curses.wrapper(main_library_menu)
    finally:
        subprocess.run(["kitten", "icat", "--clear", "--silent"], stderr=subprocess.DEVNULL, check=False)

# Komori-specific implementation is kept separate from the baseline snapshot.
from pathlib import Path
exec(compile((Path(__file__).resolve().parent / 'komori_runtime.py').read_text(),
             str(Path(__file__).resolve().parent / 'komori_runtime.py'), 'exec'), globals())

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('Komori interrupted; incomplete downloads can be retried.')
        sys.exit(130)
    except Exception:
        print('Komori could not complete this operation. Check the local configuration, terminal, or network; original files are preserved.', file=sys.stderr)
        sys.exit(1)
