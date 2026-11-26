#!/usr/bin/env python3
"""
bookpages - build a small static website with one page per mini book.
Each page shows the book's details and buttons for the audiobook services that have it.
Put the printed address of a page on an NFC tag; tapping the book opens that page.

Needs only Python 3 (Pillow is optional, used to shrink cover images).

  python bookpages.py books.csv --out site --base-url https://yourname.github.io/shelf

Project layout:
  bookpages.py        this script (CSV -> site)
  strings.py          page texts (en / de)
  templates/          HTML templates ($placeholders), partials/ for reusable chunks
  static/style.css    stylesheet (a style.css next to this script also works)
  static/js/          browser scripts, copied to <out>/js/

Optional: for services where no direct link is known yet, add an outlined "Search on <service>" button.
Storytel and BookBeat search buttons are built in; add or change one by giving the service's search
address with {q} where the title belongs (copy it from your browser), or use --no-search-links:

  --search-link audible="https://www.audible.de/search?keywords={q}" --search-link storytel="..." --search-link bookbeat="..."

Then upload the 'site' folder's contents to GitHub (Settings > Pages > deploy from branch).

CSV columns (header row; only 'title' is required; extra columns such as the ones the sticker
tool uses - front, size, trim - are fine):

  title, author, publisher       shown on the page
  description                    1-3 sentences (optional)
  finished                       e.g. 2026-03-12 or 2026-03 (optional)
  audible, storytel, bookbeat    full https:// link to the book at that service (optional)
  audible_status, storytel_status, bookbeat_status   what the subscription situation is (optional):
                                   included = listen as part of the subscription
                                   credit   = Audible only: can be your monthly pick, or bought
                                   buy      = purchase only
                                   none     = checked, not available on that service
                                   (blank = not checked, nothing is shown)
  audible_note, storytel_note, bookbeat_note         short extra remark shown under the button (optional)
  checked                        date links / plan info were last checked (optional)
  front                          cover image path, relative to the CSV (optional; shrunk to a thumbnail)
  trim                           same crop box the sticker tool uses: left,top,right,bottom (optional)
  id                             fixed page id (optional; normally generated and remembered)

Page ids are generated once and stored in <csv name>.ids.json next to the CSV. KEEP THAT FILE:
it makes sure a book keeps the same address, so tags you already wrote stay valid.
Also written next to the CSV: tag_urls.csv (id, title, author, url) - the list to program onto tags.
"""
import argparse
import csv
import hashlib
import html
import json
import os
import re
import secrets
import shutil
import sys
import unicodedata
import urllib.parse
from pathlib import Path
from string import Template

from strings import MONTHS, STRINGS

# ------------------------------------------------------------------ settings
BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

SERVICES = [("audible", "Audible"), ("storytel", "Storytel"), ("bookbeat", "BookBeat")]
STATUSES = {"included", "credit", "buy", "none"}

# Search addresses copied from the services' own websites ({q} = book title).
# Add audible=... once confirmed.
DEFAULT_SEARCH = {
    "storytel": "https://www.storytel.com/de/search/all?query={q}&formats=abook",
    "bookbeat": "https://www.bookbeat.com/de/search?query={q}",
}

ID_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no look-alike characters (i l o 0 1)
ID_LENGTH = 5
COVER_WIDTH = 420

ZXING_SCRIPT = "https://unpkg.com/@zxing/browser@0.2.1"

# Scripts for the index page, in load order (later ones use globals of earlier ones).
INDEX_SCRIPTS = [
    "config", "search", "book-dialog", "turnstile", "overlay", "book-entries",
    "isbn-lookup", "barcode-scanner", "processing-monitor", "submit",
    "admin-actions",
]
BOOK_SCRIPTS = ["config", "admin-actions"]
ADMIN_SCRIPTS = ["config", "admin"]

ARCHIVE_SCRIPTS = ["search", "config", "admin-actions"]


# ------------------------------------------------------------------ helpers
def esc(s):
    return html.escape(s or "", quote=True)


def render(name, **context):
    """Fill a template from templates/ ($name or ${name} placeholders)."""
    text = (TEMPLATE_DIR / name).read_text(encoding="utf-8")
    return Template(text).safe_substitute(context)


