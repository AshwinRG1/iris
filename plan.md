# Iris: Team Plan

Oct 16, 2026 · @ashwin

## Overview

Iris streams Meta glasses camera frames to a Python server that detects people in rubble, storm and flood footage, then speaks an alert like "possible person, left" through the glasses. Repo: [github.com/AshwinRG1/iris](https://github.com/AshwinRG1/iris).

| Person | Owns | Files |
| --- | --- | --- |
| A | iOS app: Meta DAT SDK, Mock Device Kit, frame upload, spoken alerts | `app/` |
| B | Server and infra: FastAPI, alert logic, live view, Vultr deploy | `server/main.py`, `server/config.py` |
| C | Model: datasets, storm augmentation, YOLO26 fine-tuning, zero-shot baseline, evaluation | `training/`, `data/`, `server/zeroshot.py`, `server/weights/` |
| D | Integrations and demo: Gemini, command-center page, demo setup, Devpost, pitch | `server/gemini.py`, `server/dashboard/`, `docs/` |

Each person edits only their own files. B owns `main.py` and wires in C's weights and D's `gemini.py`. The API contract below is the one thing everyone depends on, so change it only after talking.

**API contract:** the phone sends `POST /frame` with a multipart field `file` holding a JPEG (640px wide, quality 70). The server replies:

```json
{"detections": [{"label": "person", "conf": 0.71, "side": "left"}], "alert": "possible person, left"}
```

`alert` is `null` when nothing should be spoken. The server owns all alert logic (confidence, consecutive frames, cooldown), so tuning never needs an app rebuild.

## Checkpoints

Stop and check in together at each one. If a checkpoint slips more than 2 hours, cut scope using the order at the bottom of this doc.

- [ ] **Hour 1.5, Checkpoint 0:** the CameraAccess sample streams a mock feed on the iPhone, and the phone's browser loads `http://<laptop-ip>:8000/docs`.
- [ ] **Hour 3, Checkpoint 1:** `curl -F "file=@test.jpg" http://localhost:8000/frame` returns JSON, and the app can POST a frame and get a reply.
- [ ] **Hour 12, Checkpoint 2:** a mock video streams from the app, the server returns detections, and the dashboard shows boxes.
- [ ] **Hour 16, Checkpoint 3:** full loop on every demo clip: you hear "possible person, left" at the right moments, with no spam.
- [ ] **Hour 26, Feature freeze:** only bug fixes after this.

## Person A: iOS app

A owns everything on the phone: getting frames out of the glasses (or Mock Device Kit), sending them to the server, and speaking the alerts.

**Setup (hours 0 to 1.5)**

- [ ] Install Xcode and enable Developer Mode on the iPhone.
- [ ] Clone [meta-wearables-dat-ios](https://github.com/facebook/meta-wearables-dat-ios) and copy the CameraAccess sample into `app/`.
- [ ] Add the Application ID (from the Wearables Developer Center) to the app config.
- [ ] Run on the iPhone: ladybug icon, then Pair RayBan Meta, Power On, Unfold, Don, and stream the phone camera.
- [ ] In `Info.plist`, add `NSAppTransportSecurity` with `NSAllowsLocalNetworking` = YES, plus `NSLocalNetworkUsageDescription`.
- [ ] Add `app/**/xcuserdata/`, `app/**/DerivedData/` and `.DS_Store` to `.gitignore`.

**Hours 1.5 to 3**

- [ ] Find where the sample turns each frame into an image for the screen.
- [ ] POST one test frame to B's server with `URLSession` and print the JSON reply.

**Hours 3 to 12**

- [ ] `FrameUploader.swift`: every 400ms (2 to 3 fps), scale the frame to 640px wide, encode JPEG at 0.7, POST as multipart field `file`. Skip a frame if the last request hasn't returned.
- [ ] `AlertSpeaker.swift`: wrap `AVSpeechSynthesizer`; speak `alert` whenever it isn't null, stopping any speech already playing.
- [ ] Status screen: editable server URL (not hardcoded), an on/off toggle, and the last alert text.
- [ ] Load each h265 demo clip from C into the mock camera feed and confirm it plays.

**Hours 12 to 16**

- [ ] Run the full loop with B and C on every demo clip; fix frame size, timeout and network issues on the app side.

**Hours 16 to 26**

- [ ] Point the server URL at Vultr once B has it running, and retest over cellular.
- [ ] Show the Gemini description on the status screen once D adds it to the response.
- [ ] Measure app-side latency per frame (send to reply) for the results table.

**Hours 26 to 36**

- [ ] Screen-record the phone for the backup demo video.
- [ ] Own the live demo: sweeping the camera over the rubble pile and handing the phone to judges.

## Person B: server and infra

B owns the running server: receiving frames, alert logic, the live view, and the cloud deploy.

**Setup (hours 0 to 1.5)**

- [ ] Create the Wearables Developer Center org and project; send the Application ID to A.
- [ ] Create a venv and `pip install ultralytics fastapi uvicorn python-multipart opencv-python google-genai`; save it as `server/requirements.txt`.
- [ ] Smoke test: `yolo predict model=yolo26s.pt source='https://ultralytics.com/images/bus.jpg'`.

**Hours 1.5 to 3**

- [ ] `server/main.py`: `/frame` running stock YOLO26, `/latest` returning the last annotated frame, `/` auto-refreshing live view.
- [ ] Alert logic: confidence threshold, 2 consecutive frames, 4-second cooldown, and left/ahead/right from the box center.
- [ ] `server/config.py`: CONF, NEEDED\_STREAK, COOLDOWN, model path; keys read from environment variables.
- [ ] Run with `uvicorn main:app --host 0.0.0.0 --port 8000` and pass the curl test.

**Hours 3 to 12**

- [ ] Add a `/events` endpoint returning recent alerts (time, side, conf) for D's command-center page.
- [ ] Log per-frame server latency.
- [ ] Stand up the Vultr instance early (Ubuntu, clone, install, open port 8000) so the deploy isn't a last-minute surprise.

**Hours 12 to 16**

- [ ] Run the full loop with A and C; tune CONF, NEEDED\_STREAK and COOLDOWN until alerts feel right.

**Hours 16 to 26**

- [ ] Swap in C's `best.pt` and update the person labels. Keep `yolo26s.pt` as a fallback.
- [ ] Wire D's `gemini.py` into `/frame` so it runs on alerts only.
- [ ] Move the server to Vultr, copy the weights, and send A the URL.

**Hours 26 to 36**

- [ ] Keep the server up during judging; have the laptop version ready as a fallback.

## Person C: model

C owns the detector: data, storm augmentation, training, the zero-shot baseline, and the results.

**Setup (hours 0 to 1.5)**

- [ ] Same venv packages as B, plus `albumentations` and `transformers`.
- [ ] Make a Roboflow account and shortlist rubble/victim and flood/person-in-water datasets.
- [ ] Install ffmpeg; convert one test clip with `ffmpeg -i clip.mp4 -c:v libx265 -tag:v hvc1 clip_h265.mp4` and send it to A.

**Hours 1.5 to 12**

- [ ] `server/zeroshot.py`: OWLv2 (`google/owlv2-base-patch16-ensemble`) with prompts like "human hand", "arm", "person lying down". Give B a function with the same output shape as YOLO.
- [ ] Export the datasets in YOLO format and merge into `data/merged/` with one class set (`training/merge_datasets.py`).
- [ ] `training/augment.py`: one storm copy (rain, fog, motion blur, dark, noise) of each training image. Leave val and test unaugmented.
- [ ] Start training on Colab or a Vultr GPU: `yolo26s.pt`, 50 epochs, imgsz 640, patience 10.
- [ ] Pick 3 to 4 demo clips not used in training, including one with no people; convert to h265 and give them to A.

**Hours 12 to 16**

- [ ] Join the full-loop test with A and B; note which clips the stock model misses.

**Hours 16 to 26**

- [ ] Hand `best.pt` and the class names to B.
- [ ] `training/evaluate.py`: stock vs fine-tuned vs zero-shot on the test split, plus detection rate on demo clips; write `docs/results.csv`.

**Hours 26 to 36**

- [ ] Turn the results into one table or slide with D.
- [ ] Answer the model questions at the table: training data, storm augmentation, results.

## Person D: integrations and demo

D owns the sponsor features and everything the judges see beyond the phone.

**Setup (hours 0 to 1.5)**

- [ ] Get a Gemini API key from Google AI Studio and Vultr credits through the MLH link; share both with B privately, not in git.
- [ ] Find raw demo footage (collapsed building, flooded street, rain at night) for C to filter.

**Hours 1.5 to 12**

- [ ] `server/gemini.py`: a `describe(jpeg_bytes)` function returning one sentence for a rescuer (people visible, how exposed, nearby hazards). Use a current Flash model from AI Studio.
- [ ] `server/dashboard/`: a command-center page that polls B's `/events` and shows the live frame, an alert log, and Gemini descriptions.
- [ ] Draw the architecture diagram for Devpost and the pitch.

**Hours 12 to 16**

- [ ] Test `gemini.py` on frames from the full loop; keep descriptions short enough to read at a glance.

**Hours 16 to 26**

- [ ] Hand `gemini.py` to B to wire in; point the dashboard at the Vultr URL.
- [ ] Build the rubble pile and test it under venue lighting.
- [ ] Draft the Devpost write-up.

**Hours 26 to 36**

- [ ] Record the backup demo video with A.
- [ ] Lead the pitch: problem, live demo handoff to A, results from C, limits and next steps.

## Shared

These belong to everyone; the owner listed in each person's section leads, the rest help.

- [ ] Each person creates their own GitHub token; never share tokens or commit `local.properties`, `.env` or keys.
- [ ] Commit and push after every working change. Weights, datasets and demo clips go in a shared Google Drive folder, not git.
- [ ] Build the rubble pile at the table (boxes, blanket or tarp, crumpled paper) and test it under venue lighting.
- [ ] Record the backup demo video: phone screen, dashboard and audio, start to finish. Keep it open in a tab during judging.
- [ ] Devpost write-up: problem, how it works, architecture diagram, results table, limits, next steps. Select AI/ML, Vultr and Gemini.
- [ ] Practice the 3-minute pitch at least 5 times: D opens, A runs the live demo, C covers results, B handles infra questions.
- [ ] Sleep in shifts before judging, two at a time, so someone always has the server and app running.

**If you fall behind, cut in this order:**

1. Gemini description
2. Vultr deploy (stay on the laptop with a phone hotspot)
3. Fine-tuning (stock YOLO26 plus the zero-shot comparison still demos well)

Never cut the working end-to-end loop or the backup video.
