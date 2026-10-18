# Request for Person C: exact v2 training splits

Can you check the Colab notebook/runtime and Google Drive location used for the v2 runs? The weights reference `/content/data/merged/data.yaml` and `/content/data/merged_storm/data.yaml`, with outputs under `/content/drive/MyDrive/iris_runs`.

D needs the exact training and validation image lists used for each run, their labels, and dataset YAMLs. Please confirm whether all 634 images in D's test set were excluded from both training and validation.

If no split manifest exists, export a file list from the original merged folders, ideally including image SHA-256 hashes. Do not recreate a random split: that would not verify what these models actually saw. If the runtime is gone, share the notebook, dataset archives, merge/split code, and any saved records.

The original source-training exports still contain byte-identical copies of all 634 test images. This does not prove that the actual merged v2 training runs included them, but we need the original run's split records to verify exclusion. Also confirm source-video grouping, near-duplicate separation, class remapping, and augmentation-label handling.
