from __future__ import annotations

import unittest

from backend.evaluation.ragas_evaluator import _score_value
from backend.evaluation.ragas_runner import case_key, select_records, summarize_ragas


class FakeMetricResult:
    def __init__(self, value):
        self.value = value


class RagasRunnerTests(unittest.TestCase):
    def test_select_records_is_deterministic_and_balanced(self):
        rows = [
            {
                "query_id": f"q{index}",
                "row_idx": index,
                "baseline": baseline,
                "generated_answer": "answer",
            }
            for index in range(3)
            for baseline in ("bm25", "dense")
        ]
        selected = select_records(
            list(reversed(rows)),
            baselines=["bm25", "dense"],
            max_cases_per_baseline=2,
        )
        self.assertEqual(len(selected), 4)
        self.assertEqual(
            [case_key(row) for row in selected],
            ["q0::bm25", "q0::dense", "q1::bm25", "q1::dense"],
        )

    def test_summary_uses_only_valid_metric_values(self):
        rows = [
            {
                "baseline": "dense",
                "ragas": {
                    "scores": {"faithfulness": 1.0},
                    "ragas_quality_score": 1.0,
                    "errors": {},
                },
            },
            {
                "baseline": "dense",
                "ragas": {
                    "scores": {"faithfulness": 0.5},
                    "ragas_quality_score": 0.5,
                    "errors": {"context_recall": "failed"},
                },
            },
        ]
        summary = summarize_ragas(rows, ("faithfulness", "context_recall"))
        self.assertEqual(summary["dense"]["faithfulness"], 0.75)
        self.assertEqual(summary["dense"]["faithfulness_valid_cases"], 2)
        self.assertIsNone(summary["dense"]["context_recall"])
        self.assertEqual(summary["dense"]["cases_with_errors"], 1)

    def test_non_finite_score_is_rejected(self):
        with self.assertRaises(ValueError):
            _score_value(FakeMetricResult(float("nan")))


if __name__ == "__main__":
    unittest.main()
