# Person C: retraining and model-improvement handoff

## Objective

Produce a verified, reproducible detector that finds more visible people in difficult disaster scenes while accepting a measured increase in false positives. Demonstrate improvement against stock YOLO26s on validation data, then hand the selected model and operating threshold to D for final testing.

Work on Person C's branch. Preserve existing checkpoints, datasets, and results. This document is a work order; it does not mean retraining or the data audit has already been completed.

## Read first

- [Team plan](../plan.md): ownership and integration contract.
- [Existing evaluation report](evaluation_results.md) and [raw results](results.csv).
- [Checkpoint metadata](checkpoint_metadata.json): actual architecture and provenance.
- [Training script](../training/train.py), [merge script](../training/merge_datasets.py), and [augmentation script](../training/augment.py).
- [Evaluation instructions](../training/EVALUATION.md).
- D's selected demo footage and review notes, shared separately: final-evaluation candidates must not enter training or threshold selection.

## What D's testing established

All models were evaluated on the same corrected test set: **634 images, 1,819 person boxes, 565 positive images, and 69 backgrounds**. Evaluation used image size 640, batch 1, CPU, FP32, candidate confidence floor 0.001, and NMS IoU 0.7. See the evaluation report for environment details and artifacts.

| Model | Precision | Recall | mAP50 | mAP50-95 | Inference ms/image |
| --- | ---: | ---: | ---: | ---: | ---: |
| Stock YOLO26s | 49.02% | 31.12% | 32.48% | 14.81% | 125.3 |
| Stock YOLO11s | 47.68% | 26.06% | 27.25% | 12.86% | 348.4 |
| Plain fine-tune, YOLO11s | 2.74% | 9.35% | 1.51% | 0.61% | 123.7 |
| Storm fine-tune, YOLO11s | 15.12% | 3.57% | 2.38% | 1.06% | 128.7 |
| OWLv2, whole-person prompts | 34.53% | 36.89% | 23.24% | 6.52% | 5585.6 |

Interpretation:

- Both supplied fine-tunes substantially underperform the pretrained detectors. Storm fine-tuning improved mAP over plain fine-tuning but lowered the reported summary recall. It did not demonstrate an overall improvement over stock.
- Precision and recall are Ultralytics confidence-curve summaries, **not measurements at the server's deployed thresholds** or a common false-positive budget. Do not interpret the candidate floor of 0.001 as the confidence at which these summary values were measured.
- Timings are single-run CPU measurements, exclude networking and speech, and vary with load. Stock YOLO11s slowed during its run; this table is not a controlled latency ranking.
- The supplied fine-tuned checkpoints identify a YOLO11s base. The plan and current training script specify YOLO26s. Trace the actual training command/configuration that produced those files.
- Earlier merging silently dropped human annotations whose names were not exactly lowercase `person`. D restored test labels from original exports. Correct training/validation mapping has not yet been established. Low scores alone do not prove the cause.
- The team confirmed that the test images were excluded from training and validation. Exact/near-duplicate and neighboring-video-frame leakage still need auditing.

**Keep stock YOLO26s as the deployment candidate until a replacement demonstrates improvement.**

## 1. Audit and repair the data before spending another training run

- [ ] Identify the exact original exports, merge version, class mappings, augmentation version, training configuration, and checkpoints used previously.
- [ ] List every original class name and ID. Explicitly map legitimate whole-person categories into the agreed final class set; report unmapped categories instead of silently dropping them.
- [ ] Review body-part classes separately. A hand or arm box is not interchangeable with a whole-person box. Document the annotation convention rather than mixing incompatible targets.
- [ ] Validate image/label pairing, missing labels, corrupt images, class IDs, normalized coordinates, zero-area boxes, and boxes outside image bounds.
- [ ] Check that apparent negative images actually contain no annotated target people. Unknown/missing annotations must not silently become confirmed negatives.
- [ ] Restore damaged annotations from original exports where possible. Do not use unreviewed model predictions as ground truth.
- [ ] Produce counts by source and split: images, positive/background images, person boxes, and small/medium/large boxes. Record the box-size definition and resolution used.
- [ ] Save representative label overlays from every source and split, including small people, occlusions, water, rubble, and backgrounds. Inspect augmented overlays too.
- [ ] Check exact duplicates, near duplicates, and source-video/scene overlap across all splits, including D's held-out data.
- [ ] Write an audit report with verified facts, repairs, before/after counts, and unresolved gaps. If an original export is missing, identify it rather than silently training on questionable data.

