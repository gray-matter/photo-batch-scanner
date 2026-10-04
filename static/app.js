const gallery = document.getElementById("gallery");
const emptyMsg = document.getElementById("empty-msg");
const statusEl = document.getElementById("status");
const scanBtn = document.getElementById("scan-btn");
const markTaggedDoneBtn = document.getElementById("mark-tagged-done-btn");
const scanProgressEl = document.getElementById("scan-progress");
const scanProgressBarEl = document.getElementById("scan-progress-bar");
const showDoneCheckbox = document.getElementById("show-done-checkbox");
const selectionBar = document.getElementById("selection-bar");
const selectionCountEl = document.getElementById("selection-count");
const clearSelectionBtn = document.getElementById("clear-selection-btn");
const openTagModalBtn = document.getElementById("open-tag-modal-btn");
const applyBtn = document.getElementById("apply-btn");
const dateInput = document.getElementById("date-input");
const timeInput = document.getElementById("time-input");
const placeInput = document.getElementById("place-input");
const geoResultsEl = document.getElementById("geo-results");
const recentAddressesEl = document.getElementById("recent-addresses");
const recentAddressListEl = document.getElementById("recent-address-list");
const tagErrorEl = document.getElementById("tag-error");
const clearDateBtn = document.getElementById("clear-date-btn");
const clearLocationBtn = document.getElementById("clear-location-btn");

const selected = new Set();
let selectionAnchor = null;
let map, marker;
let chosenLatLon = null;
let geocodeAbortController = null;
let clearDate = false;
let clearLocation = false;
const RECENT_ADDRESSES_KEY = "mass-scanner.recent-addresses";
const MAX_RECENT_ADDRESSES = 4;

function loadRecentAddresses() {
  try {
    const saved = JSON.parse(localStorage.getItem(RECENT_ADDRESSES_KEY) || "[]");
    if (!Array.isArray(saved)) return [];
    return saved.filter((address) =>
      address && typeof address.display_name === "string" &&
      Number.isFinite(Number(address.lat)) && Number.isFinite(Number(address.lon))
    ).slice(0, MAX_RECENT_ADDRESSES).map((address) => ({
      display_name: address.display_name,
      lat: Number(address.lat),
      lon: Number(address.lon),
    }));
  } catch {
    return [];
  }
}

let recentAddresses = loadRecentAddresses();

function renderRecentAddresses() {
  recentAddressListEl.innerHTML = "";
  recentAddressesEl.classList.toggle("hidden", recentAddresses.length === 0);
  for (const address of recentAddresses) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "recent-address-btn";
    button.textContent = address.display_name;
    button.title = address.display_name;
    button.setAttribute("aria-label", `Use recent address: ${address.display_name}`);
    button.addEventListener("click", () => selectAddress(address));
    recentAddressListEl.appendChild(button);
  }
}

function rememberAddress(address) {
  recentAddresses = [
    address,
    ...recentAddresses.filter((recent) => recent.lat !== address.lat || recent.lon !== address.lon),
  ].slice(0, MAX_RECENT_ADDRESSES);
  try {
    localStorage.setItem(RECENT_ADDRESSES_KEY, JSON.stringify(recentAddresses));
  } catch {
    // Keep the current session's recent addresses if storage is unavailable.
  }
  renderRecentAddresses();
}

// --- Modal helpers: focus trap + Escape handling shared by all modals ---

