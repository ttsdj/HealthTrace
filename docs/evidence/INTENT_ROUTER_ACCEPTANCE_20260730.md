# Intent Router Acceptance — 2026-07-30

## Scope

This acceptance run evaluates the end-to-end HealthTrace intent router: deterministic safety preflight, protected-intent rules, FastModel JSON/Pydantic routing for eligible normal requests, and deterministic fallback when the model is unavailable or invalid.

Dataset: `evaluation/intent_router_v1.jsonl`
SHA-256: `6516c980adf9687eb435eec539a61d65652ed3fc6c6039e472a5bae21657acae`

## Reported result

On the frozen dataset of 100 de-identified test cases covering 10 intent strata, the end-to-end fault-tolerant router reached **97% Macro-F1**. This is the combined outcome of deterministic safety rules, protected-intent rules, FastModel routing, and fallback — not a pure FastModel score.

## Real FastModel run

Command:

```powershell
python scripts/evaluate_intent_router.py --router-mode fastmodel
```

Route sources were:

- deterministic safety: high-risk cases (model deliberately bypassed);
- deterministic protected rules: medication, report, patient-record, timeline, and health-task cases;
- FastModel JSON/Pydantic output: eligible cases;
- deterministic fallback after model timeout/error: eligible cases.

The metrics above describe the fault-tolerant router, not a pure FastModel score. The fallback rate is an operational signal: before enabling `HEALTHTRACE_INTENT_ROUTER_MODE=fastmodel` in production, the provider timeout/error rate should be reduced or the FastModel path should run in shadow mode.

## Dataset construction

The dataset is synthetic and de-identified. It has ten equal strata: urgent care, symptom assessment, medication safety, report interpretation, patient-record query, timeline/trend, lifestyle guidance, care navigation, health task, and general medical QA. Each record includes the expected primary intent, high-risk label, and whether a write action is expected. The manifest locks the exact file hash, and the evaluator rejects a changed file.

One intentionally retained hard case (`report-05`, “转氨酶升高”) is labelled report interpretation but conservatively routes to general medical QA without an explicit report carrier. This keeps the conservative routing visible instead of hiding the ambiguity.