Do not start the main retraining comparison until the labels and split boundaries are credible. A checkpoint naming class 0 `person` does not prove the training boxes were correct.

## 2. Preserve a genuine train / validation / test separation

| Group | Permitted use | Augmentation |
| --- | --- | --- |
| Training | Learn weights; add independently sourced training examples | Training-only augmentation allowed |
| Validation | Select checkpoints, training settings, resolution, and confidence thresholds | No offline storm copies or test-time augmentation for the primary comparison |
| Test | D's final evaluation after model and thresholds are fixed | None |

**Do not reshuffle the existing test set merely to obtain better results.** If it is independent, keep D's 634 images frozen and create or verify training/validation splits from the remaining data. An 80/20 split of the remaining pool can be a starting allocation, but source/scene separation matters more than an exact percentage.

If the old 20% was used for both validation/model selection and claimed final testing, it was not an untouched test set. Document that history and agree an independent final test set with D.

Split by original video, scene, recording session, or other acquisition group. Neighboring frames must stay in the same split. Keep crops, augmented copies, and other derivatives with their original training example. Group before augmentation.

If leakage is found, repair the source grouping and document the replacement split. D must rerun stock baselines on any changed final test set. Never compare new-model results on a new test set with old baseline results on another one.

Selected demo clips and their original footage/extracted frames remain excluded from training and tuning if used for final evaluation. If the team intentionally uses one for development, reclassify it and replace it with new independent evaluation footage. Do not repeatedly optimize against test results already seen by the team.

Save a split manifest with stable image IDs, source/group IDs, hashes, assigned split, seed, and dataset version.

## 3. Run a controlled retraining comparison

Start fresh from pretrained **`yolo26s.pt`**, not either poorly performing fine-tuned checkpoint.

| Run | Purpose |
| --- | --- |
| Stock YOLO26s | Reference evaluated on the same validation data and protocol |
| Corrected plain v2 | Verified original training images; documented standard training augmentations |
| Corrected storm v2 | Matched experiment with realistic storm augmentation added |
| Optional higher-resolution run | Separate experiment if small-person misses remain a major weakness |

For the first plain/storm pair, match the base checkpoint, validation split, seed, resolution, optimizer settings, and training budget. Record effective samples and optimizer steps: adding offline augmented images changes work per epoch. A comparison with the same epoch count but twice the samples must disclose that difference.

The current script uses 50 epochs, image size 640, and patience 10. These are starting settings, not evidence of convergence. Inspect training and validation curves before extending the budget. Do not run longer to compensate for damaged labels.

Save complete arguments, software versions, hardware, seed, dataset/split version, pretrained checkpoint hash, actual architecture, and final checkpoint hash. Keep both the validation-selected checkpoint and training history. If selection remains based on the trainer's default fitness rather than a recall-focused criterion, state that explicitly; do not call it an F2-selected checkpoint.

### Required script changes before using the current wrapper

`training/train.py` currently copies outputs to `server/weights/best_plain.pt` and `best_storm.pt`, overwriting the previous artifacts. **Do not invoke it unchanged for v2.** First add distinct run/output names and refuse accidental checkpoint replacement. Preserve the old files and hashes.

Make the experiment's important settings configurable and recorded. Use the flags actually implemented in your branch; this document does not assume unsupported CLI options already exist. Document exact reproducible commands in the PR/report.

Suggested new artifacts:

- `server/weights/best_plain_v2.pt`
- `server/weights/best_storm_v2.pt`
- Versioned dataset YAMLs and split manifests.
- Unique experiment directories with curves, arguments, and validation predictions.

### Improve coverage of the cases we actually care about

Prioritize verified examples of small/distant people, partial bodies in rubble, people partly hidden by water or vegetation, rooftop stranding, rain, darkness, and blur. Record each source and its permission/license. New training footage must be separate from final evaluation sources.

Use realistic augmentation strengths and inspect the resulting labels and visibility. Heavy weather effects can erase tiny people while leaving their boxes behind. Avoid near-identical frame duplication that exaggerates dataset diversity. Keep realistic background examples rather than removing them to force higher recall.

