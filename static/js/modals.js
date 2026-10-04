export function trapFocus(panel, evt) {
  if (evt.key !== "Tab") return;
  const focusable = Array.from(
    panel.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])')
  ).filter((el) => !el.disabled && el.offsetParent !== null);
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (evt.shiftKey && document.activeElement === first) {
    evt.preventDefault();
    last.focus();
  } else if (!evt.shiftKey && document.activeElement === last) {
    evt.preventDefault();
    first.focus();
  }
}

export function anyModalOpen(exclude) {
  return Array.from(document.querySelectorAll(".modal-backdrop")).some(
    (el) => el !== exclude && !el.classList.contains("hidden")
  );
}

export function withLoading(button, fn) {
  return async (...args) => {
    const original = button.textContent;
    button.disabled = true;
    if (button.dataset.loadingText) button.textContent = button.dataset.loadingText;
    try {
      await fn(...args);
    } finally {
      button.disabled = false;
      button.textContent = original;
    }
  };
}

export function initModals() {
  const confirmModal = document.getElementById("confirm-modal");
  const confirmPanel = confirmModal.querySelector(".modal-panel");
  const confirmTitleEl = document.getElementById("confirm-title");
  const confirmMessageEl = document.getElementById("confirm-message");
  const confirmOkBtn = document.getElementById("confirm-ok");
  const confirmCancelBtn = document.getElementById("confirm-cancel");
  let confirmResolve = null;
  let confirmLastFocus = null;

  function askConfirm(message, { title = "Confirm", confirmLabel = "Delete" } = {}) {
    return new Promise((resolve) => {
      confirmLastFocus = document.activeElement;
      confirmTitleEl.textContent = title;
      confirmMessageEl.textContent = message;
      confirmOkBtn.textContent = confirmLabel;
      confirmResolve = resolve;
      confirmModal.classList.remove("hidden");
      document.body.classList.add("modal-open");
      confirmCancelBtn.focus();
    });
  }
  function closeConfirm(result) {
    confirmModal.classList.add("hidden");
    if (!anyModalOpen(confirmModal)) document.body.classList.remove("modal-open");
    if (confirmLastFocus) confirmLastFocus.focus();
    const resolve = confirmResolve;
    confirmResolve = null;
    if (resolve) resolve(result);
  }

  confirmOkBtn.addEventListener("click", () => closeConfirm(true));
  confirmCancelBtn.addEventListener("click", () => closeConfirm(false));
  confirmModal.addEventListener("keydown", (evt) => {
    if (evt.key === "Escape") closeConfirm(false);
    else trapFocus(confirmPanel, evt);
  });

  const photoViewerModal = document.getElementById("photo-viewer-modal");
  const photoViewerPanel = photoViewerModal.querySelector(".photo-viewer-panel");
  const photoViewerTitle = document.getElementById("photo-viewer-title");
  const photoViewerImage = document.getElementById("photo-viewer-image");
  const photoViewerCloseBtn = document.getElementById("photo-viewer-close");
  let photoViewerLastFocus = null;

  function openPhotoViewer(filename, directory) {
    photoViewerLastFocus = document.activeElement;
    photoViewerTitle.textContent = filename;
    photoViewerImage.src = `/${directory}/${encodeURIComponent(filename)}?t=${Date.now()}`;
    photoViewerImage.alt = `Enlarged photo ${filename}`;
    photoViewerModal.classList.remove("hidden");
    document.body.classList.add("modal-open");
    photoViewerCloseBtn.focus();
  }

  function closePhotoViewer() {
    photoViewerModal.classList.add("hidden");
    photoViewerImage.removeAttribute("src");
    if (!anyModalOpen(photoViewerModal)) document.body.classList.remove("modal-open");
    if (photoViewerLastFocus?.isConnected) photoViewerLastFocus.focus();
  }

  photoViewerCloseBtn.addEventListener("click", closePhotoViewer);
  photoViewerModal.addEventListener("click", (evt) => {
    if (evt.target === photoViewerModal) closePhotoViewer();
  });
  photoViewerModal.addEventListener("keydown", (evt) => {
    if (evt.key === "Escape") closePhotoViewer();
    else trapFocus(photoViewerPanel, evt);
  });

  return { askConfirm, openPhotoViewer };
}
