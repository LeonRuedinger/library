#!/usr/bin/env python3
"""
audiolink - find each book's audiobook page on Audible, Storytel and BookBeat and write the links
(plus subscription info) into your books CSV, ready for bookpages.py.

For every book and service it
  1. looks up search results (Audible: its public catalog data or the result cards; Storytel/BookBeat:
     the search page in a real browser, so JavaScript pages work),
  2. checks the best few candidates for title, author and LANGUAGE,
  3. fills the CSV if one candidate is clearly the right one - otherwise it writes the candidates to
     review.csv so you can pick by hand.

During weekly refreshes (--refresh), a listing is marked unavailable only
after three consecutive weekly scans return no search results. Errors and
ambiguous matches do not count as missing listings.

Setup (once):
    pip install playwright
    playwright install chromium

Usage:
    python audiolink.py books.csv                          # all books, all three services
    python audiolink.py books.csv --limit 10               # next 10 books that are not done yet
    python audiolink.py books.csv --only "lady macbeth"    # a single book (good for a first test)
    python audiolink.py books.csv --services storytel,bookbeat --headed --debug debug

Audible modes (--audible-mode):
    api    (default) asks Audible's public catalog data for the title (no product pages are opened,
           so Audible's error pages cannot get in the way). Falls back to 'cards' if that fails.
    cards  reads title, language and plan info from the search RESULT page only (one page per book).
    pages  opens every candidate's product page (what the first version did; Audible often blocks this).

Useful CSV columns (all optional except title):
    author      used to confirm a match
    lang        language of HER copy: de, en, fr, it, es ... (the match must be in this language)
                if missing, the script guesses from the title and uses --default-lang otherwise

Results: <name>_linked.csv (your CSV + audible/storytel/bookbeat, *_status, *_note, checked, lookup),
review.csv (candidates that need a human decision), <name>_linked.csv.state.json (what is done).
Rerun the same command any time: finished books/services are skipped.

Please use it gently: it waits a few seconds between pages and is meant for your own list of books.
Check the services' terms of use; if one of them objects to automated access, use the manual route.
"""

import argparse, csv, difflib, json, os, random, re, sys, time, unicodedata
import urllib.error, urllib.parse, urllib.request
from datetime import date

AUDIBLE_API = "https://api.audible.de/1.0/catalog/products"
AUDIBLE_PDP = "https://www.audible.de/pd/{asin}"
SERVICES = {
    "audible": dict(label="Audible", search="https://www.audible.de/search?keywords={q}&k={q}", link=r"/pd/[^/?#]+"),
    "storytel": dict(label="Storytel", search="https://www.storytel.com/de/search/all?query={q}&formats=abook",
                     link=r"/de/books/[^/?#]+"),
    "bookbeat": dict(label="BookBeat", search="https://www.bookbeat.com/de/search?query={q}", link=r"/de/book/[^/?#]+"),
}
LANGS = {"deutsch": "de", "german": "de", "englisch": "en", "english": "en", "französisch": "fr", "french": "fr",
         "italienisch": "it", "italian": "it", "spanisch": "es", "spanish": "es", "niederländisch": "nl",
         "portugiesisch": "pt", "russisch": "ru", "japanisch": "ja"}
LANG_RE = re.compile(r"Sprache\s*:?\s*\n?\s*(" + "|".join(LANGS) + r")\b", re.I)
DE_WORDS = {"der", "die", "das", "und", "ein", "eine", "von", "zu", "mit", "für", "des", "dem", "den", "im", "ist",
            "nicht", "sich", "auf", "aus", "bei", "nach", "zum", "zur", "über", "einer", "eines", "oder"}
EN_WORDS = {"the", "of", "and", "a", "an", "in", "to", "for", "with", "at", "on", "is", "are", "my", "your", "from"}
JUNK = ["zusammenfassung", "summary", "analyse", "analysis", "lekturehilfe", "interpretation", "study guide",
        "leseprobe", "hoerspiel", "dramatized", "workbook", "zusammengefasst", "in 15 minuten", "buchzusammenfassung"]
