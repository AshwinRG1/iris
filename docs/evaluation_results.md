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

Precision and recall are the Ultralytics confidence-curve summary, not the deployed alert threshold. Inference timing is a single-run measurement, excludes network and speech, and varies with machine load. Stock YOLO11s slowed substantially during its run; these numbers are not a controlled speed ranking.

## Findings and next steps

- The supplied checkpoints identify their training base as `yolo11s.pt`, not the YOLO26s specified in the plan. Stock YOLO11s was therefore added as the matching-architecture baseline.
- Storm training has higher mAP than plain training, but lower recall at the reported operating point. Both remain substantially below stock YOLO11s. This is not evidence of an overall improvement over pretrained detection.
- Earlier local merging dropped human annotations with names other than lowercase `person`. Check whether the training exports used that same logic. The low scores alone do not prove the cause.
- Inspect C's actual training and validation labels, class mapping, box overlays, source composition, and split separation before retraining. A checkpoint reporting `person` at ID 0 does not establish that its training boxes were correct.
- Use stock YOLO26s for the next server/demo integration test; leave server configuration changes to B.
- OWLv2, independent clip accuracy, and full alert-loop evaluation remain separate work.

## Artifacts

Raw results: [results.csv](results.csv). Checkpoint hashes and training metadata: [checkpoint_metadata.json](checkpoint_metadata.json). The CSV `artifacts` column gives each local plot directory. Checkpoints, images, and plots remain outside Git.

Reproduce the new comparison from the repository root:

```powershell
.\.venv-eval\Scripts\python.exe training/evaluate.py --models server/weights/best_plain.pt server/weights/best_storm.pt yolo11s.pt
```
