# Iris

Iris helps rescuers spot visible people in rubble, flood, and storm footage using a wearable camera and a person detector. Meta Ray-Ban Display glasses send frames through an iPhone app to a FastAPI server. Detection boxes appear on the phone and glasses HUD; optional Gemini scene descriptions are spoken through the phone's audio route.

The stock-YOLO26 prototype is being demoed by the team. The fine-tuned v2 models have completed local image evaluation, offline clip checks, and isolated HTTP checks. The remaining acceptance checks are in the [MVP handoff](docs/MVP_HANDOFF.md).

## How it works

1. Glasses or the Mock Device Kit provide camera frames to the iOS app.
2. The app uploads 640-pixel JPEGs at up to 2.5 fps, with one request at a time.
3. The server detects people, returns normalized boxes and directions, and controls detection events with confidence thresholds and cooldowns.
4. The app draws detections and speaks new Gemini descriptions once per description ID. Directional `alert` text is display-only in the current app; without descriptions, detection boxes still work but there is no spoken alert.

The server also provides a browser live view, recent events, health/model identity, and latency statistics. See the [app guide](app/README.md) and [server design](docs/server-design.md) for setup and behavior. [The original team plan](plan.md) records the initial division of work; newer implementation guides describe the current behavior.

## Training and evaluation

Two v2 YOLO26s checkpoints were supplied: plain training and storm augmentation. On the corrected 634-image / 1,819-person-box benchmark:

| Model | mAP50 | mAP50-95 |
| --- | ---: | ---: |
| Stock YOLO26s | 32.48% | 14.81% |
| Plain v2 | 57.88% | 31.81% |
| Storm v2 | 64.56% | 37.76% |

These are conditional benchmark results: C's actual training/test exclusions still need verification. Original source-training exports retain copies of the test images; that alone does not establish which images the v2 runs saw. The [v2 report](docs/POST_TRAINING_V2.md) includes frozen-threshold recall/F2, recovered training histories, clip limitations, and the [split request for C](docs/REQUEST_V2_SPLITS.md). Historical stock, v1, and OWLv2 results remain in `docs/results.csv`.

The detector finds visible people. It does not distinguish rescuers from survivors or predict where an unseen survivor is located. Clip detections and event counts are not survivor accuracy; target annotations still need review.

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
