import io
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
from urllib.parse import unquote, urlsplit

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
CHROME_CANDIDATES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")


def chrome_executable() -> str | None:
    executable = next((shutil.which(name) for name in CHROME_CANDIDATES if shutil.which(name)), None)
    if executable:
        return executable
    mac_chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    return str(mac_chrome) if mac_chrome.is_file() else None


def run_browser(prelude: str, scenario: str, remote_prelude: str = "") -> dict[str, object]:
    chrome = chrome_executable()
    if chrome is None:
        raise unittest.SkipTest("Google Chrome or Chromium is required for browser workflow tests")

    page = (STATIC / "index.html").read_text()
    page = page.replace(
        '  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css" />\n', ""
    ).replace(
        '  <script src="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js"></script>',
        "<script>" + prelude + "</script>",
    )
    harness = """
      <script type="module">
        import "/app.js";
        const results = {};
        const byId = (id) => document.getElementById(id);
        async function waitFor(predicate, description) {
          const deadline = performance.now() + 3000;
          while (!predicate()) {
            if (performance.now() > deadline) throw new Error(`Timed out: ${description}`);
            await new Promise((resolve) => setTimeout(resolve, 10));
          }
        }
        try {
    """ + scenario + """
        } catch (error) {
          results.harnessError = error.stack;
        }
        document.documentElement.dataset.browserResults = encodeURIComponent(JSON.stringify(results));
        const report = new XMLHttpRequest();
        report.open("POST", "/__browser_results");
        report.send(JSON.stringify(results));
      </script>
    """
    page = page.replace("</body>", harness + "</body>")
    remote_page = (STATIC / "scan-control.html").read_text().replace(
        '<script type="module" src="/scan-control.js"></script>',
        "<script>" + remote_prelude + '</script><script type="module" src="/scan-control.js"></script>',
    )
    image_buffer = io.BytesIO()
    Image.new("RGB", (400, 300), "white").save(image_buffer, format="PNG")
    scan_image = image_buffer.getvalue()
    served_paths: list[str] = []
    reported = threading.Event()
    browser_results: dict[str, object] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            if self.path != "/__browser_results":
                self.send_error(404)
                return
            body = self.rfile.read(int(self.headers["Content-Length"]))
            browser_results.update(json.loads(body))
            self.send_response(204)
            self.end_headers()
            reported.set()

        def do_GET(self) -> None:
            path = unquote(urlsplit(self.path).path)
            served_paths.append(path)
            if path in ("/", "/index.html"):
                body, content_type = page.encode(), "text/html; charset=utf-8"
            elif path == "/scan-control":
                body, content_type = remote_page.encode(), "text/html; charset=utf-8"
            elif path.startswith(("/raw/", "/cropped/", "/done/")):
                body, content_type = scan_image, "image/png"
            else:
                file = (STATIC / path.lstrip("/")).resolve()
                if not file.is_relative_to(STATIC) or not file.is_file():
                    self.send_error(404)
                    return
                body = file.read_bytes()
                content_type = "text/javascript; charset=utf-8" if file.suffix == ".js" else "text/css; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            pass

    with tempfile.TemporaryDirectory() as temporary_directory:
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        try:
            process = subprocess.Popen(
                [chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
                 "--disable-background-networking", "--disable-sync",
                 "--user-data-dir=" + str(Path(temporary_directory) / "chrome-profile"),
                 f"http://127.0.0.1:{server.server_port}/"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
            )
            completed = reported.wait(timeout=30)
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
            try:
                _output, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                _output, stderr = process.communicate()
            if not completed:
                raise AssertionError(
                    f"Headless Chrome exceeded its 30s startup/workflow budget; served {served_paths}: {stderr[-3000:]}"
                )
            if "harnessError" in browser_results:
                raise AssertionError(browser_results["harnessError"])
            return browser_results
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join()
