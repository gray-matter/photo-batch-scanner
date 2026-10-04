# Mass Scanner refactoring plan

User decisions: include correctness fixes as separate tasks; remove the unused `python-multipart` dependency; preserve one hardware scan at a time, background detection, and queued reviews while subsequent scans can start. Keep FastAPI, plain JavaScript, OpenCV, Pillow, httpx, zeroconf, and the existing exiftool wrapper. No framework migration or additional runtime library is proposed.

Suggested phases: remove the unused dependency (task 1), address correctness and safety independently (tasks 2–6), then make structural changes (tasks 7–9). Each task should be a separate reviewable change. The user can change the ordering or batching.

Review evidence: both browser scripts pass `node --check`. Temporary-file checks confirmed EXIF date loss during rotation and duplicate points for a diamond selection. An isolated execution of the current extraction handler with a simulated failed write confirmed that it returns a created filename and removes the review despite creating no file. The detector concurrency and filename rendering findings below come from source inspection, not a reproduced production incident.

Source references describe the repository as reviewed; locate the named functions if earlier tasks shift line numbers. Application source and dependencies have not been changed by this review.

## 1. Remove the confirmed unused multipart dependency
**Criticality:** Medium
**Issue:** `python-multipart` is explicitly installed although every request body is JSON and there are no upload or form endpoints. It adds an unused dependency and its maintenance burden.

**For the coding agent:**
Remove the direct dependency from [pyproject.toml](/Users/x.noelle/projects/mass-scanner/pyproject.toml:13) and regenerate `uv.lock` with uv. The user explicitly chose removal; planned upload support is outside this task. Do not update unrelated dependency versions. Validate with `uv sync --locked` and an application startup check. If the resolver retains multipart as a transitive dependency, retain that required transitive entry. Done means it is no longer a direct requirement and the lockfile matches the project metadata.

## 2. Preserve EXIF metadata when rotating a photo
**Criticality:** Critical
**Issue:** [api_rotate](/Users/x.noelle/projects/mass-scanner/app.py:254) saves the rotated JPEG without passing its EXIF data. A temporary JPEG with `DateTimeOriginal` lost that tag after the current operation, so rotating an already tagged photo silently removes metadata.

**For the coding agent:**
Preserve date and GPS EXIF tags and the ICC profile through 90, 180, and 270 degree rotations; normalize orientation metadata consistently with physically rotated pixels. Keep the API's counter-clockwise degree convention, response shape, and JPEG quality of 92. Use context-managed image reads and save to a temporary sibling before replacing the original so an encoding failure preserves the source file. Keep this fix separate from service extraction and from changes to tagging behavior. Manually verify with disposable tagged photos. Done means they retain their metadata, have the expected dimensions and orientation, invalid degrees still return the existing 400 response, and a failed save leaves the original readable.

## 3. Report crop write failures and keep failed reviews available
**Criticality:** Critical
**Issue:** [api_extract](/Users/x.noelle/projects/mass-scanner/app.py:269) and [crop_scanned_photos](/Users/x.noelle/projects/mass-scanner/crop.py:230) ignore `cv2.imwrite`'s Boolean result. The API reports files that do not exist and removes the pending review even when writing fails.

**For the coding agent:**
Treat `imwrite` returning false or raising an exception as a failure, including failure after an earlier crop succeeds. The API must return a non-success response with a readable error and retain the matching pending review; the CLI must terminate unsuccessfully rather than report an unwritten crop. Stage outputs in temporary files in the output directory and check encoding success before publishing them. Clean up staged files on failure. If publication fails partway, retain the review and report failure; do not claim multi-file filesystem atomicity or delete pre-existing photos while cleaning up. Preserve numbering, successful response shapes, quality settings, the CLI output directory, and the existing handling of crops that return `None`; changing collision policy or all-degenerate selection behavior is outside this task. Manually check successful and failed writes using disposable scans and output directories. Done means unwritten names never appear in a success result and the user can retry a failed review.

