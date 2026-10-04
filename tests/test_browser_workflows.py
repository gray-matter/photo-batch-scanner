import unittest
from typing import Any

from browser_harness import run_browser


PRELUDE = r"""
window.ticks = {};
window.setInterval = (callback, interval) => { ticks[interval] = callback; return interval; };
window.requests = [];
window.state = { stage: "idle" };
window.pending = [{filename: "z.jpg", gps: true, time: true}, {filename: "m.jpg"}, {filename: "a.jpg"}];
window.done = [{filename: "n.jpg"}];
window.quad = (x) => [[x, 10], [x + 100, 10], [x + 100, 110], [x, 110]];
window.tagFails = true;
window.extractFails = true;
window.staleAborted = false;
window.releaseStale = null;
localStorage.clear();
localStorage.setItem("mass-scanner.photo-count", "2");
localStorage.setItem("mass-scanner.recent-addresses", JSON.stringify(
  Array.from({length: 5}, (_, i) => ({display_name: `Address ${i}`, lat: i + 40, lon: i + 2}))
));
window.L = {
  map: () => ({setView() { return this; }, on() {}, invalidateSize() {}}),
  tileLayer: () => ({addTo() {}}),
  marker: (point) => ({addTo() { return this; }, on() {}, setLatLng() {}, remove() {},
    getLatLng() { return {lat: point[0], lng: point[1]}; }}),
};
const response = (data, ok = true) => ({ok, json: async () => data});
window.fetch = async (url, options = {}) => {
  const body = options.body ? JSON.parse(options.body) : null;
  requests.push({url, method: options.method || "GET", body});
  if (url === "/api/photos") return response({photos: pending});
  if (url === "/api/photos/done") return response({photos: done});
  if (url === "/api/scan/status") return response(structuredClone(state));
  if (url === "/api/scan") { state = {stage: "discovering"}; return response({}); }
  if (url === "/api/tag") {
    if (tagFails) { tagFails = false; return response({detail: "Temporary tagging failure"}, false); }
    return response({});
  }
  if (url.endsWith("/detect")) {
    if (url.includes("b.jpg")) {
      options.signal.addEventListener("abort", () => { staleAborted = true; });
      return new Promise((resolve) => { releaseStale = () => resolve(response({quads: [quad(10), quad(150), quad(280)], expected_count: 3})); });
    }
    return response({quads: [quad(10), quad(150)], expected_count: body.expected_count});
  }
  if (url.endsWith("/extract")) {
    if (extractFails) { extractFails = false; return response({detail: "Temporary extraction failure"}, false); }
    state = {stage: "review", pending_review: {raw: "b.jpg", quads: [quad(10)], expected_count: 2}};
    return response({photos: ["created.jpg"]});
  }
  if (url.endsWith("/discard")) { state = {stage: "idle"}; return response({}); }
  throw new Error(`Unexpected fixture request: ${url}`);
};
"""

