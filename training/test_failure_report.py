"""Numerical checks for failure-report matching and metric accounting."""
import unittest
from failure_report import match, metrics


class MatchingTests(unittest.TestCase):
    def setUp(self):
        self.gt = [{'box': [0, 0, 10, 10]}]
        self.predictions = [[0, 0, 10, 10, .9, 0], [0, 0, 10, 10, .8, 0], [20, 20, 30, 30, .2, 0]]

    def test_duplicate_detection_is_not_a_second_true_positive(self):
        result = match(self.predictions, self.gt, [], .15)
        self.assertEqual(result['pairs'], [(0, 0)])
        self.assertEqual(result['fp'], [1, 2])
        self.assertEqual(result['fn'], [])

    def test_confidence_order_not_input_order_controls_matching(self):
        result = match(list(reversed(self.predictions)), self.gt, [], .15)
        self.assertEqual(result['pairs'], [(2, 0)])

    def test_threshold_recomputes_matches_and_misses(self):
        self.assertEqual(match(self.predictions, self.gt, [], .85)['fp'], [])
        self.assertEqual(match(self.predictions, self.gt, [], .95)['fn'], [0])

    def test_ignored_region_excludes_only_unmatched_predictions(self):
        result = match(self.predictions, self.gt, [[0, 0, 30, 30]], .15)
        self.assertEqual(result['pairs'], [(0, 0)])
        self.assertEqual(result['ignored'], [1, 2])
        self.assertEqual(result['fp'], [])

    def test_localization_failure_counts_both_miss_and_false_prediction(self):
        result = match([[0, 0, 4, 4, .9, 0]], self.gt, [], .15)
        self.assertEqual(result['fn'], [0])
        self.assertEqual(result['fp'], [0])

    def test_empty_predictions_or_ground_truth(self):
        self.assertEqual(match([], self.gt, [], .15)['fn'], [0])
        self.assertEqual(match(self.predictions, [], [], .15)['fp'], [0, 1, 2])
        self.assertEqual(match([], [], [], .15)['pairs'], [])

    def test_f2_prioritizes_recall_and_handles_zero(self):
        self.assertEqual(metrics(0, 0, 0)['f2'], 0)
        self.assertEqual(metrics(1, 0, 0)['f2'], 1)
        self.assertAlmostEqual(metrics(2, 1, 3)['f2'], 10/23)


if __name__ == '__main__':
    unittest.main()