## 4. Order quadrilateral corners without duplicates
**Criticality:** Critical
**Issue:** [order_quad_points](/Users/x.noelle/projects/mass-scanner/crop.py:64) independently chooses extrema of sums and differences. Ties can select the same input point for multiple corners: the diamond `[[50,0],[100,50],[50,100],[0,50]]` produces only three unique corners, corrupting the perspective transform.

**For the coding agent:**
Use a deterministic cyclic ordering of four distinct convex points, with a documented stable starting-corner tie-break and consistent winding. Preserve the existing top-left, top-right, bottom-right, bottom-left correspondence for ordinary rectangles and keep the output as a float32 NumPy array. Manually inspect crops for axis-aligned rectangles, diamonds, and skewed convex quads using a disposable image with distinguishable corners. Validate four finite, distinct points with nonzero area before calling OpenCV. Preserve the existing `None` outcome for geometrically degenerate selections; malformed point counts or non-finite coordinates must yield an actionable 400 response at the extraction boundary rather than an unhandled error. Limit this change to geometry and its boundary handling; do not retune photo detection or face scoring. Done means valid quads retain four unique corners and produce correctly oriented crops, while invalid selections receive the specified handling.

## 5. Serialize access to the mutable face detector
**Criticality:** High
**Issue:** [_face_score](/Users/x.noelle/projects/mass-scanner/crop.py:14) changes the input size of one global detector before detection. Concurrent extraction requests can interleave those operations on the same detector; this is a race risk visible in the source.

**For the coding agent:**
Protect the `setInputSize` and `detect` pair with one dedicated lock; ensure both operations occur under the same lock and release it if detection raises. Keep detector construction, model location, score threshold 0.6, high-confidence threshold 0.85, scoring order, and rotation tie-break behavior unchanged. Do not use the scanner hardware lock or serialize the entire scan/review pipeline. Inspect lock ownership and exception handling, then manually check extraction of disposable photos with different dimensions, including photos without faces. Done means detector access is serialized while background detection and hardware scanning remain independent. No production race was reproduced during review.

## 6. Render gallery filenames as text
**Criticality:** Critical
**Issue:** [renderCard](/Users/x.noelle/projects/mass-scanner/static/app.js:222) and [renderDoneCard](/Users/x.noelle/projects/mass-scanner/static/app.js:301) interpolate filenames directly into HTML `alt` attributes. Filenames are not universally generated by the scanner: the documented CLI accepts user-named input files. A crafted filename containing quotes and markup can become browser markup.

**For the coding agent:**
Retain the fixed card template, but create images or assign their `src` and `alt` through DOM properties after constructing that template. Assign all dynamic filename text and attributes using DOM APIs; continue applying `encodeURIComponent` to URL path segments. Keep trusted static badge markup and the existing card controls, labels, selection behavior, and cache-busting URLs. Do not combine this with module restructuring. Manually verify pending and done cards with filenames containing quotes, ampersands, and angle brackets: the exact filename appears in the alt text and no injected element or event attribute is created. Run both JavaScript syntax checks. Done means filenames remain literal attribute values and all card controls work as before. The exploit was identified by inspection, not executed against the running application.

## 7. Give the scan lifecycle one owner
**Criticality:** High
**Issue:** Locks, mutable status, review ordering, and worker orchestration are spread across [app.py](/Users/x.noelle/projects/mass-scanner/app.py:52). The same review is represented in both the queue and status dictionary, making changes require reasoning about several loosely coupled mutations.

**For the coding agent:**
Extract a `ScanCoordinator` into `scan_jobs.py`. It should own the hardware lock, state lock, status, queued reviews, start operation, detection completion, re-detection updates, and review removal. Keep HTTP handlers in `app.py`; pass discovery, transfer, image loading, detection, and the raw directory explicitly into the coordinator to make its boundaries clear. Use explicit typed state/review records internally and preserve the exact public status fields, including `pending_review` and optional `expected_count`; do not rename stages or change HTTP responses.

