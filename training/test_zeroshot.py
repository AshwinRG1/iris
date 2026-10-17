"""Regression checks for OWLv2 box conversion and shared YOLO metric matching."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
from zeroshot import normalized_detections, OwlV2Detector
from evaluate_zeroshot import score_predictions


class ZeroShotTests(unittest.TestCase):
    def test_normalized_person_contract(self):
        self.assertEqual(normalized_detections([[-10,20,120,100]],[0.8],100,100),
            [{'label':'person','conf':0.8,'box':[0.,0.2,1.,1.]}])

    def test_invalid_boxes_and_scores_removed(self):
        self.assertEqual(normalized_detections([[30,20,10,40],[0,0,float('nan'),1]],
                         [0.8,0.5],100,100),[])
        self.assertEqual(normalized_detections([[0,0,10,10]],[float('inf')],100,100),[])

    def test_empty_detections(self):
        self.assertEqual(normalized_detections([],[],100,100),[])

    def test_invalid_configuration(self):
        for kwargs in [{'prompts':[]},{'threshold':2},{'imgsz':641},{'iou':-1},{'max_det':0}]:
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):
                OwlV2Detector(**kwargs)

    def test_matching_duplicate_prediction_and_missed_person(self):
        from ultralytics.utils.metrics import DetMetrics
        with tempfile.TemporaryDirectory() as directory:
            label=Path(directory)/'label.txt'
            label.write_text('0 0.5 0.5 0.4 0.4\n')
            metrics=DetMetrics(names={0:'person'})
            det={'label':'person','conf':0.8,'box':[0.3,0.3,0.7,0.7]}
            score_predictions([det,dict(det,conf=0.6)],label,100,100,metrics)
            self.assertTrue(metrics.stats['tp'][0][0].all())
            self.assertFalse(metrics.stats['tp'][0][1].any())
            score_predictions([],label,100,100,metrics)
            self.assertEqual(metrics.stats['tp'][1].shape,(0,10))
            self.assertEqual(len(metrics.stats['target_cls'][1]),1)

    def test_ground_truth_clipped_like_yolo(self):
        from ultralytics.utils.metrics import DetMetrics
        with tempfile.TemporaryDirectory() as directory:
            label=Path(directory)/'edge.txt'
            label.write_text('0 0.1 0.5 0.4 0.4\n')
            metrics=DetMetrics(names={0:'person'})
            score_predictions([{'label':'person','conf':0.8,'box':[0.,0.3,0.3,0.7]}],
                              label,100,100,metrics)
            self.assertTrue(metrics.stats['tp'][0].all())

    def test_background_detections_are_false_positives(self):
        from ultralytics.utils.metrics import DetMetrics
        with tempfile.TemporaryDirectory() as directory:
            label=Path(directory)/'empty.txt'
            label.write_text('')
            metrics=DetMetrics(names={0:'person'})
            score_predictions([{'label':'person','conf':0.8,'box':[0.1,0.1,0.5,0.5]}],
                              label,100,100,metrics)
            self.assertFalse(metrics.stats['tp'][0].any())


if __name__=='__main__':
    unittest.main()
