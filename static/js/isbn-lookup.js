// Look up a book by ISBN-13 (Google Books via a worker) and copy it into the form.
const isbnInput = document.getElementById("barcode-isbn");
const isbnLookupButton = document.getElementById("lookup-isbn");
const barcodeMessage = document.getElementById("barcode-message");
const barcodePreview = document.getElementById("barcode-preview");

let currentBookMetadata = null;

function normalizeISBN(value) {
  return value.replace(/[\s-]/g, "");
}

function isValidISBN13(isbn) {
  if (!/^97[89]\d{10}$/.test(isbn)) return false;

  const sum = [...isbn].reduce(
    (total, digit, index) => total + Number(digit) * (index % 2 === 0 ? 1 : 3),
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
    barcodeMessage.textContent = "Please enter a valid ISBN-13.";
    return;
  }

  isbnLookupButton.disabled = true;
  barcodeMessage.textContent = "Looking up book...";

  try {
    const response = await fetch(
      GOOGLE_BOOKS_URL + "?isbn=" + encodeURIComponent(isbn)
    );

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
      item.volumeInfo?.industryIdentifiers?.some(id =>
        id.type === "ISBN_13" && id.identifier === isbn
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
    barcodeMessage.textContent = "Book found. Check the details before adding it.";
  } catch (error) {
    barcodeMessage.textContent = error.message;
  } finally {
    isbnLookupButton.disabled = false;
  }
}

function createElement(tag, className, text) {
  const element = document.createElement(tag);
  element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function renderBookPreview(book) {
  barcodePreview.innerHTML = "";

  const card = createElement("div", "barcode-book-card");

  if (book.cover) {
    const image = createElement("img", "barcode-book-cover");
    image.src = book.cover.replace("http://", "https://");
    image.alt = "Cover of " + book.title;
    card.appendChild(image);
  }

  const details = createElement("div", "barcode-book-details");
  details.appendChild(createElement("strong", "barcode-book-title", book.title));
  details.appendChild(createElement("p", "barcode-book-author", book.author));
  if (book.publisher) {
    details.appendChild(createElement("p", "barcode-book-publisher", book.publisher));
  }

  const button = createElement("button", "barcode-book-add", "Add to form");
  button.type = "button";
  button.addEventListener("click", addBookMetadata);
  details.appendChild(button);

  card.appendChild(details);
  barcodePreview.appendChild(card);
  barcodePreview.hidden = false;
}

// Downloads the cover through the proxy and puts it into the entry's file input.
async function attachCover(book, coverInput) {
  const coverUrl =
    COVER_PROXY_URL + "?url=" +
    encodeURIComponent(book.cover.replace(/^http:\/\//i, "https://"));

  const response = await fetch(coverUrl);
  if (!response.ok) throw new Error("Cover download failed.");

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

  const file = new File([blob], `cover-${book.isbn}.${extension}`, { type: blob.type });
  const transfer = new DataTransfer();
  transfer.items.add(file);
  coverInput.files = transfer.files;
  coverInput.dispatchEvent(new Event("change", { bubbles: true }));
}

async function addBookMetadata() {
  if (!currentBookMetadata) return;

  const lastEntry = () => [...entries.querySelectorAll(".book-entry")].pop();
  let target = lastEntry();

  // Reuse the last entry if it is still empty, otherwise add a new one.
  const hasContent = [...target.querySelectorAll(
    'input[name="title"], input[name="author"]'
  )].some(input => input.value.trim());

  if (hasContent) {
    addButton.click();
    target = lastEntry();
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
      barcodeMessage.textContent = "Adding book and downloading cover...";
      await attachCover(book, coverInput);
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