SCENARIO = r"""
await waitFor(() => document.querySelectorAll("#gallery .card").length === 3, "initial gallery");
byId("show-done-checkbox").checked = true;
byId("show-done-checkbox").dispatchEvent(new Event("change"));
await waitFor(() => document.querySelectorAll("#gallery .card").length === 4, "done gallery");
let cards = Array.from(document.querySelectorAll("#gallery .card"));
results.order = cards.map((card) => card.dataset.filename);
cards[0].click();
cards[3].dispatchEvent(new MouseEvent("click", {bubbles: true, shiftKey: true}));
results.selection = {count: byId("selection-count").textContent, pressed: cards.map((card) => card.getAttribute("aria-pressed"))};
byId("open-tag-modal-btn").focus();
byId("open-tag-modal-btn").click();
results.tagThumbs = Array.from(byId("tag-thumbs").querySelectorAll("img")).map((image) => new URL(image.src).pathname);
results.recentCount = byId("recent-address-list").children.length;
byId("tag-modal").dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", bubbles: true}));
results.tagFocusRestored = document.activeElement === byId("open-tag-modal-btn");
byId("open-tag-modal-btn").click();
byId("clear-date-btn").click();
byId("clear-location-btn").click();
byId("apply-btn").click();
await waitFor(() => byId("tag-error").textContent !== "", "tag failure");
results.tagFailure = {error: byId("tag-error").textContent, open: !byId("tag-modal").classList.contains("hidden")};
await waitFor(() => !byId("apply-btn").disabled, "retry tagging");
byId("apply-btn").click();
await waitFor(() => byId("tag-modal").classList.contains("hidden"), "tag completion");
results.removalPayload = requests.filter((request) => request.url === "/api/tag").at(-1).body;
await waitFor(() => byId("selection-count").textContent === "", "selection reset");
cards = Array.from(document.querySelectorAll("#gallery .card"));
cards[0].dispatchEvent(new KeyboardEvent("keydown", {key: "Enter", bubbles: true}));
byId("open-tag-modal-btn").click();
byId("recent-address-list").querySelector("button").click();
byId("date-input").value = "2001-02-03";
byId("date-input").dispatchEvent(new Event("input"));
byId("time-input").value = "12:34";
byId("apply-btn").click();
await waitFor(() => byId("tag-modal").classList.contains("hidden"), "date/location tagging");
await waitFor(() => byId("selection-count").textContent === "", "tagged gallery reload");
results.dateLocationPayload = requests.filter((request) => request.url === "/api/tag").at(-1).body;
results.recentSaved = JSON.parse(localStorage.getItem("mass-scanner.recent-addresses"));
const enlarge = document.querySelector("#gallery button[data-action=enlarge]");
enlarge.focus(); enlarge.click();
byId("photo-viewer-modal").dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", bubbles: true}));
results.viewerFocusRestored = document.activeElement === enlarge;

const remoteFrame = document.createElement("iframe");
remoteFrame.src = "/scan-control";
document.body.appendChild(remoteFrame);
await waitFor(() => ticks[1000] && remoteFrame.contentDocument.getElementById("remote-scan-btn"), "remote modules");
const remoteButton = remoteFrame.contentDocument.getElementById("remote-scan-btn");
const remoteStatus = remoteFrame.contentDocument.getElementById("remote-status");
remoteButton.click();
await waitFor(() => remoteButton.textContent === "Scan in progress", "remote scan started");
state = {stage: "detecting"};
await ticks[1000]();
await ticks[700]();
results.detectingPolicies = {remoteDisabled: remoteButton.disabled, mainDisabled: byId("scan-btn").disabled, remoteText: remoteStatus.textContent};
state = {stage: "review", pending_review: {raw: "a.jpg", quads: [quad(10)], expected_count: null}};
await ticks[1000]();
await ticks[700]();
await waitFor(() => !byId("review-modal").classList.contains("hidden") && !byId("review-detect").disabled && byId("review-list").children.length === 2, "stored-count detection");
results.reviewReady = {remoteDisabled: remoteButton.disabled, mainDisabled: byId("scan-btn").disabled, count: byId("review-photo-count").value, stored: localStorage.getItem("mass-scanner.photo-count")};
byId("review-modal").dispatchEvent(new KeyboardEvent("keydown", {key: "Escape", bubbles: true}));
results.reviewIgnoresEscape = !byId("review-modal").classList.contains("hidden");
const canvas = byId("review-canvas");
canvas.setPointerCapture = () => {};
canvas.releasePointerCapture = () => {};
function drag(start, end) {
  const rect = canvas.getBoundingClientRect();
  for (const [type, point] of [["pointerdown", start], ["pointermove", end], ["pointerup", end]]) {
    canvas.dispatchEvent(new PointerEvent(type, {pointerId: 1, clientX: rect.left + point[0], clientY: rect.top + point[1]}));
  }
}
drag([10, 10], [20, 20]);
drag([60, 60], [70, 70]);
drag([300, 150], [305, 155]);
results.smallBoxRejected = byId("review-list").children.length === 2;
drag([300, 150], [350, 200]);
byId("review-confirm").click();
await waitFor(() => byId("review-error").textContent !== "", "extraction failure");
results.extractFailure = {error: byId("review-error").textContent, open: !byId("review-modal").classList.contains("hidden")};
await waitFor(() => !byId("review-confirm").disabled, "retry extraction");
byId("review-confirm").click();
await waitFor(() => byId("review-modal").classList.contains("hidden"), "extracted review");
results.extracted = requests.filter((request) => request.url.endsWith("/extract")).at(-1);
await ticks[700]();
await waitFor(() => !byId("review-modal").classList.contains("hidden") && byId("review-list").children.length === 1, "queued review");
results.queuedReview = byId("review-detection-status").textContent;
byId("review-photo-count").value = "3";
byId("review-detection-form").dispatchEvent(new Event("submit", {bubbles: true, cancelable: true}));
await waitFor(() => releaseStale !== null, "pending stale detection");
state = {stage: "review", pending_review: {raw: "c.jpg", quads: [quad(150), quad(10)], expected_count: 2}};
await ticks[700]();
await waitFor(() => staleAborted && !byId("review-detect").disabled && byId("review-list").children.length === 2, "stale detection aborted");
releaseStale();
await new Promise((resolve) => setTimeout(resolve, 0));
results.staleDetection = {aborted: staleAborted, selections: byId("review-list").children.length, count: byId("review-photo-count").value, stored: localStorage.getItem("mass-scanner.photo-count")};
byId("review-discard").click();
results.discardPrompt = {title: byId("confirm-title").textContent, message: byId("confirm-message").textContent, label: byId("confirm-ok").textContent};
byId("confirm-cancel").click();
await waitFor(() => !byId("review-discard").disabled, "discard canceled");
results.discardCanceled = !byId("review-modal").classList.contains("hidden") && !requests.some((request) => request.url.endsWith("/discard"));
byId("review-discard").click();
byId("confirm-ok").click();
await waitFor(() => byId("review-modal").classList.contains("hidden"), "discarded review");
results.discarded = requests.filter((request) => request.url.endsWith("/discard")).at(-1).url;
"""


