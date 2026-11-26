// The list of book entries inside the form (add, collapse, summaries, covers).
const entries = document.getElementById("book-entries");
const addButton = document.getElementById("add-book");

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

  summary.textContent = (title || author)
    ? [title, author].filter(Boolean).join(" — ")
    : "New book";
  summary.textContent += ` · ${lang === "de" ? "DE" : "EN"}`;
}

function setEntryCollapsed(entry, collapsed) {
  entry.classList.toggle("collapsed", collapsed);
  entry.querySelector(".entry-toggle")
    .setAttribute("aria-expanded", String(!collapsed));
}

function clearCoverPreview(entry) {
  const preview = entry.querySelector(".cover-preview");
  preview.innerHTML = "";
  preview.style.display = "none";
  entry.dataset.compressedCover = "";

  const thumbnail = entry.querySelector(".entry-cover");
  if (thumbnail) {
    thumbnail.removeAttribute("src");
    thumbnail.style.display = "none";
  }
}

// Shrinks the chosen image to max 1200px and keeps it as a JPEG data URL.
function setupCoverInput(entry) {
  const coverInput = entry.querySelector('input[name="cover"]');
  const coverPreview = entry.querySelector(".cover-preview");

  coverInput.addEventListener("change", async () => {
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
      const scale = Math.min(1, maxSize / Math.max(image.width, image.height));

      const canvas = document.createElement("canvas");
      canvas.width = Math.round(image.width * scale);
      canvas.height = Math.round(image.height * scale);
      canvas.getContext("2d").drawImage(image, 0, 0, canvas.width, canvas.height);

      const compressedCover = canvas.toDataURL("image/jpeg", 0.82);
      entry.dataset.compressedCover = compressedCover;

      const preview = document.createElement("img");
      preview.src = compressedCover;
      coverPreview.appendChild(preview);
      coverPreview.style.display = "block";

      // thumbnail on the collapsed card
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

function setupEntry(entry) {
  entry.querySelector(".entry-toggle").addEventListener("click", () => {
    setEntryCollapsed(entry, !entry.classList.contains("collapsed"));
  });

  entry.querySelectorAll("input:not([type='file']), select").forEach(input => {
    input.addEventListener("input", () => updateEntrySummary(entry));
    input.addEventListener("change", () => updateEntrySummary(entry));
  });

  setupCoverInput(entry);
  updateEntrySummary(entry);
}

// Back to a single empty entry (after a successful submission).
function resetBookEntries() {
  entries.querySelectorAll(".book-entry:not(:first-child)")
    .forEach(entry => entry.remove());

  const first = entries.querySelector(".book-entry");
  clearCoverPreview(first);
  setEntryCollapsed(first, false);
  updateBookNumbers();
}

function collectBooks() {
  return [...entries.querySelectorAll(".book-entry")].map(entry => ({
    title: entry.querySelector('[name="title"]').value.trim(),
    author: entry.querySelector('[name="author"]').value.trim(),
    publisher: entry.querySelector('[name="publisher"]').value.trim(),
    lang: entry.querySelector('[name="lang"]').value,
    cover: entry.dataset.compressedCover || null
  }));
}

addButton.addEventListener("click", () => {
  const allEntries = entries.querySelectorAll(".book-entry");
  const newEntry = allEntries[0].cloneNode(true);

  allEntries.forEach(entry => setEntryCollapsed(entry, true));

  newEntry.querySelectorAll("input").forEach(input => { input.value = ""; });
  newEntry.querySelector("select").value = "de";
  clearCoverPreview(newEntry);
  newEntry.classList.remove("collapsed");

  entries.appendChild(newEntry);
  setupEntry(newEntry);
  updateBookNumbers();
});

setupEntry(entries.querySelector(".book-entry"));
