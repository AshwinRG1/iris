# Dataset evaluation

Run commands from the repository root. Install C's shared dependencies:

```shell
python -m pip install -r training/requirements.txt
```

## Early baseline

C's current merge script creates train and val splits only. You can evaluate the stock detector on validation while C trains:

```shell
python training/evaluate.py --models yolo26s.pt --split val --output docs/validation_results.csv
```

These are validation results, not independent test results. The stock checkpoint downloads on first use. All other checkpoint paths must already exist.

## Final comparison

Ask C to provide a held-out test split and add its image path as `test: images/test` in the original dataset's `data.yaml`. Matching annotations belong in `labels/test`. Hold out whole source videos/scenes before training, rather than adjacent frames. Do not move already-trained-on images into the test split.

C's current augmentation script does not copy a test split. Evaluate every model on the SAME original YAML, not on `data/merged_storm`:

```shell
python training/evaluate.py --device 0
```

Defaults match C's paths: `data/merged/data.yaml`, `server/weights/best_plain.pt`, and `server/weights/best_storm.pt`, alongside stock `yolo26s.pt`. Use `--models` to select available checkpoints. The default device is CPU; use `--device 0` only on a working CUDA environment.

The evaluator refuses to substitute validation when test is absent. Dataset class IDs must match model IDs. A one-class `person` dataset at ID 0 is compatible with stock COCO person ID 0; unrelated stock classes are excluded. Unsupported remappings fail explicitly.

## Outputs and interpretation

`docs/results.csv` appends one row per completed model with precision, recall, mAP50, mAP50-95, image count, runtime settings, package version, and artifact directory. Ultralytics saves plots and its validation artifacts under `runs/evaluation/`. Existing CSV files with a different schema are rejected.

Precision and recall are Ultralytics validation summary values selected from the confidence curve, not measurements of the server's alert threshold. The low default `--conf 0.001` preserves candidates for AP calculation. `--iou` controls NMS, not the IoU thresholds used to calculate mAP.

Timing columns are Ultralytics mean preprocessing, inference, and postprocessing milliseconds per image. They exclude phone upload, speech, and network latency. Compare models on the same hardware with the same image size, batch, and settings; the default batch is 1 and image size is 640. These results do not measure the server's consecutive-frame or cooldown logic.

## Remaining evaluation work

This script evaluates YOLO bounding-box checkpoints only. OWLv2 needs a separate prediction adapter and consistent class/box semantics before a fair mAP comparison. Independent video clips need frame-level ground truth before detection rate and false-positive metrics can be reported; raw detection frequency is not accuracy.

Ultralytics validation API reference: https://docs.ultralytics.com/modes/val/
