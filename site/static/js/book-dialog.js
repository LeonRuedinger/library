// Opening and closing the "Add books" dialog.
const bookDialog = document.getElementById("book-form-dialog");
const openBookForm = document.getElementById("open-book-form");
const closeBookForm = document.getElementById("close-book-form");

openBookForm.addEventListener("click", () => bookDialog.showModal());

closeBookForm.addEventListener("click", () => {
  if (!submissionOverlay.hidden) return;  // busy: don't close
  bookDialog.close();
});

// Click on the backdrop closes the dialog.
bookDialog.addEventListener("click", event => {
  if (event.target === bookDialog) bookDialog.close();
});
