# -*- coding: utf-8 -*-
"""
vk_topic_dump.py — скачивает обсуждения VK: одну тему (topic) или всю доску (board).

Не требует API-ключа. Использует установленный Google Chrome через Playwright
(чистый requests/curl_cffi VK отклоняет как бота по TLS-фингерпринту).

Установка зависимостей:
    pip install playwright requests

Использование:
    python vk_topic_dump.py "https://vk.ru/topic-210485732_50314759" -o vk_dump
    python vk_topic_dump.py "https://m.vk.ru/board210485732" -o vk_board   # вся доска
    python vk_topic_dump.py ... --headed     # показать окно браузера (если headless заблокируют)
    python vk_topic_dump.py ... --force      # игнорировать метки .done, качать заново
    python vk_topic_dump.py ... --videos     # дополнительно скачать видео через yt-dlp

Результат на тему:
    <out>/index.html   — читабельный дамп обсуждения (тексты + фото + ссылки)
    <out>/posts.json   — структурированные данные
    <out>/photos/      — все фото в максимальном разрешении
    <out>/docs/        — все прикреплённые документы
    <out>/videos.txt   — ссылки на видео
    <out>/.done        — метка завершения (для возобновления прерванного прогона)

Результат на доску: подпапка на каждую тему + общий index.html со списком тем.
"""
import argparse
import html as html_mod
import json
import re
import sys
import time
from pathlib import Path

import requests

TOPIC_RE = re.compile(r"topic-(\d+)_(\d+)")
BOARD_RE = re.compile(r"board(\d+)")
KNOWN_EXT = r"pdf|docx?|xlsx?|pptx?|zip|rar|7z|png|jpe?g|gif|mp3|epub|fb2"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"}

# --- JS, выполняемый в контексте страницы m.vk.ru (проходит антибот-проверку) ---

JS_EXTRACT_POSTS = """
async (offsets) => {
  const base = location.origin + location.pathname;
  const posts = [];
  for (const offset of offsets) {
    const html = await (await fetch(`${base}?offset=${offset}`, {credentials: "include"})).text();
    const doc = new DOMParser().parseFromString(html, "text/html");
    doc.querySelectorAll(".post_item").forEach(p => {
      const id = p.id.replace("topic_comment-", "");
      const author = p.querySelector(".pi_author")?.textContent.trim() || "";
      const text = (p.querySelector(".pi_text")?.innerHTML || "")
        .replace(/<br\\s*\\/?>/gi, "\\n").replace(/<[^>]+>/g, "").trim();
      const dateEl = p.querySelector('a[href*="?post="]');
      const date = dateEl ? dateEl.textContent.trim() : "";
      // Прямая ссылка на фото (с as=списком размеров) лежит прямо в превью на странице темы —
      // отдельные страницы фото открывать не нужно (VK их режет рейт-лимитом).
      const photos = [...p.querySelectorAll('a[href^="/photo"]')].map(a => {
        const el = a.querySelector("[style*='background-image']");
        const m = el ? (el.getAttribute("style") || "").match(/background-image:\\s*url\\(([^)]+)\\)/) : null;
        return {href: a.getAttribute("href"), thumb: m ? m[1].replace(/^['"]|['"]$/g, "") : null};
      });
      const docs = [...p.querySelectorAll('a[href^="/doc"]')].map(a => ({
        href: a.getAttribute("href"),
        name: a.textContent.trim().replace(/\\s+/g, " ")
      }));
      const videos = [...p.querySelectorAll('a[href^="/video"]')].map(a => ({
        href: a.getAttribute("href"),
        label: (a.getAttribute("aria-label") || "").trim()
      }));
      posts.push({id, author, date, text, photos, docs, videos});
    });
  }
  return posts;
}
"""

