"""BERT+LoRA intent-classification layer, safely optional.

This is the middle tier of the "rules -> LoRA-BERT -> LLM" routing stack.  It
wraps a small Chinese sequence-classification transformer with a PEFT/LoRA
adapter.  Every heavy dependency (torch / transformers / peft) is imported
lazily inside a method and guarded with try/except, so importing this module
never fails when those packages or the trained adapter are absent.  When they
are, ``classify`` returns ``None`` and callers fall through to the
deterministic rule layer.
"""

from __future__ import annotations

import os
import threading

# The classifier emits the same label space as the Intent enum.  ``intent_router``
# is responsible for mapping these strings to ``Intent`` values via an explicit
# alias map; here we only advertise the canonical label set so the label list
# for a model's ``id2label`` can be built deterministically.
INTENT_LABELS: tuple[str, ...] = (
    "urgent_care",
    "symptom_assessment",
    "medication_safety",
    "report_interpretation",
    "patient_record_query",
    "timeline_trend",
    "lifestyle_guidance",
    "care_navigation",
    "health_task",
    "general_medical_qa",
)

_INTENT_CLASSIFIER: IntentClassifier | None = None
_INTENT_CLASSIFIER_LOCK = threading.Lock()


def _model_name() -> str:
    return os.getenv("HEALTHTRACE_INTENT_LORA_MODEL", "hfl/chinese-roberta-wwm-ext")


def _adapter_path() -> str:
    return os.getenv("HEALTHTRACE_INTENT_LORA_ADAPTER", "").strip()


class IntentClassifier:
    """Wraps a base sequence-classification model plus a LoRA adapter.

    Construction is cheap: it merely captures configuration.  The heavy model /
    tokenizer / adapter are loaded lazily on first successful ``classify`` call.
    Any absence of dependencies, model weights or the adapter yields ``None``
    from ``classify`` rather than raising.
    """

    def __init__(self, model_name: str | None = None, adapter_path: str | None = None) -> None:
        self._model_name = model_name or _model_name()
        self._adapter_path = adapter_path if adapter_path is not None else _adapter_path()
        self._model = None
        self._tokenizer = None
        self._id2label: dict[int, str] = {}
        self._loaded = False
        self._load_succeeded = False

    # -- lazy loading ------------------------------------------------------
    def _load(self) -> bool:
        if self._loaded:
            return self._load_succeeded
        self._loaded = True
        if not self._adapter_path:
            # Disabled: no adapter configured, so we stay "unavailable".
            return False
        try:
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
            from peft import PeftModel  # type: ignore[import-not-found]
        except Exception:
            self._load_succeeded = False
            return False

        try:
            self._tokenizer = AutoTokenizer.from_pretrained(self._model_name)
            base = AutoModelForSequenceClassification.from_pretrained(
                self._model_name,
                num_labels=len(INTENT_LABELS),
                id2label={i: label for i, label in enumerate(INTENT_LABELS)},
                label2id={label: i for i, label in enumerate(INTENT_LABELS)},
            )
            self._model = PeftModel.from_pretrained(base, self._adapter_path)
            self._model.eval()
            self._id2label = self._model.config.id2label
            self._load_succeeded = bool(self._model is not None)
        except Exception:
            self._model = None
            self._tokenizer = None
            self._id2label = {}
            self._load_succeeded = False
        return self._load_succeeded

    # -- public API --------------------------------------------------------
    def classify(self, text: str) -> tuple[str, float] | None:
        """Return ``(label, confidence)`` or ``None`` when unavailable.

        Never raises: any failure (missing deps, missing adapter, decode error,
        model loading error, bad output) degrades to ``None`` so the caller can
        fall back to the deterministic rule layer.
        """
        try:
            if not self._adapter_path:
                return None
            if not self._load():
                return None
            if self._model is None or self._tokenizer is None:
                return None
            if text is None or not str(text).strip():
                return None

            inputs = self._tokenizer(
                str(text),
                truncation=True,
                max_length=256,
                return_tensors="pt",
            )
            with _torch_no_grad():
                logits = self._model(**inputs).logits
            probabilities = _softmax(_flatten(logits))
            idx = int(_argmax(probabilities))
            confidence = float(min(1.0, max(0.0, probabilities[idx])))
            label = self._id2label.get(idx)
            if label is None:
                return None
            return label, confidence
        except Exception:
            return None


def get_intent_classifier() -> IntentClassifier:
    """Return the process-wide classifier singleton, built lazily under a lock."""
    global _INTENT_CLASSIFIER
    if _INTENT_CLASSIFIER is None:
        with _INTENT_CLASSIFIER_LOCK:
            if _INTENT_CLASSIFIER is None:
                _INTENT_CLASSIFIER = IntentClassifier()
    return _INTENT_CLASSIFIER


def classify(text: str) -> tuple[str, float] | None:
    """Convenience proxy over the singleton."""
    return get_intent_classifier().classify(text)


def _torch_no_grad():
    import torch  # lazy; guarded by caller's try/except

    return torch.no_grad()


def _flatten(obj) -> list:
    import torch  # lazy

    if isinstance(obj, torch.Tensor):
        return obj.detach().cpu().tolist()
    return obj


def _argmax(values) -> int:
    return max(range(len(values)), key=values.__getitem__)


def _softmax(values) -> list[float]:
    import math  # lazy / dependency-free

    if not values:
        return []
    maximum = max(values)
    exps = [math.exp(v - maximum) for v in values]
    total = sum(exps)
    if total <= 0:
        return [1.0 / len(values)] * len(values)
    return [e / total for e in exps]
