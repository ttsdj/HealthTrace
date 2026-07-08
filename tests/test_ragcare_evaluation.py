from __future__ import annotations

import unittest

from backend.evaluation.ragcare_dataset import (
    convert_rows,
    stratified_pilot_split,
)
from backend.evaluation.ragcare_judges import (
    _extract_json,
    normalize_judge_result,
)
from backend.evaluation.ragcare_metrics import (
    answer_token_f1,
    choice_accuracy,
    retrieval_metrics_at_k,
)
from backend.evaluation.ragcare_retrieval import RagcareEvalStore


class RagcareDatasetTests(unittest.TestCase):
    def test_stable_document_id_and_context_deduplication(self):
        rows = [
            {
                "_row_idx": 0,
                "Question": "Question one?",
                "Context": "Shared evidence.",
                "Reference": "Book",
                "Page": "10",
                "Answer": "a.",
                "Text Answer": "Answer one",
            },
            {
                "_row_idx": 1,
                "Question": "Question two?",
                "Context": "Shared evidence.",
                "Reference": "Book",
                "Page": "10",
                "Answer": "b.",
                "Text Answer": "Answer two",
            },
        ]
        corpus_a, cases_a = convert_rows(rows)
        corpus_b, cases_b = convert_rows(rows)
        self.assertEqual(len(corpus_a), 1)
        self.assertEqual(corpus_a, corpus_b)
        self.assertEqual(cases_a[0]["gold_document_ids"], cases_b[0]["gold_document_ids"])
        self.assertNotIn("question", corpus_a[0])
        self.assertNotIn("answer", corpus_a[0])
        self.assertNotIn("text_answer", corpus_a[0])

    def test_stratified_split_has_exact_size(self):
        cases = [
            {
                "query_id": f"q-{index}",
                "row_idx": index,
                "complexity": "basic" if index % 2 else "complex",
                "medicine_type": f"type-{index % 4}",
                "rag_pipeline": f"pipeline-{index % 3}",
            }
            for index in range(420)
        ]
        pilot, heldout = stratified_pilot_split(cases, pilot_size=100, seed=20260627)
        self.assertEqual(len(pilot), 100)
        self.assertEqual(len(heldout), 320)
        self.assertFalse(
            {item["query_id"] for item in pilot}
            & {item["query_id"] for item in heldout}
        )


class RagcareMetricTests(unittest.TestCase):
    def test_document_metrics(self):
        docs = [
            {"document_id": "wrong"},
            {"document_id": "gold"},
            {"document_id": "gold"},
        ]
        result = retrieval_metrics_at_k(docs, ["gold"], k=5)
        self.assertEqual(result["recall_at_5"], 1.0)
        self.assertEqual(result["precision_at_5"], 0.2)
        self.assertEqual(result["f1_at_5"], 0.333333)
        self.assertEqual(result["mrr"], 0.5)
        self.assertGreater(result["ndcg_at_5"], 0)

    def test_answer_metrics(self):
        self.assertEqual(answer_token_f1("The answer is BNP", "BNP"), 0.4)
        self.assertEqual(choice_accuracy("The answer is d.", "d."), 1.0)
        self.assertEqual(choice_accuracy("Option c", "d."), 0.0)


class FakeRagcareStore(RagcareEvalStore):
    def __init__(self):
        self.calls = []

    def embed_query(self, query: str):
        self.calls.append("embed")
        return [1.0]

    def _dense_search(self, vector, limit):
        self.calls.append("dense")
        return [{"document_id": "dense", "text": "dense"}]

    def _bm25_search(self, query, limit):
        self.calls.append("bm25")
        return [{"document_id": "bm25", "text": "bm25"}]

    def _hybrid_search(self, query, vector, *, limit, candidate_k, rrf_k):
        self.calls.append("hybrid")
        return [{"document_id": "hybrid", "text": "hybrid"}]


class FakeReranker:
    def rerank(self, query, docs, *, top_k):
        return [{**docs[0], "rerank_score": 1.0}]


class BaselineIsolationTests(unittest.TestCase):
    def test_bm25_does_not_embed(self):
        store = FakeRagcareStore()
        result = store.retrieve("q", mode="bm25", top_k=5)
        self.assertEqual(store.calls, ["bm25"])
        self.assertFalse(result["meta"]["dense_used"])
        self.assertTrue(result["meta"]["sparse_used"])

    def test_dense_does_not_use_sparse(self):
        store = FakeRagcareStore()
        result = store.retrieve("q", mode="dense", top_k=5)
        self.assertEqual(store.calls, ["embed", "dense"])
        self.assertTrue(result["meta"]["dense_used"])
        self.assertFalse(result["meta"]["sparse_used"])

    def test_hybrid_rerank_requires_and_uses_reranker(self):
        store = FakeRagcareStore()
        result = store.retrieve(
            "q",
            mode="hybrid_rerank",
            top_k=5,
            candidate_k=20,
            reranker=FakeReranker(),
        )
        self.assertEqual(store.calls, ["embed", "hybrid"])
        self.assertTrue(result["meta"]["dense_used"])
        self.assertTrue(result["meta"]["sparse_used"])
        self.assertTrue(result["meta"]["reranker_used"])


class JudgeParsingTests(unittest.TestCase):
    def test_json_fence_and_score_normalization(self):
        payload = _extract_json(
            '```json\n{"context_relevance": 1, "faithfulness": 0.75, '
            '"answer_relevance": 0.5, "rationale": "brief"}\n```'
        )
        result = normalize_judge_result(payload)
        self.assertEqual(result["context_relevance"], 1.0)
        self.assertEqual(result["rag_quality_score"], 0.75)

    def test_missing_score_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_judge_result(
                {
                    "context_relevance": 1,
                    "faithfulness": 1,
                    "rationale": "missing answer relevance",
                }
            )


if __name__ == "__main__":
    unittest.main()
