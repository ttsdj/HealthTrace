from __future__ import annotations

import unittest

from backend.evaluation.mirage_dataset import normalize_benchmark, select_cases
from backend.evaluation.mirage_metrics import extract_choice, summarize_records
from backend.evaluation.mirage_runner import run_benchmark


class MirageDatasetTests(unittest.TestCase):
    def test_normalize_benchmark(self):
        raw = {
            "medqa": {
                "0001": {
                    "question": "Which option is best?",
                    "options": {"a": "Alpha", "B": "Beta"},
                    "answer": "b",
                }
            }
        }
        cases = normalize_benchmark(raw)
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["query_id"], "mirage-medqa-0001")
        self.assertEqual(cases[0]["options"], {"A": "Alpha", "B": "Beta"})
        self.assertEqual(cases[0]["gold_answer"], "B")
        self.assertEqual(cases[0]["gold_answer_text"], "Beta")

    def test_select_max_cases_per_dataset(self):
        cases = [
            {"dataset": "a", "raw_id": "1"},
            {"dataset": "a", "raw_id": "2"},
            {"dataset": "b", "raw_id": "1"},
            {"dataset": "b", "raw_id": "2"},
        ]
        selected = select_cases(cases, max_cases_per_dataset=1)
        self.assertEqual([item["raw_id"] for item in selected], ["1", "1"])


class MirageMetricTests(unittest.TestCase):
    def test_extract_json_choice(self):
        choice = extract_choice('{"answer": "B", "confidence": 0.8}', {"A": "x", "B": "y"})
        self.assertEqual(choice, "B")

    def test_extract_yes_no_word(self):
        choice = extract_choice("The answer is yes.", {"A": "yes", "B": "no"})
        self.assertEqual(choice, "A")

    def test_summarize_records(self):
        summary = summarize_records(
            [
                {
                    "dataset": "medqa",
                    "is_correct": True,
                    "is_valid": True,
                    "latency_seconds": 1.0,
                },
                {
                    "dataset": "medqa",
                    "is_correct": False,
                    "is_valid": False,
                    "error": "bad",
                    "latency_seconds": 3.0,
                },
            ]
        )
        self.assertEqual(summary["overall"]["accuracy"], 0.5)
        self.assertEqual(summary["overall"]["invalid_answer_rate"], 0.5)
        self.assertEqual(summary["overall"]["failure_rate"], 0.5)
        self.assertEqual(summary["overall"]["mean_latency_seconds"], 2.0)


class FakeClient:
    def answer(self, case, contexts=None):
        return '{"answer": "A", "confidence": 0.9}'


class MirageRunnerTests(unittest.TestCase):
    def test_run_llm_only_benchmark(self):
        records = run_benchmark(
            [
                {
                    "query_id": "q",
                    "dataset": "medqa",
                    "raw_id": "1",
                    "question": "q",
                    "options": {"A": "x", "B": "y"},
                    "gold_answer": "A",
                    "gold_answer_text": "x",
                }
            ],
            mode="llm_only",
            llm_client=FakeClient(),
            workers=1,
        )
        self.assertEqual(records[0]["predicted_answer"], "A")
        self.assertTrue(records[0]["is_correct"])

    def test_run_rag_agent_uses_question_only_retriever(self):
        seen = []

        def retriever(question):
            seen.append(question)
            return ([{"text": "context"}], {"mode": "fake"})

        case = {
            "query_id": "q",
            "dataset": "medqa",
            "raw_id": "1",
            "question": "question only",
            "options": {"A": "x", "B": "y"},
            "gold_answer": "A",
            "gold_answer_text": "x",
        }
        records = run_benchmark(
            [case],
            mode="rag_agent",
            llm_client=FakeClient(),
            retriever=retriever,
            workers=1,
        )
        self.assertEqual(seen, ["question only"])
        self.assertEqual(records[0]["retrieval_meta"], {"mode": "fake"})


if __name__ == "__main__":
    unittest.main()
