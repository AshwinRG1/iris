"""Ground-truth validation regression tests; no model downloads required."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
import argparse
import csv
import sys
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('evaluate', Path(__file__).with_name('evaluate.py'))
evaluate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluate)


class DatasetAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'test/images').mkdir(parents=True)
        (self.root / 'test/labels').mkdir()
        self.meta = {'path': str(self.root), 'test': 'test/images', 'names': ['person']}
        self.yaml = self.root / 'data.yaml'

    def add(self, name, annotation):
        (self.root / 'test/images' / (name + '.jpg')).touch()
        if annotation is not None:
            (self.root / 'test/labels' / (name + '.txt')).write_text(annotation)

    def test_positive_and_explicit_background(self):
        self.add('person', '0 0.5 0.5 0.2 0.3\n')
        self.add('empty', '')
        self.assertEqual(evaluate.audit_dataset(self.meta, self.yaml, 'test'),
                         {'images': 2, 'positive_images': 1, 'background_images': 1, 'boxes': 1})

    def test_missing_label_is_not_silently_background(self):
        self.add('missing', None)
        with self.assertRaisesRegex(ValueError, 'Missing annotation'):
            evaluate.audit_dataset(self.meta, self.yaml, 'test')

    def test_all_empty_annotations_rejected(self):
        self.add('empty', '')
        with self.assertRaisesRegex(ValueError, 'No annotated objects'):
            evaluate.audit_dataset(self.meta, self.yaml, 'test')

    def test_wrong_class_and_invalid_boxes(self):
        for row in ['1 0.5 0.5 0.2 0.3', '0 nan 0.5 0.2 0.3', '0 0.5 0.5 0 0.3', '0 0.5 0.5']:
            with self.subTest(row=row):
                self.add('bad', row)
                with self.assertRaises(ValueError):
                    evaluate.audit_dataset(self.meta, self.yaml, 'test')

    def test_duplicate_split_directory_rejected(self):
        self.add('person', '0 0.5 0.5 0.2 0.3')
        self.meta['test'] = ['test/images', 'test/images']
        with self.assertRaisesRegex(ValueError, 'repeats'):
            evaluate.audit_dataset(self.meta, self.yaml, 'test')


class EvaluationExportTests(unittest.TestCase):
    def test_completed_validation_writes_metrics_without_model_validator_attribute(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / 'data.yaml'
            data.write_text('placeholder')
            metrics = SimpleNamespace(
                box=SimpleNamespace(mp=0.8, mr=0.6, map50=0.7, map=0.4),
                speed={'inference': 20.0, 'preprocess': 1.0, 'postprocess': 2.0},
                save_dir=root / 'artifacts')
            calls = []

            class FakeYOLO:
                task = 'detect'
                names = {0: 'person', 1: 'bicycle'}
                device = 'cpu'

                def __init__(self, checkpoint):
                    pass

                def add_callback(self, name, callback):
                    self.callback = callback

                def val(self, **kwargs):
                    calls.append(kwargs)
                    self.callback(SimpleNamespace(save_dir=root / 'artifacts', seen=634))
                    return metrics

            meta = {'test': 'images/test', 'names': ['person']}
            args = argparse.Namespace(data=data, split='test', check_data=False,
                output=root / 'results.csv', models=['yolo26s.pt'], imgsz=640,
                batch=1, device='cpu', conf=0.001, iou=0.7, project=root / 'runs')
            modules = {'yaml': SimpleNamespace(safe_load=lambda handle: meta),
                       'ultralytics': SimpleNamespace(YOLO=FakeYOLO, __version__='test')}
            with patch.dict(sys.modules, modules), patch.object(evaluate, 'audit_dataset', return_value={'images': 634}):
                evaluate.evaluate(args)
                evaluate.evaluate(args)
            with args.output.open(newline='') as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]['images'], '634')
            self.assertEqual(rows[0]['map50'], '0.7')
            self.assertEqual(rows[0]['device'], 'cpu')
            self.assertEqual(calls[0]['classes'], [0])
            self.assertEqual(calls[0]['split'], 'test')


if __name__ == '__main__':
    unittest.main()
