import { checkedRequest, requestJSON } from "./api.js";
import { withLoading } from "./modals.js";

export function initGallery({ askConfirm, openPhotoViewer, onTagSelected, setStatus }) {
  const gallery = document.getElementById("gallery");
  const emptyMsg = document.getElementById("empty-msg");
  const markTaggedDoneBtn = document.getElementById("mark-tagged-done-btn");
  const showDoneCheckbox = document.getElementById("show-done-checkbox");
  const selectionBar = document.getElementById("selection-bar");
  const selectionCountEl = document.getElementById("selection-count");
  const clearSelectionBtn = document.getElementById("clear-selection-btn");
  const openTagModalBtn = document.getElementById("open-tag-modal-btn");

  const selected = new Set();
  let selectionAnchor = null;

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
    const data = await requestJSON("/api/photos", {}, { checkStatus: false });
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
    const data = await requestJSON("/api/photos/done", {}, { checkStatus: false });
    for (const photo of data.photos) {
      gallery.appendChild(renderDoneCard(photo));
    }
  }

  showDoneCheckbox.addEventListener("change", () => loadPhotos());

  markTaggedDoneBtn.addEventListener(
    "click",
    withLoading(markTaggedDoneBtn, async () => {
      try {
        const data = await requestJSON("/api/photos/mark-tagged-done", { method: "POST" }, {
          errorMessage: "Couldn't mark tagged photos done",
        });
        setStatus(`Marked ${data.photos.length} photo${data.photos.length === 1 ? "" : "s"} done.`);
        await loadPhotos();
      } catch (error) {
        setStatus(error.message);
      }
    })
  );

  async function rotatePhoto(filename, degrees) {
    await checkedRequest(`/api/photos/${encodeURIComponent(filename)}/rotate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ degrees }),
    }, { errorMessage: "Rotate failed" });
  }

  async function deletePhoto(filename) {
    await checkedRequest(`/api/photos/${encodeURIComponent(filename)}`, { method: "DELETE" }, { errorMessage: "Delete failed" });
  }

  async function markPhotoDone(filename) {
    await checkedRequest(`/api/photos/${encodeURIComponent(filename)}/done`, { method: "POST" }, { errorMessage: "Mark done failed" });
  }

  async function deleteDonePhoto(filename) {
    await checkedRequest(`/api/photos/done/${encodeURIComponent(filename)}`, { method: "DELETE" }, { errorMessage: "Delete failed" });
  }

  async function restoreDonePhoto(filename) {
    await checkedRequest(`/api/photos/done/${encodeURIComponent(filename)}/restore`, { method: "POST" }, { errorMessage: "Restore failed" });
  }

  openTagModalBtn.addEventListener("click", onTagSelected);

  function getSelection() {
    return [...selected].map((filename) => {
      const card = Array.from(gallery.children).find((item) => item.dataset.filename === filename);
      return { filename, directory: card?.classList.contains("done") ? "done" : "cropped" };
    });
  }

  return { loadPhotos, getSelection };
}
