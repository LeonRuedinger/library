// Polls the worker until the submitted books are published, then reloads the page.
// The state is kept in sessionStorage so it survives the reload.
const MONITOR_STORAGE_KEY = "libraryProcessingMonitor";

const STAGE_TITLES = {
  queued: "Waiting for processing",
  preparing: "Preparing your books",
  processing: "Processing your books",
  publishing_final: "Publishing the library",
  pending_ready: "Your books are ready!"
};

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function monitorProcessing(status, submissionMessage, submissionStartedAt) {
  const statusUrl = STATUS_URL + "?since=" + encodeURIComponent(submissionStartedAt);

  let monitorData = JSON.parse(sessionStorage.getItem(MONITOR_STORAGE_KEY) || "null");

  if (!monitorData || monitorData.submissionStartedAt !== submissionStartedAt) {
    monitorData = { submissionMessage, submissionStartedAt, pendingReloaded: false };
    sessionStorage.setItem(MONITOR_STORAGE_KEY, JSON.stringify(monitorData));
  }

  while (true) {
    try {
      const response = await fetch(statusUrl, { method: "GET", cache: "no-store" });
      if (!response.ok) throw new Error("Status request failed.");

      const data = await response.json();

      showSubmissionOverlay(
        STAGE_TITLES[data.stage] || "Processing your books",
        data.message
      );

      if (data.state === "pending_ready" && !monitorData.pendingReloaded) {
        document.getElementById("book-form-dialog")?.close();
        monitorData.pendingReloaded = true;
        sessionStorage.setItem(MONITOR_STORAGE_KEY, JSON.stringify(monitorData));

        status.textContent = submissionMessage + "\n\n✓ New books are visible! Refreshing...";
        await sleep(1500);
        window.location.reload();
        return;
      }

      if (data.state === "completed") {
        sessionStorage.removeItem(MONITOR_STORAGE_KEY);

        status.textContent = submissionMessage + "\n\n✓ Library fully updated! Refreshing...";
        await sleep(1500);
        window.location.reload();
        return;
      }

      if (data.state === "failed") {
        sessionStorage.removeItem(MONITOR_STORAGE_KEY);

        showSubmissionOverlay(
          "Processing failed",
          "Something went wrong while updating the library. " +
          "Please check the workflow details."
        );
        overlayMessage.textContent += " " + data.url;
        return;
      }
    } catch (error) {
      status.textContent = submissionMessage + "\n\n⚠ Could not retrieve status. Retrying...";
      hideSubmissionOverlay();
    }

    await sleep(1000);
  }
}

// Resume monitoring after a page reload.
(() => {
  const saved = sessionStorage.getItem(MONITOR_STORAGE_KEY);
  if (!saved) return;

  try {
    const data = JSON.parse(saved);
    const status = document.getElementById("submission-status");
    if (status) {
      monitorProcessing(status, data.submissionMessage, data.submissionStartedAt);
    }
  } catch (error) {
    sessionStorage.removeItem(MONITOR_STORAGE_KEY);
  }
})();