ACCEPT, REVIEW = 0.80, 0.55
CONSENT = re.compile(r"alle akzeptieren|akzeptieren|zustimmen|alle zulassen|accept all|allow all|einverstanden", re.I)
PLUS_MARKERS = ("kostenlos hören", "im plus-katalog", "free with membership")


# ------------------------------------------------------------------ text helpers
def norm(s):
    s = unicodedata.normalize("NFKD", (s or "").casefold().replace("ß", "ss"))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s)).strip()


def title_sim(expected, text):
    e = norm(expected)
    if not e or not text:
        return 0.0
    variants = {text, re.split(r"[:\u2013\u2014(\[]| - ", text)[0]}
    best = 0.0
    for v in variants:
        n = norm(v)
        if not n:
            continue
        r = difflib.SequenceMatcher(None, e, n).ratio()
        if n == e:
            r = 1.0
        elif set(e.split()) <= set(n.split()):
            r = max(r, 0.92)
        best = max(best, r)
    return best


def author_match(author, text):
    parts = [p.strip() for p in re.split(r"[;,&]| und | and ", author or "") if p.strip()]
    if not parts:
        return 0.5
    hay = norm(text[:4000])
    words = set(hay.split())
    for p in parts:
        sur = norm(p).split()[-1] if norm(p) else ""
        if len(sur) < 3:
            continue
        if sur in hay or any(difflib.SequenceMatcher(None, sur, w).ratio() >= 0.85 for w in words if abs(len(w) - len(sur)) <= 2):
            return 1.0
    return 0.0


def infer_lang(title):
    toks = set(norm(title).split())
    if re.search(r"[äöüß]", (title or "").lower()) or toks & DE_WORDS:
        return "de"
    if toks & EN_WORDS:
        return "en"
    return None


def page_lang(body):
    m = LANG_RE.search(body)
    return LANGS[m.group(1).lower()] if m else None


def score(exp_title, exp_author, exp_lang, cand_title, cand_lang, text):
    t = title_sim(exp_title, cand_title)
    a = author_match(exp_author, cand_title + "\n" + text)
    if exp_lang and cand_lang:
        lg = 1.0 if exp_lang == cand_lang else 0.0
    else:
        lg = 0.5
    s = 0.60 * t + 0.25 * a + 0.15 * lg
    low = norm(cand_title)
    if any(j in low for j in JUNK) and not any(j in norm(exp_title) for j in JUNK):
        s -= 0.40
    if exp_lang and cand_lang and exp_lang != cand_lang:
        s = min(s, REVIEW + 0.04)            # right book, wrong language -> never auto-accepted
    return round(max(s, 0.0), 3)


# ------------------------------------------------------------------ browser steps
def click_consent(page):
    try:
        for b in page.get_by_role("button").all()[:40]:
            if CONSENT.search((b.inner_text(timeout=300) or "").strip()):
                b.click(timeout=1500)
                page.wait_for_timeout(600)
                return
    except Exception:
        pass


def validate_search_page(page, service):
    title = (page.title() or "").lower()
    text = (page.locator("body").inner_text(timeout=10000) or "").lower()

    blocked_markers = [
        "captcha",
        "robot check",
        "are you a robot",
        "access denied",
        "unusual traffic",
        "verify you are human",
        "automated requests",
        "temporarily blocked",
    ]

    combined = title + "\n" + text

    for marker in blocked_markers:
        if marker in combined:
            raise RuntimeError(
                f"{service}: search page appears blocked ({marker})"
            )


