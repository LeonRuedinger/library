document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-admin-action]");

  if (!button) return;

  const action = button.dataset.adminAction;
  const bookId = button.dataset.bookId;

  if (!["archive", "restore", "refresh"].includes(action)) return;

  const labels = {
    archive: "Archive this book?",
    restore: "Restore this book?",
    refresh: "Refresh audiobook links for this book?"
  };

  if (!confirm(labels[action])) return;

  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "Working…";

  try {
    const token = sessionStorage.getItem("library_admin_token");

    const response = await fetch(
      `${SUBMISSIONS_URL}/admin/${action}`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${token}`
        },
        body: JSON.stringify({ bookId })
      }
    );

    const data = await response.json();

    if (!response.ok) {
      if (response.status === 401) {
        sessionStorage.removeItem("library_admin_token");
        window.location.reload();
        return;
      }

      throw new Error(data.error || "Request failed.");
    }

    if (action === "refresh") {
      button.textContent = "Refresh started";
    } else {
      button.textContent = action === "archive" ? "Archived" : "Restored";
    }

    // The Worker starts a rebuild for archive/restore.
    // Refresh starts its own workflow, which triggers a rebuild afterwards.
    button.closest(".book-card-wrapper").style.opacity = "0.5";

    alert(data.message || "Request completed successfully.");

  } catch (error) {
    alert(error.message);
    button.disabled = false;
    button.textContent = originalText;
  }
});