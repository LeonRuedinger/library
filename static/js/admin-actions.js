
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

  const wrappers = [
    ...document.querySelectorAll(".book-card-wrapper"),
    ...document.querySelectorAll(".book-manage-row")
  ];

  wrappers.forEach((element) => {
    if (
      element.classList.contains("book-manage-row") &&
      !element.querySelector(`[data-book-id="${bookId}"]`)
    ) return;

    const wrapper = element.classList.contains("book-manage-row")
      ? element.closest(".card")
      : element;

    if (!wrapper) return;

    wrapper.classList.add("is-processing", `is-${action}`);

    const cover = wrapper.querySelector(".cover");
    if (!cover) return;

    const labels = {
      archive: "Archiving…",
      restore: "Restoring…",
      delete: "Deleting…",
      refresh: "Refreshing links…"
    };

    cover.dataset.processingLabel = labels[action] || "Working…";

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

      const completedAction = pending[bookId].action;

      delete pending[bookId];
      savePendingOperations(pending);

      if (data.conclusion === "success") {
        if (
          completedAction === "delete" &&
          window.location.pathname.split("/").includes(bookId)
        ) {
          const libraryUrl = new URL("../", window.location.href);
          libraryUrl.searchParams.set("v", Date.now());
          window.location.replace(libraryUrl.href);
        } else {
          window.location.reload();
        }

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

function renderOperationNotifications() {
  const container = document.getElementById("operation-notifications");
  if (!container) return;

  container.replaceChildren();

  const labels = {
    archive: "Archiving book",
    restore: "Restoring book",
    delete: "Deleting book",
    refresh: "Refreshing audiobook links"
  };

  for (const [bookId, operation] of Object.entries(getPendingOperations())) {
    const notification = document.createElement("div");
    notification.className = "operation-notification";

    const message = document.createElement("span");
    message.textContent =
      `${labels[operation.action] || "Processing book"} (ID: ${bookId})…`;

    const spinner = document.createElement("span");
    spinner.className = "operation-spinner";
    spinner.setAttribute("aria-hidden", "true");

    notification.append(message, spinner);
    container.appendChild(notification);
  }
}

function restorePendingOperations() {
  renderOperationNotifications();

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

  if (!["archive", "restore", "refresh", "delete"].includes(action)) return;

  const labels = {
    archive: "Archive this book?",
    restore: "Restore this book?",
    refresh: "Refresh audiobook links for this book?",
    delete: "PERMANENTLY delete this book? This cannot be undone."
  };

  if (action !== "refresh" && !confirm(labels[action])) return;

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
