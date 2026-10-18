# Historical v1 development failure report - October 17, 2026

This report describes the earlier YOLO11s fine-tunes. For the supplied v2 YOLO26s weights, see [POST_TRAINING_V2.md](POST_TRAINING_V2.md).

**Stock YOLO26s still recovers substantially more annotated people than either supplied fine-tuned checkpoint. Before retraining, prioritize annotation completeness and consistent person-box conventions, then small-person coverage.**

This is a development diagnostic on the ORIGINAL exports' validation folders, not a new final test result and not a verified reconstruction of C's merged validation set. No held-out test images were used for inference. Do not compare these percentages directly with the earlier 634-image test report.

## Data and protocol

- 1,807 images: rubble 905, flood1 501, flood2 401; 9,365 mapped person boxes.
- Explicit original-label mapping follows the earlier repair. The `in-car` class remains provisional and needs semantic review.
- Exact validation duplicates and byte-identical matches to the 634-image test set were checked; no exclusions were needed. Near duplicates, source-video grouping, and overlap with C's actual training set are NOT verified. These may be previously seen images.
- CPU, FP32, image size 640, square inference (`rect=False`), batch 1, eight threads, candidate confidence 0.001, NMS IoU 0.7, maximum 300 predictions per image. Every model processed every included image.
- Matching uses IoU >= 0.5 and one-to-one greedy assignment in descending prediction confidence. This is a fixed-threshold diagnostic, not COCO/Ultralytics AP. No mAP was recomputed.
- Rubble ignored-region boxes suppress an unmatched prediction if at least half its area lies inside one. Ignored-region predictions and their counts are saved.
- Small: ground-truth box area <32^2 pixels after scaling the longest image side to 640; medium <96^2; large otherwise. These are explicit recall bins, not COCO AP results.
- Rain, darkness, occlusion severity, survivor status, and distance are not inferred from filenames or dataset names. This run does not measure the video alert policy.

## Matched operating points

All numbers below are relative to the supplied annotations. **FP means unmatched prediction, not an independently verified false alarm.** Missing labels and differing box conventions can materially distort precision and miss counts.

| Model | Confidence | Matched people | Unmatched predictions | Missed boxes | Precision | Recall | F2 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| yolo26s | 0.05 | 3,354 | 4,361 | 6,011 | 43.47% | 35.81% | 37.12% |
| yolo26s | 0.15 | 2,416 | 1,330 | 6,949 | 64.50% | 25.80% | 29.32% |
| yolo26s | 0.35 | 1,528 | 326 | 7,837 | 82.42% | 16.32% | 19.43% |
| best_plain | 0.05 | 118 | 647 | 9,247 | 15.42% | 1.26% | 1.54% |
| best_plain | 0.15 | 66 | 149 | 9,299 | 30.70% | 0.70% | 0.88% |
| best_plain | 0.35 | 39 | 47 | 9,326 | 45.35% | 0.42% | 0.52% |
| best_storm | 0.05 | 179 | 1,404 | 9,186 | 11.31% | 1.91% | 2.29% |
| best_storm | 0.15 | 116 | 287 | 9,249 | 28.78% | 1.24% | 1.53% |
| best_storm | 0.35 | 80 | 67 | 9,285 | 54.42% | 0.85% | 1.06% |

For stock, moving from 0.35 to 0.15 recovers **888 additional labeled people**, adding **1,004 unmatched predictions**. Moving further to 0.05 recovers another 938, with 3,031 additional unmatched predictions. This demonstrates a tradeoff; it does not approve a production threshold or a spoken false-alert rate. The six-point grid is not a guarantee of an optimal F2 operating point.

## Size-specific recall at confidence 0.15

| GT size | Annotated boxes | Stock YOLO26s | Plain fine-tune | Storm fine-tune |
| --- | ---: | ---: | ---: | ---: |
| small | 6,756 | 20.66% | 0.09% | 0.13% |
| medium | 1,164 | 24.83% | 2.15% | 4.55% |
| large | 1,445 | 50.59% | 2.42% | 3.74% |

Small boxes account for 6,756/9,365 labels. The stock model misses 5,360 of them at 0.15. The trained checkpoints recover very few. This is the largest count-based improvement opportunity, subject to label review.

## Recall by original annotation category at confidence 0.15

| Source / category | Annotated boxes | Stock | Plain | Storm |
| --- | ---: | ---: | ---: | ---: |
| flood1:Person drowning | 189 | 13.23% | 0.00% | 0.00% |
| flood1:Person in water | 1,027 | 22.78% | 0.00% | 0.00% |
| flood1:Person out of water | 141 | 5.67% | 0.00% | 0.00% |
| flood2:drowning | 391 | 22.25% | 0.51% | 1.28% |
| flood2:in-car | 142 | 21.83% | 0.00% | 0.70% |
| flood2:person | 489 | 18.61% | 13.09% | 22.29% |
| rubble:Person | 6,387 | 23.88% | 0.00% | 0.02% |
| rubble:Person in Rubble | 599 | 69.28% | 0.00% | 0.00% |

Subgroup precision/F2 are intentionally omitted: an unmatched prediction cannot be reliably assigned to a ground-truth size or semantic category.