def script_tags(names, external=(), root=""):
    tags = [f'<script src="{url}"></script>' for url in external]
    tags += [f'<script src="{root}static/js/{name}.js"></script>' for name in names]
    return "\n".join(tags)


def norm_key(title, author):
    t = unicodedata.normalize("NFKC", f"{title}|{author}").casefold()
    return re.sub(r"\s+", " ", t).strip()


def normalize_row(raw):
    return {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}


class IdStore:
    """Remembers which page id belongs to which book, so addresses never change."""

    def __init__(self, path):
        self.path, self.ids = path, {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                self.ids = json.load(f).get("ids", {})

    def get(self, key, explicit=""):
        if explicit:
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,23}", explicit):
                raise ValueError(f"id '{explicit}' must be 3-24 chars: a-z, 0-9, dash")
            owner = next((k for k, v in self.ids.items() if v == explicit and k != key), None)
            if owner:
                raise ValueError(f"id '{explicit}' is already used by another book")
            self.ids[key] = explicit
            return explicit

        if key not in self.ids:
            used = set(self.ids.values())
            while True:
                new = "".join(secrets.choice(ID_ALPHABET) for _ in range(ID_LENGTH))
                if new not in used:
                    break
            self.ids[key] = new
        return self.ids[key]

    def save(self):
        data = {"note": "Keep this file! It keeps every book's page address stable.",
                "ids": self.ids}
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)


def fmt_date(s, lang):
    s = (s or "").strip()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})(?:-(\d{1,2}))?", s)
    if not m:
        return s
    year, month, day = int(m.group(1)), int(m.group(2)), m.group(3)
    if not 1 <= month <= 12:
        return s
    name = MONTHS[lang][month - 1]
    if day:
        return f"{int(day)}. {name} {year}" if lang == "de" else f"{int(day)} {name} {year}"
    return f"{name} {year}"


def clean_link(url, service, warnings, title):
    url = (url or "").strip()
    if not url:
        return None
    if not re.match(r"https?://", url, re.I):
        if " " not in url and "." in url:
            url = "https://" + url
        else:
            warnings.append(f"'{title}': {service} link ignored (not a web address): {url[:40]}")
            return None
    if service not in url.lower():
        warnings.append(f"'{title}': the {service} link does not contain '{service}' "
                        "- mixed-up columns?")
    return url


def parse_trim(s):
    s = (s or "").strip()
    if not s:
        return None
    parts = [int(float(x)) for x in re.split(r"[,; ]+", s) if x]
    return tuple(parts) if len(parts) == 4 else None


def make_cover(path, dest, trim):
    from PIL import Image

    im = Image.open(path)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        background = Image.new("RGBA", im.size, (255, 255, 255, 255))
        background.alpha_composite(im)
        im = background
    im = im.convert("RGB")
    if trim:
        im = im.crop(trim)
    if im.width > COVER_WIDTH:
        im = im.resize((COVER_WIDTH, round(im.height * COVER_WIDTH / im.width)), Image.LANCZOS)
    im.save(dest, "JPEG", quality=82, optimize=True)


def hue_for(book_id):
    return 205 + int(hashlib.md5(book_id.encode()).hexdigest(), 16) % 40  # blues only


# ------------------------------------------------------------------ html fragments
def cover_html(b, prefix=""):
    """prefix = path from the current page to the book's folder
    ('' on the book page, 'id/' on the index)."""
    if b["has_cover"]:
        return (f'<div class="cover"><img src="{prefix}cover.jpg" alt="" '
                f'loading="lazy" width="112" height="168"></div>')
    initial = esc((b["title"].strip()[:1] or "?").upper())
    return (f'<div class="cover tile" style="--h:{hue_for(b["id"])}" aria-hidden="true">'
            f'<span>{initial}</span></div>')


def link_button(text, href, sub="", ghost=False):
    css = "btn ghost" if ghost else "btn"
    small = f"<small>{esc(sub)}</small>" if sub else ""
    return (f'<a class="{css}" href="{esc(href)}">'
            f'<span class="lbl"><span>{esc(text)}</span>{small}</span>'
            f'<span class="arr" aria-hidden="true">&rarr;</span></a>')


