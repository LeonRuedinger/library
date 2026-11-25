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

INDEX_JS = """
const q=document.getElementById('q'),items=[...document.querySelectorAll('[data-s]')],n=document.getElementById('n');
q.addEventListener('input',()=>{const v=q.value.trim().toLowerCase();let c=0;
  items.forEach(e=>{const m=!v||e.dataset.s.includes(v);e.hidden=!m;if(m)c++});n.textContent=n.dataset.t.replace('{n}',c)});

const bookDialog=document.getElementById('book-form-dialog');
const openBookForm=document.getElementById('open-book-form');
const closeBookForm=document.getElementById('close-book-form');

openBookForm.addEventListener('click',()=>bookDialog.showModal());
closeBookForm.addEventListener('click',()=>bookDialog.close());

bookDialog.addEventListener('click',event=>{
  if(event.target===bookDialog) bookDialog.close();
});
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
    return (head(b["title"], site, S, lang, "../") + f"""
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
    cards = [
        '<button type="button" class="cardlink add-book-card" '
        'id="open-book-form">'
        '<div class="add-book-icon">+</div>'
        '<div class="t">Add book(s)</div>'
        '</button>'
             ]
    for b in books:
        if b.get("processing"):
            badge = '<span class="badge processing-badge">Processing…</span>'
        else:
            badge = f'<span class="badge">{esc(S["audio"])}</span>' if b["links"] else ""
        s = esc((b["title"] + " " + b["author"]).lower())
        tag = "div" if b.get("processing") else "a"
        href = "" if b.get("processing") else f' href="{b["id"]}/"'

        cards.append(
            f'<{tag} class="cardlink" {href} data-s="{s}">'
            f'{cover_html(b, b["id"] + "/")}'
            f'<div class="t">{esc(b["title"])}</div>'
            f'<div class="a">{esc(b["author"])}</div>{badge}</{tag}>'
        )

    count = S["count"].format(n=len(books))

    form_html = """
    <dialog id="book-form-dialog" class="book-form-dialog">
    <button type="button" id="close-book-form"
            class="book-form-close" aria-label="Close">×</button>
    <section class="submission">
        <h2>Add books</h2>

        <form id="book-form">
            
            <div class="barcode-scanner">
                <p><strong>Add a book by ISBN</strong><br>
                   Scan the barcode with your camera or enter the ISBN manually.</p>
            
                <div class="barcode-actions">
                    <button type="button" id="start-barcode-scan" class="primary">
                        Scan barcode
                    </button>
                    <button type="button" id="stop-barcode-scan" hidden>
                        Stop camera
                    </button>
                </div>
            
                <div class="barcode-video-container" hidden>
                    <video id="barcode-video" playsinline></video>
                    <div class="barcode-scan-frame">
                        <span></span>
                    </div>
                </div>
            
                <div class="barcode-isbn-input">
                    <input
                        type="text"
                        id="barcode-isbn"
                        inputmode="numeric"
                        placeholder="Enter ISBN-13"
                        maxlength="17"
                        aria-label="ISBN-13">
                    <button type="button" id="lookup-isbn">
                        Look up
                    </button>
                </div>
            
                <div id="barcode-message" role="status" aria-live="polite"></div>
                <div id="barcode-preview" hidden></div>
            </div>
            
            <div id="barcode-library"></div>


            <div id="book-entries">

                <div class="book-entry">
                
                    <div class="book-entry-header">
                        <button type="button" class="entry-toggle" aria-expanded="true">
                            <span class="entry-number">Book 1</span>
                            <span class="entry-summary">New book</span>
                            <img class="entry-cover" alt="" style="display:none">
                            <span class="entry-chevron">⌄</span>
                        </button>
                    </div>
                
                    <div class="book-entry-fields">
                
                        <label>Title *</label>
                        <input name="title" required maxlength="250">
                
                        <label>Author *</label>
                        <input name="author" required maxlength="200">
                
                        <label>Publisher</label>
                        <input name="publisher" maxlength="200">
                
                        <label>Language *</label>
                        <select name="lang" required>
                            <option value="de">Deutsch</option>
                            <option value="en">English</option>
                        </select>
                
                        <label>Book cover (optional)</label>
                        <input name="cover" type="file"
                               accept="image/*" capture="environment">
                
                        <div class="cover-preview"></div>
                
                    </div>
                </div>

            </div>

            <button type="button" id="add-book">
                + Add another book
            </button>

            <div id="turnstile-widget"></div>

            <button type="submit" id="submit-book">
                Submit all books
            </button>

            <p id="submission-status" role="status"
               aria-live="polite"></p>

        </form>
    </section>
    </dialog>
    """

    submission_js = """
    <script>
    let turnstileToken = "";
    let bookCount = 1;
    
    const isLocalTest =
        window.location.hostname === "localhost" &&
        window.location.port === "8000";
        
    window.onTurnstileLoaded = function () {
        if (isLocalTest || typeof turnstile === "undefined") return;

        turnstile.render("#turnstile-widget", {
            sitekey: "0x4AAAAAAGRIEytKju80-3JK",
            callback: function (token) {
                turnstileToken = token;
            },
            "expired-callback": function () {
                turnstileToken = "";
            },
            "error-callback": function () {
                turnstileToken = "";
            }
        });
    };

    const entries = document.getElementById("book-entries");
    const addButton = document.getElementById("add-book");
    
    
const isbnInput = document.getElementById("barcode-isbn");
const isbnLookupButton = document.getElementById("lookup-isbn");
const barcodeMessage = document.getElementById("barcode-message");
const barcodePreview = document.getElementById("barcode-preview");

let currentBookMetadata = null;

function normalizeISBN(value) {
    return value.replace(/[\\s-]/g, "");
}

function isValidISBN13(isbn) {
    if (!/^97[89]\\d{10}$/.test(isbn)) return false;

    const sum = [...isbn].reduce(
        (total, digit, index) =>
            total + Number(digit) * (index % 2 === 0 ? 1 : 3),
        0
    );

    return sum % 10 === 0;
}

async function lookupISBN() {
    const isbn = normalizeISBN(isbnInput.value);

    barcodePreview.hidden = true;
    barcodePreview.innerHTML = "";
    currentBookMetadata = null;

    if (!isValidISBN13(isbn)) {
        barcodeMessage.textContent =
            "Please enter a valid ISBN-13.";
        return;
    }

    isbnLookupButton.disabled = true;
    barcodeMessage.textContent = "Looking up book...";

    try {
        const url =
            "https://library-google-books.thisamail2.workers.dev/lookup?isbn=" +
            encodeURIComponent(isbn);

        const response = await fetch(url);

        if (!response.ok) {
            const details = await response.text();
            throw new Error(
                `Google Books returned HTTP ${response.status}: ${details.slice(0, 300)}`
            );
        }

        const data = await response.json();

        if (!data.items || data.items.length === 0) {
            throw new Error("No book found for this ISBN.");
        }

        const book = data.items.find(item =>
            item.volumeInfo?.industryIdentifiers?.some(identifier =>
                identifier.type === "ISBN_13" &&
                identifier.identifier === isbn
            )
        ) || data.items[0];

        const info = book.volumeInfo || {};

        if (!info.title || !info.authors?.length) {
            throw new Error("Book metadata is incomplete.");
        }

        currentBookMetadata = {
            isbn,
            title: info.title,
            author: info.authors.join(", "),
            publisher: info.publisher || "",
            language: info.language === "de" ? "de" : "en",
            cover: info.imageLinks?.thumbnail || ""
        };

        renderBookPreview(currentBookMetadata);

        barcodeMessage.textContent =
            "Book found. Check the details before adding it.";

    } catch (error) {
        barcodeMessage.textContent = error.message;
    } finally {
        isbnLookupButton.disabled = false;
    }
}


function renderBookPreview(book) {
    barcodePreview.innerHTML = "";

    const card = document.createElement("div");
    card.className = "barcode-book-card";

    if (book.cover) {
        const image = document.createElement("img");
        image.className = "barcode-book-cover";
        image.src = book.cover.replace("http://", "https://");
        image.alt = "Cover of " + book.title;
        card.appendChild(image);
    }

    const details = document.createElement("div");
    details.className = "barcode-book-details";

    const title = document.createElement("strong");
    title.className = "barcode-book-title";
    title.textContent = book.title;
    details.appendChild(title);

    const author = document.createElement("p");
    author.className = "barcode-book-author";
    author.textContent = book.author;
    details.appendChild(author);

    if (book.publisher) {
        const publisher = document.createElement("p");
        publisher.className = "barcode-book-publisher";
        publisher.textContent = book.publisher;
        details.appendChild(publisher);
    }

    const button = document.createElement("button");
    button.type = "button";
    button.className = "barcode-book-add";
    button.textContent = "Add to form";
    button.addEventListener("click", addBookMetadata);

    details.appendChild(button);
    card.appendChild(details);
    barcodePreview.appendChild(card);

    barcodePreview.hidden = false;
}



    async function addBookMetadata() {
        if (!currentBookMetadata) return;
    
        let allEntries = [...entries.querySelectorAll(".book-entry")];
        let target = allEntries[allEntries.length - 1];
    
        const hasContent = [...target.querySelectorAll(
            'input[name="title"], input[name="author"]'
        )].some(input => input.value.trim());
    
        if (hasContent) {
            addButton.click();
            allEntries = [...entries.querySelectorAll(".book-entry")];
            target = allEntries[allEntries.length - 1];
        }
    
        const book = currentBookMetadata;
    
        target.querySelector('[name="title"]').value = book.title;
        target.querySelector('[name="author"]').value = book.author;
        target.querySelector('[name="publisher"]').value = book.publisher;
        target.querySelector('[name="lang"]').value = book.language;
    
        target.querySelectorAll(
            '[name="title"], [name="author"], [name="publisher"], [name="lang"]'
        ).forEach(input => {
            input.dispatchEvent(new Event("input", { bubbles: true }));
            input.dispatchEvent(new Event("change", { bubbles: true }));
        });
    
        setEntryCollapsed(target, false);
    
        const coverInput = target.querySelector('input[name="cover"]');
        let coverAdded = false;
    
        if (book.cover && coverInput) {
            try {
                barcodeMessage.textContent =
                    "Adding book and downloading cover...";
    
                const coverUrl =
                    "https://library-submissions.thisamail2.workers.dev/cover?url=" +
                    encodeURIComponent(
                        book.cover.replace(/^http:\\/\\//i, "https://")
                    );
                
                const response = await fetch(coverUrl);
    
                if (!response.ok) {
                    throw new Error("Cover download failed.");
                }
    
                const blob = await response.blob();
    
                if (!blob.type.startsWith("image/")) {
                    throw new Error("Downloaded file is not an image.");
                }
    
                const extension = {
                    "image/jpeg": "jpg",
                    "image/png": "png",
                    "image/webp": "webp",
                    "image/gif": "gif"
                }[blob.type] || "jpg";
    
                const file = new File(
                    [blob],
                    `cover-${book.isbn}.${extension}`,
                    { type: blob.type }
                );
    
                const transfer = new DataTransfer();
                transfer.items.add(file);
                coverInput.files = transfer.files;
    
                coverInput.dispatchEvent(
                    new Event("change", { bubbles: true })
                );
    
                coverAdded = true;
    
            } catch (error) {
                console.warn("Could not automatically download book cover:", error);
            }
        }
    
        barcodeMessage.textContent = coverAdded
            ? "Book and cover added to the form. You can edit the details."
            : "Book added to the form. Please upload the cover manually if needed.";
    
        barcodePreview.hidden = true;
        isbnInput.value = "";
        currentBookMetadata = null;
    }


isbnLookupButton.addEventListener("click", lookupISBN);

isbnInput.addEventListener("keydown", event => {
    if (event.key === "Enter") {
        event.preventDefault();
        lookupISBN();
    }
});

    
    
    
async function monitorProcessing(status, submissionMessage, submissionStartedAt) {
    const storageKey = "libraryProcessingMonitor";

    const statusUrl =
        "https://library-submissions.thisamail2.workers.dev/status?since=" +
        encodeURIComponent(submissionStartedAt);

    let monitorData = JSON.parse(
        sessionStorage.getItem(storageKey) || "null"
    );

    if (!monitorData || monitorData.submissionStartedAt !== submissionStartedAt) {
        monitorData = {
            submissionMessage,
            submissionStartedAt,
            pendingReloaded: false
        };

        sessionStorage.setItem(storageKey, JSON.stringify(monitorData));
    }

    while (true) {
        try {
            const response = await fetch(statusUrl, {
                method: "GET",
                cache: "no-store"
            });

            if (!response.ok) {
                throw new Error("Status request failed.");
            }

            const data = await response.json();

            status.textContent =
                submissionMessage + "\\n\\n⏳ " + data.message;

            if (data.state === "pending_ready" && !monitorData.pendingReloaded) {
                document.getElementById("book-form-dialog")?.close();
                monitorData.pendingReloaded = true;
                sessionStorage.setItem(storageKey, JSON.stringify(monitorData));

                status.textContent =
                    submissionMessage +
                    "\\n\\n✓ New books are visible! Refreshing...";

                await new Promise(resolve => setTimeout(resolve, 1500));
                window.location.reload();
                return;
            }

            if (data.state === "completed") {
                sessionStorage.removeItem(storageKey);

                status.textContent =
                    submissionMessage +
                    "\\n\\n✓ Library fully updated! Refreshing...";

                await new Promise(resolve => setTimeout(resolve, 1500));
                window.location.reload();
                return;
            }

            if (data.state === "failed") {
                sessionStorage.removeItem(storageKey);
                status.textContent += "\\n\\nWorkflow details: " + data.url;
                return;
            }

        } catch (error) {
            status.textContent =
                submissionMessage +
                "\\n\\n⚠ Could not retrieve status. Retrying...";
        }

        await new Promise(resolve => setTimeout(resolve, 1000));
    }
}

// Resume monitoring after a page reload
const savedMonitor = sessionStorage.getItem("libraryProcessingMonitor");

if (savedMonitor) {
    try {
        const monitorData = JSON.parse(savedMonitor);
        const status = document.getElementById("submission-status");

        if (status) {
            monitorProcessing(
                status,
                monitorData.submissionMessage,
                monitorData.submissionStartedAt
            );
        }
    } catch (error) {
        sessionStorage.removeItem("libraryProcessingMonitor");
    }
}



    
function updateBookNumbers() {
    entries.querySelectorAll(".book-entry").forEach((entry, index) => {
        entry.querySelector(".entry-number").textContent = `Book ${index + 1}`;
        updateEntrySummary(entry);
    });
}

function updateEntrySummary(entry) {
    const title = entry.querySelector('[name="title"]').value.trim();
    const author = entry.querySelector('[name="author"]').value.trim();
    const lang = entry.querySelector('[name="lang"]').value;

    const summary = entry.querySelector(".entry-summary");

    if (title || author) {
        summary.textContent = [title, author].filter(Boolean).join(" — ");
    } else {
        summary.textContent = "New book";
    }

    const languageLabel = lang === "de" ? "DE" : "EN";
    summary.textContent += ` · ${languageLabel}`;
}

function setEntryCollapsed(entry, collapsed) {
    entry.classList.toggle("collapsed", collapsed);

    const toggle = entry.querySelector(".entry-toggle");
    toggle.setAttribute("aria-expanded", String(!collapsed));
}

// Set up collapsible behavior and live summaries
function setupEntry(entry) {
    const toggle = entry.querySelector(".entry-toggle");

    toggle.addEventListener("click", () => {
        setEntryCollapsed(entry, !entry.classList.contains("collapsed"));
    });

    entry.querySelectorAll("input:not([type='file']), select").forEach(input => {
        input.addEventListener("input", () => updateEntrySummary(entry));
        input.addEventListener("change", () => updateEntrySummary(entry));
    });

    setupCoverInput(entry);
    updateEntrySummary(entry);
}


    function setupCoverInput(entry) {
        const coverInput = entry.querySelector('input[name="cover"]');
        const coverPreview = entry.querySelector(".cover-preview");

        coverInput.addEventListener("change", async function () {
            coverPreview.innerHTML = "";
            coverPreview.style.display = "none";
            entry.dataset.compressedCover = "";

            const file = coverInput.files[0];
            if (!file) return;

            if (!file.type.startsWith("image/")) {
                alert("Please select an image file.");
                coverInput.value = "";
                return;
            }

            const image = new Image();
            const objectUrl = URL.createObjectURL(file);
            image.src = objectUrl;

            try {
                await new Promise((resolve, reject) => {
                    image.onload = resolve;
                    image.onerror = reject;
                });

                const maxSize = 1200;
                const scale = Math.min(
                    1,
                    maxSize / Math.max(image.width, image.height)
                );

                const canvas = document.createElement("canvas");
                canvas.width = Math.round(image.width * scale);
                canvas.height = Math.round(image.height * scale);

                canvas.getContext("2d").drawImage(
                    image, 0, 0, canvas.width, canvas.height
                );

                const compressedCover = canvas.toDataURL("image/jpeg", 0.82);
                entry.dataset.compressedCover = compressedCover;

                const preview = document.createElement("img");
                preview.src = compressedCover;
                
                coverPreview.appendChild(preview);
                coverPreview.style.display = "block";
                
                // Update collapsed-card thumbnail
                const thumbnail = entry.querySelector(".entry-cover");
                thumbnail.src = compressedCover;

            } catch (error) {
                alert("Could not load this image.");
                coverInput.value = "";
            } finally {
                URL.revokeObjectURL(objectUrl);
            }
        });
    }

    // Set up the initial book entry
    setupEntry(entries.querySelector(".book-entry"));

    // Add another book
    
    addButton.addEventListener("click", function () {
        const allEntries = entries.querySelectorAll(".book-entry");
        const firstEntry = allEntries[0];
        const newEntry = firstEntry.cloneNode(true);
    
        // Collapse all existing entries
        allEntries.forEach(entry => setEntryCollapsed(entry, true));
    
        // Clear the cloned fields
        newEntry.querySelectorAll("input").forEach(input => {
            input.value = "";
        });
        
        const thumbnail = newEntry.querySelector(".entry-cover");
        thumbnail.removeAttribute("src");
        thumbnail.style.display = "none";
    
        newEntry.querySelector("select").value = "de";
        newEntry.querySelector(".cover-preview").innerHTML = "";
        newEntry.querySelector(".cover-preview").style.display = "none";
        newEntry.dataset.compressedCover = "";
    
        // Open the new entry
        newEntry.classList.remove("collapsed");
    
        entries.appendChild(newEntry);
        setupEntry(newEntry);
        updateBookNumbers();
    });


    // Submit all books
    document.getElementById("book-form").addEventListener("submit", async function (e) {
        e.preventDefault();

        const form = e.target;
        const button = document.getElementById("submit-book");
        const status = document.getElementById("submission-status");

        if (!isLocalTest && !turnstileToken) {
            status.textContent = "Please complete the security verification.";
            return;
        }

        const books = Array.from(
            entries.querySelectorAll(".book-entry")
        ).map(entry => ({
            title: entry.querySelector('[name="title"]').value.trim(),
            author: entry.querySelector('[name="author"]').value.trim(),
            publisher: entry.querySelector('[name="publisher"]').value.trim(),
            lang: entry.querySelector('[name="lang"]').value,
            cover: entry.dataset.compressedCover || null
        }));

        button.disabled = true;
        addButton.disabled = true;
        status.textContent = "Submitting...";

        try {
            const submissionStartedAt = new Date().toISOString();
            const response = await fetch(
                "https://library-submissions.thisamail2.workers.dev",
                {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        books: books,
                        turnstileToken: turnstileToken
                    })
                }
            );

            
            const result = await response.json();
            
            if (!response.ok && !result.duplicates) {
                throw new Error(result.error || "Submission failed.");
            }
            
            const messages = [];
            
            if (result.added && result.added.length) {
                messages.push("✓ Added:");
                result.added.forEach(book => {
                    messages.push(`• ${book.title} — ${book.author}`);
                });
            }
            
            if (result.duplicates && result.duplicates.length) {
                messages.push("");
                messages.push("Skipped (already exists):");
                result.duplicates.forEach(book => {
                    messages.push(`• ${book.title} — ${book.author}`);
                });
            }
            
            if (result.message) {
                messages.push("");
                messages.push(result.message);
            }
            
            status.textContent = messages.join("\\n");
            
            // Only reset the form if at least one book was successfully added
            if (result.success) {
                form.reset();
            
                entries.querySelectorAll(".book-entry:not(:first-child)").forEach(
                    entry => entry.remove()
                );
            
                const firstEntry = entries.querySelector(".book-entry");
                firstEntry.dataset.compressedCover = "";
                firstEntry.querySelector(".cover-preview").innerHTML = "";
                firstEntry.querySelector(".cover-preview").style.display = "none";
            
                const thumbnail = firstEntry.querySelector(".entry-cover");
                if (thumbnail) {
                    thumbnail.removeAttribute("src");
                    thumbnail.style.display = "none";
                }
            
                firstEntry.classList.remove("collapsed");
                updateBookNumbers();
            }
            if (result.success && result.deploymentTriggered) {
                monitorProcessing(status, messages.join("\\n"), submissionStartedAt);
            }
            
            if (!isLocalTest && typeof turnstile !== "undefined") {
                turnstile.reset();
            }
            turnstileToken = "";

            entries.querySelectorAll(".book-entry:not(:first-child)").forEach(
                entry => entry.remove()
            );

            entries.querySelector(".book-entry").dataset.compressedCover = "";
            entries.querySelector(".cover-preview").innerHTML = "";
            entries.querySelector(".cover-preview").style.display = "none";

            updateBookNumbers();
            if (!isLocalTest && typeof turnstile !== "undefined") {
                turnstile.reset();
            }
            turnstileToken = "";

        } catch (error) {
            status.textContent = error.message;
            if (!isLocalTest && typeof turnstile !== "undefined") {
                turnstile.reset();
            }
            turnstileToken = "";

        } finally {
            button.disabled = false;
            addButton.disabled = false;
        }
    });
    
    // Camera-based ISBN barcode scanning
    const startBarcodeScanButton = document.getElementById("start-barcode-scan");
    const stopBarcodeScanButton = document.getElementById("stop-barcode-scan");
    const barcodeVideo = document.getElementById("barcode-video");
    const barcodeVideoContainer = document.querySelector(".barcode-video-container");    
    let barcodeScanControls = null;
    let barcodeScanActive = false;

    function stopBarcodeScan() {
        barcodeScanActive = false;
    
        if (barcodeScanControls) {
            barcodeScanControls.stop();
            barcodeScanControls = null;
        }
    
        barcodeVideo.pause();
        barcodeVideo.srcObject = null;
    
        barcodeVideoContainer.hidden = true;
    
        startBarcodeScanButton.hidden = false;
        stopBarcodeScanButton.hidden = true;
    }

    
    startBarcodeScanButton.addEventListener("click", async () => {
        if (!window.isSecureContext) {
            barcodeMessage.textContent =
                "Camera access requires HTTPS or localhost.";
            return;
        }
    
        if (typeof ZXingBrowser === "undefined") {
            barcodeMessage.textContent =
                "Barcode scanner library could not be loaded.";
            return;
        }
    
        barcodeScanActive = true;
        startBarcodeScanButton.hidden = true;
        stopBarcodeScanButton.hidden = false;
    
        barcodeVideoContainer.hidden = false;
    
        barcodeMessage.textContent =
            "Starting camera… Point it at the ISBN barcode.";
    
        const reader = new ZXingBrowser.BrowserMultiFormatReader();
    
        try {
            const controls = await reader.decodeFromVideoDevice(
                undefined,
                barcodeVideo,
                async (result, error, callbackControls) => {
                    if (!result || !barcodeScanActive) return;
    
                    const isbn = normalizeISBN(result.getText());
    
                    if (!isValidISBN13(isbn)) {
                        barcodeMessage.textContent =
                            "Barcode detected, but it is not a valid ISBN-13. Keep scanning.";
                        return;
                    }
    
                    barcodeScanActive = false;
    
                    if (callbackControls) {
                        callbackControls.stop();
                    }
    
                    barcodeScanControls = null;
                    stopBarcodeScanButton.hidden = true;
                    startBarcodeScanButton.hidden = false;
    
                    barcodeVideo.pause();
                    barcodeVideo.srcObject = null;
                    barcodeVideoContainer.hidden = true;
    
                    isbnInput.value = isbn;
                    barcodeMessage.textContent =
                        "ISBN detected: " + isbn + ". Looking up book…";
    
                    await lookupISBN();
                }
            );
    
            barcodeScanControls = controls;
    
            // Handle the possibility that the user stopped while the camera
            // permission prompt was still open.
            if (!barcodeScanActive) {
                controls.stop();
                barcodeScanControls = null;
            }
        } catch (error) {
            console.error("Barcode scanner error:", error);
    
            stopBarcodeScan();
    
            barcodeMessage.textContent =
                error.name === "NotAllowedError"
                    ? "Camera access was denied. Please allow camera access in your browser."
                    : "Could not start the camera: " + error.message;
        }
    });
    
    stopBarcodeScanButton.addEventListener("click", () => {
        stopBarcodeScan();
        barcodeMessage.textContent = "Camera stopped.";
    });
    </script>
    """

    return (head(S["all"], site, S, lang, "") + f"""
<body>
<header class="top"><span>{esc(site)}</span></header>
<main class="wrap">
<div class="card">
<input id="q" class="search" type="search"
       placeholder="{esc(S["search"])}"
       aria-label="{esc(S["search"])}">
<p class="count" id="n" data-t="{esc(S["count"])}">{esc(count)}</p>

{form_html}

<div class="grid">{"".join(cards)}</div>
</div>
</main>

<script>
if (
    window.location.hostname !== "localhost" ||
    window.location.port !== "8000"
) {{
    const script = document.createElement("script");
    script.src =
        "https://challenges.cloudflare.com/turnstile/v0/api.js?onload=onTurnstileLoaded&render=explicit";
    script.async = true;
    script.defer = true;
    document.head.appendChild(script);
}}
</script>
<script src="https://unpkg.com/@zxing/browser@0.2.1"></script>
{submission_js}
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
def build(csv_path, out, base_url, site, lang, with_index, covers_dir, use_covers, ids_file, tags_out, cname, search=None, master_csv=None):
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


    # Books present in the master CSV but not yet in the linked CSV
    index_by_key = {
        norm_key(b["title"], b["author"]): b
        for b in books
    }
    index_books = []

    if master_csv and os.path.exists(master_csv):
        with open(master_csv, newline="", encoding="utf-8-sig") as f:
            master_rows = list(csv.DictReader(f))

        for raw in master_rows:
            row = {
                (k or "").strip().lower(): (v or "").strip()
                for k, v in raw.items()
            }

            title = row.get("title", "")
            author = row.get("author", "")

            if not title:
                continue

            key = norm_key(title, author)

            # Already present in the linked CSV
            # Use the existing book entry, or create a pending placeholder.
            if key in index_by_key:
                index_books.append(index_by_key[key])
                continue

            bid = store.get(key, row.get("id", ""))
            seen[key] = bid

            index_books.append(dict(
                id=bid,
                title=title,
                author=author,
                publisher=row.get("publisher", ""),
                links=[],
                absent=[],
                has_cover=False,
                processing=True
            ))

    css_source = os.path.join(os.path.dirname(os.path.abspath(__file__)), "style.css")
    with open(css_source, "r", encoding="utf-8") as src:
        css = src.read()

    with open(os.path.join(out, "style.css"), "w", encoding="utf-8") as f:
        f.write(css)
    for b in books:
        os.makedirs(os.path.join(out, b["id"]), exist_ok=True)
        with open(os.path.join(out, b["id"], "index.html"), "w", encoding="utf-8") as f:
            f.write(book_page(b, site, S, lang, with_index, search))
    root = index_page(index_books, site, S, lang) if with_index else simple_page(site, S["landing"], site, S, lang)
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
    ap.add_argument("--master-csv", help="master CSV containing books not yet linked")
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
          a.covers_dir, not a.no_covers, a.ids_file, a.tags_out, a.cname, search,
          a.master_csv)


if __name__ == "__main__":
    main()