## Visual findings requiring C's review

1. **Apparent missing person annotations.** In `flood2/c01721_jpg.rf.9a354f1278395cfd5c7486a501852e29.jpg`, only two mapped person boxes are present, while several other visible people have stock predictions over them. This is a concrete label-completeness review case. Do not train those apparent unlabeled people as background or call all these predictions true false positives without review.
2. **Person-box convention mismatch.** In `flood1/318351193_jpg.rf.652672a83bd7230461444eacc68ab6ee.jpg`, original swimmer boxes include substantial water around heads. Stock predictions tightly cover visible heads and fail the whole-box IoU criterion. A metric miss here can include localization/convention failure rather than complete failure to notice a person. Decide whether the target is visible person extent or estimated full-body extent and apply the convention consistently.
3. **Tiny aerial people dominate some scenes.** `rubble/0000086_01954_d_0000005_jpg.rf.eac98256c9410fdf4292816154a26510.jpg` is a crowded sports-court aerial scene with many small people, not a rubble rescue. The source name alone is not a scene label. Review source balance so generic crowd images do not dominate the intended rescue benchmark.
4. **Difficult source quality and export artifacts.** The inspected `flood2/yt-false-0002_jpg.rf.924c6038890198451c808e0e00a1a623.jpg` has heavy speckle, rotation, and tiny in-car annotations. Audit whether validation exports already contain augmentation/near-duplicate derivatives. Do not assume that leaving these folders untouched means they contain only raw unaugmented captures.
5. **Confidence is not the only issue.** Even at 0.05, the supplied fine-tuned checkpoints remain far below stock on matched recall. Recheck labels, actual training inputs, class semantics, and the YOLO11s-versus-YOLO26s provenance mismatch before another long training run.

The local `*_misses_at_015.csv` files include each missed GT box, its best IoU with retained predictions, and whether a prediction center falls inside it. These are review cues, not proof that a particular person was recognized.

## Recommended retraining priorities

1. Fix and verify annotation completeness and box conventions using original exports and human review. Preserve repair manifests; do not auto-promote predictions to ground truth.
2. Establish grouped training/validation provenance and check exact/near duplicates before using this diagnostic as model-selection evidence. Keep the existing held-out test set and final demo sources out of tuning.
3. Start controlled plain/storm v2 experiments from `yolo26s.pt`, preserving previous files. Include verified small, partially visible, water-obscured people and realistic negatives. Inspect difficult examples after augmentation.
4. Compare stock and v2 models at matched false-positive/precision budgets on verified validation data. Assess confidence tuning and input-resolution changes separately. A higher server image size cannot restore detail discarded by the phone's 640-pixel upload.
5. Have D run final held-out testing only after selecting the model and threshold. B owns integration; measure actual alert recall, false alerts per minute, and latency.

See [the full retraining handoff](archive/RETRAINING_HANDOFF.md).

## Checkpoints and provenance

| Model | Training base recorded in checkpoint | SHA-256 |
| --- | --- | --- |
| yolo26s | yolo26s-objv1-150.pt | `646f8bc3fe0a656803d95c294f7852321748cb29d13466a1af8862e2db384a1b` |
| best_plain | yolo11s.pt | `79f86229223766484a42687eaf8fbca1b7b0701930e1fe3c0be9196d41b95bcf` |
| best_storm | yolo11s.pt | `bb1968b10462ff0a4913a36933cecb98cd6e6372ba5cb7f235cd133f32ed59fd` |

## Artifacts and reproduction

- [Visual review gallery](../runs/failure_report_20261017/gallery.html): 34 selected worst-miss/unmatched-prediction overlays. Selection is illustrative, not random prevalence sampling.
- [Raw run summary](../runs/failure_report_20261017/summary.json) and [dataset/label manifest](../runs/failure_report_20261017/dataset_manifest.json).
- [Compact JSON](FAILURE_REPORT.json) and [all six operating points as CSV](FAILURE_REPORT.csv).
- Per-model prediction JSONL, per-image statistics, missed-box CSVs, selected-example metadata, and visual overlays are in the local run folder. They include original dataset content/paths and remain outside Git; share privately with the team through the data-sharing workflow.
- Source images, labels, test results, checkpoints, and server settings were not modified. No retraining or deployment was performed.
- Seven numerical tests passed for one-to-one matching, duplicates, confidence filtering, ignored regions, localization failures, empty inputs, and F2. All three inference passes completed; no eligible images were silently skipped.

Run from the repository root in the evaluation environment. Use a fresh output directory for a new inference run:

```powershell
.\.venv-eval\Scripts\python.exe -B -u training/failure_report.py --output runs/failure_report_NEW
.\.venv-eval\Scripts\python.exe -B training/render_failure_report.py --input runs/failure_report_NEW --report docs/FAILURE_REPORT.md
.\.venv-eval\Scripts\python.exe -B -m unittest discover -s training -p test_failure_report.py
```

Rendering only reads saved predictions; it does not rerun inference. Reports can be regenerated from this run using `--input runs/failure_report_20261017`. Keep the prior run directory for comparison.
