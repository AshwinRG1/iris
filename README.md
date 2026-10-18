# Iris

Iris is a wearable computer vision project designed to help rescuers spot people in rubble, storm, and flood scenes. Camera frames from Meta glasses travel through an iPhone app to a Python server, which detects possible people and returns short directional alerts such as **"possible person, left."** The phone speaks these alerts through the glasses so the wearer can keep looking at the scene.

The project is in development. The architecture and features below describe the implementation planned in [the team plan](plan.md).

## How it works

1. Meta Ray-Ban Display glasses, or a matching Mock Device Kit device for demos, provide camera frames to the iOS app.
2. The app sends JPEG frames at approximately 2–3 frames per second to the server.
3. A YOLO26 detector locates people. The server uses confidence thresholds, consecutive detections, and a cooldown to control spoken alerts.
4. The app receives a direction—left, ahead, or right—and speaks the alert.

A browser live view is planned to show annotated frames and recent alerts. Optional Gemini descriptions will provide a short account of visible people and nearby hazards when an alert occurs. The server can run on a laptop or a Vultr instance.

## Training and evaluation

The training workflow combines rubble and person-in-water datasets in YOLO format, with a shared class set. Two detectors will be fine-tuned: one using the original training images and another including storm augmentation such as rain, fog, blur, darkness, and noise.

Evaluation will compare stock YOLO26, an OWLv2 zero-shot baseline, and both fine-tuned models on a held-out test split and independent demo clips. Planned results include mAP, detection rate on clips, and latency per frame. Validation and test images remain unaugmented and separate from training.

## Project layout

The planned file layout is:

| Path | Purpose |
| --- | --- |
| `app/` | iOS camera streaming, frame upload, and spoken alerts |
| `server/` | FastAPI server, detection, alert logic, and optional Gemini integration |
| `training/` | Dataset merging, augmentation, training, and evaluation scripts |
| `data/merged/` | Prepared datasets and their splits |
| `data/demo_clips/` | Independent footage for evaluation and demos |
| `server/weights/` | Fine-tuned detector checkpoints |
| `docs/results.csv` | Model comparison results |

Datasets, model weights, and demo clips are shared separately through Google Drive rather than committed to Git.

## Dependencies and setup

See [dependencies.md](dependencies.md) for the planned Python packages, iOS tools, model assets, and external services, grouped by component.
