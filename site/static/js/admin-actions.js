
const PENDING_KEY = "library_pending_operations";
const ADMIN_TOKEN_KEY = "library_admin_token";

function getPendingOperations() {
  try {
    return JSON.parse(localStorage.getItem(PENDING_KEY) || "{}");
  } catch {
    return {};
  }
}

function savePendingOperations(operations) {
  localStorage.setItem(PENDING_KEY, JSON.stringify(operations));
}


function setBookProcessing(bookId, action) {
  document.querySelectorAll(`[data-book-id="${bookId}"]`).forEach((button) => {
    button.disabled = true;
  });

  document.querySelectorAll(".book-card-wrapper").forEach((wrapper) => {
    if (!wrapper.querySelector(`[data-book-id="${bookId}"]`)) return;

    wrapper.classList.add("is-processing");

    if (action === "restore") {
      wrapper.classList.add("is-restoring");
    }

    const cover = wrapper.querySelector(".cover");
    if (!cover) return;

    cover.dataset.processingLabel =
      action === "archive" ? "Archiving book…" : "Restoring book…";

    if (!cover.querySelector(".processing-book")) {
      const animatedBook = document.createElement("span");
      animatedBook.className = "processing-book";
      animatedBook.setAttribute("aria-hidden", "true");
      cover.appendChild(animatedBook);
    }
  });
}

async function checkWorkflow(runId) {
  const token = sessionStorage.getItem(ADMIN_TOKEN_KEY);

  const response = await fetch(`${SUBMISSIONS_URL}/admin/workflow-status`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${token}`
    },
    body: JSON.stringify({ runId })
  });

  const data = await response.json();

  if (response.status === 401) {
    sessionStorage.removeItem(ADMIN_TOKEN_KEY);
    window.location.reload();
    throw new Error("Session expired.");
  }

  if (!response.ok) {
    throw new Error(data.error || "Could not check workflow status.");
  }

  return data;
}

async function pollWorkflow(bookId, runId) {
  while (true) {
    await new Promise(resolve => setTimeout(resolve, 5000));

    // Stop if this operation was removed or replaced.
    const pending = getPendingOperations();

    if (!pending[bookId] || pending[bookId].runId !== runId) {
      return;
    }

    try {
      const data = await checkWorkflow(runId);

      if (data.status !== "completed") {
        continue;
      }

      delete pending[bookId];
      savePendingOperations(pending);

      if (data.conclusion === "success") {
        window.location.reload();
        return;
      }

      alert(`Website rebuild failed: ${data.conclusion || "Unknown result"}`);
      window.location.reload();
      return;

    } catch (error) {
      if (error.message === "Session expired.") return;

      console.error("Workflow status check failed:", error);
      // Retry on temporary network/API errors.
    }
  }
}

function restorePendingOperations() {
  const pending = getPendingOperations();

  for (const [bookId, operation] of Object.entries(pending)) {
    if (!operation.runId) continue;

    setBookProcessing(bookId, operation.action);
    pollWorkflow(bookId, operation.runId);
  }
}

document.addEventListener("click", (event) => {
  const link = event.target.closest("#archive-link, #library-link");

  if (!link) return;

  const url = new URL(link.href);
  url.searchParams.set("v", Date.now());

  link.href = url.href;
});

document.addEventListener("DOMContentLoaded", restorePendingOperations);

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
    const token = sessionStorage.getItem(ADMIN_TOKEN_KEY);

    const response = await fetch(`${SUBMISSIONS_URL}/admin/${action}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${token}`
      },
      body: JSON.stringify({ bookId })
    });

    const data = await response.json();

    if (!response.ok) {
      if (response.status === 401) {
        sessionStorage.removeItem(ADMIN_TOKEN_KEY);
        window.location.reload();
        return;
      }

      throw new Error(data.error || "Request failed.");
    }

    if (action === "refresh") {
      button.textContent = "Refresh started";
      button.closest(".book-card-wrapper").style.opacity = "0.5";
      alert(data.message || "Refresh started successfully.");
      return;
    }

    if (!data.runId) {
      throw new Error("The rebuild started, but GitHub did not return a workflow run ID.");
    }

    const pending = getPendingOperations();

    pending[bookId] = {
      runId: data.runId,
      action
    };

  savePendingOperations(pending);
  window.location.reload();

  } catch (error) {
    alert(error.message);
    button.disabled = false;
    button.textContent = originalText;
  }
});
