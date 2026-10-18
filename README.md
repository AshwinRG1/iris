# Iris

Iris helps rescuers spot visible people in rubble, flood, and storm footage using a wearable camera and a person detector. Meta Ray-Ban Display glasses send frames through an iPhone app to a FastAPI server. Detection boxes appear on the phone and glasses HUD; optional Gemini scene descriptions are spoken through the phone's audio route.

The team is demonstrating the stock-YOLO26 prototype. The latest v3 model evaluation is complete, with results and charts below. See the [MVP handoff](docs/MVP_HANDOFF.md) for the app/server workflow.

## How it works

1. Glasses or the Mock Device Kit provide camera frames to the iOS app.
2. The app uploads 640-pixel JPEGs at up to 2.5 fps, with one request at a time.
3. The server detects people, returns normalized boxes and directions, and controls detection events with confidence thresholds and cooldowns.
4. The app draws detections and speaks new Gemini descriptions once per description ID. Directional `alert` text is display-only in the current app; without descriptions, detection boxes still work but there is no spoken alert.

The server also provides a browser live view, recent events, health/model identity, and latency statistics. See the [app guide](app/README.md) and [server design](docs/server-design.md) for setup and behavior. [The original team plan](plan.md) records the initial division of work; newer implementation guides describe the current behavior.

## Training and evaluation

The v3 YOLO26s models completed evaluation on the same corrected **634-image, 1,819-person-box** benchmark. Each v3 model records a fresh 15-epoch training run.

| Model | mAP50 |
| --- | ---: |
| Stock YOLO26s | 32.48% |
| Plain v3 | 53.04% |
| Storm v3 | 57.96% |

See the [v3 comparison and charts](docs/V3_MODEL_COMPARISON.md) for mAP50-95, precision/recall/F2, evaluation settings, and historical v2 results. Historical stock, v1, and OWLv2 results remain in `docs/results.csv`.

The detector identifies visible people. It does not distinguish rescuers from survivors or locate unseen people.

## Project layout

| Path | Purpose |
| --- | --- |
| `app/` | iOS streaming, uploads, detection HUD, and description speech |
| `server/` | FastAPI API, inference, event policy, live view, and Gemini |
| `training/` | Dataset preparation, model evaluation, diagnostics, and integration checks |
| `data/` | Local datasets, annotations, and demo footage; ignored by Git |
| `server/weights/` | Local checkpoints; ignored by Git |
| `docs/` | Results, footage credits, and handoff guides |

Start with [dependencies.md](dependencies.md), the [evaluation commands](training/EVALUATION.md), and the [demo clip guide](docs/DEMO_CLIPS.md). Datasets, weights, demo videos, and large run artifacts are shared separately through the team's Drive, rather than Git.

## Links
Devpost: https://devpost.com/software/iris-ni0pdr
Youtube Demo: https://www.youtube.com/watch?v=AVd83GBtmb0