The user chose to preserve the current pipeline: hold the hardware lock through discovery/transfer, release it while detection continues, allow subsequent scans while reviews remain pending, and order available reviews by raw filename. The main and remote pages intentionally have different scan-button policies; keep those policies. Preserve current single-process operation, daemon worker behavior, in-memory review lifetime, routes, and the `uvicorn app:app` entry point. Persistent jobs, multi-worker support, event streams, and new scan gating are out of scope. Do not slip exception-policy changes into this structural move. Manually check the 409 response during hardware scanning, starting another scan with a review pending, queue ordering, re-detection, and extraction/discard removing only the matching review. Done means those workflows and status responses remain unchanged and `app.py` no longer manipulates coordinator internals. Overlapping worker behavior needs particular care during the move.

## 8. Share photo storage and crop output workflows
**Criticality:** Medium
**Issue:** [safe path helpers](/Users/x.noelle/projects/mass-scanner/app.py:111) repeat directory containment logic, while the API and [CLI crop loop](/Users/x.noelle/projects/mass-scanner/crop.py:230) repeat naming, extraction, and JPEG output rules. Fixes otherwise need to be made in multiple places.

**For the coding agent:**
After tasks 2–5, introduce `photo_store.py` for directory configuration, containment-checked file lookup, listing, delete, move, and rotation operations. Keep domain lookup failures independent of FastAPI and translate them to the existing endpoint-specific 404 messages in `app.py`. Preserve rejection of symlinks resolving outside the requested directory, reverse filename ordering, current done/restore semantics, and all public response shapes. Put shared extraction/output handling in `photo_processing.py`, leaving detection and perspective algorithms in `crop.py`. Both `api_extract` and `crop_scanned_photos` should call the same output workflow with explicit output directory and JPEG quality; preserve each caller's logging and error mapping.

Avoid a generic repository framework or new storage dependency. Preserve the documented `crop.py` entry point and its current output directory `photos_decoupees`; correct the README's claim that this command automatically writes into `scans/cropped/`, and correct the stale CLI usage string naming `crop_photos.py`. Also correct the README's claim that tagging automatically moves files to done: currently marking done is a separate action, and that behavior must remain separate. Update the README layout for the new modules. Manually compare API and CLI extraction using disposable inputs, check done/restore and file ordering, and inspect containment checks. Done means filenames, image output, metadata, and path safeguards preserve existing behavior plus the preceding fixes.

## 9. Split browser code by feature and share request/polling mechanics
**Criticality:** High
**Issue:** [static/app.js](/Users/x.noelle/projects/mass-scanner/static/app.js:1) mixes gallery selection, confirmations, scan status, tagging/map state, and crop geometry in 1,063 lines. The [remote control script](/Users/x.noelle/projects/mass-scanner/static/scan-control.js:31) repeats request and polling mechanics, while mutable feature state lives in shared globals.

**For the coding agent:**
After task 6, retain `static/app.js` as a small entry point and create native ES modules under `static/js/`: `api.js`, `modals.js`, `gallery.js`, `scan-status.js`, `tagging.js`, and `review.js`. Use explicit initialization functions and callbacks for cross-feature interactions; keep each feature's DOM references and mutable state within its module. Share checked JSON requests and a polling helper that skips overlapping requests. Configure the existing 700 ms main-page interval and 1,000 ms remote interval independently; keep page-specific status wording and button policies. Update [index.html](/Users/x.noelle/projects/mass-scanner/static/index.html:125) and [scan-control.html](/Users/x.noelle/projects/mass-scanner/static/scan-control.html:21) to load module entry points. Keep Leaflet's current loading and configuration; add no build tool or frontend framework.

Preserve gallery ordering, shift selection, confirmation prompts, focus restoration, review's deliberate lack of Escape dismissal, quad editing, minimum drawn selection size, asynchronous re-detection cancellation, stored photo counts, recent-address keys/limit, date removal and GPS removal flags, and all endpoints. Share mechanics rather than forcing different page UX into one component. Keep existing behavior for network failures during this structural task; a broader error-handling redesign is outside its scope. Manually check selection/tagging, review extraction/discard, aborting stale detection, stored preferences, and queued reviews arriving from the remote page. Run syntax checks on all modules. Update the README layout. Done means the entry point only wires features, feature state is locally owned, and both pages preserve their workflows.
