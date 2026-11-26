// Full-dialog overlay shown while a submission is being processed.
const submissionOverlay = document.getElementById("submission-overlay");
const overlayTitle = document.getElementById("submission-overlay-title");
const overlayMessage = document.getElementById("submission-overlay-message");

function showSubmissionOverlay(title, message) {
  overlayTitle.textContent = title;
  overlayMessage.textContent = message;
  submissionOverlay.hidden = false;
  closeBookForm.hidden = true;
}

function hideSubmissionOverlay() {
  submissionOverlay.hidden = true;
  closeBookForm.hidden = false;
}
