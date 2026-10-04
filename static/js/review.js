import { checkedRequest, requestJSON } from "./api.js";
import { anyModalOpen, trapFocus, withLoading } from "./modals.js";

export function initReview({ askConfirm, setScanBlocked, setStatus, loadPhotos }) {
  const reviewModal = document.getElementById("review-modal");
  const reviewPanel = reviewModal.querySelector(".modal-panel");
  const reviewCanvas = document.getElementById("review-canvas");
  const reviewCtx = reviewCanvas.getContext("2d");
  const reviewListEl = document.getElementById("review-list");
  const reviewErrorEl = document.getElementById("review-error");
  const reviewDiscardBtn = document.getElementById("review-discard");
  const reviewConfirmBtn = document.getElementById("review-confirm");
  const reviewDetectionForm = document.getElementById("review-detection-form");
  const reviewPhotoCountInput = document.getElementById("review-photo-count");
  const reviewDetectBtn = document.getElementById("review-detect");
  const reviewDetectionStatusEl = document.getElementById("review-detection-status");
  const reviewBody = reviewModal.querySelector(".review-body");
  const PHOTO_COUNT_STORAGE_KEY = "mass-scanner.photo-count";

  const HANDLE_RADIUS = 7;
  const MIN_QUAD_SIZE = 10;
  const REVIEW_COLORS = [
    { solid: "#ff5a5f", fill: "rgba(255, 90, 95, 0.18)" },
    { solid: "#00a6a6", fill: "rgba(0, 166, 166, 0.18)" },
    { solid: "#e7a900", fill: "rgba(231, 169, 0, 0.2)" },
    { solid: "#8b5cf6", fill: "rgba(139, 92, 246, 0.18)" },
    { solid: "#e83e8c", fill: "rgba(232, 62, 140, 0.18)" },
    { solid: "#35a853", fill: "rgba(53, 168, 83, 0.18)" },
    { solid: "#3478d4", fill: "rgba(52, 120, 212, 0.18)" },
    { solid: "#f07824", fill: "rgba(240, 120, 36, 0.18)" },
  ];

  let reviewImage = null;
  let reviewRaw = null;
  let reviewQuads = []; // each selection has four corners in raw-image pixel space
  let reviewScale = 1;
  let reviewLastFocus = null;
  let reviewDrag = null;
  let reviewExpectedCount = null;
  let reviewDetecting = false;
  let reviewDetectionController = null;

  function preferredPhotoCount() {
    try {
      const saved = Number(localStorage.getItem(PHOTO_COUNT_STORAGE_KEY));
      return Number.isInteger(saved) && saved >= 1 && saved <= 20 ? saved : null;
    } catch {
      return null;
    }
  }

  function setReviewDetecting(busy) {
    reviewDetecting = busy;
    reviewDetectBtn.disabled = busy;
    reviewDetectBtn.textContent = busy ? "Detecting…" : "Detect again";
    reviewPhotoCountInput.disabled = busy;
    reviewDiscardBtn.disabled = busy;
    reviewConfirmBtn.disabled = busy || reviewQuads.length === 0;
    reviewBody.setAttribute("aria-busy", String(busy));
    reviewListEl.querySelectorAll("button").forEach((button) => { button.disabled = busy; });
  }

  async function detectReviewPhotos() {
    if (reviewDetecting || !reviewDetectionForm.reportValidity()) return;
    const raw = reviewRaw;
    const expectedCount = reviewPhotoCountInput.value === "" ? null : Number(reviewPhotoCountInput.value);
    const controller = new AbortController();
    reviewDetectionController = controller;
    reviewDrag = null;
    reviewErrorEl.textContent = "";
    setReviewDetecting(true);
    reviewDetectionStatusEl.textContent = "Detecting photos…";
    try {
      const result = await requestJSON(`/api/raw/${encodeURIComponent(raw)}/detect`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expected_count: expectedCount }),
        signal: controller.signal,
      }, { errorMessage: "Couldn't detect photos", detail: "string" });
      if (reviewRaw !== raw || controller !== reviewDetectionController) return;
      reviewQuads = result.quads.map((quad) => quad.map(clampToImage));
      reviewExpectedCount = result.expected_count;
      try {
        localStorage.setItem(PHOTO_COUNT_STORAGE_KEY, expectedCount === null ? "" : String(expectedCount));
      } catch {
        // Cropping remains available when the browser blocks preference storage.
      }
      drawReview();
    } catch (error) {
      if (error.name !== "AbortError" && reviewRaw === raw) reviewErrorEl.textContent = error.message;
    } finally {
      if (controller === reviewDetectionController) {
        reviewDetectionController = null;
        setReviewDetecting(false);
        renderReviewList();
      }
    }
  }

  reviewDetectionForm.addEventListener("submit", (event) => {
    event.preventDefault();
    detectReviewPhotos();
  });


  function openReviewModal(raw, quads, expectedCount = null) {
    if (reviewDetectionController) reviewDetectionController.abort();
    reviewDetectionController = null;
    reviewRaw = raw;
    reviewExpectedCount = expectedCount;
    reviewPhotoCountInput.value = expectedCount ?? preferredPhotoCount() ?? "";
    reviewQuads = (quads || []).map((q) => q.map(([x, y]) => [x, y]));
    setReviewDetecting(false);
    reviewLastFocus = document.activeElement;
    reviewErrorEl.textContent = "";
    setScanBlocked(true);
    reviewImage = new Image();
    reviewImage.onload = () => {
      const maxW = Math.min(window.innerWidth * 0.85, 900);
      reviewScale = Math.min(1, maxW / reviewImage.width);
      reviewCanvas.width = reviewImage.width * reviewScale;
      reviewCanvas.height = reviewImage.height * reviewScale;
      // Detection can propose a corner slightly outside the scan (a rotated
      // bounding box overshooting the edge); clamp once the image size is known.
      reviewQuads = reviewQuads.map((q) => q.map(clampToImage));
      drawReview();
      renderReviewList();
      reviewModal.classList.remove("hidden");
      document.body.classList.add("modal-open");
      reviewPanel.focus();
      const preferredCount = reviewPhotoCountInput.value === "" ? null : Number(reviewPhotoCountInput.value);
      if (preferredCount !== expectedCount) detectReviewPhotos();
    };
    reviewImage.src = `/raw/${encodeURIComponent(raw)}?t=${Date.now()}`;
  }

  function closeReviewModal() {
    if (reviewDetectionController) reviewDetectionController.abort();
    reviewDetectionController = null;
    setReviewDetecting(false);
    reviewModal.classList.add("hidden");
    if (!anyModalOpen(reviewModal)) document.body.classList.remove("modal-open");
    if (reviewLastFocus) reviewLastFocus.focus();
    setScanBlocked(false);
  }

  // No backdrop-click / Escape dismissal: there's no neutral "cancel" here, only
  // "Discard scan" (destructive, confirmed) or "Create photos" (confirmed by design).
  reviewModal.addEventListener("keydown", (evt) => {
    if (evt.key === "Tab") trapFocus(reviewPanel, evt);
  });

  function clampToImage([x, y]) {
    return [Math.min(Math.max(x, 0), reviewImage.width), Math.min(Math.max(y, 0), reviewImage.height)];
  }

  function pointInQuad(pt, quad) {
    let inside = false;
    for (let i = 0, j = quad.length - 1; i < quad.length; j = i++) {
      const [xi, yi] = quad[i];
      const [xj, yj] = quad[j];
      if (yi > pt[1] !== yj > pt[1] && pt[0] < ((xj - xi) * (pt[1] - yi)) / (yj - yi) + xi) {
        inside = !inside;
      }
    }
    return inside;
  }

  function findCorner(canvasX, canvasY) {
    for (let qi = 0; qi < reviewQuads.length; qi++) {
      for (let ci = 0; ci < 4; ci++) {
        const [x, y] = reviewQuads[qi][ci];
        const dx = x * reviewScale - canvasX;
        const dy = y * reviewScale - canvasY;
        if (Math.sqrt(dx * dx + dy * dy) <= HANDLE_RADIUS + 5) return { qi, ci };
      }
    }
    return null;
  }

  function imagePoint(evt) {
    const rect = reviewCanvas.getBoundingClientRect();
    const x = Math.min(Math.max(evt.clientX - rect.left, 0), reviewCanvas.width) / reviewScale;
    const y = Math.min(Math.max(evt.clientY - rect.top, 0), reviewCanvas.height) / reviewScale;
    return [x, y];
  }

  function drawReview() {
    reviewCtx.clearRect(0, 0, reviewCanvas.width, reviewCanvas.height);
    reviewCtx.drawImage(reviewImage, 0, 0, reviewCanvas.width, reviewCanvas.height);
    reviewQuads.forEach((quad, qi) => {
      const color = REVIEW_COLORS[qi % REVIEW_COLORS.length];
      reviewCtx.beginPath();
      quad.forEach(([x, y], i) => {
        const cx = x * reviewScale, cy = y * reviewScale;
        if (i === 0) reviewCtx.moveTo(cx, cy);
        else reviewCtx.lineTo(cx, cy);
      });
      reviewCtx.closePath();
      reviewCtx.fillStyle = color.fill;
      reviewCtx.fill();
      reviewCtx.strokeStyle = color.solid;
      reviewCtx.lineWidth = 2;
      reviewCtx.stroke();

      const cx = (quad.reduce((s, p) => s + p[0], 0) / 4) * reviewScale;
      const cy = (quad.reduce((s, p) => s + p[1], 0) / 4) * reviewScale;
      reviewCtx.fillStyle = "#0b1416";
      reviewCtx.font = "bold 13px sans-serif";
      reviewCtx.textAlign = "center";
      reviewCtx.textBaseline = "middle";
      reviewCtx.lineWidth = 3;
      reviewCtx.strokeStyle = "rgba(255, 255, 255, 0.9)";
      reviewCtx.strokeText(String(qi + 1), cx, cy);
      reviewCtx.fillText(String(qi + 1), cx, cy);

      quad.forEach(([x, y]) => {
        reviewCtx.beginPath();
        reviewCtx.arc(x * reviewScale, y * reviewScale, HANDLE_RADIUS, 0, Math.PI * 2);
        reviewCtx.fillStyle = color.solid;
        reviewCtx.fill();
        reviewCtx.strokeStyle = "#0b1416";
        reviewCtx.lineWidth = 1;
        reviewCtx.stroke();
      });
    });
  }

  function renderReviewList() {
    reviewListEl.innerHTML = "";
    reviewQuads.forEach((_, qi) => {
      const li = document.createElement("li");
      const label = document.createElement("span");
      label.className = "review-photo-label";
      label.style.setProperty("--selection-color", REVIEW_COLORS[qi % REVIEW_COLORS.length].solid);
      label.textContent = `Photo ${qi + 1}`;
      const removeBtn = document.createElement("button");
      removeBtn.type = "button";
      removeBtn.className = "link-btn";
      removeBtn.textContent = "Remove";
      removeBtn.setAttribute("aria-label", `Remove photo ${qi + 1} selection`);
      removeBtn.addEventListener("click", () => {
        if (reviewDetecting) return;
        reviewQuads.splice(qi, 1);
        drawReview();
        renderReviewList();
      });
      li.appendChild(label);
      li.appendChild(removeBtn);
      reviewListEl.appendChild(li);
    });
    reviewConfirmBtn.disabled = reviewDetecting || reviewQuads.length === 0;
    reviewConfirmBtn.textContent = reviewQuads.length
      ? `Create ${reviewQuads.length} photo${reviewQuads.length === 1 ? "" : "s"}`
      : "Create photos";
    if (!reviewDetecting) {
      const count = reviewQuads.length;
      reviewDetectionStatusEl.textContent = reviewExpectedCount !== null && count !== reviewExpectedCount
        ? `${count} selections; expected ${reviewExpectedCount}. Adjust the selections or try detection again.`
        : `${count} photo${count === 1 ? "" : "s"} selected.`;
    }
  }

  reviewCanvas.addEventListener("pointerdown", (evt) => {
    if (reviewDetecting) return;
    const rect = reviewCanvas.getBoundingClientRect();
    const cx = evt.clientX - rect.left;
    const cy = evt.clientY - rect.top;
    const hit = findCorner(cx, cy);
    const pt = imagePoint(evt);

    if (hit) {
      reviewDrag = { type: "corner", ...hit };
    } else {
      const qi = reviewQuads.findIndex((q) => pointInQuad(pt, q));
      if (qi !== -1) {
        reviewDrag = { type: "move", qi, last: pt };
      } else {
        reviewQuads.push([pt, pt, pt, pt]);
        reviewDrag = { type: "create", qi: reviewQuads.length - 1, start: pt };
      }
    }
    reviewCanvas.setPointerCapture(evt.pointerId);
  });

  reviewCanvas.addEventListener("pointermove", (evt) => {
    if (reviewDetecting || !reviewDrag) return;
    const pt = imagePoint(evt);

    if (reviewDrag.type === "corner") {
      reviewQuads[reviewDrag.qi][reviewDrag.ci] = clampToImage(pt);
    } else if (reviewDrag.type === "move") {
      const dx = pt[0] - reviewDrag.last[0];
      const dy = pt[1] - reviewDrag.last[1];
      reviewQuads[reviewDrag.qi] = reviewQuads[reviewDrag.qi].map(([x, y]) => clampToImage([x + dx, y + dy]));
      reviewDrag.last = pt;
    } else if (reviewDrag.type === "create") {
      const [sx, sy] = reviewDrag.start;
      const [ex, ey] = clampToImage(pt);
      reviewQuads[reviewDrag.qi] = [
        [sx, sy],
        [ex, sy],
        [ex, ey],
        [sx, ey],
      ];
    }
    drawReview();
  });

  reviewCanvas.addEventListener("pointerup", (evt) => {
    if (reviewDetecting) return;
    if (reviewDrag && reviewDrag.type === "create") {
      const quad = reviewQuads[reviewDrag.qi];
      const w = Math.abs(quad[1][0] - quad[0][0]);
      const h = Math.abs(quad[3][1] - quad[0][1]);
      if (w < MIN_QUAD_SIZE || h < MIN_QUAD_SIZE) reviewQuads.splice(reviewDrag.qi, 1);
    }
    reviewDrag = null;
    reviewCanvas.releasePointerCapture(evt.pointerId);
    drawReview();
    renderReviewList();
  });

  reviewDiscardBtn.addEventListener(
    "click",
    withLoading(reviewDiscardBtn, async () => {
      if (reviewDetecting) return;
      const ok = await askConfirm("Discard this scan? None of the photos on it will be created.", {
        title: "Discard scan",
        confirmLabel: "Discard",
      });
      if (!ok) return;
      reviewErrorEl.textContent = "";
      try {
        await checkedRequest(`/api/raw/${encodeURIComponent(reviewRaw)}/discard`, { method: "POST" }, { errorMessage: "Discard failed" });
        closeReviewModal();
        setStatus("Scan discarded.");
      } catch (e) {
        reviewErrorEl.textContent = e.message;
      }
    })
  );

  reviewConfirmBtn.addEventListener(
    "click",
    withLoading(reviewConfirmBtn, async () => {
      if (reviewDetecting || !reviewQuads.length) return;
      reviewErrorEl.textContent = "";
      try {
        const data = await requestJSON(`/api/raw/${encodeURIComponent(reviewRaw)}/extract`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ quads: reviewQuads }),
        }, { errorMessage: "Creating photos failed", detail: true });
        closeReviewModal();
        setStatus(`Added ${data.photos.length} photo(s).`);
        await loadPhotos();
      } catch (e) {
        reviewErrorEl.textContent = e.message;
      }
    })
  );

  return { open: openReviewModal, getRaw: () => reviewRaw };
}