A separately controlled higher-resolution experiment may help small targets, but measure it after the actual 640-pixel upload/resize pipeline as well as offline. Pixels already removed by the phone cannot be recovered by enlarging the server input. Measure throughput and timeout impact on B's hardware.

Do not blindly change loss weights, duplicate all positives, or remove negatives. Treat each major change as an experiment and compare using validation data.

## 4. Select a recall-focused operating point on validation data

Our preference is fewer missed visible people, accepting some additional false positives. That preference must become a measured tradeoff, not a claim that any extra detections are helpful.

For stock and each new checkpoint:

- Sweep confidence thresholds on the same validation split; choose a documented range that spans useful precision/recall tradeoffs.
- Report precision, recall, F2, TP/FP/FN, mAP50, mAP50-95, and latency. Keep IoU matching, NMS settings, image size, maximum detections, and other settings consistent or disclose differences.
- F2 = `5 * precision * recall / (4 * precision + recall)`, with a defined zero case. It weights recall more than precision but does not replace an agreed false-positive budget.
- Report small-person recall separately when annotations and sample counts support it. Define size groups explicitly and show their denominators.
- Compare recall at comparable precision/false-positive budgets, alongside each model's precision-recall curve. Equal numeric confidence thresholds need not mean equal operating behavior across models.
- If no false-positive budget is agreed, present several operating points for the team to choose from. Do not invent an approved requirement or optimize on test data.
- If choosing among epochs by F2, apply the same validation selection procedure consistently, record the candidate set, and preserve the selected checkpoint. Use a separate calibration/source group within the validation allocation if sufficient data permits.
- Preserve validation detections and examples of misses/false positives for D's review. Keep fixed-threshold statistics separate from Ultralytics' summary precision/recall.

Training and threshold selection are different controls. A lower threshold can expose low-confidence detections; it cannot recover every missing person or repair a damaged model. Lowering the AP evaluator's candidate floor alone is not a training strategy; the earlier evaluation already used 0.001.

## 5. Validate the real alert loop with B and D

Current server defaults are detection confidence 0.25, alert confidence 0.35, and two qualifying detections in a recent-frame window. Both confidence floors affect sensitivity; lowering the alert threshold cannot recover a detection discarded at the detection floor. Alert confidence must remain at least detection confidence.

Leave server implementation/configuration ownership with B. Hand over recommended settings and validation evidence. Thresholds such as 0.10 detection / 0.15 alert are experimental examples, not validated defaults.

Image false positives are not spoken false alerts. Measure video alert recall, false alerts per minute, alert delay, skipped frames, and server/round-trip latency through the actual alert policy on development footage. Keep inference misses separate from streak/cooldown/network behavior. In mixed scenes, detections of rescuers do not prove the intended survivor was found.

The phone currently has a 1.5-second upload timeout. Measure deployment latency and sustained frame delivery, not just offline inference. OWLv2's approximately 5.6-second CPU inference in D's baseline is unsuitable for that configuration without other changes.

The model detects visible people. It does not establish injury, identify someone as a victim, or predict where a completely hidden survivor is likely to be.

## 6. Handoff and final evaluation

C provides:

- [ ] Audit report: provenance, class mapping, repairs, split/duplicate checks, counts, and overlays.
- [ ] Reproducible training commands/configurations and complete run metadata.
- [ ] Uniquely named v2 weights without overwriting prior checkpoints.
- [ ] Validation comparison against stock YOLO26s, including threshold tradeoffs, F2, small-person performance where supported, and latency.
- [ ] Explicit model/checkpoint selection rule and chosen validation operating point.
- [ ] Checkpoint hash, architecture, class names, dataset/split version, known limitations, and a concise recommendation for B and D.

D then runs the frozen candidate and stock baseline on the held-out test set with a fixed protocol and checks the independent clips. D reports test performance without retuning on those outcomes. B integrates a replacement only when the evidence supports it.

Success is demonstrated improvement in the agreed recall/false-positive tradeoff with acceptable serving performance. If neither v2 model improves on stock, report that honestly and keep stock YOLO26s as the deployment candidate. Do not select a model because its training losses look better or its filename says `best`.

Commit code/configuration/reports on C's branch and prepare a reviewable PR. Coordinate merging with the team. Keep datasets, checkpoints, and videos outside Git using the project's shared storage convention. Never commit credentials.
