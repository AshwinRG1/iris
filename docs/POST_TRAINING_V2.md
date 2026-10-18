# V2 post-training validation - October 18, 2026

**Both v2 files load correctly and contain all 50 recorded training epochs. The measured v2 benchmark results improve on stock YOLO26s. Split provenance and final phone/glasses testing remain unresolved, so this is not deployment approval.**

## Completed checks

- Copied `best_plain_v2.pt` and `best_storm_v2.pt` from Downloads into `server/weights/`; source/destination hashes matched. Earlier weights were preserved. All `.pt` files remain ignored by Git.
- Both identify `yolo26s.pt` as the training base and detect `person` at class ID 0. They contain 9,948,638 parameters before inference fusion; the one-class head differs normally from stock COCO output.
- Both embedded histories contain epochs 1 through 50 with finite values in all recorded columns. The previous missing-epoch concern is resolved by the saved history. The stripped checkpoint epoch field is -1 and does not mean no training occurred.
- Recovered training-history CSVs, training-argument YAMLs, checkpoint metadata, and curves from the weights. These are recovered records, not original run-folder files.
- Original-export development diagnostic completed for all 1,807 images / 9,365 person boxes on stock and both v2 models. All six thresholds include TP/FP/FN, precision, recall, F2, source classes, and size breakdowns.
- Froze a single provisional threshold per model before opening this test comparison. Test thresholds were not swept or retuned.
- Compared all three models on the same corrected 634-image / 1,819-box test dataset. Exported person predictions with checkpoint/dataset/settings provenance while preserving the ordinary Ultralytics AP calculation.
- Exercised the actual server `YoloDetector` adapter and pure alert policy on seven clips for every model. These are offline subsystem checks, not HTTP or phone tests.

## Critical split/provenance limitation

The 6,348 original source-training images contain **634 byte-identical matches to the 634-image test set**. They contain 0 byte-identical matches to the original validation folders. The diagnostic validation and test images do not overlap byte-for-byte.

The team previously reported excluding the 634 test images from actual training and validation. The original exports retaining those images does not itself prove leakage into C's v2 runs. However, `/content/data/merged` and `/content/data/merged_storm` are not available locally, so the actual exclusions cannot be independently verified from these weights. C needs to supply the exact merged split manifest or dataset snapshot. Until then, treat test results as a conditional benchmark, not proof of independent generalization. Near-duplicate and neighboring-frame/source-video separation remain unverified.

The original validation annotations also retain the missing-label/box-convention concerns documented in [the failure report](FAILURE_REPORT.md). Selected thresholds remain explicitly provisional. No automatic server model swap was performed.

## Test AP comparison

All rows: 634 images, 1,819 boxes, CPU, FP32, image size 640, batch 1, candidate floor 0.001, NMS IoU 0.7, eight threads, Ultralytics 8.4.178. Training checkpoints record Ultralytics 8.4.181; load and inference succeeded under the installed evaluator. Stock mAP reproduced the earlier result exactly.

| Model | AP-summary precision | AP-summary recall | mAP50 | mAP50-95 | Inference ms/image |
| --- | ---: | ---: | ---: | ---: | ---: |
| yolo26s | 49.02% | 31.12% | 32.48% | 14.81% | 125.5 |
| best_plain_v2 | 72.51% | 53.77% | 57.88% | 31.81% | 126.8 |
| best_storm_v2 | 76.77% | 60.31% | 64.56% | 37.76% | 130.7 |

The precision/recall above are Ultralytics confidence-curve summaries, not the deployed alert operating point. Timings are single-run measurements and exclude upload/speech; they are not a controlled hardware ranking. The JSON export was checked on a development image with saving enabled/disabled: the four ordinary AP-summary values matched exactly.

## Frozen operating-point test results

| Model | Frozen confidence | Precision | Recall | F2 | Matched boxes | Unmatched predictions | Missed boxes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| yolo26s | 0.05 | 37.29% | 37.66% | 37.58% | 685 | 1152 | 1134 |
| best_plain_v2 | 0.15 | 51.60% | 61.90% | 59.53% | 1126 | 1056 | 693 |
| best_storm_v2 | 0.15 | 57.94% | 67.01% | 64.98% | 1219 | 885 | 600 |

