import shutil
import subprocess
import unittest
from pathlib import Path


API_MODULE = (Path(__file__).resolve().parents[1] / "static/js/api.js").as_uri()


class BrowserApiTests(unittest.TestCase):
    def run_node(self, script: str) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node.js is required for browser request/polling tests")
        process = subprocess.run(
            [node, "--input-type=module", "-e", f'import {{checkedRequest, requestJSON, createPolling}} from "{API_MODULE}";\n' + script],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(process.returncode, 0, process.stderr)

    def test_request_errors_keep_details_fallbacks_and_response_contracts(self) -> None:
        self.run_node("""
          import assert from "node:assert/strict";
          const options = {method: "POST", signal: new AbortController().signal};
          globalThis.fetch = async (url, actualOptions) => {
            assert.equal(url, "/fixture"); assert.equal(actualOptions, options);
            return {ok: false, json: async () => ({detail: "Server detail"})};
          };
          await assert.rejects(requestJSON("/fixture", options, {detail: true}), /Server detail/);
          await assert.rejects(checkedRequest("/fixture", options, {errorMessage: "Rotate failed"}), /Rotate failed/);
          globalThis.fetch = async () => ({ok: false, json: async () => {throw new SyntaxError("Not JSON");}});
          await assert.rejects(requestJSON("/fixture", {}, {detail: true, errorMessage: "Scan failed"}), /Scan failed/);
          globalThis.fetch = async () => ({ok: false, json: async () => ({detail: [{msg: "Invalid count"}]})});
          await assert.rejects(requestJSON("/fixture", {}, {detail: "string", errorMessage: "Couldn't detect photos"}), /Couldn't detect photos/);
          const networkError = new TypeError("Network unavailable");
          globalThis.fetch = async () => {throw networkError;};
          await assert.rejects(requestJSON("/fixture"), (error) => error === networkError);
          globalThis.fetch = async () => ({ok: true, json: async () => {throw new SyntaxError("Invalid success body");}});
          await checkedRequest("/fixture");
          await assert.rejects(requestJSON("/fixture"), /Invalid success body/);
          globalThis.fetch = async () => ({ok: false, json: async () => ({results: []})});
          assert.deepEqual(await requestJSON("/fixture", {}, {checkStatus: false}), {results: []});
        """)

    def test_polling_skips_overlapping_ticks_and_releases_guard_after_failure(self) -> None:
        self.run_node("""
          import assert from "node:assert/strict";
          const timers = new Map();
          globalThis.setInterval = (callback, interval) => {timers.set(interval, callback); return interval;};
          globalThis.clearInterval = (timer) => timers.delete(timer);
          let calls = 0;
          let release;
          let fail = false;
          const main = createPolling(async () => {
            calls += 1;
            if (fail) throw new Error("Refresh failed");
            await new Promise((resolve) => {release = resolve;});
          }, 700);
          const remote = createPolling(async () => {}, 1000);
          main.start(); main.start(); remote.start();
          assert.deepEqual([...timers.keys()], [700, 1000]);
          const pending = main.refresh();
          await timers.get(700)(); await main.refresh();
          assert.equal(calls, 1);
          release(); await pending;
          fail = true;
          await assert.rejects(main.refresh(), /Refresh failed/);
          fail = false;
          const next = timers.get(700)();
          assert.equal(calls, 3);
          release(); await next;
          main.stop(); remote.stop(); assert.equal(timers.size, 0);
        """)


if __name__ == "__main__":
    unittest.main()
