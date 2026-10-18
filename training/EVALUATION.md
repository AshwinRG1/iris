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
python training/evaluate.py --models yolo26s.pt server/weights/best_plain_v2.pt server/weights/best_storm_v2.pt
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
python training/evaluate.py --models yolo26s.pt server/weights/best_plain_v2.pt server/weights/best_storm_v2.pt --device 0
```

The default is the repaired test YAML and stock `yolo26s.pt` only. The supplied v2 checkpoints are `server/weights/best_plain_v2.pt` and `server/weights/best_storm_v2.pt`; the current training script and earlier reports use the older filenames. Use `--models` to select available checkpoints. The default device is CPU; use `--device 0` only on a working CUDA environment.

The evaluator refuses to substitute validation when test is absent. Dataset class IDs must match model IDs. A one-class `person` dataset at ID 0 is compatible with stock COCO person ID 0; unrelated stock classes are excluded. Unsupported remappings fail explicitly.

## Outputs and interpretation

`docs/results.csv` appends one row per completed model with precision, recall, mAP50, mAP50-95, image count, runtime settings, package version, and artifact directory. Ultralytics saves plots and validation artifacts under `runs/`; the CSV `artifacts` column records the exact directory (this version may prefix relative project paths with `runs/detect/`). Existing CSV files with a different schema are rejected.

Precision and recall are Ultralytics validation summary values selected from the confidence curve, not measurements of the server's alert threshold. The low default `--conf 0.001` preserves candidates for AP calculation. `--iou` controls NMS, not the IoU thresholds used to calculate mAP.

Timing columns are Ultralytics mean preprocessing, inference, and postprocessing milliseconds per image. They exclude phone upload, speech, and network latency. Compare models on the same hardware with the same image size, batch, and settings; the default batch is 1 and image size is 640. These results do not measure the server's consecutive-frame or cooldown logic.

## Remaining evaluation work

This script evaluates YOLO bounding-box checkpoints only. OWLv2 is implemented in `server/zeroshot.py` and scored by `training/evaluate_zeroshot.py`; see [OWLV2.md](OWLV2.md) for the fixed whole-person prompts and matching metric settings. Independent video clips need frame-level ground truth before detection rate and false-positive metrics can be reported; raw detection frequency is not accuracy.

Ultralytics validation API reference: https://docs.ultralytics.com/modes/val/

## Historical v1 checkpoints

The original v1 plain/storm checkpoints were trained from YOLO11s. The supplied v2 weights identify YOLO26s as their base. Compare them with stock `yolo11s.pt` as well as the original YOLO26s baseline. See [the recorded comparison](../docs/evaluation_results.md) for results and limitations.


## V2 post-training workflow

V2 checkpoint paths are `server/weights/best_plain_v2.pt` and `server/weights/best_storm_v2.pt`. The stock-only default of `evaluate.py` remains available; select all checkpoints explicitly for comparisons. Missing v2 files are not replaced by old weights. Historical v1 reports keep their original checkpoint paths and results.

The development failure script includes fixed-threshold F2 and a six-point sweep (0.05, 0.10, 0.15, 0.25, 0.35, 0.50):

```powershell
python training/failure_report.py --output runs/v2_validation --models yolo26s.pt server/weights/best_plain_v2.pt server/weights/best_storm_v2.pt
python training/select_operating_points.py --source-run runs/v2_validation --output runs/v2_operating_points.json
```

This script currently uses the original export `valid/` folders and the explicit human-class mapping. It is a development diagnostic, not proof of C's exact merged validation membership. Selection files are marked provisional and not deployment approved. Audit labels, source/video grouping, and actual train/validation membership before using the selection as production evidence. Optional `--min-precision` constrains selection; if no threshold qualifies, no operating point is selected. Selection maximizes F2 only within the supplied grid and is not a guarantee of a global optimum.

Freeze threshold choices before opening held-out results. For the existing 634-image test set, save AP predictions while retaining the ordinary Ultralytics AP calculation:

```powershell
python training/evaluate.py --models yolo26s.pt server/weights/best_plain_v2.pt server/weights/best_storm_v2.pt --save-json --output docs/results_v2.csv --project runs/v2_test
python training/score_operating_points.py --data data/test/data/data.yaml --ap-results docs/results_v2.csv --selection runs/v2_operating_points.json --output docs/results_v2_operating_points.csv
```

The second command applies only the frozen validation thresholds; it does not sweep the test set. It verifies checkpoint hashes, image identities, and exact validation/test separation. Preserve the selection JSON and the complete run folder. AP summary precision/recall and fixed-threshold precision/recall/F2 are separate measures. The fixed scorer uses greedy confidence-order matching at IoU 0.5. It scores person predictions serialized by the AP validator; inference/retention details can differ from the development `predict` path. Unmatched predictions remain annotation-relative until reviewed.

Use fresh output paths per run: operating-point files and fixed-score outputs refuse replacement. `evaluate.py` appends its AP CSV, so use a new CSV for a clean comparison rather than combining duplicate model rows.

Afterward, test the actual server detector adapter and alert policy on the seven selected clips:

```powershell
python training/check_clip_adapters.py --selection runs/v2_operating_points.json --output runs/v2_clip_checks
```

This is an offline subsystem test with 640-pixel JPEG70 inputs at nominal 2.5 fps. Its detections and alerts are not clip accuracy and it does not test HTTP, network timing, iPhone playback, speech, or unfinalized survivor boxes. B and A still need the live replay/phone checks in the demo guide. No script changes production server settings automatically.

Regression checks:

```powershell
python -B -m unittest discover -s training -p test_evaluate.py
python -B -m unittest discover -s training -p test_failure_report.py
python -B -m unittest discover -s training -p test_operating_points.py
```

## Running-server checks and MVP acceptance

See [MVP_HANDOFF.md](../docs/MVP_HANDOFF.md) for model hashes, the controlled server trial, the real HTTP contract checker, and remaining phone/glasses/speech acceptance. The current app speaks Gemini descriptions; directional alert text is display-only. An alert-policy test does not test audible output.

Run the full local evaluation regression suite in the ML environment with `python -B -m unittest discover -s training -p "test_*.py"`. The lightweight CI suite installs `requirements-checks.txt` and runs the four non-ML test modules explicitly; OWLv2's metric test additionally requires the ML environment.

The fixed scorer also writes a detailed JSON beside its CSV, including per-image ground truth and scores. `docs/results_v2_operating_points.json` is ignored by Git; share it with the large run artifacts. Versioned comparison tables and `POST_TRAINING_V2.json` contain the compact results.