These are greedy confidence-order, one-to-one person matches at IoU 0.5 using saved AP-validator predictions. They are not mAP, spoken false alerts, or survivor-specific clip accuracy. FP means unmatched against the available labels; incomplete labels can overstate false positives. Prediction retention can differ from the development `predict` path, so selection is a provisional operating point, not proof of a globally optimal threshold.

## Development threshold choice

| Model | Chosen confidence | Development precision | Development recall | Development F2 |
| --- | ---: | ---: | ---: | ---: |
| yolo26s | 0.05 | 43.47% | 35.81% | 37.12% |
| best_plain_v2 | 0.15 | 37.52% | 54.60% | 50.04% |
| best_storm_v2 | 0.15 | 41.21% | 52.53% | 49.79% |

The provisional candidate under the predeclared development F2 rule is **best_plain_v2**. Plain and storm are close; this run does not establish a statistically significant difference between them or prove a storm-augmentation benefit. This candidate was selected from development data, not from the test outcome. Resolve the actual split and annotation questions before production approval.

## Offline clip integration

Every model/clip run starts with fresh alert state. Inputs emulate 640-pixel JPEG70 frames at nominal 2.5 fps, using the frozen model confidence for alert hits and the server's existing streak/cooldown policy. Nominal clip time is used; request-arrival delays and skipped-upload behavior are not simulated.

| Model | Clip | Sampled frames | Frames with person detections | Alerts | Detector p95 ms |
| --- | --- | ---: | ---: | ---: | ---: |
| yolo26s | rubble_survivor_extraction.mp4 | 50 | 50 | 5 | 144.0 |
| yolo26s | florence_rooftop_survivor.mp4 | 60 | 59 | 5 | 131.4 |
| yolo26s | harvey_night_flood_survivors.mp4 | 43 | 43 | 5 | 117.2 |
| yolo26s | aerial_harvey_distant_flood_group.mp4 | 18 | 18 | 1 | 107.7 |
| yolo26s | aerial_harvey_rooftop_survivors.mp4 | 40 | 36 | 3 | 120.0 |
| yolo26s | lakewithrain_mock.mp4 | 45 | 45 | 3 | 110.4 |
| yolo26s | rainy_night_negative.mp4 | 15 | 0 | 0 | 141.1 |
| best_plain_v2 | rubble_survivor_extraction.mp4 | 50 | 16 | 3 | 115.1 |
| best_plain_v2 | florence_rooftop_survivor.mp4 | 60 | 40 | 4 | 115.7 |
| best_plain_v2 | harvey_night_flood_survivors.mp4 | 43 | 38 | 4 | 117.2 |
| best_plain_v2 | aerial_harvey_distant_flood_group.mp4 | 18 | 11 | 2 | 118.4 |
| best_plain_v2 | aerial_harvey_rooftop_survivors.mp4 | 40 | 37 | 2 | 120.7 |
| best_plain_v2 | lakewithrain_mock.mp4 | 45 | 18 | 3 | 122.8 |
| best_plain_v2 | rainy_night_negative.mp4 | 15 | 14 | 1 | 123.1 |
| best_storm_v2 | rubble_survivor_extraction.mp4 | 50 | 20 | 2 | 116.1 |
| best_storm_v2 | florence_rooftop_survivor.mp4 | 60 | 45 | 5 | 119.2 |
| best_storm_v2 | harvey_night_flood_survivors.mp4 | 43 | 40 | 3 | 106.3 |
| best_storm_v2 | aerial_harvey_distant_flood_group.mp4 | 18 | 17 | 2 | 122.3 |
| best_storm_v2 | aerial_harvey_rooftop_survivors.mp4 | 40 | 36 | 2 | 117.0 |
| best_storm_v2 | lakewithrain_mock.mp4 | 45 | 0 | 0 | 110.4 |
| best_storm_v2 | rainy_night_negative.mp4 | 15 | 0 | 0 | 137.3 |