function trapFocus(panel, evt) {
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

// --- Confirmation modal (replaces window.confirm for destructive actions) ---

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

function anyModalOpen(exclude) {
  return Array.from(document.querySelectorAll(".modal-backdrop")).some(
    (el) => el !== exclude && !el.classList.contains("hidden")
  );
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

function withLoading(button, fn) {
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

// --- Selection: click a card to select it, shift+click to extend the range ---

function updateSelectionBar() {
  const n = selected.size;
  selectionCountEl.textContent = n ? `${n} selected` : "";
  selectionBar.classList.toggle("visible", n > 0);
  openTagModalBtn.disabled = n === 0;
}

function selectableCards() {
  return Array.from(gallery.querySelectorAll(".card"));
}

function setCardSelected(card, isSelected) {
  card.classList.toggle("selected", isSelected);
  card.setAttribute("aria-pressed", String(isSelected));
}

function handleCardClick(evt, filename, card) {
  if (evt.target.closest("button")) return;
  const cards = selectableCards();
  const index = cards.indexOf(card);

  if (evt.shiftKey && selectionAnchor !== null) {
    const anchorIndex = cards.findIndex((c) => c.dataset.filename === selectionAnchor);
    if (anchorIndex !== -1) {
      const [start, end] = [anchorIndex, index].sort((a, b) => a - b);
      for (let i = start; i <= end; i++) {
        selected.add(cards[i].dataset.filename);
        setCardSelected(cards[i], true);
      }
      updateSelectionBar();
      return;
    }
  }

  if (selected.has(filename)) {
    selected.delete(filename);
    setCardSelected(card, false);
  } else {
    selected.add(filename);
    setCardSelected(card, true);
  }
  selectionAnchor = filename;
  updateSelectionBar();
}

function clearSelection() {
  selected.clear();
  selectionAnchor = null;
  selectableCards().forEach((card) => setCardSelected(card, false));
  updateSelectionBar();
}

clearSelectionBtn.addEventListener("click", clearSelection);

function badgeHtml(gps, time) {
  let html = "";
  if (time) html += '<svg class="ic" title="Date/time set"><use href="#ic-clock"></use></svg>';
  if (gps) html += '<svg class="ic" title="GPS location set"><use href="#ic-pin"></use></svg>';
  return html;
}

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

function renderCard({ filename, gps, time }) {
  const card = document.createElement("div");
  card.className = "card";
  card.dataset.filename = filename;
  card.setAttribute("role", "button");
  card.setAttribute("tabindex", "0");
  card.setAttribute("aria-pressed", "false");
  card.setAttribute("aria-label", `Select ${filename} for tagging`);
  card.innerHTML = `
    <div class="card-thumb">
      <img />
      <span class="sel-indicator"><svg class="ic"><use href="#ic-check"></use></svg></span>
      <button class="enlarge-photo-btn" data-action="enlarge" title="View larger">⤢</button>
    </div>
    <div class="card-body">
      <div class="card-tags">${badgeHtml(gps, time)}</div>
      <div class="card-actions">
        <button data-action="rotate" data-degrees="90" title="Rotate counter-clockwise" aria-label="Rotate counter-clockwise">⟲</button>
        <button data-action="rotate" data-degrees="270" title="Rotate clockwise" aria-label="Rotate clockwise">⟳</button>
      </div>
      <div class="card-actions">
        <button data-action="delete" class="danger" title="Delete" aria-label="Delete photo">🗑</button>
        <button data-action="done" class="success" title="Mark done" aria-label="Mark photo done">✓</button>
      </div>
    </div>
  `;

  const image = card.querySelector("img");
  image.src = `/cropped/${encodeURIComponent(filename)}?t=${Date.now()}`;
  image.alt = `Scanned photo ${filename}`;
  card.querySelector("button[data-action=enlarge]").setAttribute("aria-label", `View ${filename} larger`);

  card.addEventListener("click", (evt) => handleCardClick(evt, filename, card));
  card.addEventListener("keydown", (evt) => {
    if (evt.key === "Enter" || evt.key === " ") {
      evt.preventDefault();
      handleCardClick(evt, filename, card);
    }
  });

  card.querySelector("button[data-action=enlarge]").addEventListener("click", () => {
    openPhotoViewer(filename, "cropped");
  });

  card.querySelectorAll("button[data-action=rotate]").forEach((btn) => {
    btn.addEventListener(
      "click",
      withLoading(btn, async () => {
        card.setAttribute("aria-busy", "true");
        await rotatePhoto(filename, parseInt(btn.dataset.degrees, 10));
        card.querySelector("img").src = `/cropped/${encodeURIComponent(filename)}?t=${Date.now()}`;
        card.removeAttribute("aria-busy");
      })
    );
  });

  card.querySelector("button[data-action=delete]").addEventListener(
    "click",
    withLoading(card.querySelector("button[data-action=delete]"), async () => {
      const ok = await askConfirm(`Delete ${filename}? This cannot be undone.`, { title: "Delete photo" });
      if (!ok) return;
      card.setAttribute("aria-busy", "true");
      await deletePhoto(filename);
      selected.delete(filename);
      card.remove();
      updateSelectionBar();
      refreshEmptyMsg();
    })
  );

  card.querySelector("button[data-action=done]").addEventListener(
    "click",
    withLoading(card.querySelector("button[data-action=done]"), async () => {
      card.setAttribute("aria-busy", "true");
      await markPhotoDone(filename);
      selected.delete(filename);
      updateSelectionBar();
      if (showDoneCheckbox.checked) {
        await loadPhotos();
      } else {
        card.remove();
      }
      refreshEmptyMsg();
    })
  );

  return card;
}

function renderDoneCard({ filename, gps, time }) {
  const card = document.createElement("div");
  card.className = "card done";
  card.dataset.filename = filename;
  card.setAttribute("role", "button");
  card.setAttribute("tabindex", "0");
  card.setAttribute("aria-pressed", "false");
  card.setAttribute("aria-label", `Select ${filename} for tagging`);
  card.innerHTML = `
    <div class="card-thumb">
      <img />
      <span class="sel-indicator"><svg class="ic"><use href="#ic-check"></use></svg></span>
      <button class="enlarge-photo-btn" data-action="enlarge" title="View larger">⤢</button>
    </div>
    <div class="card-body">
      <div class="card-tags">${badgeHtml(gps, time)}</div>
      <span class="badge-done">Done</span>
      <div class="card-actions">
        <button data-action="restore" title="Move back to edit" aria-label="Move back to edit">↺</button>
        <button data-action="delete-done" class="danger" title="Delete" aria-label="Delete photo">🗑</button>
      </div>
    </div>
  `;

  const image = card.querySelector("img");
  image.src = `/done/${encodeURIComponent(filename)}?t=${Date.now()}`;
  image.alt = `Done photo ${filename}`;
  card.querySelector("button[data-action=enlarge]").setAttribute("aria-label", `View ${filename} larger`);

  card.addEventListener("click", (evt) => handleCardClick(evt, filename, card));
  card.addEventListener("keydown", (evt) => {
    if (evt.key === "Enter" || evt.key === " ") {
      evt.preventDefault();
      handleCardClick(evt, filename, card);
    }
  });

  card.querySelector("button[data-action=enlarge]").addEventListener("click", () => {
    openPhotoViewer(filename, "done");
  });

  card.querySelector("button[data-action=restore]").addEventListener(
    "click",
    withLoading(card.querySelector("button[data-action=restore]"), async () => {
      card.setAttribute("aria-busy", "true");
      await restoreDonePhoto(filename);
      await loadPhotos();
    })
  );

  card.querySelector("button[data-action=delete-done]").addEventListener(
    "click",
    withLoading(card.querySelector("button[data-action=delete-done]"), async () => {
      const ok = await askConfirm(`Delete ${filename}? This cannot be undone.`, { title: "Delete photo" });
      if (!ok) return;
      card.setAttribute("aria-busy", "true");
      await deleteDonePhoto(filename);
      selected.delete(filename);
      card.remove();
      updateSelectionBar();
      refreshEmptyMsg();
    })
  );

  return card;
}

function refreshEmptyMsg() {
  emptyMsg.style.display = gallery.querySelector(".card") ? "none" : "block";
}

async function loadPhotos() {
  const res = await fetch("/api/photos");
  const data = await res.json();
  gallery.innerHTML = "";
  selected.clear();
  selectionAnchor = null;
  updateSelectionBar();
  for (const photo of data.photos) {
    gallery.appendChild(renderCard(photo));
  }
  markTaggedDoneBtn.disabled = !data.photos.some((photo) => photo.gps && photo.time);
  if (showDoneCheckbox.checked) {
    await loadDonePhotos();
    const cards = Array.from(gallery.children);
    cards.sort((a, b) => (a.dataset.filename < b.dataset.filename ? 1 : a.dataset.filename > b.dataset.filename ? -1 : 0));
    cards.forEach((card) => gallery.appendChild(card));
  }
  refreshEmptyMsg();
}

async function loadDonePhotos() {
  const res = await fetch("/api/photos/done");
  const data = await res.json();
  for (const photo of data.photos) {
    gallery.appendChild(renderDoneCard(photo));
  }
}

showDoneCheckbox.addEventListener("change", () => loadPhotos());

markTaggedDoneBtn.addEventListener(
  "click",
  withLoading(markTaggedDoneBtn, async () => {
    try {
      const res = await fetch("/api/photos/mark-tagged-done", { method: "POST" });
      if (!res.ok) throw new Error("Couldn't mark tagged photos done");
      const data = await res.json();
      statusEl.textContent = `Marked ${data.photos.length} photo${data.photos.length === 1 ? "" : "s"} done.`;
      await loadPhotos();
    } catch (error) {
      statusEl.textContent = error.message;
    }
  })
);

async function rotatePhoto(filename, degrees) {
  const res = await fetch(`/api/photos/${encodeURIComponent(filename)}/rotate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ degrees }),
  });
  if (!res.ok) throw new Error("Rotate failed");
}

async function deletePhoto(filename) {
  const res = await fetch(`/api/photos/${encodeURIComponent(filename)}`, { method: "DELETE" });
  if (!res.ok) throw new Error("Delete failed");
}

async function markPhotoDone(filename) {
  const res = await fetch(`/api/photos/${encodeURIComponent(filename)}/done`, { method: "POST" });
  if (!res.ok) throw new Error("Mark done failed");
}

async function deleteDonePhoto(filename) {
  const res = await fetch(`/api/photos/done/${encodeURIComponent(filename)}`, { method: "DELETE" });
  if (!res.ok) throw new Error("Delete failed");
}

async function restoreDonePhoto(filename) {
  const res = await fetch(`/api/photos/done/${encodeURIComponent(filename)}/restore`, { method: "POST" });
  if (!res.ok) throw new Error("Restore failed");
}

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
let scanStatusRequestActive = false;

async function refreshScanStatus() {
  if (scanStatusRequestActive) return;
  scanStatusRequestActive = true;
  try {
    const res = await fetch("/api/scan/status");
    if (!res.ok) throw new Error("Couldn't get scan status");
    const state = await res.json();
    const busy = ["discovering", "scanning", "transferring", "detecting", "review"].includes(state.stage);
    setScanBtnBlocked(busy);
    const pendingReview = state.pending_review;
    if (pendingReview && pendingReview.raw !== reviewRaw) {
      openReviewModal(pendingReview.raw, pendingReview.quads || [], pendingReview.expected_count ?? null);
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
  } catch (e) {
    statusEl.textContent = e.message;
  } finally {
    scanStatusRequestActive = false;
  }
}

setInterval(refreshScanStatus, 700);

scanBtn.addEventListener(
  "click",
  async () => {
    scanBtn.disabled = true;
    statusEl.textContent = "Starting scan…";
    try {
      const res = await fetch("/api/scan", { method: "POST" });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || "Scan failed");
      }
      await refreshScanStatus();
    } catch (e) {
      statusEl.textContent = e.message;
      await refreshScanStatus();
    }
  }
);

// --- Tag modal: batch date/location for the current selection ---

const tagModal = document.getElementById("tag-modal");
const tagPanel = tagModal.querySelector(".modal-panel");
const tagModalCountEl = document.getElementById("tag-modal-count");
const tagThumbsEl = document.getElementById("tag-thumbs");
const tagCancelBtn = document.getElementById("tag-cancel-btn");
let tagModalLastFocus = null;

function openTagModal() {
  if (selected.size === 0) return;
  tagModalLastFocus = document.activeElement;
  tagModalCountEl.textContent = `${selected.size} photo${selected.size === 1 ? "" : "s"}`;
  tagThumbsEl.innerHTML = "";
  for (const filename of selected) {
    const img = document.createElement("img");
    const card = Array.from(gallery.children).find((item) => item.dataset.filename === filename);
    const directory = card?.classList.contains("done") ? "done" : "cropped";
    img.src = `/${directory}/${encodeURIComponent(filename)}?t=${Date.now()}`;
    img.alt = filename;
    tagThumbsEl.appendChild(img);
  }
  tagErrorEl.textContent = "";
  dateInput.value = "";
  timeInput.value = "";
  placeInput.value = "";
  geoResultsEl.innerHTML = "";
  geoResultsEl.classList.remove("visible");
  renderRecentAddresses();
  chosenLatLon = null;
  clearDate = false;
  clearLocation = false;
  if (marker) {
    marker.remove();
    marker = null;
  }
  tagModal.classList.remove("hidden");
  document.body.classList.add("modal-open");
  updateApplyState();
  dateInput.focus();
  ensureMap();
  setTimeout(() => map && map.invalidateSize(), 0);
}

function closeTagModal() {
  tagModal.classList.add("hidden");
  document.body.classList.remove("modal-open");
  if (tagModalLastFocus) tagModalLastFocus.focus();
}

openTagModalBtn.addEventListener("click", openTagModal);
tagCancelBtn.addEventListener("click", closeTagModal);
tagModal.addEventListener("keydown", (evt) => {
  if (evt.key === "Escape") closeTagModal();
  else trapFocus(tagPanel, evt);
});

// --- Geocoding + map ---

function ensureMap() {
  if (map) return;
  map = L.map("map").setView([48.8566, 2.3522], 5);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors",
  }).addTo(map);
  map.on("click", (e) => setLocation(e.latlng.lat, e.latlng.lng));
}

function setLocation(lat, lon) {
  ensureMap();
  chosenLatLon = { lat, lon };
  clearLocation = false;
  if (marker) marker.setLatLng([lat, lon]);
  else {
    marker = L.marker([lat, lon], { draggable: true }).addTo(map);
    marker.on("dragend", () => {
      const p = marker.getLatLng();
      chosenLatLon = { lat: p.lat, lon: p.lng };
      updateApplyState();
    });
  }
  map.setView([lat, lon], 14);
  updateApplyState();
}

function selectAddress(address) {
  const selectedAddress = {
    display_name: address.display_name,
    lat: Number(address.lat),
    lon: Number(address.lon),
  };
  setLocation(selectedAddress.lat, selectedAddress.lon);
  placeInput.value = selectedAddress.display_name;
  geoResultsEl.classList.remove("visible");
  rememberAddress(selectedAddress);
}

clearLocationBtn.addEventListener("click", () => {
  chosenLatLon = null;
  clearLocation = true;
  placeInput.value = "";
  geoResultsEl.innerHTML = "";
  geoResultsEl.classList.remove("visible");
  if (marker) {
    marker.remove();
    marker = null;
  }
  updateApplyState();
});

clearDateBtn.addEventListener("click", () => {
  dateInput.value = "";
  timeInput.value = "";
  clearDate = true;
  updateApplyState();
});

function updateApplyState() {
  timeInput.disabled = !dateInput.value;
  applyBtn.disabled = !(selected.size > 0 && (chosenLatLon || dateInput.value || clearLocation || clearDate));
}

placeInput.addEventListener("input", () => {
  clearTimeout(placeInput._debounce);
  const q = placeInput.value.trim();
  if (q.length < 3) {
    geoResultsEl.classList.remove("visible");
    return;
  }
  placeInput._debounce = setTimeout(() => runGeocode(q), 400);
});

async function runGeocode(q) {
  if (geocodeAbortController) geocodeAbortController.abort();
  geocodeAbortController = new AbortController();
  try {
    const res = await fetch(`/api/geocode?q=${encodeURIComponent(q)}`, {
      signal: geocodeAbortController.signal,
    });
    const data = await res.json();
    geoResultsEl.innerHTML = "";
    for (const r of data.results) {
      const li = document.createElement("li");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = r.display_name;
      btn.addEventListener("click", () => {
        selectAddress(r);
      });
      li.appendChild(btn);
      geoResultsEl.appendChild(li);
    }
    geoResultsEl.classList.toggle("visible", data.results.length > 0);
  } catch (e) {
    if (e.name !== "AbortError") console.error(e);
  }
}

dateInput.addEventListener("input", () => {
  if (dateInput.value) clearDate = false;
  updateApplyState();
});
timeInput.addEventListener("input", updateApplyState);

applyBtn.addEventListener(
  "click",
  withLoading(applyBtn, async () => {
    tagErrorEl.textContent = "";
    try {
      const res = await fetch("/api/tag", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          filenames: [...selected],
          lat: chosenLatLon ? chosenLatLon.lat : null,
          lon: chosenLatLon ? chosenLatLon.lon : null,
          date: dateInput.value ? dateInput.value + (timeInput.value ? `T${timeInput.value}` : "") : null,
          clear_gps: clearLocation,
          clear_date: clearDate,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || "Tagging failed");
      }
      const taggedCount = selected.size;
      closeTagModal();
      statusEl.textContent = `Tagged ${taggedCount} photo(s).`;
      await loadPhotos();
    } catch (e) {
      tagErrorEl.textContent = e.message;
    }
  })
);

// --- Review modal: validate/adjust the auto-detected split before any photo is created ---
//
// Each detected selection is an editable quadrilateral fitted to the visible
// print boundary. Nothing is written until confirmed; the raw scan is tracked
// server-side so a page reload re-opens this same review.

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
    const res = await fetch(`/api/raw/${encodeURIComponent(raw)}/detect`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_count: expectedCount }),
      signal: controller.signal,
    });
    if (!res.ok) {
      const error = await res.json().catch(() => ({}));
      throw new Error(typeof error.detail === "string" ? error.detail : "Couldn't detect photos");
    }
    const result = await res.json();
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

function setScanBtnBlocked(blocked) {
  scanBtn.disabled = blocked;
  scanBtn.title = blocked ? "Finish reviewing the pending scan first" : "";
}

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
  setScanBtnBlocked(true);
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
  setScanBtnBlocked(false);
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
      const res = await fetch(`/api/raw/${encodeURIComponent(reviewRaw)}/discard`, { method: "POST" });
      if (!res.ok) throw new Error("Discard failed");
      closeReviewModal();
      statusEl.textContent = "Scan discarded.";
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
      const res = await fetch(`/api/raw/${encodeURIComponent(reviewRaw)}/extract`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ quads: reviewQuads }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || "Creating photos failed");
      }
      const data = await res.json();
      closeReviewModal();
      statusEl.textContent = `Added ${data.photos.length} photo(s).`;
      await loadPhotos();
    } catch (e) {
      reviewErrorEl.textContent = e.message;
    }
  })
);

loadPhotos();
refreshScanStatus();
