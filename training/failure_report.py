"""Development-only person failure analysis on original export validation folders.

Never loads the evaluation-only YAML. Keeps predictions and matches for inspection.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import html
import json
import math
import os
from pathlib import Path
import statistics
import time

import numpy as np

SOURCES = {
    'rubble': ('data/rubble', {0: 'Person', 1: 'Person in Rubble'}, {3}),
    'flood1': ('data/flood1/Drowning Human.v1i.yolo26',
               {1: 'Person drowning', 2: 'Person in water', 3: 'Person out of water'}, set()),
    'flood2': ('data/flood2', {1: 'drowning', 2: 'in-car', 3: 'person'}, set()),
}
THRESHOLDS = (0.05, 0.10, 0.15, 0.25, 0.35, 0.50)
MODELS = ('yolo26s.pt', 'server/weights/best_plain_v2.pt', 'server/weights/best_storm_v2.pt')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def overlap(a, b, prediction_area=False):
    a, b = np.asarray(a, dtype=float).reshape(-1, 4), np.asarray(b, dtype=float).reshape(-1, 4)
    inter = np.maximum(0, np.minimum(a[:, None, 2:], b[None, :, 2:]) - np.maximum(a[:, None, :2], b[None, :, :2])).prod(2)
    area_a = np.maximum(0, a[:, 2:]-a[:, :2]).prod(1)
    area_b = np.maximum(0, b[:, 2:]-b[:, :2]).prod(1)
    denom = area_a[:, None] if prediction_area else area_a[:, None]+area_b[None, :]-inter
    return inter / np.maximum(denom, 1e-12)


def match(predictions, gt, ignore, threshold):
    # Greedy confidence-order matching; each GT and prediction used at most once.
    selected = [i for i, p in enumerate(predictions) if p[4] >= threshold]
    selected.sort(key=lambda i: (-predictions[i][4], i))
    ious = overlap([p[:4] for p in predictions], [g['box'] for g in gt])
    used, tp, fp, ignored, pairs = set(), [], [], [], []
    for pi in selected:
        candidates = [(ious[pi, gi], gi) for gi in range(len(gt)) if gi not in used and ious[pi, gi] >= .5]
        if candidates:
            _, gi = max(candidates, key=lambda x: (x[0], -x[1]))
            used.add(gi); tp.append(pi); pairs.append((pi, gi))
        elif ignore and overlap([predictions[pi][:4]], ignore, prediction_area=True).max() >= .5:
            ignored.append(pi)
        else:
            fp.append(pi)
    return {'tp': tp, 'fp': fp, 'fn': [i for i in range(len(gt)) if i not in used], 'ignored': ignored, 'pairs': pairs}


def metrics(tp, fp, fn):
    p = tp/(tp+fp) if tp+fp else 0.
    r = tp/(tp+fn) if tp+fn else 0.
    return {'tp': tp, 'fp': fp, 'fn': fn, 'precision': p, 'recall': r, 'f2': 5*p*r/(4*p+r) if 4*p+r else 0.}


def inventory():
    from PIL import Image
    test = {sha(p) for p in Path('data/test/data/test/images').iterdir() if p.is_file()}
    records, seen, excluded, counts = [], set(), [], Counter()
    for source, (folder, humans, ignore_ids) in SOURCES.items():
        images = sorted((Path(folder)/'valid/images').glob('*'))
        for p in images:
            if p.suffix.lower() not in {'.jpg', '.jpeg', '.png', '.bmp', '.webp'}:
                continue
            digest = sha(p)
            if digest in test or digest in seen:
                excluded.append({'image': str(p), 'reason': 'held_out_test_byte_match' if digest in test else 'duplicate_validation_image'})
                continue
            seen.add(digest)
            label = p.parent.parent/'labels'/(p.stem+'.txt')
            if not label.is_file():
                raise ValueError(f'Missing original labels: {label}')
            with Image.open(p) as im:
                w, h = im.size
                orientation = im.getexif().get(274, 1)
                if orientation not in (None, 1):
                    raise ValueError(f'EXIF orientation needs explicit label handling: {p}')
            gt, ignored, discarded = [], [], Counter()
            for line in label.read_text(encoding='utf-8-sig').splitlines():
                if not line.strip(): continue
                values = line.split()
                if len(values) != 5: raise ValueError(f'Non-detection annotation: {label}')
                cls = int(values[0]); x, y, bw, bh = map(float, values[1:])
                if not all(math.isfinite(v) and 0<=v<=1 for v in (x,y,bw,bh)) or bw<=0 or bh<=0:
                    raise ValueError(f'Invalid annotation: {label}')
                box = [max(0,(x-bw/2)*w), max(0,(y-bh/2)*h), min(w,(x+bw/2)*w), min(h,(y+bh/2)*h)]
                if cls in humans:
                    area = (box[2]-box[0])*(box[3]-box[1])*(640/max(w,h))**2
                    gt.append({'box': box, 'source_class': humans[cls], 'size': 'small' if area<32**2 else 'medium' if area<96**2 else 'large'})
                    counts[source+':'+humans[cls]] += 1
                elif cls in ignore_ids:
                    ignored.append(box)
                else: discarded[cls] += 1
            records.append({'id': source+'/'+p.name, 'source': source, 'image': str(p.resolve()), 'image_sha256': digest,
                            'label': str(label.resolve()), 'label_sha256': sha(label), 'width': w, 'height': h,
                            'gt': gt, 'ignore_boxes': ignored, 'nonperson_class_counts': dict(discarded)})
    return records, excluded, dict(counts)


def summarize(records, predictions):
    overall, groups, by_image = {}, {}, []
    for threshold in THRESHOLDS:
        total = Counter(); bins = defaultdict(Counter)
        for record in records:
            result = match(predictions[record['id']]['boxes'], record['gt'], record['ignore_boxes'], threshold)
            tp, fp, fn = len(result['tp']),len(result['fp']),len(result['fn'])
            total.update(tp=tp,fp=fp,fn=fn,ignored=len(result['ignored']))
            bins['source:'+record['source']].update(tp=tp,fp=fp,fn=fn)
            matched = {gi for _,gi in result['pairs']}
            for gi,g in enumerate(record['gt']):
                for key in ['size:'+g['size'], 'class:'+record['source']+':'+g['source_class']]:
                    bins[key].update(tp=int(gi in matched),fn=int(gi not in matched))
            if threshold == .15:
                by_image.append({'id':record['id'],'image':record['image'],'source':record['source'],**metrics(tp,fp,fn),'ignored_predictions':len(result['ignored'])})
        overall[str(threshold)] = {**metrics(total['tp'],total['fp'],total['fn']), 'ignored_predictions':total['ignored']}
        groups[str(threshold)] = {k: metrics(c['tp'],c['fp'],c['fn']) for k,c in sorted(bins.items())}
    return overall, groups, by_image


def gallery(output, records, model_predictions, summaries):
    from PIL import Image, ImageDraw
    folder = output/'examples'; folder.mkdir()
    index = {r['id']:r for r in records}; blocks=[]; selected={}
    # Select per source by missed fraction, then missed count. This is illustrative, not random prevalence sampling.
    for name, summary in summaries.items():
        chosen=[]
        for source in SOURCES:
            cases = [r for r in summary['per_image_at_015'] if r['source']==source and r['fn']]
            chosen.extend(sorted(cases,key=lambda r:(-r['fn']/max(1,r['tp']+r['fn']),-r['fn'],r['id']))[:3])
            fps = [r for r in summary['per_image_at_015'] if r['source']==source and r['fp']]
            chosen.extend(sorted(fps,key=lambda r:(-r['fp'],r['id']))[:1])
        selected[name]=[]
        for n,case in enumerate(chosen):
            record=index[case['id']]; pred=model_predictions[name][record['id']]['boxes']; matches=match(pred,record['gt'],record['ignore_boxes'],.15)
            with Image.open(record['image']) as src: im=src.convert('RGB')
            scale=min(1,960/max(im.size)); im=im.resize((round(im.width*scale),round(im.height*scale))); draw=ImageDraw.Draw(im)
            missed=set(matches['fn']); fps=set(matches['fp']); skipped=set(matches['ignored'])
            for gi,g in enumerate(record['gt']):
                b=[v*scale for v in g['box']]; color='#ff4141' if gi in missed else '#2de36e'; draw.rectangle(b,outline=color,width=2)
                draw.text((b[0],b[1]),('MISS ' if gi in missed else 'GT ')+g['source_class'],fill=color)
            for pi,p in enumerate(pred):
                if p[4]<.15 or pi in skipped: continue
                color='#ff48e0' if pi in fps else '#37dfff'; b=[v*scale for v in p[:4]]; draw.rectangle(b,outline=color,width=2)
                draw.text((b[0],max(0,b[1]-11)),f'P {p[4]:.2f}',fill=color)
            file=f'{name}_{n:02d}.jpg'; im.save(folder/file,quality=90)
            selected[name].append({**case,'overlay':'examples/'+file})
            blocks.append(f'<section><h3>{html.escape(name+": "+case["id"])}</h3><p>TP={case["tp"]} FN={case["fn"]} unmatched predictions={case["fp"]}</p><img loading="lazy" src="examples/{file}"></section>')
    (output/'gallery.html').write_text('<!doctype html><meta charset="utf-8"><title>Development failure review</title><style>body{font-family:Arial;max-width:1100px;margin:24px auto;background:#151515;color:#eee}img{max-width:100%}section{margin:30px 0}</style><h1>Development failure review at confidence 0.15</h1><p>Red: missed ground truth. Green: matched ground truth. Cyan: matched prediction. Magenta: unmatched prediction (requires label review before calling it a real false positive). Selected worst cases, not prevalence estimates.</p>'+''.join(blocks),encoding='utf-8')
    (output/'selected_examples.json').write_text(json.dumps(selected,indent=2),encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--output',type=Path,required=True); parser.add_argument('--models',nargs='+',default=list(MODELS)); args=parser.parse_args()
    missing=[p for p in args.models if not Path(p).is_file()]
    if missing: parser.error('Missing checkpoints: '+', '.join(missing))
    if args.output.exists(): raise FileExistsError(args.output)
    args.output.mkdir(parents=True)
    os.environ['OMP_NUM_THREADS']='8'
    import torch
    torch.set_num_threads(8)
    import ultralytics
    from ultralytics import YOLO
    started=time.monotonic(); records,excluded,counts=inventory()
    manifest={'created_utc':datetime.now(timezone.utc).isoformat(),'purpose':'Development diagnostic; source-export validation folders, not verified C validation. No test images used.',
      'images':len(records),'gt_boxes':sum(len(r['gt']) for r in records),'source_images':dict(Counter(r['source'] for r in records)),
      'source_class_boxes':counts,'excluded':excluded,'records':records,'matching':'IoU >= 0.5, one-to-one greedy matching in descending prediction confidence. Unmatched predictions with >=50% prediction area inside rubble ignored-region boxes are excluded.',
      'size_bins':'GT area after aspect-preserving scaling longest side to 640: small <32^2, medium <96^2, large otherwise. Recall bins only, not COCO AP.',
      'settings':{'imgsz':640,'rect':False,'batch':1,'device':'cpu','half':False,'augment':False,'candidate_conf':.001,'nms_iou':.7,'max_det':300,'thresholds':THRESHOLDS,'threads':8,'ultralytics':ultralytics.__version__,'torch':torch.__version__},
      'limitations':['No verified provenance for C merged train/val; these originals may have been seen during training.','Excluded exact test and validation duplicates; near-duplicate, source-video grouping, and train-overlap audits not completed.','Original human-class mapping follows prior repair; in-car annotation semantics need review.','Unknown weather/occlusion conditions are not inferred from class names.','Unmatched predictions are annotation-relative candidates, not independently reviewed false positives.','Size/class subgroup precision is not meaningful because unmatched predictions cannot be assigned to a GT subgroup.','Not a deployed alert, clinical, survivor-status, or full video performance test.']}
    (args.output/'dataset_manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8'); print(f'Audit: {len(records)} images, {manifest["gt_boxes"]} boxes; exclusions={len(excluded)}',flush=True)
    all_predictions={}; summaries={}
    for model_path in args.models:
        name=Path(model_path).stem; model=YOLO(model_path)
        if model.names.get(0)!='person': raise ValueError(f'Unexpected person class ID: {model_path}')
        model_hash=sha(Path(model_path)); train_args=model.ckpt.get('train_args',{}); predictions={}; model_started=time.monotonic()
        model.predict(np.zeros((640,640,3),dtype=np.uint8),imgsz=640,device='cpu',verbose=False,classes=[0],conf=.001,rect=False)
        with (args.output/(name+'_predictions.jsonl')).open('w',encoding='utf-8') as stream:
            for i,record in enumerate(records,1):
                result=model.predict(record['image'],imgsz=640,device='cpu',augment=False,verbose=False,classes=[0],conf=.001,iou=.7,max_det=300,rect=False)[0]
                boxes=result.boxes.data.detach().cpu().numpy().tolist()
                item={'id':record['id'],'boxes':boxes,'speed_ms':result.speed}
                predictions[record['id']]=item; stream.write(json.dumps(item)+'\n')
                if i%50==0 or i==len(records): print(f'{name}: {i}/{len(records)}, elapsed={time.monotonic()-model_started:.1f}s',flush=True); stream.flush()
        if sha(Path(model_path))!=model_hash: raise RuntimeError('Checkpoint changed during diagnostic')
        overall,groups,per_image=summarize(records,predictions)
        # Size/class precision and F2 cannot be computed without assigning false predictions to those groups.
        for threshold in groups.values():
            for key,stats in threshold.items():
                if not key.startswith('source:'):
                    stats['fp']=None; stats['precision']=None; stats['f2']=None
        summary={'model':model_path,'sha256':model_hash,'parameters':sum(p.numel() for p in model.model.parameters()),'base_model':train_args.get('model'),
          'overall':overall,'groups':groups,'per_image_at_015':per_image,
          'mean_inference_ms':statistics.mean(p['speed_ms']['inference'] for p in predictions.values())}
        (args.output/(name+'_summary.json')).write_text(json.dumps(summary,indent=2),encoding='utf-8')
        summaries[name]=summary; all_predictions[name]=predictions
        print(json.dumps({'model':name,'at_015':overall['0.15'],'at_035':overall['0.35']}),flush=True)
    gallery(args.output,records,all_predictions,summaries)
    combined={'dataset':{k:v for k,v in manifest.items() if k!='records'},'models':summaries,'elapsed_seconds':time.monotonic()-started}
    (args.output/'summary.json').write_text(json.dumps(combined,indent=2),encoding='utf-8')
    print(f'Complete: {args.output.resolve()}; elapsed={time.monotonic()-started:.1f}s',flush=True)


if __name__=='__main__': main()
