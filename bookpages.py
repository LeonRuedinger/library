#!/usr/bin/env python3
"""
bookpages - build a small static website with one page per mini book.
Each page shows the book's details and buttons for the audiobook services that have it.
Put the printed address of a page on an NFC tag; tapping the book opens that page.

Needs only Python 3 (Pillow is optional, used to shrink cover images).

  python bookpages.py books.csv --out site --base-url https://yourname.github.io/shelf

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
import argparse, csv, hashlib, html, json, os, re, secrets, shutil, sys, unicodedata, urllib.parse

# ------------------------------------------------------------------ settings
SERVICES = [("audible", "Audible"), ("storytel", "Storytel"), ("bookbeat", "BookBeat")]
STATUSES = {"included", "credit", "buy", "none"}
# search addresses copied from the services' own websites ({q} = book title); add audible=... once confirmed
DEFAULT_SEARCH = {
    "storytel": "https://www.storytel.com/de/search/all?query={q}&formats=abook",
    "bookbeat": "https://www.bookbeat.com/de/search?query={q}",
}
ID_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"       # no look-alike characters (i l o 0 1)
ID_LENGTH = 5
COVER_WIDTH = 420

STRINGS = {
    "en": dict(listen="Listen", listen_on="Listen on {s}", none="No audiobook link has been added for this book yet.",
               st_included="Included in the subscription", st_credit="Monthly pick or purchase",
               st_buy="Purchase only", none_on="Not available on {s}", search_on="Search on {s}", st_unchecked="Not checked yet",
               checked="Links and plan info checked {d}", finished="Finished {d}", mini="Mini Library",
               landing="Hold your phone to a book to open its page.", nf_t="Book not found",
               nf_p="This page doesn't exist (anymore).", search="Search books", all="All books",
               audio="Audio", count="{n} books"),
    "de": dict(listen="Hörbuch", listen_on="Anhören bei {s}", none="Für dieses Buch ist noch kein Hörbuch-Link hinterlegt.",
               st_included="Im Abo enthalten", st_credit="Als Monatstitel wählbar oder kaufbar",
               st_buy="Nur als Kauf", none_on="Bei {s} nicht verfügbar", search_on="Bei {s} suchen", st_unchecked="Noch nicht geprüft",
               checked="Links und Abo-Infos geprüft am {d}", finished="Gelesen am {d}", mini="Mini-Bibliothek",
               landing="Halte dein Handy an ein Buch, um die Seite zu öffnen.", nf_t="Buch nicht gefunden",
               nf_p="Diese Seite gibt es nicht (mehr).", search="Bücher durchsuchen", all="Alle Bücher",
               audio="Hörbuch", count="{n} Bücher"),
}
MONTHS = {
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December"],
    "de": ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
           "November", "Dezember"],
}

# ------------------------------------------------------------------ stylesheet
CSS = """
:root{
  --bg:#eef3fb; --surface:#ffffff; --text:#0f1b33; --muted:#5a6b8c; --line:#dbe4f3;
  --navy:#0b2a5b; --blue:#1d4ed8; --sky:#38bdf8; --btn:#1d4ed8; --btn-hover:#1e40af; --chip:#e3ecff; --chip-text:#1e3a8a;
  --shadow:0 12px 32px rgba(11,42,91,.14); --radius:20px;
  color-scheme: light dark;
}
@media (prefers-color-scheme: dark){
  :root{ --bg:#0a1226; --surface:#111d3b; --text:#e9efff; --muted:#9fb0d3; --line:#223463;
         --btn:#3b82f6; --btn-hover:#60a5fa; --chip:#1b2c57; --chip-text:#bcd2ff; --shadow:0 12px 32px rgba(0,0,0,.45); }
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;min-height:100vh;min-height:100dvh;background:var(--bg);color:var(--text);
  font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;line-height:1.5;
  padding-bottom:env(safe-area-inset-bottom)}
.top{background:linear-gradient(160deg,var(--navy) 0%,#1740b8 60%,#2563eb 100%);
  padding:calc(18px + env(safe-area-inset-top)) 22px 150px;color:#fff}
.top a,.top span{color:#fff;text-decoration:none;font-size:.78rem;font-weight:700;letter-spacing:.14em;text-transform:uppercase;opacity:.92}
.wrap{max-width:560px;margin:-96px auto 0;padding:0 16px 40px}
.card{background:var(--surface);border-radius:var(--radius);box-shadow:var(--shadow);padding:22px;border:1px solid var(--line)}
.hero{display:grid;grid-template-columns:112px 1fr;gap:18px;align-items:end}
.cover{width:112px;aspect-ratio:2/3;border-radius:6px 12px 12px 6px;overflow:hidden;
  box-shadow:0 10px 24px rgba(11,42,91,.35),inset 5px 0 0 rgba(0,0,0,.16);background:var(--chip);margin-top:-44px}
.cover img{display:block;width:100%;height:100%;object-fit:cover}
.tile{display:grid;place-items:center;color:#fff;font-weight:800;font-size:2.6rem;
  background:linear-gradient(155deg,hsl(var(--h) 78% 46%),hsl(calc(var(--h) + 16) 80% 24%))}
h1{margin:0 0 4px;font-size:clamp(1.3rem,5.2vw,1.8rem);line-height:1.18;letter-spacing:-.01em;overflow-wrap:anywhere}
.author{margin:0;color:var(--muted);font-weight:600}
.pub{margin:2px 0 0;color:var(--muted);font-size:.85rem}
.chip{display:inline-block;margin:14px 0 0;padding:4px 12px;border-radius:999px;background:var(--chip);color:var(--chip-text);
  font-size:.8rem;font-weight:700}
.desc{margin:18px 0 0;color:var(--text)}
.desc+.desc{margin-top:10px}
h2{margin:26px 0 10px;font-size:.78rem;letter-spacing:.14em;text-transform:uppercase;color:var(--muted)}
.btns{display:grid;gap:10px}
.btn{display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:58px;padding:14px 18px;border-radius:14px;
  background:var(--btn);color:#fff;text-decoration:none;font-weight:700;font-size:1.02rem;
  box-shadow:0 6px 16px rgba(29,78,216,.28);transition:transform .12s ease,background .12s ease}
.btn:hover{background:var(--btn-hover)}
.btn:active{transform:scale(.985)}
.btn:focus-visible,.cardlink:focus-visible{outline:3px solid var(--sky);outline-offset:2px}
.btn .arr{font-size:1.25rem;opacity:.9}
.btn .lbl{display:flex;flex-direction:column;line-height:1.25}
.btn small{font-weight:500;font-size:.8rem;opacity:.9;margin-top:2px}
.btn.ghost{background:transparent;color:var(--btn);border:1.5px solid var(--btn);box-shadow:none}
.btn.ghost:hover{background:var(--chip)}
.btn.off{background:transparent;color:var(--muted);border:1.5px dashed var(--line);box-shadow:none;font-weight:600;min-height:48px}
.empty{padding:16px 18px;border:1.5px dashed var(--line);border-radius:14px;color:var(--muted)}
.foot{margin:16px 2px 0;color:var(--muted);font-size:.8rem}
.back{display:inline-block;margin:18px 2px 0;color:var(--blue);font-weight:700;text-decoration:none}
@media (prefers-color-scheme: dark){ .back{color:var(--btn-hover)} }
/* index */
.search{width:100%;padding:14px 16px;border-radius:14px;border:1px solid var(--line);background:var(--surface);color:var(--text);
  font:inherit;margin:0 0 6px}
.count{margin:10px 2px 14px;color:var(--muted);font-size:.85rem}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(132px,1fr));gap:16px}
.cardlink{display:block;text-decoration:none;color:inherit}
.cardlink .cover{width:100%;margin:0 0 8px}
.cardlink .t{font-weight:700;font-size:.92rem;line-height:1.25;overflow-wrap:anywhere}
.cardlink .a{color:var(--muted);font-size:.8rem}
.badge{display:inline-block;margin-top:4px;padding:1px 8px;border-radius:999px;background:var(--chip);color:var(--chip-text);font-size:.7rem;font-weight:700}
.center{text-align:center;padding:30px 10px}
"""

INDEX_JS = """
const q=document.getElementById('q'),items=[...document.querySelectorAll('[data-s]')],n=document.getElementById('n');
q.addEventListener('input',()=>{const v=q.value.trim().toLowerCase();let c=0;
  items.forEach(e=>{const m=!v||e.dataset.s.includes(v);e.hidden=!m;if(m)c++});n.textContent=n.dataset.t.replace('{n}',c)});
"""


# ------------------------------------------------------------------ helpers
def esc(s):
    return html.escape(s or "", quote=True)


def norm_key(title, author):
    t = unicodedata.normalize("NFKC", f"{title}|{author}").casefold()
    return re.sub(r"\s+", " ", t).strip()


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
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"note": "Keep this file! It keeps every book's page address stable.", "ids": self.ids},
                      f, ensure_ascii=False, indent=1, sort_keys=True)


def fmt_date(s, lang):
    s = (s or "").strip()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})(?:-(\d{1,2}))?", s)
    if not m:
        return s
    y, mo, d = int(m.group(1)), int(m.group(2)), m.group(3)
    if not 1 <= mo <= 12:
        return s
    name = MONTHS[lang][mo - 1]
    if d:
        return f"{int(d)}. {name} {y}" if lang == "de" else f"{int(d)} {name} {y}"
    return f"{name} {y}"


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
        warnings.append(f"'{title}': the {service} link does not contain '{service}' - mixed-up columns?")
    return url


def parse_trim(s):
    s = (s or "").strip()
    if not s:
        return None
    p = [int(float(x)) for x in re.split(r"[,; ]+", s) if x]
    return tuple(p) if len(p) == 4 else None


def make_cover(path, dest, trim):
    from PIL import Image
    im = Image.open(path)
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255)); bg.alpha_composite(im); im = bg
    im = im.convert("RGB")
    if trim:
        im = im.crop(trim)
    if im.width > COVER_WIDTH:
        im = im.resize((COVER_WIDTH, round(im.height * COVER_WIDTH / im.width)), Image.LANCZOS)
    im.save(dest, "JPEG", quality=82, optimize=True)


def hue_for(book_id):
    return 205 + int(hashlib.md5(book_id.encode()).hexdigest(), 16) % 40      # blues only


def cover_html(b, prefix=""):
    """prefix = path from the current page to the book's folder ('' on the book page, 'id/' on the index)."""
    if b["has_cover"]:
        return f'<div class="cover"><img src="{prefix}cover.jpg" alt="" loading="lazy" width="112" height="168"></div>'
    initial = esc((b["title"].strip()[:1] or "?").upper())
    return f'<div class="cover tile" style="--h:{hue_for(b["id"])}" aria-hidden="true"><span>{initial}</span></div>'


# ------------------------------------------------------------------ page templates
def head(title, site, S, lang, css_path, noindex=True):
    robots = '<meta name="robots" content="noindex,nofollow">' if noindex else ""
    return f"""<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light dark">
<meta name="theme-color" content="#0b2a5b">
{robots}
<title>{esc(title)} · {esc(site)}</title>
<link rel="stylesheet" href="{css_path}style.css">
</head>"""


def book_page(b, site, S, lang, with_index, search=None):
    paras = "".join(f'<p class="desc">{esc(p.strip())}</p>' for p in re.split(r"\r?\n+", b["description"]) if p.strip())
    def status_text(st, note):
        parts = [S["st_" + st]] if st in ("included", "credit", "buy") else []
        if note:
            parts.append(note)
        return " \u00b7 ".join(parts)

    rows = []
    for name, u, st, note in b["links"]:
        sub = status_text(st, note)
        small = f"<small>{esc(sub)}</small>" if sub else ""
        rows.append(f'<a class="btn" href="{esc(u)}"><span class="lbl"><span>{esc(S["listen_on"].format(s=name))}</span>{small}</span>'
                    f'<span class="arr" aria-hidden="true">&rarr;</span></a>')
    have = {name for name, *_ in b["links"]} | set(b["absent"])
    q = urllib.parse.quote_plus(b["title"].strip())          # title only: author spellings often differ
    for svc, label in SERVICES:
        if label not in have and search and svc in search:
            rows.append(f'<a class="btn ghost" href="{esc(search[svc].replace("{q}", q))}"><span class="lbl">'
                        f'<span>{esc(S["search_on"].format(s=label))}</span><small>{esc(S["st_unchecked"])}</small></span>'
                        f'<span class="arr" aria-hidden="true">&rarr;</span></a>')
    off = "".join(f'<div class="btn off"><span>{esc(S["none_on"].format(s=n))}</span></div>' for n in b["absent"])
    if b["links"]:
        listen = f'<h2>{esc(S["listen"])}</h2><div class="btns">{"".join(rows)}{off}</div>'
    else:
        extra = "".join(rows) + off
        listen = f'<h2>{esc(S["listen"])}</h2><div class="empty">{esc(S["none"])}</div>' + (f'<div class="btns" style="margin-top:10px">{extra}</div>' if extra else "")
    chip = f'<span class="chip">{esc(S["finished"].format(d=fmt_date(b["finished"], lang)))}</span>' if b["finished"] else ""
    pub = f'<p class="pub">{esc(b["publisher"])}</p>' if b["publisher"] else ""
    author = f'<p class="author">{esc(b["author"])}</p>' if b["author"] else ""
    checked = f'<p class="foot">{esc(S["checked"].format(d=fmt_date(b["checked"], lang)))}</p>' if b["checked"] and (b["links"] or b["absent"]) else ""
    back = f'<a class="back" href="../">&larr; {esc(S["all"])}</a>' if with_index else ""
    return (head(b["title"], site, S, lang, "../../") + f"""
<body>
<header class="top"><span>{esc(site)}</span></header>
<main class="wrap">
<article class="card">
<div class="hero">
{cover_html(b)}
<div><h1>{esc(b["title"])}</h1>{author}{pub}</div>
</div>
{chip}{paras}
{listen}
{checked}
</article>
{back}
</main>
</body>
</html>
""")


def index_page(books, site, S, lang):
    cards = []
    for b in books:
        badge = f'<span class="badge">{esc(S["audio"])}</span>' if b["links"] else ""
        s = esc((b["title"] + " " + b["author"]).lower())
        cards.append(f'<a class="cardlink" href="{b["id"]}/" data-s="{s}">{cover_html(b, b["id"] + "/")}'
                     f'<div class="t">{esc(b["title"])}</div><div class="a">{esc(b["author"])}</div>{badge}</a>')
    count = S["count"].format(n=len(books))
    return (head(S["all"], site, S, lang, "") + f"""
<body>
<header class="top"><span>{esc(site)}</span></header>
<main class="wrap">
<div class="card">
<input id="q" class="search" type="search" placeholder="{esc(S["search"])}" aria-label="{esc(S["search"])}">
<p class="count" id="n" data-t="{esc(S["count"])}">{esc(count)}</p>
<div class="grid">{"".join(cards)}</div>
</div>
</main>
<script>{INDEX_JS}</script>
</body>
</html>
""")


def simple_page(title, text, site, S, lang):
    return (head(title, site, S, lang, "") + f"""
<body>
<header class="top"><span>{esc(site)}</span></header>
<main class="wrap"><div class="card center"><h1>{esc(title)}</h1><p class="author">{esc(text)}</p></div></main>
</body>
</html>
""")


# ------------------------------------------------------------------ build
def build(csv_path, out, base_url, site, lang, with_index, covers_dir, use_covers, ids_file, tags_out, cname, search=None):
    S = STRINGS[lang]
    csv_dir = os.path.dirname(os.path.abspath(csv_path))
    covers_dir = covers_dir or csv_dir
    ids_file = ids_file or os.path.splitext(os.path.abspath(csv_path))[0] + ".ids.json"
    tags_out = tags_out or os.path.join(csv_dir, "../tag_urls.csv")
    store, warnings, books, seen = IdStore(ids_file), [], [], {}

    have_pil = True
    if use_covers:
        try:
            import PIL  # noqa: F401
        except ImportError:
            have_pil = False
            warnings.append("Pillow is not installed - covers skipped (pip install pillow).")

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows or "title" not in {k.strip().lower() for k in rows[0] if k}:
        sys.exit("The CSV needs a header row with at least a 'title' column.")

    # keep user's CNAME (custom domain) when the folder is rebuilt
    keep_cname = None
    if os.path.exists(os.path.join(out, "CNAME")):
        keep_cname = open(os.path.join(out, "CNAME"), encoding="utf-8").read()
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out)

    missing_covers = []
    for n, raw in enumerate(rows, start=2):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        title = row.get("title", "")
        if not title:
            continue
        key = norm_key(title, row.get("author", ""))
        if key in seen:
            warnings.append(f"row {n}: '{title}' appears twice - the first entry is used.")
            continue
        try:
            bid = store.get(key, row.get("id", ""))
        except ValueError as e:
            sys.exit(f"row {n}: {e}")
        seen[key] = bid
        links, absent = [], []
        for svc, label in SERVICES:
            u = clean_link(row.get(svc, ""), svc, warnings, title)
            st = row.get(svc + "_status", "").strip().lower()
            if st and st not in STATUSES:
                warnings.append(f"'{title}': {svc}_status '{st}' ignored (use: {', '.join(sorted(STATUSES))})")
                st = ""
            note = row.get(svc + "_note", "")
            if u:
                links.append((label, u, "" if st == "none" else st, note))
            elif st == "none":
                absent.append(label)
        b = dict(id=bid, title=title, author=row.get("author", ""), publisher=row.get("publisher", ""),
                 description=row.get("description", ""), finished=row.get("finished", ""),
                 checked=row.get("checked", ""), links=links, absent=absent, has_cover=False)
        front = row.get("front", "")
        if use_covers and have_pil and front:
            src = front if os.path.isabs(front) else os.path.join(covers_dir, front)
            if os.path.exists(src):
                os.makedirs(os.path.join(out, bid), exist_ok=True)
                try:
                    make_cover(src, os.path.join(out, bid, "cover.jpg"), parse_trim(row.get("trim")))
                    b["has_cover"] = True
                except Exception as e:
                    warnings.append(f"'{title}': cover could not be used ({e})")
            else:
                missing_covers.append(front)
        books.append(b)

    with open(os.path.join(out, "style.css"), "w", encoding="utf-8") as f:
        f.write(CSS.strip() + "\n")
    for b in books:
        os.makedirs(os.path.join(out, b["id"]), exist_ok=True)
        with open(os.path.join(out, b["id"], "index.html"), "w", encoding="utf-8") as f:
            f.write(book_page(b, site, S, lang, with_index, search))
    root = index_page(books, site, S, lang) if with_index else simple_page(site, S["landing"], site, S, lang)
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as f:
        f.write(root)
    with open(os.path.join(out, "404.html"), "w", encoding="utf-8") as f:
        f.write(simple_page(S["nf_t"], S["nf_p"], site, S, lang))
    with open(os.path.join(out, ".nojekyll"), "w") as f:
        f.write("")
    with open(os.path.join(out, "robots.txt"), "w") as f:
        f.write("User-agent: *\nDisallow: /\n")
    if cname or keep_cname:
        with open(os.path.join(out, "CNAME"), "w", encoding="utf-8") as f:
            f.write((cname or keep_cname).strip() + "\n")

    store.save()
    base = (base_url or "https://YOUR-NAME.github.io/REPO").rstrip("/")
    with open(tags_out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["id", "title", "author", "url"])
        for b in books:
            w.writerow([b["id"], b["title"], b["author"], f"{base}/{b['id']}/"])

    with_audio = sum(1 for b in books if b["links"])
    longest = max((len(f"{base}/{b['id']}/") for b in books), default=0)
    print(f"{len(books)} book pages written to {out}/")
    print(f"  with audiobook links: {with_audio}   without: {len(books) - with_audio}")
    print(f"  covers used: {sum(1 for b in books if b['has_cover'])}   (others get a blue title tile)")
    if missing_covers:
        print(f"  {len(missing_covers)} cover file(s) not found, e.g. {missing_covers[0]}")
    print(f"  tag addresses: {tags_out}  (longest: {longest} characters)")
    print(f"  id memory:     {ids_file}  <- keep and back up this file")
    if not base_url:
        print("  NOTE: no --base-url given, tag_urls.csv contains a placeholder address.")
    for w_ in warnings:
        print("  warning:", w_)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", help="books CSV (same file as for the sticker tool works)")
    ap.add_argument("--out", default="site", help="output folder (default: site)")
    ap.add_argument("--base-url",  help="public address of the site, e.g. https://yourname.github.io/shelf")
    ap.add_argument("--site-title", help="name shown in the page header")
    ap.add_argument("--lang", choices=["en", "de"], default="en", help="language of the page texts")
    ap.add_argument("--with-index", action="store_true",
                    help="also build a searchable list of ALL books on the start page (makes your list public)")
    ap.add_argument("--covers-dir", help="folder the 'front' paths are relative to (default: CSV folder)")
    ap.add_argument("--no-covers", action="store_true", help="never publish cover images (use title tiles)")
    ap.add_argument("--ids-file", help="where page ids are remembered (default: <csv>.ids.json)")
    ap.add_argument("--tags-out", help="where to write the tag address list (default: tag_urls.csv next to the CSV)")
    ap.add_argument("--search-link", action="append", default=[], metavar="NAME=URL",
                    help='fallback search button, e.g. audible="https://www.audible.de/search?keywords={q}" (repeatable)')
    ap.add_argument("--no-search-links", action="store_true", help="do not add the built-in 'Search on ...' buttons")
    ap.add_argument("--cname", help="custom domain to write into CNAME, e.g. books.example.com")
    a = ap.parse_args()
    search = {} if a.no_search_links else dict(DEFAULT_SEARCH)
    for item in a.search_link:
        name, _, url = item.partition("=")
        if name.strip().lower() not in {k for k, _ in SERVICES} or "{q}" not in url:
            ap.error("--search-link needs NAME=URL with {q} in it, NAME one of: " + ", ".join(k for k, _ in SERVICES))
        search[name.strip().lower()] = url.strip().strip('"')
    build(a.csv, a.out, a.base_url, a.site_title or STRINGS[a.lang]["mini"], a.lang, a.with_index,
          a.covers_dir, not a.no_covers, a.ids_file, a.tags_out, a.cname, search)


if __name__ == "__main__":
    main()
