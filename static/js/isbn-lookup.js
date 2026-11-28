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

async function fetchBookMetadataByISBN(isbn) {
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

  const metadata = {
    isbn,
    title: info.title || "",
    author: info.authors?.join(", ") || "",
    publisher: info.publisher || "",
    language: info.language || "",
    cover: info.imageLinks?.thumbnail || ""
  };

  if (!metadata.title || !metadata.author) {
    return { ...metadata, incomplete: true };
  }

  return metadata;
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
    const info = await fetchBookMetadataByISBN(isbn);

    currentBookMetadata = {
      ...info,
      language: info.language === "de" ? "de" : "en"
    };

    if (info.incomplete) {
      renderManualBookLookup(
        info.isbn,
        info.language,
        info.cover
      );
      barcodeMessage.textContent =
        "ISBN found, but metadata is incomplete. Enter the title and author.";
    } else {
      renderBookPreview(currentBookMetadata);
      barcodeMessage.textContent =
        "Book found. Check the details before adding it.";
    }
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

  const availabilityContainer = createElement(
    "div",
    "audiobook-availability"
  );

  availabilityContainer.appendChild(
    createElement("strong", "barcode-book-availability-title", "Availability")
  );

  const availabilityResults = createElement("div");
  availabilityContainer.appendChild(availabilityResults);


  details.appendChild(availabilityContainer);

  const button = createElement("button", "barcode-book-add", "Add to form");
  button.type = "button";
  button.addEventListener("click", addBookMetadata);
  details.appendChild(button);

  checkAudiobookAvailability(book, availabilityResults);
  card.appendChild(details);
  barcodePreview.appendChild(card);
  barcodePreview.hidden = false;
}

const AUDIOBOOK_SEARCH_URLS = {
  audible: "https://www.audible.de/search?keywords={q}",
  storytel: "https://www.storytel.com/de/search/all?query={q}&formats=abook",
  bookbeat: "https://www.bookbeat.com/de/search?query={q}"
};

async function checkAudiobookAvailability(book, resultsContainer) {
  resultsContainer.innerHTML = "";
  resultsContainer.appendChild(
    createElement("p", "audiobook-loading", "Checking audiobook availability…")
  );

  const services = [
    { key: "audible", name: "Audible", logo: "images/audible.svg" },
    { key: "storytel", name: "Storytel", logo: "images/storytel.svg" },
    { key: "bookbeat", name: "BookBeat", logo: "images/bookbeat.svg" }
  ];

  try {
    console.log("Availability request:", {
      title: book.title,
      author: book.author,
      language: book.language
    });
    const response = await fetch(SUBMISSIONS_URL + "/availability", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        title: book.title,
        author: book.author,
        language: book.language
      })
    });

    const data = await response.json();
    console.log("Availability response:", data);
    if (!response.ok) {
      throw new Error(data.error || "Availability check failed.");
    }

    const query = encodeURIComponent(book.title + " " + book.author);
    const table = createElement("div", "audiobook-results");

    for (const service of services) {
      const result = data[service.key] || { status: "unknown" };
      const row = createElement("div", "audiobook-result");

      const name = createElement("span", "audiobook-service");
      const logo = createElement("img");
      logo.src = logo.src = "static/" + service.logo;
      logo.alt = service.name;
      name.appendChild(logo);

      const status = createElement("span", "audiobook-status");

      if (
        result.status === "found" &&
        (
          result.availability === "included" ||
          (service.key === "audible" && result.availability === "credit")
        )
      ) {
        status.textContent = "✓";
        status.classList.add("included");
        status.title = "Included";
      } else if (
        result.status === "found" &&
        result.availability === "purchase"
      ) {
        const cart = document.createElementNS(
          "http://www.w3.org/2000/svg",
          "svg"
        );

        cart.setAttribute("viewBox", "0 0 24 24");
        cart.setAttribute("fill", "none");
        cart.setAttribute("stroke", "currentColor");
        cart.setAttribute("stroke-width", "2");
        cart.setAttribute("stroke-linecap", "round");
        cart.setAttribute("stroke-linejoin", "round");

        const path = document.createElementNS(
          "http://www.w3.org/2000/svg",
          "path"
        );
        path.setAttribute("d", "M2 3h2l2.4 12.2a2 2 0 0 0 2 1.6h9.7a2 2 0 0 0 2-1.6L22 7H5");

        const wheels = ["10", "19"].map(x => {
          const circle = document.createElementNS(
            "http://www.w3.org/2000/svg",
            "circle"
          );
          circle.setAttribute("cx", x);
          circle.setAttribute("cy", "21");
          circle.setAttribute("r", "1");
          return circle;
        });

        cart.append(path, ...wheels);
        status.appendChild(cart);
        status.classList.add("cart");
        status.title = "Purchase required";
      } else {
        const template = AUDIOBOOK_SEARCH_URLS[service.key];

        const searchLink = createElement("a", "manual-search", "?");
        searchLink.href = template.replace("{q}", query);
        searchLink.target = "_blank";
        searchLink.rel = "noopener noreferrer";
        searchLink.title = "Search manually on " + service.name;
        searchLink.setAttribute(
          "aria-label",
          "Search manually on " + service.name
        );

        status.appendChild(searchLink);
      }

      row.append(name, status);
      table.appendChild(row);
    }

    resultsContainer.innerHTML = "";
    resultsContainer.appendChild(table);

  } catch (error) {
    console.error("Audiobook availability check failed:", error);

    resultsContainer.innerHTML = "";

    const message = createElement(
      "p",
      "audiobook-loading",
      "Availability could not be checked."
    );
    resultsContainer.appendChild(message);
  }
}

function renderManualBookLookup(isbn, language, cover) {
  barcodePreview.innerHTML = "";

  const card = createElement("div", "barcode-book-card");
  const details = createElement("div", "barcode-book-details");

  details.appendChild(
    createElement("strong", "barcode-book-title", "Complete book details")
  );

  details.appendChild(
    createElement(
      "p",
      "barcode-book-author",
      "Google Books found this ISBN, but the metadata is incomplete. Please enter the missing details."
    )
  );

  const titleLabel = createElement("label", "", "Title *");
  const titleInput = createElement("input", "manual-book-title");
  titleInput.type = "text";
  titleInput.required = true;
  titleInput.maxLength = 250;
  titleInput.placeholder = "Book title";

  const authorLabel = createElement("label", "", "Author *");
  const authorInput = createElement("input", "manual-book-author");
  authorInput.type = "text";
  authorInput.required = true;
  authorInput.maxLength = 200;
  authorInput.placeholder = "Author";

  const button = createElement(
    "button",
    "barcode-book-add",
    "Continue"
  );
  button.type = "button";

  button.addEventListener("click", () => {
    const title = titleInput.value.trim();
    const author = authorInput.value.trim();

    if (!title || !author) {
      barcodeMessage.textContent = "Please enter both title and author.";
      return;
    }

    currentBookMetadata = {
      isbn,
      title,
      author,
      publisher: "",
      language: language === "de" ? "de" : "en",
      cover
    };

    renderBookPreview(currentBookMetadata);

    barcodeMessage.textContent =
      "Book details ready. Check the metadata and audiobook availability.";
  });

  details.append(
    titleLabel,
    titleInput,
    authorLabel,
    authorInput,
    button
  );

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
