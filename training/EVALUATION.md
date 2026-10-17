# Dataset evaluation

Run commands from the repository root. The evaluator uses Ultralytics 8.4.178,
matching the server ML requirements. On Windows, create a standard CPython environment
(the MSYS Python installation is unsuitable for these Windows ML wheels):

```powershell
python3.11 -m venv .venv-eval
.\.venv-eval\Scripts\python.exe -m pip install -r training/requirements-eval.txt
.\.venv-eval\Scripts\python.exe training/evaluate.py
```

The commands below assume that environment is activated, or that `python` points
to its interpreter. CPU is the default device. GPU evaluation requires a compatible
PyTorch build. Run regression tests with:

```shell
python -B -m unittest discover -s training -p test_evaluate.py -v
```

## Repaired test dataset (current setup)

The corrected dataset is `data/test/data/data.yaml`: 634 images, 565 with
person annotations, 69 backgrounds, and 1,819 person boxes. Original source
labels were restored using `repair_test_labels.py`; provenance and the backup
location are recorded in `data/test/data/repair_manifest.json`.

Check annotations without loading any models:

```shell
python training/evaluate.py --check-data
```

Run the baseline (now the default):

```shell
python training/evaluate.py
```

When C delivers the checkpoints, explicitly select all three:

```shell
python training/evaluate.py --models yolo26s.pt server/weights/best_plain.pt server/weights/best_storm.pt
```

Confirm C has excluded these test images from training and model selection.
Restoring annotations does not establish split independence. Do not train using
this evaluation-only YAML, even though it contains train/val aliases for schema
compatibility.

## Early baseline

C's current merge script creates train and val splits only. You can evaluate the stock detector on validation while C trains:

```shell
python training/evaluate.py --data data/merged/data.yaml --models yolo26s.pt --split val --output docs/validation_results.csv
```

These are validation results, not independent test results. Stock `yolo26s.pt` and `yolo11s.pt` checkpoints download on first use. All other checkpoint paths must already exist.

## Final comparison

Ask C to provide a held-out test split and add its image path as `test: images/test` in the original dataset's `data.yaml`. Matching annotations belong in `labels/test`. Hold out whole source videos/scenes before training, rather than adjacent frames. Do not move already-trained-on images into the test split.

C's current augmentation script does not copy a test split. Evaluate every model on the SAME original YAML, not on `data/merged_storm`:

```shell
python training/evaluate.py --models yolo26s.pt server/weights/best_plain.pt server/weights/best_storm.pt --device 0
```

The default is the repaired test YAML and stock `yolo26s.pt` only. C saves checkpoints to `server/weights/best_plain.pt` and `server/weights/best_storm.pt`. Use `--models` to select available checkpoints. The default device is CPU; use `--device 0` only on a working CUDA environment.

The evaluator refuses to substitute validation when test is absent. Dataset class IDs must match model IDs. A one-class `person` dataset at ID 0 is compatible with stock COCO person ID 0; unrelated stock classes are excluded. Unsupported remappings fail explicitly.

## Outputs and interpretation

`docs/results.csv` appends one row per completed model with precision, recall, mAP50, mAP50-95, image count, runtime settings, package version, and artifact directory. Ultralytics saves plots and validation artifacts under `runs/`; the CSV `artifacts` column records the exact directory (this version may prefix relative project paths with `runs/detect/`). Existing CSV files with a different schema are rejected.

Precision and recall are Ultralytics validation summary values selected from the confidence curve, not measurements of the server's alert threshold. The low default `--conf 0.001` preserves candidates for AP calculation. `--iou` controls NMS, not the IoU thresholds used to calculate mAP.

Timing columns are Ultralytics mean preprocessing, inference, and postprocessing milliseconds per image. They exclude phone upload, speech, and network latency. Compare models on the same hardware with the same image size, batch, and settings; the default batch is 1 and image size is 640. These results do not measure the server's consecutive-frame or cooldown logic.

## Remaining evaluation work

This script evaluates YOLO bounding-box checkpoints only. OWLv2 is implemented in `server/zeroshot.py` and scored by `training/evaluate_zeroshot.py`; see [OWLV2.md](OWLV2.md) for the fixed whole-person prompts and matching metric settings. Independent video clips need frame-level ground truth before detection rate and false-positive metrics can be reported; raw detection frequency is not accuracy.

Ultralytics validation API reference: https://docs.ultralytics.com/modes/val/

## Supplied checkpoints

The supplied plain/storm checkpoints were trained from YOLO11s. Compare them with stock `yolo11s.pt` as well as the original YOLO26s baseline. See [the recorded comparison](../docs/evaluation_results.md) for results and limitations.
