// Camera-based ISBN barcode scanning (needs the ZXing library loaded first).
const startBarcodeScanButton = document.getElementById("start-barcode-scan");
const stopBarcodeScanButton = document.getElementById("stop-barcode-scan");
const barcodeVideo = document.getElementById("barcode-video");
const barcodeVideoContainer = document.querySelector(".barcode-video-container");

let barcodeScanControls = null;
let barcodeScanActive = false;

function stopBarcodeScan() {
  barcodeScanActive = false;

  if (barcodeScanControls) {
    barcodeScanControls.stop();
    barcodeScanControls = null;
  }

  barcodeVideo.pause();
  barcodeVideo.srcObject = null;
  barcodeVideoContainer.hidden = true;

  startBarcodeScanButton.hidden = false;
  stopBarcodeScanButton.hidden = true;
}

startBarcodeScanButton.addEventListener("click", async () => {
  if (!window.isSecureContext) {
    barcodeMessage.textContent = "Camera access requires HTTPS or localhost.";
    return;
  }

  if (typeof ZXingBrowser === "undefined") {
    barcodeMessage.textContent = "Barcode scanner library could not be loaded.";
    return;
  }

  barcodeScanActive = true;
  startBarcodeScanButton.hidden = true;
  stopBarcodeScanButton.hidden = false;
  barcodeVideoContainer.hidden = false;
  barcodeMessage.textContent = "Starting camera… Point it at the ISBN barcode.";

  const reader = new ZXingBrowser.BrowserMultiFormatReader();

  try {
    const controls = await reader.decodeFromVideoDevice(
      undefined,
      barcodeVideo,
      async (result, error, callbackControls) => {
        if (!result || !barcodeScanActive) return;

        const isbn = normalizeISBN(result.getText());

        if (!isValidISBN13(isbn)) {
          barcodeMessage.textContent =
            "Barcode detected, but it is not a valid ISBN-13. Keep scanning.";
          return;
        }

        barcodeScanActive = false;
        if (callbackControls) callbackControls.stop();
        barcodeScanControls = null;

        stopBarcodeScanButton.hidden = true;
        startBarcodeScanButton.hidden = false;
        barcodeVideo.pause();
        barcodeVideo.srcObject = null;
        barcodeVideoContainer.hidden = true;

        isbnInput.value = isbn;
        barcodeMessage.textContent = "ISBN detected: " + isbn + ". Looking up book…";
        await lookupISBN();
      }
    );

    barcodeScanControls = controls;

    // The user may have pressed "Stop" while the permission prompt was open.
    if (!barcodeScanActive) {
      controls.stop();
      barcodeScanControls = null;
    }
  } catch (error) {
    console.error("Barcode scanner error:", error);
    stopBarcodeScan();

    barcodeMessage.textContent = error.name === "NotAllowedError"
      ? "Camera access was denied. Please allow camera access in your browser."
      : "Could not start the camera: " + error.message;
  }
});

stopBarcodeScanButton.addEventListener("click", () => {
  stopBarcodeScan();
  barcodeMessage.textContent = "Camera stopped.";
});
