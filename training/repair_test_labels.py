"""Repair merged test annotations from byte-matched ORIGINAL exports.

Run without flags to validate; --apply backs up and replaces merged labels.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import shutil

SOURCES = {
    'rubble': ('rubble', {0: 'Person', 1: 'Person in Rubble'}, {2, 3}),
    'flood1': ('flood1/Drowning Human.v1i.yolo26',
               {1: 'Person drowning', 2: 'Person in water', 3: 'Person out of water'}, {0, 4}),
    'flood2': ('flood2', {1: 'drowning', 2: 'in-car', 3: 'person'}, {0}),
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(data, target):
    indexes = {}
    for prefix, (relative, _, _) in SOURCES.items():
        index = {}
        for path in (data / relative).rglob('*.txt'):
            if path.parent.name == 'labels':
                index.setdefault(path.name, []).append(path)
        indexes[prefix] = index
    images = sorted((target / 'test/images').glob('*.jpg'))
    labels = list((target / 'test/labels').glob('*.txt'))
    if not images or {p.stem for p in images} != {p.stem for p in labels}:
        raise ValueError('Expected matching nonempty JPG and TXT sets.')
    prepared, counts = [], Counter()
    for image in images:
        prefix, _, original = image.stem.partition('_')
        image_hash = sha(image)
        matches = []
        for source in indexes.get(prefix, {}).get(original + '.txt', []):
            original_image = source.parent.parent / 'images' / (original + '.jpg')
            if original_image.is_file() and sha(original_image) == image_hash:
                matches.append(source)
        if not matches:
            raise ValueError(f'No identical ORIGINAL image/label pair: {image}')
        if len({p.read_text(encoding='utf-8-sig') for p in matches}) != 1:
            raise ValueError(f'Conflicting originals: {image}')
        source = matches[0]
        _, humans, excluded = SOURCES[prefix]
        output = []
        for line in source.read_text(encoding='utf-8-sig').splitlines():
            if not line.strip():
                continue
            parts = line.split()
            if len(parts) != 5:
                raise ValueError(f'Not a detection annotation: {source}: {line}')
            cls = int(parts[0])
            values = [float(v) for v in parts[1:]]
            if not all(math.isfinite(v) and 0 <= v <= 1 for v in values) or min(values[2:]) <= 0:
                raise ValueError(f'Invalid box: {source}: {line}')
            if cls in humans:
                output.append('0 ' + ' '.join(parts[1:]))
                counts[prefix + '_person_boxes'] += 1
            elif cls not in excluded:
                raise ValueError(f'Unknown class {cls}: {source}')
        counts[prefix + '_images'] += 1
        counts['positive_images' if output else 'background_images'] += 1
        prepared.append((target / 'test/labels' / (image.stem + '.txt'),
                         '\n'.join(output) + ('\n' if output else ''),
                         {'image': str(image), 'image_sha256': image_hash,
                          'original_labels': [str(p) for p in matches],
                          'original_label_sha256': sha(source), 'person_boxes': len(output)}))
    return prepared, dict(counts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=Path('data'))
    parser.add_argument('--target', type=Path, default=Path('data/test/data'))
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    target = args.target.resolve()
    prepared, counts = prepare(args.data.resolve(), target)
    print(json.dumps(counts, indent=2))
    if not args.apply:
        print('Dry run passed: every image matched an original by SHA-256.')
        return
    backup = target / ('labels_backup_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    shutil.copytree(target / 'test/labels', backup)
    for label, content, _ in prepared:
        label.write_text(content, encoding='utf-8')
    # train exists only to satisfy Ultralytics schema; this YAML is evaluation-only.
    yaml = ('# EVALUATION ONLY: never train or tune using this YAML.\npath: '
            + json.dumps(target.as_posix())
            + '\ntrain: test/images\nval: test/images\ntest: test/images\nnc: 1\nnames: [person]\n')
    (target / 'data.yaml').write_text(yaml, encoding='utf-8')
    manifest = {'counts': counts, 'backup': str(backup),
                'mapping': {key: {'human_classes': human, 'excluded_class_ids': sorted(excluded)}
                            for key, (_, human, excluded) in SOURCES.items()},
                'files': [entry for _, _, entry in prepared]}
    (target / 'repair_manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    for label, content, entry in prepared:
        if label.read_text(encoding='utf-8') != content or sha(Path(entry['image'])) != entry['image_sha256']:
            raise RuntimeError(f'Post-write verification failed: {label}')
    print(f'Repaired and verified {len(prepared)} labels. Backup: {backup}')
    print(f'Evaluation YAML: {target / "data.yaml"}')


if __name__ == '__main__':
    main()