class BrowserWorkflowTests(unittest.TestCase):
    results: dict[str, Any]

    @classmethod
    def setUpClass(cls) -> None:
        cls.results = run_browser(PRELUDE, SCENARIO, """
          window.fetch = (...args) => parent.fetch(...args);
          window.setInterval = (callback, interval) => parent.setInterval(callback, interval);
        """)

    def test_gallery_order_shift_keyboard_selection_and_modal_focus(self) -> None:
        self.assertEqual(self.results["order"], ["z.jpg", "n.jpg", "m.jpg", "a.jpg"])
        self.assertEqual(self.results["selection"], {"count": "4 selected", "pressed": ["true"] * 4})
        self.assertEqual(self.results["tagThumbs"], ["/cropped/z.jpg", "/done/n.jpg", "/cropped/m.jpg", "/cropped/a.jpg"])
        self.assertTrue(self.results["tagFocusRestored"])
        self.assertTrue(self.results["viewerFocusRestored"])

    def test_tagging_removal_date_location_errors_and_recent_address_limit(self) -> None:
        self.assertEqual(self.results["tagFailure"], {"error": "Temporary tagging failure", "open": True})
        self.assertEqual(self.results["removalPayload"], {
            "filenames": ["z.jpg", "n.jpg", "m.jpg", "a.jpg"], "lat": None, "lon": None,
            "date": None, "clear_gps": True, "clear_date": True,
        })
        self.assertEqual(self.results["dateLocationPayload"], {
            "filenames": ["z.jpg"], "lat": 40, "lon": 2, "date": "2001-02-03T12:34",
            "clear_gps": False, "clear_date": False,
        })
        self.assertEqual(self.results["recentCount"], 4)
        self.assertEqual(len(self.results["recentSaved"]), 4)

    def test_remote_scan_and_main_page_keep_different_button_policies(self) -> None:
        self.assertEqual(self.results["detectingPolicies"], {
            "remoteDisabled": True, "mainDisabled": True,
            "remoteText": "Scan complete. Preparing the review on the main screen; you can start another scan.",
        })
        self.assertEqual(self.results["reviewReady"], {"remoteDisabled": False, "mainDisabled": True, "count": "2", "stored": "2"})
        self.assertTrue(self.results["reviewIgnoresEscape"])

    def test_quad_editing_extract_retry_and_queued_review(self) -> None:
        self.assertTrue(self.results["smallBoxRejected"])
        self.assertEqual(self.results["extractFailure"], {"error": "Temporary extraction failure", "open": True})
        extraction = self.results["extracted"]
        self.assertEqual(extraction["url"], "/api/raw/a.jpg/extract")
        self.assertEqual(extraction["body"]["quads"], [
            [[30, 30], [120, 20], [120, 120], [20, 120]],
            [[150, 10], [250, 10], [250, 110], [150, 110]],
            [[300, 150], [350, 150], [350, 200], [300, 200]],
        ])
        self.assertEqual(self.results["queuedReview"], "1 selections; expected 2. Adjust the selections or try detection again.")

    def test_superseded_detection_and_discard_confirmation_preserve_review(self) -> None:
        self.assertEqual(self.results["staleDetection"], {"aborted": True, "selections": 2, "count": "2", "stored": "2"})
        self.assertEqual(self.results["discardPrompt"], {
            "title": "Discard scan", "message": "Discard this scan? None of the photos on it will be created.", "label": "Discard",
        })
        self.assertTrue(self.results["discardCanceled"])
        self.assertEqual(self.results["discarded"], "/api/raw/c.jpg/discard")


if __name__ == "__main__":
    unittest.main()
