# Context: Iris app fixes (issues 1, 3, 4)

You are fixing three issues in the Iris phone app. They come from a read-only review of the app against the Iris server contract. The full review is at `/Users/navaj/iris-kit/connection-review.md`. This file covers only the three issues that belong to the app.

Do not change the contract in `docs/contract/` or the server. Contract gaps (issues 2, 5, 6, 8) and the server issue (7) are being settled separately.

## Setup

- The phone app streams camera frames to a server running on a laptop (`http://10.138.20.97:8000` during development).
- The server exposes `POST /frame` (upload) and `GET /health` (used by the app's "Check server" button).
- The app has one settings field for the server address. Later the same field will hold a hosted address such as `https://<ip-with-dashes>.nip.io` (Vultr).

## Issue 1: A server URL with a path breaks every request

**Severity:** blocks the demo

**Symptom:** "Check server" fails with an error that is almost certainly HTTP 404. The phone can reach the laptop, so the network path is fine.

**Cause:** the app keeps whatever path the user typed and appends `/health` (health check) or `/frame` (upload) to it. If the saved URL ends in `/frame`:

- health check requests `/frame/health`, which returns 404
- uploads go to `/frame/frame`, which also fails

Server behavior was confirmed with read-only requests against the port-8000 server:

- `GET /health` returns 200
- `GET /frame/health` returns 404

**Fix:** treat the field as the server's **base URL** and build both addresses from it:

- uploads: `<base>/frame`
- health check: `<base>/health`

Normalize the input before saving or using it: strip a trailing `/frame` and any trailing `/`, so `http://10.138.20.97:8000`, `http://10.138.20.97:8000/` and `http://10.138.20.97:8000/frame` all behave the same. This must also work for the future `https://<ip-with-dashes>.nip.io` form.

**Quick workaround (no code change):** set the field to just `http://<laptop-ip>:8000`.

**Done when:**

- "Check server" turns green with the field set to the base URL and with `/frame` appended
- uploads reach `/frame` (not `/frame/frame`) in both cases

## Issue 3: Stale frame is re-sent after a video decode failure

**Severity:** degrades

**Symptom:** after a video decode failure, the app re-sends the last good frame every 400 ms until a keyframe arrives. A person who was in that old frame can therefore keep triggering an alert after they are no longer in view. (My reading of "an old person can trigger an alert" is a person from a stale frame. If the review report means something different, follow the report.)

**Why it matters:** the server cannot tell a re-sent frame from a live one, so it analyzes the stale image and may speak an alert about something that is no longer there.

**Suggested direction (not prescribed by the review):** while decoding is failing, do not upload stale frames. Skip sending until a new good frame or keyframe arrives, then resume. A stale frame should never be treated as a live one. Check the contract and the app's own tests before choosing a different approach, for example re-sending once or flagging the frame as stale.

**Must keep working:** the stream must recover on its own after the decode error, with no restart and no crash.

**Done when:** after a simulated decode failure, no further uploads carry the old frame, and normal sending resumes at the next good frame or keyframe.

## Issue 4: Uploads and health check share one connection

**Severity:** cosmetic

**Symptom:** the health check can time out because it queues behind an in-flight upload on the same connection or session. With the 1.5 s timeout, "Check server" can report a failure while the server is actually fine.

**Suggested direction:** give the health check its own session or connection, separate from the upload session. Keep uploads as they are (one shared session, one request at a time).

**Done when:** tapping "Check server" during active streaming returns the real server status and does not time out merely because an upload is in progress.

## Behavior that already works: do not change

- **Request:** multipart body, field name `file`, `image/jpeg`, 640 px wide at real pixel scale, quality 0.7. The whole body is checked against the server's 2 MB cap so a 413 cannot happen.
- **Sending:** about 2.5 fps, one request at a time with frames skipped while busy, one shared session for uploads, 1.5 s timeouts, recovery after every kind of error.
- **Decoding:** tolerant. All three fixtures in `docs/contract/` decode, and the app's tests bundle them.
- **Alerts:** spoken and interrupt current speech. `description` is never spoken. UI updates run on the main thread.
- **Key:** `X-Iris-Key` is sent when the key is set.
- **Errors:** every error code means "no alert, keep streaming".
- **Info.plist:** both settings are present.

## Working notes

- The app's code was read from `origin/main` (`git show origin/main:<path>`). Nothing has been edited or committed yet, so confirm file locations yourself.
- Verify against the laptop server with:
  ```bash
  curl -s -o /dev/null -w '%{http_code}\n' http://10.138.20.97:8000/health         # expect 200
  curl -s -o /dev/null -w '%{http_code}\n' http://10.138.20.97:8000/frame/health   # expect 404
  ```
- Keep the changes minimal and scoped to these three issues.