# Фолбэк-резолв фото через их страницы: последовательно с паузой (рейт-лимит VK).
JS_RESOLVE_PHOTOS = """
async (hrefs) => {
  const out = {};
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  for (const href of hrefs) {
    let url = null;
    try {
      const h = await (await fetch(href, {credentials: "include"})).text();
      const d = new DOMParser().parseFromString(h, "text/html");
      const img = [...d.querySelectorAll("img")].map(i => i.src)
        .find(s => s.includes("vkuserphoto") && s.includes("cs="));
      if (img) {
        const m = img.match(/as=([0-9x,]+)/);
        url = img;
        if (m) {
          const sizes = m[1].split(",").map(s => s.split("x").map(Number));
          const biggest = sizes.reduce((a, b) => (a[0] * a[1] >= b[0] * b[1] ? a : b));
          url = img.replace(/cs=\\d+x\\d+/, `cs=${biggest[0]}x${biggest[1]}`);
        }
      }
    } catch (e) {}
    out[href] = url;
    await sleep(250);
  }
  return out;
}
"""

# Достать прямую ссылку на фото из живого DOM уже открытой страницы фото
JS_PHOTO_FROM_DOM = """
() => {
  const img = [...document.querySelectorAll("img")].map(i => i.src)
    .find(s => s.includes("vkuserphoto") && s.includes("cs="));
  if (!img) return null;
  const m = img.match(/as=([0-9x,]+)/);
  if (!m) return img;
  const sizes = m[1].split(",").map(s => s.split("x").map(Number));
  const biggest = sizes.reduce((a, b) => (a[0] * a[1] >= b[0] * b[1] ? a : b));
  return img.replace(/cs=\\d+x\\d+/, `cs=${biggest[0]}x${biggest[1]}`);
}
"""

JS_BOARD_TOPICS = """
async () => {
  const all = [];
  for (let off = 0; off <= 2000; off += 20) {
    const html = await (await fetch(`${location.origin}/board${GID}?offset=${off}`, {credentials: "include"})).text();
    const doc = new DOMParser().parseFromString(html, "text/html");
    const items = [...doc.querySelectorAll(".topic_item")].map(t => ({
      href: t.querySelector(".ti_title")?.getAttribute("href"),
      title: (t.querySelector(".ti_title")?.textContent || "").trim(),
      count: (t.querySelector(".ti_count")?.textContent || "").trim()
    })).filter(t => t.href);
    if (!items.length) break;
    all.push(...items);
    if (items.length < 20) break;
  }
  return all;
}
"""


def log(msg):
    print(msg, flush=True)


def launch_browser(pw, headed):
    """Запускает установленный Chrome; fallback — встроенный Chromium."""
    try:
        return pw.chromium.launch(channel="chrome", headless=not headed)
    except Exception:
        log("! Chrome не найден, пробую Chromium (playwright install chromium)")
        return pw.chromium.launch(headless=not headed)


def new_context(browser, headed):
    return browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        locale="ru-RU",
        accept_downloads=True,
    )


def slugify(title, maxlen=60):
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", title)
    s = re.sub(r"\s+", " ", s).strip().strip(".")
    return s[:maxlen].strip() or "topic"


def vk_max_size_url(thumb):
    """URL превью содержит as=<все размеры>&cs=<текущий> — переписываем cs= на максимум."""
    m = re.search(r"as=([0-9x,]+)", thumb)
    if not m:
        return thumb
    sizes = [tuple(map(int, s.split("x"))) for s in m.group(1).split(",")]
    w, h = max(sizes, key=lambda s: s[0] * s[1])
    return re.sub(r"cs=\d+x\d+", f"cs={w}x{h}", thumb)


def clean_doc_name(raw):
    """'Файл сотворение.pdfФайл PDF, 999 КБ' -> 'сотворение.pdf' (и англ. вариант)."""
    s = re.sub(r"^(File|Файл)\s*", "", raw).strip()
    m = re.match(rf".+?\.(?:{KNOWN_EXT})", s, re.IGNORECASE)
    if m:
        return m.group(0)
    s = re.sub(r"\.?(?:File|Файл)\s+\S+.*$", "", s).strip().rstrip(".")
    return s or None


