import { checkedRequest, requestJSON, createPolling } from "./api.js";

export function initScanStatus({ getReviewRaw, openReview }) {
  const statusEl = document.getElementById("status");
  const scanBtn = document.getElementById("scan-btn");
  const scanProgressEl = document.getElementById("scan-progress");
  const scanProgressBarEl = document.getElementById("scan-progress-bar");

  const SCAN_STAGE_LABELS = {
    discovering: "Discovering scanner…",
    scanning: "Scanning… (the scanner doesn't report finer progress here)",
    transferring: "Transferring image…",
    detecting: "Detecting photos…",
  };

  function setScanProgress(stage, done, total) {
    if (stage === "idle") {
      scanProgressEl.classList.remove("visible", "indeterminate");
      scanProgressEl.removeAttribute("aria-valuenow");
      return;
    }
    scanProgressEl.classList.add("visible");
    if (stage === "transferring" && total) {
      scanProgressEl.classList.remove("indeterminate");
      const pct = Math.min(100, Math.round((done / total) * 100));
      scanProgressBarEl.style.width = `${pct}%`;
      scanProgressEl.setAttribute("aria-valuenow", pct);
    } else {
      scanProgressEl.classList.add("indeterminate");
      scanProgressEl.removeAttribute("aria-valuenow");
    }
  }

  let lastObservedScanStage = null;

  async function refreshScanStatus() {
    try {
      const state = await requestJSON("/api/scan/status", {}, { errorMessage: "Couldn't get scan status" });
      const busy = ["discovering", "scanning", "transferring", "detecting", "review"].includes(state.stage);
      setScanBtnBlocked(busy);
      const pendingReview = state.pending_review;
      if (pendingReview && pendingReview.raw !== getReviewRaw()) {
        openReview(pendingReview.raw, pendingReview.quads || [], pendingReview.expected_count ?? null);
      }

      if (state.stage === "error") {
        setScanProgress("idle");
        if (lastObservedScanStage !== "error") statusEl.textContent = state.error || "Scan failed";
      } else if (state.stage === "review") {
        setScanProgress("idle");
        if (pendingReview) statusEl.textContent = "Scan complete — review the split.";
      } else if (busy) {
        statusEl.textContent =
          state.stage === "transferring" && state.total
            ? `Transferring image… ${Math.min(100, Math.round((state.done / state.total) * 100))}%`
            : SCAN_STAGE_LABELS[state.stage] || "Scanning…";
        setScanProgress(state.stage, state.done, state.total);
      } else {
        setScanProgress("idle");
      }
      lastObservedScanStage = state.stage;
    } catch (error) {
      statusEl.textContent = error.message;
    }
  }

  const polling = createPolling(refreshScanStatus, 700);

  scanBtn.addEventListener(
    "click",
    async () => {
      scanBtn.disabled = true;
      statusEl.textContent = "Starting scan…";
      try {
        await checkedRequest("/api/scan", { method: "POST" }, { errorMessage: "Scan failed", detail: true });
        await polling.refresh();
      } catch (e) {
        statusEl.textContent = e.message;
        await polling.refresh();
      }
    }
  );

  function setScanBtnBlocked(blocked) {
    scanBtn.disabled = blocked;
    scanBtn.title = blocked ? "Finish reviewing the pending scan first" : "";
  }

  return {
    setBlocked: setScanBtnBlocked,
    setStatus: (message) => { statusEl.textContent = message; },
    start: () => { polling.start(); return polling.refresh(); },
  };
}
