import json
import unittest
from urllib.parse import quote

from browser_harness import run_browser


class GalleryFilenameSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.filename = 'frame " onerror="window.injected=1"><img src=x onerror="window.injected=2"> &.jpg'
        prelude = """
          window.fetch = async (url) => ({
            ok: true,
            json: async () => url.startsWith("/api/photos") ? { photos: [{filename: FILENAME}] } : { stage: "idle" }
          });
          window.setInterval = () => 0;
        """.replace("FILENAME", json.dumps(cls.filename))
        cls.results = run_browser(prelude, """
          await waitFor(() => document.querySelector("#gallery .card"), "pending card");
          byId("show-done-checkbox").checked = true;
          byId("show-done-checkbox").dispatchEvent(new Event("change"));
          await waitFor(() => document.querySelector("#gallery .done"), "done card");
          for (const [state, selector] of [["pending", ".card:not(.done)"], ["done", ".card.done"]]) {
            const card = document.querySelector(`#gallery ${selector}`);
            const image = card.querySelector("img");
            const elements = [card, ...card.querySelectorAll("*")];
            results[state] = {
              alt: image.alt,
              imageCount: card.querySelectorAll("img").length,
              eventAttributes: elements.flatMap((element) => Array.from(element.attributes)
                .filter((attribute) => /^on/i.test(attribute.name)).map((attribute) => attribute.name)),
              imagePath: new URL(image.src).pathname,
              enlargeLabel: card.querySelector("button[data-action=enlarge]").getAttribute("aria-label"),
            };
          }
        """)

    def assert_card_is_safe(self, state: str) -> None:
        self.maxDiff = None
        directory = "cropped" if state == "pending" else "done"
        alt_prefix = "Scanned photo" if state == "pending" else "Done photo"
        self.assertEqual(self.results[state], {
            "alt": f"{alt_prefix} {self.filename}",
            "imageCount": 1,
            "eventAttributes": [],
            "imagePath": f"/{directory}/" + quote(self.filename, safe="-_.!~*'()"),
            "enlargeLabel": f"View {self.filename} larger",
        })

    def test_pending_gallery_filename_stays_text_and_uses_encoded_image_path(self) -> None:
        self.assert_card_is_safe("pending")

    def test_done_gallery_filename_stays_text_and_uses_encoded_image_path(self) -> None:
        self.assert_card_is_safe("done")


if __name__ == "__main__":
    unittest.main()
