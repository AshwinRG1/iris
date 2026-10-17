# OWLv2 zero-shot baseline

Install in the evaluation environment from the repository root:

```powershell
.\.venv-eval\Scripts\python.exe -m pip install -r training/requirements-zeroshot.txt
```

The pretrained `google/owlv2-base-patch16-ensemble` model downloads on first use.
It does not train on the Iris dataset. The baseline uses fixed whole-person
prompts: `person`, `person lying down`, `person in water`, and
`partially visible person`. These were chosen before scoring, not tuned on test results.

## Evaluation

```powershell
.\.venv-eval\Scripts\python.exe training/evaluate_zeroshot.py --limit 2
.\.venv-eval\Scripts\python.exe training/evaluate_zeroshot.py
```

The full run audits and evaluates the same corrected test dataset as YOLO and
appends one row to `docs/results.csv` only after successful completion. Limited
runs are smoke tests and save results inside their artifact directory instead.

Predictions, stage timings, prompts, model revision, dependency version, and
precision-recall curves are saved under `runs/zeroshot/`. The full model download
and load plus warmup are excluded from timing; image preprocessing, forward
inference, and postprocessing are timed separately. Disk image loading is excluded.

Settings: 640x640 OWLv2 preprocessing with positional embedding interpolation,
FP32, batch 1, eight CPU threads (`--threads`), candidate confidence 0.001, NMS IoU 0.7, up to 300 detections per
image. All queries map to one person class; class-agnostic NMS removes duplicates.
Ultralytics 8.4.178 supplies the same IoU matching and AP/precision/recall engine
used by the YOLO evaluator. Model-specific preprocessing still differs. This is
an explicitly resized baseline, not OWLv2's native-resolution benchmark.

Hand and arm prompts are deliberately excluded from person-box AP: a hand box
cannot fairly match the entire person's ground-truth box. Do not choose prompts
or alert thresholds using the held-out test scores.

## Server integration

The existing server already imports `zeroshot.detect` when `DETECTOR=zeroshot`.
Install the server dependencies as well, then start it from the repository root:

```powershell
$env:DETECTOR = 'zeroshot'
$env:DEVICE = 'cpu'
.\.venv-eval\Scripts\python.exe -m uvicorn main:app --app-dir server --host 0.0.0.0 --port 8000
```

`GET /health` reports readiness after model loading and warmup. The normal
`POST /frame` contract is unchanged. The adapter expects a PIL image and returns
`[{"label": "person", "conf": 0.8, "box": [x1, y1, x2, y2]}]` with normalized
coordinates. Original query labels are deliberately collapsed to `person` for
compatibility with the server's alert-label filter.

The server's rescue mode adds `human hand` and `human arm` prompts. Those boxes
indicate possible human presence, not an estimated whole-person outline. Its
candidate threshold defaults to 0.1 (`ZEROSHOT_CONF`); the existing server detection
and alert thresholds still apply afterward. `ZEROSHOT_IMGSZ` defaults to 640.
Model loading is lazy and cached. `DEVICE` follows the server's CPU/CUDA/MPS setting.

## Tests

```powershell
.\.venv-eval\Scripts\python.exe -B -m unittest discover -s training -p test_zeroshot.py -v
```

Reference: [Hugging Face OWLv2 documentation](https://huggingface.co/docs/transformers/model_doc/owlv2).

The complete FastAPI server requires Python 3.12 (see `server/pyproject.toml`). This local ML environment is Python 3.11: standalone inference and the detector-adapter tests run here, but app/model-swap tests cannot collect because existing `server/store.py` uses Python 3.12 generic function syntax. Run full server integration in the team's Python 3.12 environment.

## Completed baseline and live-loop limits

The full 634-image baseline is recorded in [the comparison report](../docs/evaluation_results.md).
All thirteen evaluator regression tests and nineteen detector-adapter tests passed,
and the actual six-prompt server entry point passed a real-model smoke test.

CPU inference averaged about 5.6 seconds per image with substantial variation.
The iOS uploader currently times out after 1.5 seconds
(`app/CameraAccess/CameraAccess/Media/FrameUploader.swift`), while the server's
default alert policy requires two hits with observations expiring after two seconds
(`server/config.py` and `server/alerts.py`). Those settings need validation with A/B
before using this slower backend in the live loop. This implementation does not
change their settings. GPU latency has not been measured.
