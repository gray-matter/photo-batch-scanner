import html
import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
CHROME_CANDIDATES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
)


def chrome_executable() -> str | None:
    executable = next((shutil.which(name) for name in CHROME_CANDIDATES if shutil.which(name)), None)
    if executable:
        return executable
    mac_chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    return str(mac_chrome) if mac_chrome.is_file() else None


class GalleryFilenameSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.chrome = chrome_executable()
        if cls.chrome is None:
            raise unittest.SkipTest("Google Chrome or Chromium is required for the gallery DOM test")

        cls.temporary_directory = tempfile.TemporaryDirectory()
        cls.profile_directory = Path(cls.temporary_directory.name) / "chrome-profile"
        cls.filename = 'frame " onerror="window.injected=1"><img src=x onerror="window.injected=2"> &.jpg'
        filename_literal = json.dumps(cls.filename)
        harness = f"""
          <script>
            window.fetch = async (url) => ({{
              ok: true,
              json: async () => url === "/api/photos" ? {{ photos: [] }} : {{ stage: "idle" }}
            }});
            window.setInterval = () => 0;
          </script>
          <script src="/app.js"></script>
          <script>
            const unsafeFilename = {filename_literal};
            function inspectGalleryCard(card, prefix, directory) {{
              const image = card.querySelector("img");
              const elements = [card, ...card.querySelectorAll("*")];
              return {{
                alt: image.alt,
                imageCount: card.querySelectorAll("img").length,
                eventAttributes: elements.flatMap((element) =>
                  Array.from(element.attributes)
                    .filter((attribute) => /^on/i.test(attribute.name))
                    .map((attribute) => attribute.name)
                ),
                imagePath: new URL(image.getAttribute("src"), location.href).pathname,
                expectedPath: `/${{directory}}/${{encodeURIComponent(unsafeFilename)}}`,
                expectedAlt: `${{prefix}} ${{unsafeFilename}}`
              }};
            }}
            const pendingCard = renderCard({{ filename: unsafeFilename }});
            const doneCard = renderDoneCard({{ filename: unsafeFilename }});
            document.documentElement.dataset.galleryFilenameResults = encodeURIComponent(JSON.stringify({{
              pending: inspectGalleryCard(pendingCard, "Scanned photo", "cropped"),
              done: inspectGalleryCard(doneCard, "Done photo", "done")
            }}));
          </script>
        """
        page = (STATIC / "index.html").read_text()
        page = page.replace(
            '  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css" />\n',
            "",
        )
        page = page.replace(
            '  <script src="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js"></script>\n'
            '  <script src="/app.js"></script>',
            harness,
        )

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path == "/" or self.path == "/index.html":
                    body = page.encode()
                    content_type = "text/html; charset=utf-8"
                elif self.path == "/app.js":
                    body = (STATIC / "app.js").read_bytes()
                    content_type = "text/javascript; charset=utf-8"
                elif self.path == "/style.css":
                    body = (STATIC / "style.css").read_bytes()
                    content_type = "text/css; charset=utf-8"
                else:
                    body = b"Not found"
                    content_type = "text/plain"
                    self.send_response(404)
                    self.send_header("Content-Type", content_type)
                    self.end_headers()
                    self.wfile.write(body)
                    return

                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *_args: object) -> None:
                pass

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        url = f"http://127.0.0.1:{cls.server.server_port}/"
        process = subprocess.Popen(
            [
                cls.chrome,
                "--headless=new",
                "--disable-gpu",
                "--no-sandbox",
                "--disable-background-networking",
                "--disable-sync",
                "--user-data-dir=" + str(cls.profile_directory),
                "--dump-dom",
                "--timeout=5000",
                url,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            try:
                output, _stderr = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                output, _stderr = process.communicate(timeout=5)
        except Exception:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
            cls.server.shutdown()
            cls.server.server_close()
            cls.server_thread.join()
            cls.temporary_directory.cleanup()
            raise
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join()
        if "<html" not in output.lower():
            cls.temporary_directory.cleanup()
            raise unittest.SkipTest(
                "Headless Chrome could not start in this environment; rerun the suite with browser execution allowed"
            )
        cls.results = cls._read_browser_results(output)

    @classmethod
    def tearDownClass(cls) -> None:
        if hasattr(cls, "temporary_directory"):
            cls.temporary_directory.cleanup()

    @staticmethod
    def _read_browser_results(output: str) -> dict[str, dict[str, object]]:
        marker = 'data-gallery-filename-results="'
        start = output.find(marker)
        if start == -1:
            raise AssertionError("Headless browser did not publish gallery filename results")
        start += len(marker)
        end = output.find('"', start)
        encoded = html.unescape(output[start:end])
        return json.loads(unquote(encoded))

    def assert_card_is_safe(self, state: str) -> None:
        self.maxDiff = None
        observed = self.results[state]
        directory = "cropped" if state == "pending" else "done"
        alt_prefix = "Scanned photo" if state == "pending" else "Done photo"
        expected_path = f"/{directory}/" + quote(self.filename, safe="-_.!~*'()")
        self.assertEqual(
            observed,
            {
                "alt": f"{alt_prefix} {self.filename}",
                "imageCount": 1,
                "eventAttributes": [],
                "imagePath": expected_path,
                "expectedPath": expected_path,
                "expectedAlt": f"{alt_prefix} {self.filename}",
            },
        )

    @unittest.expectedFailure
    def test_pending_gallery_filename_stays_text_and_uses_encoded_image_path(self) -> None:
        self.assert_card_is_safe("pending")

    @unittest.expectedFailure
    def test_done_gallery_filename_stays_text_and_uses_encoded_image_path(self) -> None:
        self.assert_card_is_safe("done")


if __name__ == "__main__":
    unittest.main()