def search_candidates(page, svc, title, link_re, search_url, nmax):
    url = search_url.replace("{q}", urllib.parse.quote_plus(title))
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    click_consent(page)
    page.wait_for_timeout(1500)
    validate_search_page(page, str(svc))
    seen, out = set(), []
    for a in page.query_selector_all("a[href]"):
        try:
            href = a.get_attribute("href") or ""
            full = urllib.parse.urljoin(page.url, href).split("#")[0].split("?")[0]
            path = urllib.parse.unquote(urllib.parse.urlparse(full).path)

            if not re.search(link_re, path) or full in seen:
                continue

            text = " ".join((a.inner_text() or "").split())
            if not text:
                text = (a.get_attribute("aria-label") or
                        a.get_attribute("title") or "").strip()

            seen.add(full)
            out.append((full, text))

        except Exception as e:
            print("DEBUG extraction exception:", repr(e))
            continue

        if len(out) >= nmax:
            break

    return url, out


def read_candidate(page, url):
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(1500)
    body = page.inner_text("body")

    title = ""
    for selector in [
        'h1',
        'meta[property="og:title"]',
        '[data-testid*="title"]'
    ]:
        try:
            el = page.locator(selector).first
            if selector.startswith("meta"):
                value = el.get_attribute("content", timeout=2000)
            else:
                value = el.inner_text(timeout=2000)

            if value and len(value.strip()) > 2:
                title = value.strip()
                break
        except Exception:
            continue

    page_title = title or page.title() or ""

    if "da ist wohl wirklich was kaputt" in body.lower():
        print(f"   WARNING: Audible returned an error page for {url}")
        return "", ""

    return page_title, body


def audible_status(body):
    low = body.lower()
    if any(m in low for m in PLUS_MARKERS):
        return "included", "Plus catalog (Premium Plus)"
    if "kaufen" in low or "ausprobieren" in low:
        return "credit", ""
    return "", ""


def service_status(service, body):
    low = body.lower()

    if service == "audible":
        return audible_status(body)

    if service == "bookbeat":
        if "das hörbuch ist nicht im abo inbegriffen" in low:
            return "buy", "Not included in subscription"

    elif service == "storytel":
        if "Dieser Titel ist nur zum Kauf erhältlich" in low:
            return "buy", "Only available for purchase"

    return "included", ""

def record_history(path, title, author, service, event,
                   old_url="", new_url="",
                   old_status="", new_status="",
                   old_note="", new_note=""):

    fields = [
        "date", "title", "author", "service", "event",
        "old_url", "new_url",
        "old_status", "new_status",
        "old_note", "new_note"
    ]

    file_exists = os.path.exists(path) and os.path.getsize(path) > 0

    with open(path, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)

        if not file_exists:
            writer.writeheader()

        writer.writerow({
            "date": str(date.today()),
            "title": title,
            "author": author,
            "service": service,
            "event": event,
            "old_url": old_url,
            "new_url": new_url,
            "old_status": old_status,
            "new_status": new_status,
            "old_note": old_note,
            "new_note": new_note
        })


# ------------------------------------------------------------------ Audible: catalog data and result cards
API_GROUPS = ["contributors", "media", "product_attrs", "product_desc", "product_extended_attrs",
              "product_plan_details", "product_plans"]


