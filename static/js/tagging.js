import { checkedRequest, requestJSON } from "./api.js";
import { trapFocus, withLoading } from "./modals.js";

export function initTagging({ getSelection, loadPhotos, setStatus }) {
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

  let map, marker;
  let chosenLatLon = null;
  let geocodeAbortController = null;
  let geocodeDebounce = null;
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

  const tagModal = document.getElementById("tag-modal");
  const tagPanel = tagModal.querySelector(".modal-panel");
  const tagModalCountEl = document.getElementById("tag-modal-count");
  const tagThumbsEl = document.getElementById("tag-thumbs");
  const tagCancelBtn = document.getElementById("tag-cancel-btn");
  let tagModalLastFocus = null;

  function openTagModal() {
    if (getSelection().length === 0) return;
    tagModalLastFocus = document.activeElement;
    tagModalCountEl.textContent = `${getSelection().length} photo${getSelection().length === 1 ? "" : "s"}`;
    tagThumbsEl.innerHTML = "";
    for (const { filename, directory } of getSelection()) {
      const img = document.createElement("img");
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
    applyBtn.disabled = !(getSelection().length > 0 && (chosenLatLon || dateInput.value || clearLocation || clearDate));
  }

  placeInput.addEventListener("input", () => {
    clearTimeout(geocodeDebounce);
    const q = placeInput.value.trim();
    if (q.length < 3) {
      geoResultsEl.classList.remove("visible");
      return;
    }
    geocodeDebounce = setTimeout(() => runGeocode(q), 400);
  });

  async function runGeocode(q) {
    if (geocodeAbortController) geocodeAbortController.abort();
    geocodeAbortController = new AbortController();
    try {
      const data = await requestJSON(`/api/geocode?q=${encodeURIComponent(q)}`, {
        signal: geocodeAbortController.signal,
      }, { checkStatus: false });
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
        await checkedRequest("/api/tag", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            filenames: getSelection().map(({ filename }) => filename),
            lat: chosenLatLon ? chosenLatLon.lat : null,
            lon: chosenLatLon ? chosenLatLon.lon : null,
            date: dateInput.value ? dateInput.value + (timeInput.value ? `T${timeInput.value}` : "") : null,
            clear_gps: clearLocation,
            clear_date: clearDate,
          }),
        }, { errorMessage: "Tagging failed", detail: true });
        const taggedCount = getSelection().length;
        closeTagModal();
        setStatus(`Tagged ${taggedCount} photo(s).`);
        await loadPhotos();
      } catch (e) {
        tagErrorEl.textContent = e.message;
      }
    })
  );

  return { open: openTagModal };
}