def scrape_posts(page, topic_url, offsets):
    page.goto(topic_url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(1500)
    return page.evaluate(JS_EXTRACT_POSTS, offsets)


def scrape_all_posts(page, topic_url, retries=4):
    """Читает тему постранично; на пустой странице (рейт-лимит VK) — пауза и повтор."""
    posts = []
    offset = 0
    empty_attempts = 0
    while True:
        chunk = scrape_posts(page, topic_url, [offset])
        if not chunk:
            empty_attempts += 1
            if empty_attempts > retries:
                if not posts:
                    raise RuntimeError("не удалось получить посты (блокировка VK?)")
                log(f"   ! offset={offset} не отдался после {retries} попыток, обрываю тему")
                break
            wait = 30 * empty_attempts
            log(f"   пустая страница (offset={offset}), пауза {wait}с — рейт-лимит VK...")
            time.sleep(wait)
            continue
        empty_attempts = 0
        posts.extend(chunk)
        if len(chunk) < 20:
            break
        offset += 20
        time.sleep(1.5)
    return posts


def resolve_photos(page, photo_hrefs):
    photo_map = page.evaluate(JS_RESOLVE_PHOTOS, photo_hrefs)
    failed = [h for h in photo_hrefs if not photo_map.get(h)]
    if failed:
        log(f"   retry через навигацию: {len(failed)} фото")
        for i, href in enumerate(failed, 1):
            url = "https://m.vk.ru" + href if href.startswith("/") else href
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(400)
                photo_map[href] = page.evaluate(JS_PHOTO_FROM_DOM)
            except Exception:
                photo_map[href] = None
            if i % 10 == 0 or i == len(failed):
                log(f"   retry {i}/{len(failed)}")
    return photo_map


def resolve_doc_urls(page, docs):
    """m.vk.ru/doc...?dl=... редиректит на прямой CDN-URL (psv4.../Имя.pdf).
    Навигация — самый надёжный способ: fetch из страницы блочится CORS-редиректом."""
    resolved = {}
    for i, d in enumerate(docs, 1):
        url = "https://m.vk.ru" + d["href"] if d["href"].startswith("/") else d["href"]
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(400)
            final = page.url
            resolved[d["href"]] = final if ("psv" in final or "vkuserphoto" in final) else None
        except Exception:
            final = page.url
            resolved[d["href"]] = final if ("psv" in final or "vkuserphoto" in final) else None
        if not resolved[d["href"]]:
            log(f"   док {i}/{len(docs)}: FAIL")
    return resolved


def download_photos(photo_map, out_dir):
    ph_dir = out_dir / "photos"
    ph_dir.mkdir(parents=True, exist_ok=True)
    ok, fail = 0, 0
    items = [(h, u) for h, u in photo_map.items() if u]
    for i, (href, url) in enumerate(items, 1):
        m = re.search(r"photo(-?\d+_\d+)", href)
        fname = f"{m.group(1)}.jpg" if m else f"photo_{i:04d}.jpg"
        target = ph_dir / fname
        if target.exists() and target.stat().st_size > 0:
            ok += 1
            continue
        try:
            last_err = None
            for attempt in range(3):
                try:
                    r = requests.get(url, headers=UA, timeout=60)
                    r.raise_for_status()
                    target.write_bytes(r.content)
                    last_err = None
                    break
                except Exception as e:
                    last_err = e
                    time.sleep(5 * (attempt + 1))
            if last_err:
                raise last_err
            ok += 1
            if i % 50 == 0 or i == len(items):
                log(f"   фото {i}/{len(items)}")
            time.sleep(0.15)
        except Exception as e:
            log(f"   ОШИБКА фото {href}: {e}")
            fail += 1
    return ok, fail


def download_docs(doc_url_map, docs, out_dir):
    """Качает документы с прямых CDN-ссылок (psv4 работает без кук)."""
    dl_dir = out_dir / "docs"
    dl_dir.mkdir(parents=True, exist_ok=True)
    saved = {}
    used = {}  # fname.lower() -> href, для детерминированного разрешения коллизий имён
    for i, d in enumerate(docs, 1):
        url = doc_url_map.get(d["href"])
        if not url:
            saved[d["href"]] = None
            continue
        ext = Path(url.split("?")[0]).suffix or ".bin"
        base = d.get("suggested") or f"doc_{d.get('did', i)}"
        fname = base if base.lower().endswith(ext.lower()) else base + ext
        if fname.lower() in used and used[fname.lower()] != d["href"]:
            fname = f"{Path(fname).stem}_{d.get('did', i)}{ext}"
        used[fname.lower()] = d["href"]
        target = dl_dir / fname
        if target.exists() and target.stat().st_size > 0:
            saved[d["href"]] = target.name
            continue
        try:
            r = requests.get(url, headers=UA, timeout=180)
            r.raise_for_status()
            target.write_bytes(r.content)
            saved[d["href"]] = target.name
            log(f"   док {target.name} ({len(r.content) // 1024} KB)")
            time.sleep(0.2)
        except Exception as e:
            log(f"   ОШИБКА документа {base}: {e}")
            saved[d["href"]] = None
    return saved


def build_index(posts, photo_map, doc_map, out_dir, title, topic_url):
    """Собирает читабельный index.html по всему обсуждению."""
    def photo_file(href):
        m = re.search(r"photo(-?\d+_\d+)", href)
        return f"photos/{m.group(1)}.jpg" if m else None

    parts = [
        "<!DOCTYPE html><html lang='ru'><head><meta charset='utf-8'>",
        f"<title>{html_mod.escape(title)}</title><style>",
        "body{font-family:system-ui,sans-serif;max-width:860px;margin:20px auto;padding:0 16px;background:#f7f7f7}",
        ".post{background:#fff;border-radius:10px;padding:16px;margin:14px 0;box-shadow:0 1px 3px rgba(0,0,0,.08)}",
        ".meta{color:#888;font-size:13px;margin-bottom:8px}",
        ".text{white-space:pre-wrap;line-height:1.5}",
        "img{max-width:100%;border-radius:8px;margin:6px 0;display:block}",
        ".doc,.vid{display:inline-block;margin:4px 8px 4px 0;font-size:14px}",
        "h1{font-size:20px} a{color:#2678b8}",
        "</style></head><body>",
        f"<h1>{html_mod.escape(title)}</h1><p><a href='{topic_url}'>{topic_url}</a> · постов: {len(posts)}</p>",
    ]
    for n, p in enumerate(posts, 1):
        parts.append("<div class='post'>")
        parts.append(f"<div class='meta'>#{n} · <b>{html_mod.escape(p['author'])}</b> · {html_mod.escape(p['date'])}</div>")
        if p["text"]:
            parts.append(f"<div class='text'>{html_mod.escape(p['text'])}</div>")
        for ph in p["photos"]:
            f = photo_file(ph["href"])
            if f and photo_map.get(ph["href"]):
                parts.append(f"<a href='{f}'><img src='{f}' loading='lazy'></a>")
        for d in p["docs"]:
            local = doc_map.get(d["href"])
            name = html_mod.escape(clean_doc_name(d.get("name", "")) or "document")
            if local:
                parts.append(f"<a class='doc' href='docs/{html_mod.escape(local)}'>📄 {name}</a>")
            else:
                parts.append(f"<a class='doc' href='https://m.vk.ru{d['href']}'>📄 {name} (vk)</a>")
        for v in p["videos"]:
            label = html_mod.escape(v.get("label") or "video")
            parts.append(f"<a class='vid' href='https://m.vk.ru{v['href']}'>🎬 {label}</a>")
        parts.append("</div>")
    parts.append("</body></html>")
    (out_dir / "index.html").write_text("\n".join(parts), encoding="utf-8")


def process_topic(page, topic_url, out_dir, title, with_videos=False):
    """Полный дамп одной темы в out_dir. Возвращает статистику."""
    out_dir.mkdir(parents=True, exist_ok=True)

    posts = scrape_all_posts(page, topic_url)
    if not posts:
        raise RuntimeError("не удалось получить посты (блокировка VK?)")
    log(f"   постов: {len(posts)}")

    # прямые ссылки на фото уже есть в превью (thumb) — резолв без лишних запросов
    photo_map = {}
    for p in posts:
        for ph in p["photos"]:
            if ph.get("thumb"):
                photo_map[ph["href"]] = vk_max_size_url(ph["thumb"])
    missing = sorted({ph["href"] for p in posts for ph in p["photos"]} - set(photo_map))
    if missing:
        log(f"   резолв {len(missing)} фото без превью...")
        photo_map.update(resolve_photos(page, missing))

    docs = []
    seen = set()
    for p in posts:
        for d in p["docs"]:
            if d["href"] not in seen:
                seen.add(d["href"])
                did_m = re.search(r"doc(-?\d+_\d+)", d["href"])
                did = did_m.group(1) if did_m else str(len(seen))
                nm = clean_doc_name(d["name"])
                # безымянные вложения вида 'JPG⋅145 КБ' -> doc_<id>
                if not nm or re.fullmatch(
                        rf"(?i)(?:(?:{KNOWN_EXT})|file|файл|[\d\s.,⋅кбмгКБМГbB])+", nm):
                    nm = f"doc_{did}"
                docs.append({"href": d["href"], "suggested": nm, "did": did})
    videos = [(p["id"], v) for p in posts for v in p["videos"]]
    log(f"   фото: {len(photo_map)}, документов: {len(docs)}, видео: {len(videos)}")

    doc_url_map = resolve_doc_urls(page, docs) if docs else {}

    ok, fail = download_photos(photo_map, out_dir)
    if fail:
        log(f"   фото: {ok} ок, {fail} ошибок")
    doc_map = download_docs(doc_url_map, docs, out_dir) if docs else {}

    if videos:
        with open(out_dir / "videos.txt", "w", encoding="utf-8") as f:
            for pid, v in videos:
                f.write(f"post {pid}\thttps://vk.com{v['href'].split('?')[0]}\t{v['label']}\n")
        if with_videos:
            import shutil, subprocess
            if shutil.which("yt-dlp"):
                vd = out_dir / "videos"
                vd.mkdir(exist_ok=True)
                for pid, v in videos:
                    u = "https://vk.com" + v["href"].split("?")[0]
                    subprocess.run(["yt-dlp", "-o", str(vd / f"{pid}_%(title)s.%(ext)s"), u])
            else:
                log("! yt-dlp не установлен, видео пропускаю (ссылки в videos.txt)")

    payload = {"topic": topic_url, "title": title, "posts": posts,
               "photo_urls": photo_map, "doc_urls": doc_url_map, "doc_files": doc_map}
    (out_dir / "posts.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    build_index(posts, photo_map, doc_map, out_dir, title, topic_url)
    (out_dir / ".done").write_text("ok", encoding="utf-8")
    return {"posts": len(posts), "photos": sum(1 for u in photo_map.values() if u),
            "docs": sum(1 for v in doc_map.values() if v), "videos": len(videos)}


def build_board_index(results, out_dir, board_url):
    parts = [
        "<!DOCTYPE html><html lang='ru'><head><meta charset='utf-8'>",
        "<title>Дамп доски обсуждений VK</title><style>",
        "body{font-family:system-ui,sans-serif;max-width:860px;margin:20px auto;padding:0 16px}",
        "a{color:#2678b8;text-decoration:none} li{margin:6px 0} .n{color:#999;font-size:13px}",
        "</style></head><body>",
        f"<h1>Дамп доски VK</h1><p><a href='{board_url}'>{board_url}</a></p><ol>",
    ]
    for r in results:
        if r["status"] == "ok":
            parts.append(f"<li><a href='{r['folder']}/index.html'>{html_mod.escape(r['title'])}</a> "
                         f"<span class='n'>постов {r['posts']}, фото {r['photos']}, док {r['docs']}, видео {r['videos']}</span></li>")
        else:
            parts.append(f"<li>{html_mod.escape(r['title'])} <span class='n'>ОШИБКА: {html_mod.escape(r.get('error',''))}</span></li>")
    parts.append("</ol></body></html>")
    (out_dir / "index.html").write_text("\n".join(parts), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="Скачать тему или всю доску обсуждений VK")
    ap.add_argument("url", help="URL темы (topic-<g>_<id>) или доски (board<g>)")
    ap.add_argument("-o", "--out", default="vk_dump", help="каталог результата")
    ap.add_argument("--headed", action="store_true", help="показывать окно браузера")
    ap.add_argument("--force", action="store_true", help="игнорировать метки .done")
    ap.add_argument("--videos", action="store_true", help="скачать видео через yt-dlp (если установлен)")
    ap.add_argument("--delay", type=float, default=8.0, help="пауза между темами, сек (рейт-лимит VK)")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    tm = TOPIC_RE.search(args.url)
    bm = BOARD_RE.search(args.url)
    if not tm and not bm:
        sys.exit("Не похоже на ссылку темы (topic-...) или доски (board...) VK")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        browser = launch_browser(pw, args.headed)
        ctx = new_context(browser, args.headed)
        page = ctx.new_page()

        if tm:  # --- одна тема ---
            group_id, topic_id = tm.groups()
            topic_url = f"https://m.vk.ru/topic-{group_id}_{topic_id}"
            try:
                stats = process_topic(page, topic_url, out_dir, f"topic-{group_id}_{topic_id}", args.videos)
            except RuntimeError as e:
                if args.headed:
                    raise
                log(f"! {e} — перезапускаю с окном браузера...")
                ctx.close(); browser.close()
                browser = launch_browser(pw, True)
                ctx = new_context(browser, True)
                page = ctx.new_page()
                stats = process_topic(page, topic_url, out_dir, f"topic-{group_id}_{topic_id}", args.videos)
            log(f"\nГотово: {stats}. Открой {out_dir / 'index.html'}")

        else:  # --- вся доска ---
            group_id = bm.group(1)
            board_url = f"https://m.vk.ru/board{group_id}"
            page.goto(board_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1500)
            topics = page.evaluate(JS_BOARD_TOPICS.replace("GID", group_id))
            if not topics and not args.headed:
                log("! Доска не отдалась headless, перезапускаю с окном браузера...")
                ctx.close(); browser.close()
                browser = launch_browser(pw, True)
                ctx = new_context(browser, True)
                page = ctx.new_page()
                page.goto(board_url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(1500)
                topics = page.evaluate(JS_BOARD_TOPICS.replace("GID", group_id))
            if not topics:
                sys.exit("Не удалось получить список тем доски.")
            log(f"Тем на доске: {len(topics)}")

            results = []
            for i, t in enumerate(topics, 1):
                tid_m = TOPIC_RE.search(t["href"])
                tid = tid_m.group(2) if tid_m else str(i)
                folder = f"{i:02d}_{tid}_{slugify(t['title'])}"
                tdir = out_dir / folder
                if (tdir / ".done").exists() and not args.force:
                    log(f"[{i}/{len(topics)}] пропуск (готово): {t['title']}")
                    try:
                        old = json.loads((tdir / "posts.json").read_text(encoding="utf-8"))
                        results.append({"title": t["title"], "folder": folder, "status": "ok",
                                        "posts": len(old.get("posts", [])),
                                        "photos": sum(1 for u in old.get("photo_urls", {}).values() if u),
                                        "docs": sum(1 for v in old.get("doc_files", {}).values() if v),
                                        "videos": sum(len(p.get("videos", [])) for p in old.get("posts", []))})
                    except Exception:
                        results.append({"title": t["title"], "folder": folder, "status": "ok",
                                        "posts": "?", "photos": "?", "docs": "?", "videos": "?"})
                    continue
                log(f"[{i}/{len(topics)}] {t['title']} ({t['count']})")
                try:
                    stats = process_topic(page, f"https://m.vk.ru{t['href']}", tdir, t["title"], args.videos)
                    results.append({"title": t["title"], "folder": folder, "status": "ok", **stats})
                    time.sleep(args.delay)  # пауза между темами — рейт-лимит VK
                except Exception as e:
                    log(f"   ОШИБКА темы: {e}")
                    results.append({"title": t["title"], "folder": folder, "status": "error", "error": str(e)})
                    (tdir / ".done").unlink(missing_ok=True)
                    page.goto(board_url, wait_until="domcontentloaded", timeout=60000)  # вернуться на доску
                    page.wait_for_timeout(1000)

            build_board_index(results, out_dir, board_url)
            ok_n = sum(1 for r in results if r["status"] == "ok")
            log(f"\nГотово: {ok_n}/{len(topics)} тем. Открой {out_dir / 'index.html'}")

        ctx.close()
        browser.close()


if __name__ == "__main__":
    main()
