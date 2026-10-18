# MVP handoff

The team reports an operational stock-YOLO26 demo. The fine-tuned software path has passed local automated checks; final acceptance on the running demo server and physical phone/glasses is still pending. Do not mark the original plan's manual checkpoints complete from automated test results alone.

## Verified locally

- Full server suite: **347 tests passed**, using standard Windows CPython 3.12.15 and the pinned server dependencies. Ruff lint/format and mypy also passed.
- Evaluation suite: **33 tests passed**, including wrong-checkpoint/fallback rejection, box geometry, matching/F2, and frozen-threshold scoring safeguards.
- Both v2 weights load, match the Downloads originals by SHA-256, and contain 50 recorded training epochs.
- Three-model image comparison and seven offline clips per model completed. See [the v2 report](POST_TRAINING_V2.md).
- Dedicated local HTTP servers loaded each v2 checkpoint without fallback. Each accepted six JPEG requests and served valid frame geometry, live-view data, JPEG, and events. These checks used `CONF_DETECT=0.15`, `CONF_ALERT=0.15`, CPU inference, and descriptions off. The [HTTP record](mvp_http_checks.json) records identities and timings. They did not alter the team's running server.

## Switch the demo server for a controlled trial

B owns the running server. Copy the selected weights onto that host through the team's data-sharing workflow; a Git pull will not transfer `.pt` files. Keep stock YOLO26 available for rollback.

Start with `best_plain_v2.pt`, the provisional candidate from development F2. Storm v2 had the highest measured test scores, but the development F2 difference was small; the test results should not be used to retune the candidate or thresholds.

Expected SHA-256 hashes:

| File | SHA-256 |
| --- | --- |
| `best_plain_v2.pt` | `99ad8b2cd491c374804b19437b6c4b9a79b2302dfbb377e73b034017375dcfdf` |
| `best_storm_v2.pt` | `f64d01bc6637b3a114b389881ff2fe4ecc24a603b0ccdb43ca6a07226e0c3163` |

When launching from `server/`, configure the existing startup environment and restart:

```powershell
$env:MODEL_PATH="weights/best_plain_v2.pt"
$env:CONF_DETECT="0.15"
$env:CONF_ALERT="0.15"
```

These are experimental thresholds, not production approval. Preserve the server's existing URL, authentication, Gemini credentials, and other settings. Use standard CPython 3.12+ with `server/requirements.txt` and `server/requirements-ml.txt`; follow the CPU-PyTorch instructions in the ML requirements on CPU hosts. The model should report class `person` at ID 0. Inspect `/health`: `ready=true`, correct `model_file`/`model_sha256`, `fallback=null`.

For audio, keep `DESCRIBE=on` and the existing Gemini API key configured securely on the server. `/health` must report `describe=on`. The current app speaks descriptions, not `alert` text; disabling descriptions gives a visual-only demo. Never paste keys into issue/PR text or command arguments.

## Check B's running server

Use a dedicated test session because uploaded frames can create events and appear in the live view. From the repository root with the evaluation environment activated:

```powershell
python training/check_server_integration.py --url http://localhost:8001 --model server/weights/best_plain_v2.pt --images path/to/survivor.jpg path/to/background.jpg --output runs/live_server_check.json
```

Replace the URL and image paths with real values. If required, the checker reads the key from `IRIS_API_KEY`; use HTTPS for a remote keyed server. It uses the phone's 1.5-second request timeout, refuses model fallback/hash mismatch, checks 640-pixel JPEG upload and normalized geometry, and saves replies and round-trip timing. Use a fresh output path. This is a contract check, not an accuracy score.

Replay full clips from `server/` using B's Python 3.12+ ML environment:

```powershell
python -m scripts.replay ../data/demo_clips/aerial_harvey_distant_flood_group.mp4 --url http://localhost:8001
python -m scripts.replay ../data/demo_clips/lakewithrain_mock.mp4 --url http://localhost:8001
python -m scripts.replay ../data/demo_clips/rainy_night_negative.mp4 --url http://localhost:8001
```

Review error/skipped-frame counts and server/round-trip timing. Only use `--expect` and `--preflight` after approving a sightings JSON. Draft intervals and raw detection counts do not establish survivor detection accuracy. Known review concerns include plain v2's event in the negative candidate and storm v2's zero detections on the lake clip during the offline tests.

## Final acceptance with A, B, C, and D

- [ ] **B:** install the chosen checkpoint on the actual demo host, restart, verify its hash/no fallback, and run the checker/replays against that host.
- [ ] **A/B:** on the iPhone, configure the actual server URL/key, enable uploads, and replay the selected clips. Confirm arriving frames, aligned boxes on phone/glasses, prompt clearing after missed detections, and recovery after a failed request/reconnection.
- [ ] **A/B:** with Gemini enabled, hear each new description once, verify directions/scene content, and verify stopping the stream clears queued speech. Detection events alone do not prove audio works.
- [ ] **D:** review full clip playback, approve survivor visibility/boxes, and record target misses and false alerts. A rescuer detection must not count as finding the intended survivor.
- [ ] **C:** provide the exact v2 training/validation split records and confirm exclusion of the 634 test images; check scene/video grouping and near duplicates. Use [this request](archive/REQUEST_V2_SPLITS.md). The current repository's `training/train.py` defaults to YOLO11s; it does not by itself reproduce the supplied v2 YOLO26s Colab runs.
- [ ] **A/B/D:** save the final server commit/model hash/settings, phone round-trip measurements, and a backup recording of the visual/audio loop.

External checks above remain pending until somebody records the real observation. The stock MVP can continue being demoed while the fine-tuned candidate is trialed. The models establish visible-person detection, not hidden-victim localization or medical/survivor classification.