def status_text(status, note, S):
    parts = [S["st_" + status]] if status in ("included", "credit", "buy") else []
    if note:
        parts.append(note)
    return " \u00b7 ".join(parts)


def listen_section(b, S, search):
    buttons = [link_button(S["listen_on"].format(s=name), url, status_text(st, note, S))
               for name, url, st, note in b["links"]]

    # "Search on ..." fallback for services that were neither linked nor marked unavailable
    known = {name for name, *_ in b["links"]} | set(b["absent"])
    query = urllib.parse.quote_plus(b["title"].strip())  # title only: author spellings differ
    for svc, label in SERVICES:
        if label not in known and search and svc in search:
            buttons.append(link_button(S["search_on"].format(s=label),
                                       search[svc].replace("{q}", query),
                                       S["st_unchecked"], ghost=True))

    unavailable = "".join(
        f'<div class="btn off"><span>{esc(S["none_on"].format(s=n))}</span></div>'
        for n in b["absent"])
    heading = f'<h2>{esc(S["listen"])}</h2>'

    if b["links"]:
        return f'{heading}<div class="btns">{"".join(buttons)}{unavailable}</div>'

    extra = "".join(buttons) + unavailable
    empty = f'<div class="empty">{esc(S["none"])}</div>'
    extra_html = f'<div class="btns" style="margin-top:10px">{extra}</div>' if extra else ""
    return heading + empty + extra_html



def book_card(b, S, archived=False):
    if b.get("processing"):
        tag, href = "div", ""
        badge = '<span class="badge processing-badge">Processing…</span>'
    else:
        tag, href = "a", f' href="{b["id"]}/"'
        badge = ""

    search_text = esc((b["title"] + " " + b["author"]).lower())

    return (
        f'<div class="book-card-wrapper">'
        f'<{tag} class="cardlink"{href} data-s="{search_text}">'
        f'{cover_html(b, b["id"] + "/")}'
        f'<div class="t">{esc(b["title"])}</div>'
        f'<div class="a">{esc(b["author"])}</div>{badge}'
        f'</{tag}>'
        f'<div class="admin-book-actions" hidden>'
        f'<button type="button" data-admin-action="{"restore" if archived else "archive"}" data-book-id="{b["id"]}">'
        f'{"Restore" if archived else "Archive"}'
        f'</button>'
        f'</div>'
        f'</div>'
    )



# ------------------------------------------------------------------ pages
def page(title, body, site, lang, root="", scripts=""):
    return render("base.html", lang=lang, title=esc(title), site=esc(site),
                  root=root, body=body, scripts=scripts)


def book_page(b, site, S, lang, with_index, search=None, archived=False):
    description = "".join(f'<p class="desc">{esc(p.strip())}</p>'
                          for p in re.split(r"\r?\n+", b["description"]) if p.strip())
    chip = ""
    if b["finished"]:
        chip = (f'<span class="chip">'
                f'{esc(S["finished"].format(d=fmt_date(b["finished"], lang)))}</span>')
    checked = ""
    if b["checked"] and (b["links"] or b["absent"]):
        checked = (f'<p class="foot">'
                   f'{esc(S["checked"].format(d=fmt_date(b["checked"], lang)))}</p>')

    manage = (
        f'<div class="admin-book-actions book-manage-row" hidden>'
        f'<button type="button" class="manage-refresh" data-admin-action="refresh" data-book-id="{b["id"]}">Refresh links</button>'
        f'<button type="button" class="manage-{"restore" if archived else "archive"}" '
        f'data-admin-action="{"restore" if archived else "archive"}" '
        f'data-book-id="{b["id"]}">'
        f'{"Restore" if archived else "Archive"}</button>'
f'<button type="button" class="manage-delete" data-admin-action="delete" data-book-id="{b["id"]}">Delete</button>'
        f'</div>'
    )

    body = render(
        "book.html",
        site=esc(site),
        cover=cover_html(b),
        title=esc(b["title"]),
        author=f'<p class="author">{esc(b["author"])}</p>' if b["author"] else "",
        publisher=f'<p class="pub">{esc(b["publisher"])}</p>' if b["publisher"] else "",
        chip=chip,
        description=description,
        listen=listen_section(b, S, search),
        checked=checked,
        back=(
            f'<p class="back-link-row book-back-row">'
            f'<a class="back-library-link" href="../">← Back to library</a>'
            f'</p>'
            if with_index else ""

        ),
        manage=manage,
    )
    return page(
        b["title"], body, site, lang,
        root="../",
        scripts=(script_tags(BOOK_SCRIPTS, root="../"))
    )


