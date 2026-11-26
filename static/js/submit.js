// Submitting all book entries to the worker.
function buildResultMessage(result) {
  const lines = [];

  if (result.added?.length) {
    lines.push("✓ Added:");
    result.added.forEach(book => lines.push(`• ${book.title} — ${book.author}`));
  }

  if (result.duplicates?.length) {
    lines.push("", "Skipped (already exists):");
    result.duplicates.forEach(book => lines.push(`• ${book.title} — ${book.author}`));
  }

  if (result.message) {
    lines.push("", result.message);
  }

  return lines.join("\n");
}

document.getElementById("book-form").addEventListener("submit", async event => {
  event.preventDefault();

  const form = event.target;
  const button = document.getElementById("submit-book");
  const status = document.getElementById("submission-status");

  if (!isLocalTest && !turnstileToken) {
    status.textContent = "Please complete the security verification.";
    return;
  }

  button.disabled = true;
  addButton.disabled = true;
  status.textContent = "Submitting...";

  try {
    const submissionStartedAt = new Date().toISOString();

    const response = await fetch(SUBMISSIONS_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ books: collectBooks(), turnstileToken })
    });

    const result = await response.json();

    if (!response.ok && !result.duplicates) {
      throw new Error(result.error || "Submission failed.");
    }

    const message = buildResultMessage(result);
    status.textContent = message;

    // Only clear the form if at least one book was added.
    if (result.success) {
      form.reset();
      resetBookEntries();
    }

    if (result.success && result.deploymentTriggered) {
      showSubmissionOverlay(
        "Preparing your books",
        "Your submission was successful. Preparing the updated library..."
      );
      monitorProcessing(status, message, submissionStartedAt);
    }
  } catch (error) {
    status.textContent = error.message;
  } finally {
    resetTurnstile();
    button.disabled = false;
    addButton.disabled = false;
  }
});
