"""Render a completed development failure run without rerunning inference."""
import argparse
from collections import Counter
import csv
import json
import os
from pathlib import Path

from failure_report import match, overlap


def render(root, report):
    combined=json.loads((root/'summary.json').read_text(encoding='utf-8'))
    manifest=json.loads((root/'dataset_manifest.json').read_text(encoding='utf-8'))
    models=combined['models']; records=manifest['records']; details={}
    if set(models) != {'yolo26s', 'best_plain', 'best_storm'}:
        raise ValueError('This historical renderer accepts the v1 comparison only. Use render_post_training.py for v2.')
    example_count=sum(len(v) for v in json.loads((root/'selected_examples.json').read_text()).values())
    for name in models:
        predictions={row['id']:row for row in map(json.loads,(root/(name+'_predictions.jsonl')).read_text().splitlines())}
        if set(predictions)!={r['id'] for r in records}: raise ValueError('Incomplete or mismatched predictions')
        counters=Counter()
        with (root/(name+'_misses_at_015.csv')).open('w',newline='',encoding='utf-8') as f:
            fields=['image_id','image','gt_index','source_class','size','x1','y1','x2','y2','best_iou_with_retained_prediction','candidate_center_inside_gt']
            writer=csv.DictWriter(f,fieldnames=fields); writer.writeheader()
            for r in records:
                p=[p for p in predictions[r['id']]['boxes'] if p[4]>=.15]
                result=match(predictions[r['id']]['boxes'],r['gt'],r['ignore_boxes'],.15)
                ious=overlap([g['box'] for g in r['gt']],[v[:4] for v in p])
                for gi in result['fn']:
                    g=r['gt'][gi]; best=float(ious[gi].max()) if p else 0.
                    x1,y1,x2,y2=g['box']
                    inside=any(x1<=(v[0]+v[2])/2<=x2 and y1<=(v[1]+v[3])/2<=y2 for v in p)
                    counters['misses']+=1; counters['misses_with_iou_at_least_010']+=best>=.1; counters['misses_with_candidate_center_inside_gt']+=inside
                    writer.writerow(dict(zip(fields,[r['id'],r['image'],gi,g['source_class'],g['size'],*g['box'],best,inside])))
        details[name]=dict(counters)
    (root/'miss_diagnostics.json').write_text(json.dumps(details,indent=2)+'\n',encoding='utf-8')
    compact={'purpose':manifest['purpose'],'images':manifest['images'],'gt_boxes':manifest['gt_boxes'],'source_images':manifest['source_images'],
        'source_class_boxes':manifest['source_class_boxes'],'settings':manifest['settings'],'matching':manifest['matching'],'size_bins':manifest['size_bins'],
        'limitations':manifest['limitations'],'models':{k:{key:value for key,value in v.items() if key!='per_image_at_015'} for k,v in models.items()},'miss_diagnostics_at_015':details}
    report.parent.mkdir(parents=True,exist_ok=True)
    report.with_suffix('.json').write_text(json.dumps(compact,indent=2)+'\n',encoding='utf-8')
    with report.with_suffix('.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=['model','confidence','tp','fp','fn','precision','recall','f2','ignored_predictions']); writer.writeheader()
        for name,m in models.items():
            for threshold,stats in m['overall'].items(): writer.writerow({'model':name,'confidence':threshold,**stats})
    def pct(v): return f'{v*100:.2f}%'
    def link(path): return Path(os.path.relpath(path,report.parent)).as_posix()
    stock=models['yolo26s']; at=stock['overall']['0.15']; high=stock['overall']['0.35']; low=stock['overall']['0.05']
    lines=['# Historical v1 development failure report - October 17, 2026','', 'This report describes the earlier YOLO11s fine-tunes. For the supplied v2 YOLO26s weights, see [POST_TRAINING_V2.md](POST_TRAINING_V2.md).','',
      '**Stock YOLO26s still recovers substantially more annotated people than either supplied fine-tuned checkpoint. Before retraining, prioritize annotation completeness and consistent person-box conventions, then small-person coverage.**','',
      'This is a development diagnostic on the ORIGINAL exports\' validation folders, not a new final test result and not a verified reconstruction of C\'s merged validation set. No held-out test images were used for inference. Do not compare these percentages directly with the earlier 634-image test report.','',
      '## Data and protocol','',
      f'- {manifest["images"]:,} images: rubble 905, flood1 501, flood2 401; {manifest["gt_boxes"]:,} mapped person boxes.',
      '- Explicit original-label mapping follows the earlier repair. The `in-car` class remains provisional and needs semantic review.',
      '- Exact validation duplicates and byte-identical matches to the 634-image test set were checked; no exclusions were needed. Near duplicates, source-video grouping, and overlap with C\'s actual training set are NOT verified. These may be previously seen images.',
      '- CPU, FP32, image size 640, square inference (`rect=False`), batch 1, eight threads, candidate confidence 0.001, NMS IoU 0.7, maximum 300 predictions per image. Every model processed every included image.',
      '- Matching uses IoU >= 0.5 and one-to-one greedy assignment in descending prediction confidence. This is a fixed-threshold diagnostic, not COCO/Ultralytics AP. No mAP was recomputed.',
      '- Rubble ignored-region boxes suppress an unmatched prediction if at least half its area lies inside one. Ignored-region predictions and their counts are saved.',
      '- Small: ground-truth box area <32^2 pixels after scaling the longest image side to 640; medium <96^2; large otherwise. These are explicit recall bins, not COCO AP results.',
      '- Rain, darkness, occlusion severity, survivor status, and distance are not inferred from filenames or dataset names. This run does not measure the video alert policy.','',
      '## Matched operating points','',
      'All numbers below are relative to the supplied annotations. **FP means unmatched prediction, not an independently verified false alarm.** Missing labels and differing box conventions can materially distort precision and miss counts.','',
      '| Model | Confidence | Matched people | Unmatched predictions | Missed boxes | Precision | Recall | F2 |',
      '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for name,m in models.items():
        for threshold in ['0.05','0.15','0.35']:
            s=m['overall'][threshold]; lines.append(f'| {name} | {threshold} | {s["tp"]:,} | {s["fp"]:,} | {s["fn"]:,} | {pct(s["precision"])} | {pct(s["recall"])} | {pct(s["f2"])} |')
    lines+=['',f'For stock, moving from 0.35 to 0.15 recovers **{at["tp"]-high["tp"]:,} additional labeled people**, adding **{at["fp"]-high["fp"]:,} unmatched predictions**. Moving further to 0.05 recovers another {low["tp"]-at["tp"]:,}, with {low["fp"]-at["fp"]:,} additional unmatched predictions. This demonstrates a tradeoff; it does not approve a production threshold or a spoken false-alert rate. The six-point grid is not a guarantee of an optimal F2 operating point.','',
      '## Size-specific recall at confidence 0.15','',
      '| GT size | Annotated boxes | Stock YOLO26s | Plain fine-tune | Storm fine-tune |',
      '| --- | ---: | ---: | ---: | ---: |']
    for size in ['small','medium','large']:
        key='size:'+size; s=stock['groups']['0.15'][key]
        lines.append(f'| {size} | {s["tp"]+s["fn"]:,} | '+' | '.join(pct(m['groups']['0.15'][key]['recall']) for m in models.values())+' |')
    small=stock['groups']['0.15']['size:small']; lines+=['',f'Small boxes account for {small["tp"]+small["fn"]:,}/{manifest["gt_boxes"]:,} labels. The stock model misses {small["fn"]:,} of them at 0.15. The trained checkpoints recover very few. This is the largest count-based improvement opportunity, subject to label review.','',
      '## Recall by original annotation category at confidence 0.15','',
      '| Source / category | Annotated boxes | Stock | Plain | Storm |','| --- | ---: | ---: | ---: | ---: |']
    for key,s in stock['groups']['0.15'].items():
        if key.startswith('class:'):
            lines.append(f'| {key[6:]} | {s["tp"]+s["fn"]:,} | '+' | '.join(pct(m['groups']['0.15'][key]['recall']) for m in models.values())+' |')
    lines+=['','Subgroup precision/F2 are intentionally omitted: an unmatched prediction cannot be reliably assigned to a ground-truth size or semantic category.','',
      '## Visual findings requiring C\'s review','',
      '1. **Apparent missing person annotations.** In `flood2/c01721_jpg.rf.9a354f1278395cfd5c7486a501852e29.jpg`, only two mapped person boxes are present, while several other visible people have stock predictions over them. This is a concrete label-completeness review case. Do not train those apparent unlabeled people as background or call all these predictions true false positives without review.',
      '2. **Person-box convention mismatch.** In `flood1/318351193_jpg.rf.652672a83bd7230461444eacc68ab6ee.jpg`, original swimmer boxes include substantial water around heads. Stock predictions tightly cover visible heads and fail the whole-box IoU criterion. A metric miss here can include localization/convention failure rather than complete failure to notice a person. Decide whether the target is visible person extent or estimated full-body extent and apply the convention consistently.',
      '3. **Tiny aerial people dominate some scenes.** `rubble/0000086_01954_d_0000005_jpg.rf.eac98256c9410fdf4292816154a26510.jpg` is a crowded sports-court aerial scene with many small people, not a rubble rescue. The source name alone is not a scene label. Review source balance so generic crowd images do not dominate the intended rescue benchmark.',
      '4. **Difficult source quality and export artifacts.** The inspected `flood2/yt-false-0002_jpg.rf.924c6038890198451c808e0e00a1a623.jpg` has heavy speckle, rotation, and tiny in-car annotations. Audit whether validation exports already contain augmentation/near-duplicate derivatives. Do not assume that leaving these folders untouched means they contain only raw unaugmented captures.',
      '5. **Confidence is not the only issue.** Even at 0.05, the supplied fine-tuned checkpoints remain far below stock on matched recall. Recheck labels, actual training inputs, class semantics, and the YOLO11s-versus-YOLO26s provenance mismatch before another long training run.','',
      'The local `*_misses_at_015.csv` files include each missed GT box, its best IoU with retained predictions, and whether a prediction center falls inside it. These are review cues, not proof that a particular person was recognized.','',
      '## Recommended retraining priorities','',
      '1. Fix and verify annotation completeness and box conventions using original exports and human review. Preserve repair manifests; do not auto-promote predictions to ground truth.',
      '2. Establish grouped training/validation provenance and check exact/near duplicates before using this diagnostic as model-selection evidence. Keep the existing held-out test set and final demo sources out of tuning.',
      '3. Start controlled plain/storm v2 experiments from `yolo26s.pt`, preserving previous files. Include verified small, partially visible, water-obscured people and realistic negatives. Inspect difficult examples after augmentation.',
      '4. Compare stock and v2 models at matched false-positive/precision budgets on verified validation data. Assess confidence tuning and input-resolution changes separately. A higher server image size cannot restore detail discarded by the phone\'s 640-pixel upload.',
      '5. Have D run final held-out testing only after selecting the model and threshold. B owns integration; measure actual alert recall, false alerts per minute, and latency.','',
      'See [the full retraining handoff](RETRAINING_HANDOFF.md).','',
      '## Checkpoints and provenance','',
      '| Model | Training base recorded in checkpoint | SHA-256 |','| --- | --- | --- |']
    for name,m in models.items(): lines.append(f'| {name} | {m["base_model"]} | `{m["sha256"]}` |')
    lines+=['','## Artifacts and reproduction','',
      f'- [Visual review gallery]({link(root/"gallery.html")}): {example_count} selected worst-miss/unmatched-prediction overlays. Selection is illustrative, not random prevalence sampling.',
      f'- [Raw run summary]({link(root/"summary.json")}) and [dataset/label manifest]({link(root/"dataset_manifest.json")}).',
      f'- [Compact JSON]({report.with_suffix(".json").name}) and [all six operating points as CSV]({report.with_suffix(".csv").name}).',
      '- Per-model prediction JSONL, per-image statistics, missed-box CSVs, selected-example metadata, and visual overlays are in the local run folder. They include original dataset content/paths and remain outside Git; share privately with the team through the data-sharing workflow.',
      '- Source images, labels, test results, checkpoints, and server settings were not modified. No retraining or deployment was performed.',
      '- Seven numerical tests passed for one-to-one matching, duplicates, confidence filtering, ignored regions, localization failures, empty inputs, and F2. All three inference passes completed; no eligible images were silently skipped.','',
      'Run from the repository root in the evaluation environment. Use a fresh output directory for a new inference run:',
      '','```powershell',
      r'.\.venv-eval\Scripts\python.exe -B -u training/failure_report.py --output runs/failure_report_NEW',
      r'.\.venv-eval\Scripts\python.exe -B training/render_failure_report.py --input runs/failure_report_NEW --report docs/FAILURE_REPORT.md',
      r'.\.venv-eval\Scripts\python.exe -B -m unittest discover -s training -p test_failure_report.py',
      '```','',
      'Rendering only reads saved predictions; it does not rerun inference. Reports can be regenerated from this run using `--input runs/failure_report_20261017`. Keep the prior run directory for comparison.','']
    report.write_text('\n'.join(lines),encoding='utf-8')
    print(f'Wrote {report}, compact JSON, threshold CSV, and per-model missed-box CSVs.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args(); render(args.input,args.report)