def index_page(books, site, S, lang):
    cards = [render("partials/add_book_card.html")] + [book_card(b, S) for b in books]
    body = render(
        "index.html",
        site=esc(site),
        search=esc(S["search"]),
        count_template=esc(S["count"]),
        count=esc(S["count"].format(n=len(books))),
        book_form=render("partials/book_form.html"),
        cards="".join(cards),
    )
    return page(S["all"], body, site, lang,
                scripts=script_tags(INDEX_SCRIPTS, external=[ZXING_SCRIPT]))

def admin_page(site, S, lang):
    body = render("admin.html", site=esc(site))
    return page(
        "Administration",
        body,
        site,
        lang,
        scripts=script_tags(ADMIN_SCRIPTS),
    )

def archive_page(books, site, S, lang):
    body = render(
        "archive.html",
        site=esc(site),
        search=esc(S["search"]),
        count_template=esc(S["count"]),
        count=esc(S["count"].format(n=len(books))),
        cards="".join(book_card(b, S, archived=True) for b in books),
    )
    return page("Archived books", body, site, lang, scripts=script_tags(ARCHIVE_SCRIPTS))


def message_page(title, text, site, S, lang):
    body = render("message.html", site=esc(site), title=esc(title), text=esc(text))
    return page(title, body, site, lang)


# ------------------------------------------------------------------ build steps
def read_csv_rows(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def parse_links(row, title, warnings):
    """Returns (links, absent): links = [(label, url, status, note)], absent = [label]."""
    links, absent = [], []
    for svc, label in SERVICES:
        url = clean_link(row.get(svc, ""), svc, warnings, title)
        status = row.get(svc + "_status", "").lower()
        if status and status not in STATUSES:
            warnings.append(f"'{title}': {svc}_status '{status}' ignored "
                            f"(use: {', '.join(sorted(STATUSES))})")
            status = ""
        note = row.get(svc + "_note", "")
        if url:
            links.append((label, url, "" if status == "none" else status, note))
        elif status == "none":
            absent.append(label)
    return links, absent


def load_books(rows, store, warnings, out, covers_dir, use_covers, missing_covers):
    books, seen = [], {}
    have_pil = _pillow_available(use_covers, warnings)

    for line, raw in enumerate(rows, start=2):
        row = normalize_row(raw)
        title = row.get("title", "")
        if not title:
            continue

        key = norm_key(title, row.get("author", ""))
        if key in seen:
            warnings.append(f"row {line}: '{title}' appears twice - the first entry is used.")
            continue
        try:
            book_id = store.get(key, row.get("id", ""))
        except ValueError as e:
            sys.exit(f"row {line}: {e}")
        seen[key] = book_id

        links, absent = parse_links(row, title, warnings)
        book = dict(
            id=book_id, title=title, author=row.get("author", ""),
            publisher=row.get("publisher", ""), description=row.get("description", ""),
            finished=row.get("finished", ""), checked=row.get("checked", ""),
            links=links, absent=absent, has_cover=False,
        )

        front = row.get("front", "")
        if use_covers and have_pil and front:
            src = front if os.path.isabs(front) else os.path.join(covers_dir, front)
            if os.path.exists(src):
                os.makedirs(os.path.join(out, book_id), exist_ok=True)
                try:
                    make_cover(src, os.path.join(out, book_id, "cover.jpg"),
                               parse_trim(row.get("trim")))
                    book["has_cover"] = True
                except Exception as e:
                    warnings.append(f"'{title}': cover could not be used ({e})")
            else:
                missing_covers.append(front)
        books.append(book)
    return books, seen


def _pillow_available(use_covers, warnings):
    if not use_covers:
        return True
    try:
        import PIL  # noqa: F401
        return True
    except ImportError:
        warnings.append("Pillow is not installed - covers skipped (pip install pillow).")
        return False


def collect_index_books(books, master_csv, store):
    """Books for the index page: everything in the master CSV. Books that have no page
    yet are shown as 'Processing...' placeholders. Without a master CSV: all built books."""
    if not (master_csv and os.path.exists(master_csv)):
        return list(books)

    built = {norm_key(b["title"], b["author"]): b for b in books}
    index_books = []
    for raw in read_csv_rows(master_csv):
        row = normalize_row(raw)
        title, author = row.get("title", ""), row.get("author", "")
        if not title:
            continue

        key = norm_key(title, author)
        if key in built:
            index_books.append(built[key])
            continue

        index_books.append(dict(
            id=store.get(key, row.get("id", "")), title=title, author=author,
            publisher=row.get("publisher", ""), links=[], absent=[],
            has_cover=False, processing=True,
        ))
    return index_books


def load_archived_ids(archive_file):
    if os.path.exists(archive_file):
        with open(archive_file, encoding="utf-8") as f:
            return set(json.load(f).get("archived", []))
    with open(archive_file, "w", encoding="utf-8") as f:
        json.dump({"archived": []}, f, indent=1)
    return set()


def write_text(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def write_static(out):
    css = STATIC_DIR / "style.css"
    if not css.exists():
        css = BASE_DIR / "style.css"  # legacy location

    static_out = os.path.join(out, "static")
    os.makedirs(static_out, exist_ok=True)

    shutil.copyfile(css, os.path.join(static_out, "style.css"))
    shutil.copytree(STATIC_DIR / "js", os.path.join(static_out, "js"), dirs_exist_ok=True)


def write_tag_urls(path, books, base):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "title", "author", "url"])
        for b in books:
            w.writerow([b["id"], b["title"], b["author"], f"{base}/{b['id']}/"])


