from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.evaluation.eval_artifact import (
    build_artifact,
    compute_retrieval_metrics,
    hash_dataset_manifest,
    standard_statistical_definitions,
    validate_artifact,
    write_artifact,
)

# Synthetic, clearly-flagged stub provenance. These are NOT real benchmark numbers;
# they exist only to exercise the artifact mechanics.
SYNTHETIC_DATASET_HASH = "test-dataset-hash"
SYNTHETIC_COMMIT = "test-commit"
SYNTHETIC_COMMAND = "python scripts/evaluate_ragcare_full.py --mode dense --top-k 5"


class EvalArtifactMetricTests(unittest.TestCase):
    def test_summary_recall_and_mrr_computed_from_ranks(self):
        # Per-question input A: gold doc is at rank 2 -> recall@5=1.0, MRR=0.5.
        ranked_a = [
            {"document_id": "wrong"},
            {"document_id": "gold"},
            {"document_id": "other"},
        ]
        # Per-question input B: gold doc is at rank 1 -> recall@5=1.0, MRR=1.0.
        ranked_b = [{"document_id": "gold"}]

        record_a = {
            "query_id": "q-a",
            "baseline": "dense",
            "ranked_document_ids": ["wrong", "gold", "other"],
            "gold_document_ids": ["gold"],
            "retrieval_metrics": compute_retrieval_metrics(ranked_a, ["gold"], k=5),
        }
        record_b = {
            "query_id": "q-b",
            "baseline": "dense",
            "ranked_document_ids": ["gold"],
            "gold_document_ids": ["gold"],
            "retrieval_metrics": compute_retrieval_metrics(ranked_b, ["gold"], k=5),
        }

        artifact = build_artifact(
            dataset_hash=SYNTHETIC_DATASET_HASH,
            config={"mode": "dense", "top_k": 5, "temperature": 0.0, "model": "test"},
            per_question=[record_a, record_b],
            metric_keys=("recall_at_5", "mrr"),
            statistical_definitions=standard_statistical_definitions(),
            command=SYNTHETIC_COMMAND,
            commit=SYNTHETIC_COMMIT,
        )

        # Per-question MRR values are 0.5 and 1.0 -> mean 0.75. Recall@5 is 1.0 for both.
        self.assertAlmostEqual(artifact["summary"]["recall_at_5"], 1.0, places=6)
        self.assertAlmostEqual(artifact["summary"]["mrr"], 0.75, places=6)
        self.assertEqual(artifact["summary"]["cases"], 2)
        self.assertEqual(validate_artifact(artifact), [])

    def test_recall_mean_is_exact_when_one_retrieval_misses(self):
        ranked = [{"document_id": "wrong"}, {"document_id": "wrong"}]  # gold never found
        record = {
            "query_id": "q-miss",
            "retrieval_metrics": compute_retrieval_metrics(ranked, ["gold"], k=5),
        }
        artifact = build_artifact(
            dataset_hash=SYNTHETIC_DATASET_HASH,
            config={"mode": "bm25", "top_k": 5},
            per_question=[record],
            metric_keys=("recall_at_5", "mrr"),
            statistical_definitions=standard_statistical_definitions(),
            command=SYNTHETIC_COMMAND,
            commit=SYNTHETIC_COMMIT,
        )
        self.assertEqual(artifact["summary"]["recall_at_5"], 0.0)
        self.assertEqual(artifact["summary"]["mrr"], 0.0)


class EvalArtifactValidationTests(unittest.TestCase):
    def test_validate_rejects_missing_required_fields(self):
        # Missing dataset_hash and commit -> invalid.
        artifact = {
            "schema_version": "1.0",
            "config": {"mode": "dense"},
            "summary": {"cases": 0},
            "per_question": [],
            "command": SYNTHETIC_COMMAND,
        }
        errors = validate_artifact(artifact)
        self.assertIn("missing field: dataset_hash", errors)
        self.assertIn("missing field: commit", errors)

    def test_validate_accepts_fully_specified_artifact(self):
        artifact = build_artifact(
            dataset_hash=SYNTHETIC_DATASET_HASH,
            config={"mode": "dense", "top_k": 5},
            per_question=[],
            summary={"cases": 0, "recall_at_5": None, "mrr": None},
            statistical_definitions=standard_statistical_definitions(),
            command=SYNTHETIC_COMMAND,
            commit=SYNTHETIC_COMMIT,
        )
        # An empty run is a valid (empty) artifact, not a claim of any score.
        self.assertEqual(validate_artifact(artifact), [])

    def test_per_question_must_be_a_list(self):
        artifact = {
            "schema_version": "1.0",
            "dataset_hash": SYNTHETIC_DATASET_HASH,
            "config": {},
            "summary": {},
            "per_question": "not-a-list",
            "command": SYNTHETIC_COMMAND,
            "commit": SYNTHETIC_COMMIT,
        }
        errors = validate_artifact(artifact)
        self.assertIn("per_question must be a list", errors)


class EvalArtifactRoundTripTests(unittest.TestCase):
    def test_write_and_read_back_artifact(self):
        artifact = build_artifact(
            dataset_hash=SYNTHETIC_DATASET_HASH,
            config={"mode": "hybrid", "top_k": 5},
            per_question=[],
            summary={"cases": 0, "recall_at_5": None, "mrr": None},
            statistical_definitions=standard_statistical_definitions(),
            command=SYNTHETIC_COMMAND,
            commit=SYNTHETIC_COMMIT,
        )
        with tempfile.TemporaryDirectory() as directory:
            path = write_artifact(artifact, Path(directory), "MANIFEST.json")
            self.assertTrue(path.exists())
            loaded = __import__("json").loads(path.read_text(encoding="utf-8"))
            self.assertEqual(validate_artifact(loaded), [])
            self.assertEqual(loaded["dataset_hash"], SYNTHETIC_DATASET_HASH)
            self.assertEqual(loaded["commit"], SYNTHETIC_COMMIT)
            self.assertEqual(loaded["command"], SYNTHETIC_COMMAND)


class EvalArtifactHashTests(unittest.TestCase):
    def test_dataset_manifest_hash_is_deterministic_and_ignores_ordering(self):
        manifest = {
            "dataset_id": "ChatMED-Project/RAGCare-QA",
            "dataset_split": "train",
            "raw_sha256": "abc123",
            "rows": 420,
            "leaf_chunks": 2615,
            "seed": 20260627,
            "chunk_size": 800,
            "chunk_overlap": 100,
            "pilot_cases": 100,
            "heldout_cases": 320,
            "corpus_documents": 420,
        }
        reordered = {key: manifest[key] for key in reversed(list(manifest))}
        first = hash_dataset_manifest(dict(manifest))
        second = hash_dataset_manifest(reordered)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 32)
        # A change to the data version changes the hash.
        changed = dict(manifest, raw_sha256="def456")
        self.assertNotEqual(hash_dataset_manifest(changed), first)


if __name__ == "__main__":
    unittest.main()
