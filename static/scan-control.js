import { checkedRequest, requestJSON, createPolling } from "./js/api.js";

const scanButton = document.getElementById("remote-scan-btn");
const statusText = document.getElementById("remote-status");
const progress = document.getElementById("remote-progress");
const progressBar = document.getElementById("remote-progress-bar");

const stageLabels = {
  discovering: "Looking for the scanner…",
  scanning: "Scanning…",
  transferring: "Transferring the image…",
  detecting: "Finding photos in the scan…",
};

function setProgress(stage, done, total) {
  progress.classList.toggle("visible", stage !== "idle" && stage !== "error" && stage !== "review");
  if (stage === "transferring" && total) {
    const percent = Math.min(100, Math.round((done / total) * 100));
    progress.classList.remove("indeterminate");
    progressBar.style.width = `${percent}%`;
    progress.setAttribute("aria-valuenow", percent);
  } else if (progress.classList.contains("visible")) {
    progress.classList.add("indeterminate");
    progress.removeAttribute("aria-valuenow");
  } else {
    progress.classList.remove("indeterminate");
    progress.removeAttribute("aria-valuenow");
  }
}

async function refreshStatus() {
  try {
    const state = await requestJSON("/api/scan/status", {}, { errorMessage: "Couldn't get scan status" });
    const active = ["discovering", "scanning", "transferring", "detecting"].includes(state.stage);
    scanButton.disabled = active;
    setProgress(state.stage, state.done, state.total);

    if (state.stage === "review") {
      statusText.textContent = "Scan complete. Review is ready on the main screen; you can start another scan.";
      scanButton.textContent = "Scan again";
    } else if (state.stage === "detecting") {
      setProgress("idle");
      statusText.textContent = "Scan complete. Preparing the review on the main screen; you can start another scan.";
      scanButton.textContent = "Scan again";
    } else if (state.stage === "error") {
      statusText.textContent = state.error || "The scan failed. You can try again.";
      scanButton.textContent = "Try again";
    } else if (active) {
      statusText.textContent =
        state.stage === "transferring" && state.total
          ? `Transferring the image… ${Math.min(100, Math.round((state.done / state.total) * 100))}%`
          : stageLabels[state.stage] || "Scanning…";
      scanButton.textContent = "Scan in progress";
    } else {
      statusText.textContent = "Ready — place photos on the scanner, then start a scan.";
      scanButton.textContent = "Scan now";
    }
  } catch (error) {
    statusText.textContent = error.message;
  }
}

const polling = createPolling(refreshStatus, 1000);

scanButton.addEventListener("click", async () => {
  scanButton.disabled = true;
  statusText.textContent = "Starting scan…";
  try {
    await checkedRequest("/api/scan", { method: "POST" }, { errorMessage: "Couldn't start the scan", detail: true });
    await polling.refresh();
  } catch (error) {
    statusText.textContent = error.message;
    await polling.refresh();
  }
});

polling.refresh();
polling.start();