# ------------------------------------------------------------------ build
def build(csv_path, out, base_url, site, lang, with_index, covers_dir, use_covers,
          ids_file, tags_out, cname, search=None, master_csv=None, archive_file=None):
    S = STRINGS[lang]
    stem = os.path.splitext(os.path.abspath(csv_path))[0]
    csv_dir = os.path.dirname(os.path.abspath(csv_path))
    covers_dir = covers_dir or csv_dir
    ids_file = ids_file or stem + ".ids.json"
    archive_file = archive_file or stem + ".archive.json"
    tags_out = tags_out or os.path.join(csv_dir, "../tag_urls.csv")

    store, warnings, missing_covers = IdStore(ids_file), [], []

    rows = read_csv_rows(csv_path)
    if not rows or "title" not in {k.strip().lower() for k in rows[0] if k}:
        sys.exit("The CSV needs a header row with at least a 'title' column.")

    # keep the user's CNAME (custom domain) when the folder is rebuilt
    keep_cname = None
    cname_path = os.path.join(out, "CNAME")
    if os.path.exists(cname_path):
        with open(cname_path, encoding="utf-8") as f:
            keep_cname = f.read()
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out)

    books, _ = load_books(rows, store, warnings, out, covers_dir, use_covers, missing_covers)

    index_books = collect_index_books(books, master_csv, store)
    archived_ids = load_archived_ids(archive_file)
    active = [b for b in index_books if b["id"] not in archived_ids]
    archived = [b for b in index_books if b["id"] in archived_ids]

    write_static(out)
    for b in books:
        os.makedirs(os.path.join(out, b["id"]), exist_ok=True)
        write_text(os.path.join(out, b["id"], "index.html"),
                   book_page(b, site, S, lang, with_index, search, archived=b["id"] in archived_ids))

    root = (index_page(active, site, S, lang) if with_index
            else message_page(site, S["landing"], site, S, lang))
    write_text(os.path.join(out, "index.html"), root)
    write_text(os.path.join(out, "admin.html"),
               admin_page(site, S, lang))
    write_text(os.path.join(out, "archived.html"), archive_page(archived, site, S, lang))
    write_text(os.path.join(out, "404.html"),
               message_page(S["nf_t"], S["nf_p"], site, S, lang))
    write_text(os.path.join(out, ".nojekyll"), "")
    write_text(os.path.join(out, "robots.txt"), "User-agent: *\nDisallow: /\n")
    if cname or keep_cname:
        write_text(cname_path, (cname or keep_cname).strip() + "\n")

    store.save()
    base = (base_url or "https://YOUR-NAME.github.io/REPO").rstrip("/")
    write_tag_urls(tags_out, books, base)

    print_summary(books, out, tags_out, ids_file, base, base_url, missing_covers, warnings)


