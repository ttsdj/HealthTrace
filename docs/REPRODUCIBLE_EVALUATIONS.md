# Reproducible HealthTrace Evaluations

The HealthTrace evaluation harness produces a **uniform, reproducible artifact**
(`MANIFEST.json`) for RAGCare, RAGAS, and MIRAGE runs, so that any metric number
that appears in the README or a resume can be traced back to a real execution rather
than being an unsubstantiated claim.

## The one rule

**A metric number may only be quoted if it was read from a real evaluation artifact
(`MANIFEST.json`) that was produced by actually running the corresponding runner.**
Numbers found only in prose, or described by code without a recorded artifact, are not
evidence and must not be written as verified results.

There is no pre-populated metric result in the repository. A `MANIFEST.json` is created
only by executing the runner; the schema and the runner code are committed, the results
are not (they live under the git-ignored `data/` directory).

## Artifact schema (`schema_version = "1.0"`)

Every reproducible artifact has the following top-level fields.

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | `string` | Artifact schema version (`1.0`). |
| `created_at` | `string` | ISO timestamp of when the artifact was written. |
| `dataset_hash` | `string` | Stable hash of the dataset version (provenance + content hash). |
| `config` | `object` | Full experiment config (model, temperature, top_k, mode, base_url, collection, seed, prompt version, ...). |
| `summary` | `object` | Macro-average summary metrics over per-question results (`cases`, `recall_at_5`, `mrr`, `ndcg_at_5`, ...). |
| `per_question` | `array` | One record per question: ranks, gold document ids, and per-question scores. |
| `statistical_definitions` | `object` | The exact definition/formula for each reported metric. |
| `command` | `string` | The exact command line that reproduced this artifact. |
| `commit` | `string` | Git commit (`git rev-parse HEAD`) that the artifact was produced from. |
| `notes` | `string` | Optional. Non-empty for a `--dry-run` schema-only artifact. |

An artifact is valid when `schema_version`, `dataset_hash`, `config`, `summary`,
`per_question` (a list), `command`, and `commit` are all present and non-empty
(`per_question` is a list). `backend/evaluation/eval_artifact.py` implements
`build_artifact` / `validate_artifact` / `write_artifact` for this.

## The four (five) verifiable criteria

The reproducibility audit requires that a number be `VERIFIED` only when all of the
following are captured together in a single artifact:

1. **Data version + hash** — which dataset/split, and its content `dataset_hash`.
2. **Experiment config** — model, temperature, top_k, mode, base_url, collection, seed.
3. **Per-question ranks/scores** — the `per_question` array, so any aggregate is
   recomputable from the raw rows.
4. **Statistical definitions** — how each metric (Recall@K, MRR, nDCG, accuracy, ...) is
   computed and how it is averaged (macro over queries).
5. **Reproduce command + git commit** — the exact CLI and source revision that produced it.

## How each suite produces and verifies numbers

### RAGCare-QA retrieval
- Runner: `.venv\Scripts\python.exe scripts\evaluate_ragcare_full.py --mode <dense|bm25|hybrid|hybrid_rerank> --top-k 5`
- Reuses `backend/evaluation/ragcare_retrieval.RagcareEvalStore` for retrieval and
  `backend/evaluation/ragcare_metrics.retrieval_metrics_at_k` for per-question
  Recall@K / MRR / nDCG — metrics are not re-implemented.
- Writes `config.json`, `per_case.jsonl`, `baseline_summary.json`, `report.md`, and a
  `MANIFEST.json` into `data/evaluations/ragcare/<experiment-id>/`.
- Recovery of retrieval-only metrics does not require an LLM; it needs only Milvus.

### RAGAS (context relevance / faithfulness / answer relevance)
- Runner: `.venv\Scripts\python.exe scripts\evaluate_ragas.py --source-experiment ...`
- Operates over a persisted RAGCare experiment, checkpointed per case.
- Emits a uniform `MANIFEST.json` (via `--artifact-out`, default
  `<output_dir>/MANIFEST.json`) on top of the existing `summary.json`.

### MIRAGE (overall / macro accuracy)
- Runner: `.venv\Scripts\python.exe scripts\evaluate_mirage.py --mode llm_only` /
  `--mode rag_agent --rag-collection ...`
- Deterministic answer extraction (`mirage_metrics.extract_choice`), overall + macro
  accuracy over datasets.
- Emits a uniform `MANIFEST.json` (via `--artifact-out`) on top of `summary.json`.

## Dry runs and empty artifacts

A `--dry-run` writes a schema-only `MANIFEST.json`: `per_question` is `[]`, every metric
in `summary` is `None`, and `notes` states that no retrieval/LLM was performed and no
metric numbers are present. A dry-run artifact validates the reproducibility contract
without pretending to be a measured result.

## Where results live

Results are written under the git-ignored `data/` directory (e.g.
`data/evaluations/<id>/`, `data/ragcare/evaluations/<id>/`,
`data/mirage/evaluations/<id>/`). They are intentionally **not committed** to git — the
committed deliverables are the runner code and the artifact structure.