def audible_api_products(api_url, title, nmax=10, debug_dir=None):
    """Ask Audible's public catalog endpoint (the one many book apps use) for a title. No login needed."""
    last = None
    for groups in (API_GROUPS, API_GROUPS[:5], API_GROUPS[:3]):          # fewer groups if one is not accepted
        q = urllib.parse.urlencode({"title": title, "num_results": nmax, "products_sort_by": "Relevance",
                                    "response_groups": ",".join(groups)})
        req = urllib.request.Request(f"{api_url}?{q}", headers={
            "User-Agent": "audiolink/1.0 (personal book list)", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if debug_dir:
                path = os.path.join(debug_dir, f"audible_api_{norm(title).replace(' ', '-')[:40]}.json")
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=1)
            return data.get("products", []) or []
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (400, 422):
                continue
            raise
    raise last


def audible_api_scored(api_url, title, author, exp_lang, debug_dir=None):
    out = []
    for p in audible_api_products(api_url, title, 10, debug_dir):
        asin = p.get("asin")
        if not asin or str(p.get("content_type", "")).lower() in ("podcast", "episode"):
            continue
        names = lambda key: ", ".join(x.get("name", "") for x in (p.get(key) or []) if isinstance(x, dict))
        lang = LANGS.get(str(p.get("language") or "").lower())
        ptitle = p.get("title") or ""
        text = f"{names('authors')} {names('narrators')} {p.get('publisher_name', '')}"
        plus = bool(p.get("is_ayce")) or any(m in json.dumps(p.get("plans", ""), ensure_ascii=False).lower()
                                             for m in ("rodizio",))
        note = "Plus catalog (Premium Plus)" if plus else ""
        if str(p.get("format_type", "")).lower() == "abridged":
            note = (note + " \u00b7 " if note else "") + "abridged"
        out.append(dict(url=AUDIBLE_PDP.format(asin=asin), name=ptitle, ptitle=ptitle, lang=lang, body=text,
                        status=("included" if plus else "credit", note),
                        score=score(title, author, exp_lang, ptitle, lang, text)))
    return out


JS_CARDS = """() => [...document.querySelectorAll('a[href*="/pd/"]')].map(a => {
  const li = a.closest('li') || a.closest('[class*="productListItem"]') || a.parentElement;
  return {href: a.href, text: (a.innerText || '').trim(), card: (li ? li.innerText : '')};
})"""


def audible_cards_scored(page, search_url, title, author, exp_lang, link_re, nmax=6):
    url = search_url.replace("{q}", urllib.parse.quote_plus(title))
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    click_consent(page)
    page.wait_for_timeout(1500)
    validate_search_page(page, "Audible")

    seen, out = set(), []
    for it in page.evaluate(JS_CARDS):
        full = urllib.parse.urljoin(page.url, it["href"]).split("#")[0].split("?")[0]
        path = urllib.parse.unquote(urllib.parse.urlparse(full).path)
        text = " ".join((it["text"] or "").split())
        if not re.search(link_re, path) or full in seen or len(text) < 3:
            continue
        seen.add(full)
        card = it["card"] or ""
        lang = page_lang(card)
        plus = any(m in card.lower() for m in PLUS_MARKERS)
        out.append(dict(url=full, name=text, ptitle=text, lang=lang, body=card,
                        status=("included", "Plus catalog (Premium Plus)") if plus else ("credit", ""),
                        score=score(title, author, exp_lang, text, lang, card)))
        if len(out) >= nmax:
            break
    return out


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--out", help="result CSV (default: <csv>_linked.csv)")
    ap.add_argument("--services", default="audible,storytel,bookbeat")
    ap.add_argument("--only", help="only books whose title contains this text")
    ap.add_argument("--limit", type=int, default=0, help="stop after this many books that needed work")
    ap.add_argument("--default-lang", default="de", help="language to expect when the CSV has none (default de)")
    ap.add_argument("--candidates", type=int, default=3, help="how many search results to open per service")
    ap.add_argument("--audible-mode", choices=["api", "cards", "pages"], default="api")
    ap.add_argument("--audible-api", default=AUDIBLE_API, help="catalog endpoint (change the country here)")
    ap.add_argument("--overwrite", action="store_true", help="also redo cells that already have a link")
    ap.add_argument("--retry", action="store_true", help="redo services that were tried before")
    ap.add_argument("--refresh", action="store_true",
                    help="recheck every book and service, including completed lookups")
    ap.add_argument("--headed", action="store_true", help="show the browser window")
    ap.add_argument("--debug", metavar="DIR", help="save screenshots, HTML and raw catalog data here")
    ap.add_argument("--delay", type=float, nargs=2, default=[0.5, 1.0], metavar=("MIN", "MAX"))
    ap.add_argument("--search", action="append", default=[], metavar="NAME=URL",
                    help="override a search address, e.g. storytel=https://...query={q}")
    a = ap.parse_args()

    for item in a.search:
        n, _, u = item.partition("=")
        SERVICES[n.strip()]["search"] = u.strip()
    services = [s.strip() for s in a.services.split(",") if s.strip()]
    out_path = a.out or os.path.splitext(a.csv)[0] + "_linked.csv"
    state_path = out_path + ".state.json"
    review_path = os.path.join(os.path.dirname(os.path.abspath(out_path)), "review.csv")
    history_path = os.path.join(os.path.dirname(os.path.abspath(out_path)), "availability_history.csv")

    # Always use the master CSV as the authoritative book list.
    with open(a.csv, newline="", encoding="utf-8-sig") as f:
        rd = csv.DictReader(f)
        master_rows = list(rd)
        master_cols = list(rd.fieldnames)

    # Load previous scraper results, if available.
    old_rows = []
    old_cols = []

    if os.path.exists(out_path):
        with open(out_path, newline="", encoding="utf-8-sig") as f:
            rd = csv.DictReader(f)
            old_rows = list(rd)
            old_cols = list(rd.fieldnames)

    def book_key(r):
        return "|".join(norm(r.get(c, "")) for c in ("title", "author", "lang"))

    old_by_key = {book_key(r): r for r in old_rows}

    # Keep master data authoritative, but reuse scraper-generated fields.
    need = ["lookup", "checked"] + [
        c for s in services
        for c in (s, s + "_status", s + "_note")
    ]

    cols = list(dict.fromkeys(
        master_cols + old_cols + need
    ))

    rows = []

    for master in master_rows:
        row = dict(master)
        previous = old_by_key.get(book_key(master), {})

        for c in cols:
            if c not in master_cols:
                row[c] = previous.get(c, "")

        rows.append(row)

    # Retain state only for books that still exist.
    active_keys = {
        f"{norm(r.get('title', ''))}|{norm(r.get('author', ''))}|{norm(r.get('lang', ''))}|{s}"
        for r in rows
        for s in services
    }

    state = json.load(open(state_path, encoding="utf-8")) if os.path.exists(state_path) else {}

    # Migrate legacy keys: title|author|service -> title|author|lang|service
    for old_key, value in list(state.items()):
        parts = old_key.split("|")
        if len(parts) != 3:
            continue

        title, author, service = parts

        for row in rows:
            if (
                    norm(row.get("title", "")) == title
                    and norm(row.get("author", "")) == author
                    and service in services
            ):
                new_key = f"{title}|{author}|{norm(row.get('lang', ''))}|{service}"
                state.setdefault(new_key, value)
                break

    state = {k: v for k, v in state.items() if k in active_keys}

    review = []

    def save():
        with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); w.writeheader(); w.writerows(rows)
        json.dump(state, open(state_path, "w", encoding="utf-8"), indent=1)
        with open(review_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["title", "author", "service", "score", "language", "page_title", "url", "reason"])
            w.writeheader(); w.writerows(review)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("Playwright is missing:  pip install playwright   then   playwright install chromium")

    if a.debug:
        os.makedirs(a.debug, exist_ok=True)
    worked = 0
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            os.path.join(os.path.dirname(os.path.abspath(out_path)), ".audiolink-browser"),
            headless=not a.headed, locale="de-DE", viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        for r in rows:
            title, author = (r.get("title") or "").strip(), (r.get("author") or "").strip()
            if not title or (a.only and a.only.lower() not in title.lower()):
                continue
            exp_lang = (r.get("lang") or "").strip().lower() or infer_lang(title) or a.default_lang
            todo = []
            for s in services:
                k = f"{norm(title)}|{norm(author)}|{norm(r.get('lang', ''))}|{s}"
                force_check = a.overwrite or a.retry or a.refresh

                if (r.get(s) and not force_check) or (k in state and not force_check):
                    continue
                todo.append((s, k))
            if not todo:
                continue
            if a.limit and worked >= a.limit:
                break
            worked += 1
            print(f"\n== {title} ({author}) - expecting language: {exp_lang}")
            notes = []
            for s, k in todo:
                svc = SERVICES[s]
                try:
                    time.sleep(random.uniform(*a.delay))
                    scored = []
                    mode = a.audible_mode if s == "audible" else "pages"
                    if mode == "api":
                        try:
                            scored = audible_api_scored(a.audible_api, title, author, exp_lang, a.debug)
                            if not scored:
                                print("   Audible catalog returned nothing - trying the search page")
                        except Exception as e:
                            print(f"   Audible catalog request failed ({str(e)[:80]}) - trying the search page")
                        if not scored:
                            mode = "cards"
                    if mode == "cards":
                        scored = audible_cards_scored(page, svc["search"], title, author, exp_lang, svc["link"])
                        if a.debug:
                            base = os.path.join(a.debug, f"{s}_{norm(title).replace(' ', '-')[:40]}")
                            page.screenshot(path=base + ".png"); open(base + ".html", "w", encoding="utf-8").write(page.content())
                    elif mode == "pages":
                        url, cands = search_candidates(page, s, title, svc["link"], svc["search"], 12)
                        if a.debug:
                            base = os.path.join(a.debug, f"{s}_{norm(title).replace(' ', '-')[:40]}")
                            page.screenshot(path=base + ".png"); open(base + ".html", "w", encoding="utf-8").write(page.content())
                        # cheap pre-ranking on the link text, then open the best few
                        cands.sort(key=lambda c: -title_sim(title, c[1]))
                        for cu, ctext in cands[:a.candidates]:
                            time.sleep(random.uniform(*a.delay))
                            ptitle, body = read_candidate(page, cu)
                            if not body:
                                continue
                            name = ctext if len(ctext) > 2 else ptitle

                            sc = score(title, author, exp_lang, name or ptitle, page_lang(body), body)
                            scored.append(dict(url=cu, score=sc, name=name, ptitle=ptitle, lang=page_lang(body), body=body))
                            if (
                                sc >= 0.95
                                and exp_lang == page_lang(body)
                                and author_match(author, name + "\n" + body) == 1.0
                            ):
                                break
                    scored.sort(key=lambda c: -c["score"])
                    if not scored:
                        previous_state = state.get(k, {})

                        if a.refresh:
                            today = date.today()
                            iso_week = today.isocalendar()
                            week_key = f"{iso_week.year}-W{iso_week.week:02d}"

                            last_refresh_week = previous_state.get("last_refresh_week")

                            if last_refresh_week == week_key:
                                misses = int(previous_state.get("misses", 0))
                            else:
                                misses = int(previous_state.get("misses", 0)) + 1

                            if r.get(s) and misses == 1:
                                record_history(
                                    history_path,
                                    title,
                                    author,
                                    s,
                                    "no_search_results_unconfirmed",
                                    old_url=r.get(s, ""),
                                    old_status=r.get(s + "_status", ""),
                                    old_note=r.get(s + "_note", "")
                                )

                            if r.get(s) and misses >= 3:
                                old_url = r.get(s, "")
                                old_status = r.get(s + "_status", "")
                                old_note = r.get(s + "_note", "")

                                record_history(
                                    history_path,
                                    title,
                                    author,
                                    s,
                                    "listing_unavailable_confirmed",
                                    old_url=old_url,
                                    old_status=old_status,
                                    old_note=old_note
                                )

                                r[s] = ""
                                r[s + "_status"] = "unavailable"
                                r[s + "_note"] = "Not found in 3 consecutive weekly scans"

                                state[k] = {
                                    "result": "unavailable",
                                    "misses": misses,
                                    "ts": str(date.today()),
                                    "last_refresh_week": week_key,
                                }

                                print(f"   {svc['label']}: UNAVAILABLE after 3 weekly scans")

                            else:
                                state[k] = {
                                    **previous_state,
                                    "result": previous_state.get("result", "none"),
                                    "misses": misses,
                                    "ts": str(date.today()),
                                    "last_refresh_week": week_key,
                                }

                                print(f"   {svc['label']}: no results ({misses}/3 weekly scans)")

                            notes.append(f"{s}: no results")

                        else:
                            # Ordinary lookups and manual retries do not count as weekly misses.
                            state[k] = {
                                **previous_state,
                                "result": previous_state.get("result", "none"),
                                "ts": str(date.today())
                            }

                            notes.append(f"{s}: no results")
                            print(f"   {svc['label']}: no results (existing link retained)")
                    elif scored[0]["score"] >= ACCEPT:
                        best = scored[0]
                        st, note = best.get("status") or service_status(s, best["body"])
                        lang_note = {"de": "German edition", "en": "English edition", "fr": "French edition",
                                     "it": "Italian edition", "es": "Spanish edition"}.get(best["lang"] or "", "")
                        new_url = best["url"]
                        new_note = " · ".join(x for x in (lang_note, note) if x)

                        old_url = r.get(s, "")
                        old_status = r.get(s + "_status", "")
                        old_note = r.get(s + "_note", "")

                        events = []

                        if old_status == "unavailable":
                            events.append("listing_returned")
                        elif not old_url:
                            events.append("new_listing")
                        elif old_url != new_url:
                            events.append("listing_changed")

                        if old_url and (old_status != st or old_note != new_note):
                            events.append("subscription_status_changed")

                        if events:
                            record_history(
                                history_path,
                                title,
                                author,
                                s,
                                "+".join(events),
                                old_url,
                                new_url,
                                old_status,
                                st,
                                old_note,
                                new_note
                            )

                        r[s], r[s + "_status"] = new_url, st
                        r[s + "_note"] = new_note
                        previous_state = state.get(k, {})
                        new_state = {
                            **previous_state,
                            "result": "found",
                            "score": best["score"],
                            "ts": str(date.today()),
                            "misses": 0
                        }
                        new_state.pop("msg", None)

                        if a.refresh:
                            today = date.today()
                            iso_week = today.isocalendar()
                            new_state["last_refresh_week"] = f"{iso_week.year}-W{iso_week.week:02d}"

                        state[k] = new_state
                        notes.append(f"{s}: ok {best['score']}"); print(f"   {svc['label']}: FOUND {best['url']}  (score {best['score']}, {best['lang'] or '?'})")
                    else:
                        previous_state = state.get(k, {})
                        state[k] = {
                            **previous_state,
                            "result": "review",
                            "score": scored[0]["score"],
                            "ts": str(date.today())
                        }
                        notes.append(f"{s}: review"); print(f"   {svc['label']}: needs a look - best score {scored[0]['score']}  (see review.csv)")
                        for c in scored[:a.candidates]:
                            reason = ("wrong language" if exp_lang and c["lang"] and c["lang"] != exp_lang else "low confidence")
                            review[:] = [x for x in review if not (x["title"] == title and x["service"] == s and x["url"] == c["url"])]
                            review.append(dict(title=title, author=author, service=s, score=c["score"], language=c["lang"] or "",
                                               page_title=c["ptitle"][:90], url=c["url"], reason=reason))
                except Exception as e:
                    previous_state = state.get(k, {})
                    state[k] = {
                        **previous_state,
                        "result": "error",
                        "msg": str(e)[:120],
                        "ts": str(date.today())
                    }
                    notes.append(f"{s}: error"); print(f"   {svc['label']}: ERROR {str(e)[:100]}")
                r["checked"] = str(date.today())
                r["lookup"] = f"script {date.today()}: " + "; ".join(notes)
                save()
        ctx.close()
    save()
    print(f"\nDone. {worked} book(s) processed. Results: {out_path}   Please look at: {review_path}")


if __name__ == "__main__":
    main()