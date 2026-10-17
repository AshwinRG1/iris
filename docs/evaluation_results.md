# Checkpoint evaluation - October 17, 2026

Both supplied fine-tuned checkpoints underperform the pretrained baselines on the corrected test set. Keep stock YOLO26s for integration testing while investigating the training pipeline. Do not claim that fine-tuning improved detection from these results.

## Dataset and settings

All models were evaluated on the same 634 images with 1,819 person boxes (565 positive images and 69 backgrounds), using `data/test/data/data.yaml`. CPU: Intel Core i7-13700H; image size 640; batch 1; FP32; candidate confidence 0.001; NMS IoU 0.7; Ultralytics 8.4.178. No images were skipped.

The test annotations were restored from the original exports. That repair does not establish independence from training. The team confirmed these 634 images were excluded from both training and validation/model selection. Duplicate or neighboring-video-frame leakage has not been audited. Correct mapping of the training annotations still needs verification.

## Results

| Model | Precision | Recall | mAP50 | mAP50-95 | Inference ms/image |
| --- | ---: | ---: | ---: | ---: | ---: |
| Stock YOLO26s | 49.02% | 31.12% | 32.48% | 14.81% | 125.3 |
| Plain fine-tune (YOLO11s) | 2.74% | 9.35% | 1.51% | 0.61% | 123.7 |
| Storm fine-tune (YOLO11s) | 15.12% | 3.57% | 2.38% | 1.06% | 128.7 |
| Stock YOLO11s | 47.68% | 26.06% | 27.25% | 12.86% | 348.4 |
| OWLv2 zero-shot (640px, whole-person prompts) | 34.53% | 36.89% | 23.24% | 6.52% | 5585.6 |

Precision and recall are the Ultralytics confidence-curve summary, not the deployed alert threshold. Inference timing is a single-run measurement, excludes network and speech, and varies with machine load. Stock YOLO11s slowed substantially during its run; these numbers are not a controlled speed ranking.

## Findings and next steps

- The supplied checkpoints identify their training base as `yolo11s.pt`, not the YOLO26s specified in the plan. Stock YOLO11s was therefore added as the matching-architecture baseline.
- Storm training has higher mAP than plain training, but lower recall at the reported operating point. Both remain substantially below stock YOLO11s. This is not evidence of an overall improvement over pretrained detection.
- Earlier local merging dropped human annotations with names other than lowercase `person`. Check whether the training exports used that same logic. The low scores alone do not prove the cause.
- Inspect C's actual training and validation labels, class mapping, box overlays, source composition, and split separation before retraining. A checkpoint reporting `person` at ID 0 does not establish that its training boxes were correct.
- Use stock YOLO26s for the next server/demo integration test; leave server configuration changes to B.
- The OWLv2 zero-shot baseline is complete; independent clip accuracy and full alert-loop evaluation remain separate work.

## Artifacts

Raw results: [results.csv](results.csv). Checkpoint hashes and training metadata: [checkpoint_metadata.json](checkpoint_metadata.json). The CSV `artifacts` column gives each local plot directory. Checkpoints, images, and plots remain outside Git.

Reproduce the new comparison from the repository root:

```powershell
.\.venv-eval\Scripts\python.exe training/evaluate.py --models server/weights/best_plain.pt server/weights/best_storm.pt yolo11s.pt
```

## OWLv2 baseline

The complete OWLv2 run evaluated all 634 images using fixed whole-person prompts,
FP32, 640px processing with positional embedding interpolation, batch 1, a 0.001
candidate floor, NMS IoU 0.7, and eight CPU threads. It reused Ultralytics' IoU
matching, confidence-curve summaries, and AP calculation. No prompts or thresholds
were selected from these test results. Body-part prompts were excluded from
whole-person bounding-box scoring.

OWLv2 achieved 23.24% mAP50 and 36.89% summary recall. Stock YOLO26s retains higher
mAP50 (32.48%) and precision, while OWLv2 has higher recall at its independently
selected confidence-curve operating point. This does not establish higher recall
at the same deployed confidence threshold or false-positive rate.

The measured OWLv2 inference average was 5,585.6 ms/image; CPU latency varied
substantially. This was not a controlled deployment speed benchmark. Even the
faster observed inference exceeded the app's current 1.5-second upload timeout.
The default two-hit alert policy also expires observations after two seconds,
which can prevent streaks when frames arrive more slowly. Keep stock YOLO26s for
current live integration; A and B need to validate timeout/window settings or
measure a faster serving environment before using OWLv2 in the live loop.

The server rescue entry point adds hand/arm prompts and was smoke-tested through
the existing adapter. Its six-prompt configuration is distinct from the scored
four-prompt whole-person baseline; no accuracy score is claimed for rescue mode.

Verification: all 634 prediction records were checked for unique image paths,
valid person labels, finite confidence scores, and normalized nonempty boxes.
Thirteen evaluator regression tests and nineteen existing detector-adapter tests
passed. Actual model loading, warmup, and detection through the server adapter
also passed. Full app/model-swap tests require Python 3.12; this local ML environment
uses Python 3.11 and cannot collect the existing server's Python 3.12 syntax.

Metadata: [zeroshot_metadata.json](zeroshot_metadata.json). Commands and API contract:
[OWLv2 guide](../training/OWLV2.md). Raw predictions and curves are in the artifact
directory recorded in the CSV and metadata; they remain outside Git.