def print_summary(books, out, tags_out, ids_file, base, base_url, missing_covers, warnings):
    with_audio = sum(1 for b in books if b["links"])
    longest = max((len(f"{base}/{b['id']}/") for b in books), default=0)
    print(f"{len(books)} book pages written to {out}/")
    print(f"  with audiobook links: {with_audio}   without: {len(books) - with_audio}")
    print(f"  covers used: {sum(1 for b in books if b['has_cover'])}"
          "   (others get a blue title tile)")
    if missing_covers:
        print(f"  {len(missing_covers)} cover file(s) not found, e.g. {missing_covers[0]}")
    print(f"  tag addresses: {tags_out}  (longest: {longest} characters)")
    print(f"  id memory:     {ids_file}  <- keep and back up this file")
    if not base_url:
        print("  NOTE: no --base-url given, tag_urls.csv contains a placeholder address.")
    for w in warnings:
        print("  warning:", w)


# ------------------------------------------------------------------ command line
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", help="books CSV (same file as for the sticker tool works)")
    ap.add_argument("--out", default="site", help="output folder (default: site)")
    ap.add_argument("--base-url", help="public address of the site, "
                                       "e.g. https://yourname.github.io/shelf")
    ap.add_argument("--site-title", help="name shown in the page header")
    ap.add_argument("--lang", choices=["en", "de"], default="en",
                    help="language of the page texts")
    ap.add_argument("--with-index", action="store_true",
                    help="also build a searchable list of ALL books on the start page "
                         "(makes your list public)")
    ap.add_argument("--master-csv", help="master CSV containing books not yet linked")
    ap.add_argument("--covers-dir",
                    help="folder the 'front' paths are relative to (default: CSV folder)")
    ap.add_argument("--no-covers", action="store_true",
                    help="never publish cover images (use title tiles)")
    ap.add_argument("--ids-file", help="where page ids are remembered (default: <csv>.ids.json)")
    ap.add_argument("--archive-file",
                    help="where archived book IDs are stored (default: <csv>.archive.json)")
    ap.add_argument("--tags-out",
                    help="where to write the tag address list (default: tag_urls.csv)")
    ap.add_argument("--search-link", action="append", default=[], metavar="NAME=URL",
                    help='fallback search button, e.g. '
                         'audible="https://www.audible.de/search?keywords={q}" (repeatable)')
    ap.add_argument("--no-search-links", action="store_true",
                    help="do not add the built-in 'Search on ...' buttons")
    ap.add_argument("--cname", help="custom domain to write into CNAME, e.g. books.example.com")
    a = ap.parse_args()

    search = {} if a.no_search_links else dict(DEFAULT_SEARCH)
    for item in a.search_link:
        name, _, url = item.partition("=")
        if name.strip().lower() not in {k for k, _ in SERVICES} or "{q}" not in url:
            ap.error("--search-link needs NAME=URL with {q} in it, NAME one of: "
                     + ", ".join(k for k, _ in SERVICES))
        search[name.strip().lower()] = url.strip().strip('"')

    build(a.csv, a.out, a.base_url, a.site_title or STRINGS[a.lang]["mini"], a.lang,
          a.with_index, a.covers_dir, not a.no_covers, a.ids_file, a.tags_out, a.cname,
          search, a.master_csv, a.archive_file)


if __name__ == "__main__":
    main()