Normalized boxes, finite confidence values, person labels, checkpoint identity, and direction values passed the adapter contract checks. Detections/alerts are counts, not accuracy: survivor boxes and some timing annotations remain unfinalized. The cropped no-person candidate is useful for reviewing false alerts, but it has not replaced a fully reviewed independent negative-video benchmark.

## Still required from C, B, and A

- **C:** share the exact v2 merged train/validation manifest or data snapshot; verify the 634-image exclusion, source/video grouping, near duplicates, actual class remapping, and augmentation-label integrity. The weights alone cannot establish these.
- **D:** approve survivor-target boxes/intervals for clip scoring. Keep the evaluation clips out of future training/tuning, or replace them if used for development.
- **B:** run the chosen candidate on the real server and confirm `/health` reports its filename/hash, `ready=true`, and `fallback=null`. Isolated local HTTP checks are recorded below when supplied; B still needs to verify the actual demo host.
- **A and B:** run the iPhone/mock-camera loop, inspect actual survivor boxes in the server live view, confirm spoken Gemini descriptions and their directions (alert text is display-only), measure network/round-trip latency, false alerts, and upload timeouts. A physical iPhone/glasses path was not available here.

For B, the candidate path is `weights/best_plain_v2.pt` when launching from `server/`, or its absolute path. Set both `CONF_DETECT` and `CONF_ALERT` to the corresponding experimental threshold of 0.15. It is a development recommendation only; do not interpret the offline counts as deployment approval.

## Artifacts and reproduction

- [AP CSV](results_v2.csv) and [frozen-threshold CSV](results_v2_operating_points.csv).
- [Validation six-threshold table](validation_v2_thresholds.csv) and [compact combined JSON](POST_TRAINING_V2.json).
- [Recovered training curves](../runs/post_training_v2/training_curves.png) and [validation threshold curves](../runs/post_training_v2/validation_threshold_curves.png).
- [Development failure gallery](../runs/post_training_v2/validation_diagnostic/gallery.html) and [offline adapter snapshots](../runs/post_training_v2/clip_checks/gallery.html).
- [Full checkpoint metadata](../runs/post_training_v2/checkpoint_metadata.json), [source overlap audit](../runs/post_training_v2/original_split_audit.json), and [frozen operating points](../runs/post_training_v2/operating_points.json).
- Recovered histories/configurations, per-frame clip logs, raw predictions, and full manifests are under `runs/post_training_v2/`. Share these ignored artifacts and weights through the team's data-sharing workflow; Git contains code and compact reports only.
- [Evaluation workflow and commands](../training/EVALUATION.md) explain reruns. Use fresh output folders/CSVs, freeze thresholds before test scoring, and preserve checkpoint hashes.

No training labels, source images, previous checkpoints, historical result CSV, or production server configuration were replaced. The v2 weights and large artifacts remain local/shared separately. The software and compact reports are intended for PR review; the production server was not redeployed by these checks.

## Additional real HTTP verification

Dedicated local Python 3.12 servers loaded both v2 models without fallback. Each accepted six JPEG requests and passed checkpoint/hash, frame geometry, and live-view endpoint checks. The full server suite passed 347 tests, the evaluation suite passed 33, and server Ruff/mypy checks passed.

Plain v2 also completed unscored aerial, rainy-lake and negative clip replays with zero HTTP/authentication errors. One upload slot was skipped per clip. An event still occurred in the negative candidate; do not treat transport success as accuracy. Both servers used detection/alert confidence 0.15, CPU, and descriptions off.

The current app speaks Gemini descriptions once per description ID; directional alert text is display-only. These local HTTP checks do not test Gemini, the phone/glasses HUD, audible speech, or B's actual deployment.

See [HTTP results](mvp_http_checks.json) and [MVP handoff](MVP_HANDOFF.md) for identities, timings, reproduction, and remaining manual acceptance.
