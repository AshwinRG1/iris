"""Score OWLv2 using the same Ultralytics matching and AP engine as YOLO."""
import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
from zeroshot import MODEL_ID, PERSON_PROMPTS, OwlV2Detector
from evaluate import FIELDS, audit_dataset, ordered_names


def score_predictions(detections, label, width, height, metrics):
    import numpy as np
    import torch
    from ultralytics.engine.validator import BaseValidator
    from ultralytics.utils.metrics import box_iou
    rows = [line.split() for line in label.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    targets = []
    for row in rows:
        cx, cy, w, h = map(float, row[1:])
        targets.append([(cx-w/2)*width, (cy-h/2)*height, (cx+w/2)*width, (cy+h/2)*height])
    gt = torch.tensor(targets, dtype=torch.float32).reshape(-1, 4)
    gt[:, [0, 2]] = gt[:, [0, 2]].clamp(0, width)
    gt[:, [1, 3]] = gt[:, [1, 3]].clamp(0, height)
    # Explicitly order by score as required by Ultralytics greedy matching.
    detections = sorted(detections, key=lambda d: d['conf'], reverse=True)
    boxes = torch.tensor([d['box'] for d in detections], dtype=torch.float32).reshape(-1, 4)
    boxes *= torch.tensor([width, height, width, height])
    pred_cls, target_cls = torch.zeros(len(boxes)), torch.zeros(len(gt))
    if len(gt) and len(boxes):
        correct = BaseValidator.match_predictions(SimpleNamespace(iouv=torch.linspace(0.5,0.95,10)),
                    pred_cls, target_cls, box_iou(gt, boxes)).numpy()
    else:
        correct = np.zeros((len(boxes),10), dtype=bool)
    metrics.update_stats({'tp':correct, 'conf':np.array([d['conf'] for d in detections]),
        'pred_cls':pred_cls.numpy(), 'target_cls':target_cls.numpy(),
        'target_img':np.unique(target_cls.numpy()), 'im_name':str(label)})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',type=Path,default=Path('data/test/data/data.yaml'))
    parser.add_argument('--device',default='cpu')
    parser.add_argument('--threads',type=int,default=8,help='CPU intra-op threads; recorded in metadata')
    parser.add_argument('--imgsz',type=int,default=640)
    parser.add_argument('--conf',type=float,default=0.001)
    parser.add_argument('--iou',type=float,default=0.7)
    parser.add_argument('--limit',type=int,help='Smoke test only; never appended to final results')
    parser.add_argument('--output',type=Path,default=Path('docs/results.csv'))
    args=parser.parse_args()
    if args.limit is not None and args.limit <= 0:
        parser.error('--limit must be positive')
    if args.threads <= 0:
        parser.error('--threads must be positive')
    import numpy as np
    import torch
    import transformers
    import ultralytics
    import yaml
    from PIL import Image
    from ultralytics.utils.metrics import DetMetrics
    # Ultralytics sets OMP_NUM_THREADS=1 at import; explicitly restore inference parallelism.
    torch.set_num_threads(args.threads)
    data=args.data.resolve()
    meta=yaml.safe_load(data.read_text(encoding='utf-8-sig'))
    if ordered_names(meta.get('names')) != ['person']:
        parser.error('OWLv2 baseline requires a one-class person dataset.')
    audit=audit_dataset(meta,data,'test')
    root=Path(meta.get('path',data.parent))
    if not root.is_absolute(): root=data.parent/root
    entries=meta['test'] if isinstance(meta['test'],list) else [meta['test']]
    images=[]
    for entry in entries:
        folder=Path(entry)
        if not folder.is_absolute(): folder=root/folder
        images.extend(p for p in folder.rglob('*') if p.suffix.lower() in {'.jpg','.jpeg','.png','.webp','.bmp'})
    images=sorted(images)
    if args.limit: images=images[:args.limit]
    if args.output.exists() and not args.limit:
        with args.output.open(newline='',encoding='utf-8') as handle:
            if next(csv.reader(handle),None)!=FIELDS:
                parser.error('Existing result CSV schema differs.')
    artifact=Path('runs/zeroshot')/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    artifact.mkdir(parents=True)
    detector=OwlV2Detector(device=args.device,prompts=PERSON_PROMPTS,
                         threshold=args.conf,iou=args.iou,imgsz=args.imgsz)
    print(f'Loading {MODEL_ID}; dataset audit: {audit}',flush=True)
    detector.load()
    detector.predict(Image.new('RGB',(640,640)))  # untimed warmup
    metrics=DetMetrics(names={0:'person'})
    timings=[]
    with (artifact/'predictions.jsonl').open('w',encoding='utf-8') as output:
        for index,path in enumerate(images,1):
            with Image.open(path) as opened:
                image=opened.convert('RGB')
            detections,timing=detector.predict(image)
            timings.append(timing)
            parts=list(path.parts)
            reverse_index=parts[::-1].index('images')
            parts[len(parts)-1-reverse_index]='labels'
            label=Path(*parts).with_suffix('.txt')
            score_predictions(detections,label,image.width,image.height,metrics)
            output.write(json.dumps({'image':str(path),'detections':detections,'timing_ms':timing})+'\n')
            output.flush()
            if index%10==0 or index==len(images):
                print(f'OWLv2 {index}/{len(images)}; mean inference {np.mean([t["inference"] for t in timings]):.1f} ms',flush=True)
    metrics.process(save_dir=artifact,plot=True)
    row=dict(timestamp_utc=datetime.now(timezone.utc).isoformat(),model=MODEL_ID,
        data=str(data),split='test',classes='person',images=len(images),
        precision=float(metrics.box.mp),recall=float(metrics.box.mr),
        map50=float(metrics.box.map50),map50_95=float(metrics.box.map),
        inference_ms_per_image=float(np.mean([t['inference'] for t in timings])),
        preprocess_ms_per_image=float(np.mean([t['preprocess'] for t in timings])),
        postprocess_ms_per_image=float(np.mean([t['postprocess'] for t in timings])),
        device=detector.device,imgsz=args.imgsz,batch=1,conf=args.conf,iou=args.iou,
        ultralytics_version=ultralytics.__version__,artifacts=str(artifact.resolve()))
    metadata={'model':MODEL_ID,'revision':detector.model.config._commit_hash,
        'prompts':PERSON_PROMPTS,'body_part_prompts':False,'imgsz':args.imgsz,
        'cpu_threads':torch.get_num_threads(),'positional_embedding_interpolation':True,'transformers_version':transformers.__version__,
        'audit':audit,'complete_split':len(images)==audit['images'],'results':row}
    (artifact/'metadata.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
    output=artifact/'smoke_results.csv' if args.limit else args.output
    output.parent.mkdir(parents=True,exist_ok=True)
    new=not output.exists()
    with output.open('a',newline='',encoding='utf-8') as handle:
        writer=csv.DictWriter(handle,fieldnames=FIELDS)
        if new: writer.writeheader()
        writer.writerow(row)
    print(json.dumps(row,indent=2),flush=True)
    print(f'Saved results: {output}',flush=True)


if __name__=='__main__':
    main()
