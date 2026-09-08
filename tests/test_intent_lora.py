"""Tests for the optional BERT+LoRA intent-classification layer and wiring.

The LoRA classifier is a real, but optional, tier: when peft / the adapter /
weights are absent it must return ``None`` and route_intent must fall back to
the deterministic layer without crashing.  All tests either avoid heavy deps
entirely (by stubbing) or are guarded with ``pytest.importorskip`` so the suite
still runs when torch/peft are not installed.
"""

from __future__ import annotations

import pytest

from backend.agent import intent_router
from backend.agent.intent_router import Intent, _ModelRoute, route_intent
from backend.medical_nlp.intent_classifier import IntentClassifier, classify


# -- real classifier behaviour, no heavy deps required --------------------
def test_classifier_returns_none_when_not_configured():
    # No adapter configured -> disabled -> None (never raises, no peft needed).
    classifier = IntentClassifier(adapter_path="")
    assert classifier.classify("高血压是什么") is None


def test_classifier_returns_none_when_peft_or_adapter_absent():
    # An adapter path is configured but peft/weights are not installed, so the
    # lazy load fails safely and classify degrades to None.
    classifier = IntentClassifier(adapter_path="data/models/intent_lora/does-not-exist")
    assert classifier.classify("高血压是什么") is None


def test_module_classify_proxy_returns_none_when_not_configured(monkeypatch):
    # ``classify`` is the module proxy: monkeypatch get_intent_classifier on the
    # same module object so an unconfigured (disabled) classifier is used.
    monkeypatch.setattr(
        intent_router.intent_classifier,
        "get_intent_classifier",
        lambda: IntentClassifier(adapter_path=""),
    )
    assert classify("高血压是什么") is None


# -- wiring: classifier None -> deterministic ----------------------------
def test_classifier_none_falls_back_to_deterministic(monkeypatch):
    monkeypatch.setenv("HEALTHTRACE_INTENT_ROUTER_MODE", "lora")
    monkeypatch.setattr(intent_router.intent_classifier, "classify", lambda _: None)
    result = route_intent("高血压是什么")
    assert result.primary_intent == Intent.GENERAL_MEDICAL_QA
    assert result.route_source == "deterministic"


# -- _lora_route maps label + confidence ---------------------------------
def test_lora_route_maps_label_and_confidence(monkeypatch):
    monkeypatch.setattr(intent_router.intent_classifier, "classify", lambda _: ("symptom_assessment", 0.91))
    route = intent_router._lora_route("最近身体不太舒服")
    assert route is not None
    assert route.primary_intent == Intent.SYMPTOM_ASSESSMENT
    assert route.confidence == 0.91
    assert route.reason_code == "lora"


def test_lora_route_unknown_label_is_unavailable(monkeypatch):
    monkeypatch.setattr(intent_router.intent_classifier, "classify", lambda _: ("not_a_real_intent", 0.5))
    assert intent_router._lora_route("随便问问") is None


# -- three-layer wiring: LoRA fires and overrides determinism ------------
def test_three_layer_wiring_route_source_lora(monkeypatch):
    monkeypatch.setenv("HEALTHTRACE_INTENT_ROUTER_MODE", "lora")
    monkeypatch.setattr(intent_router.intent_classifier, "classify", lambda _: ("symptom_assessment", 0.91))
    # Deterministic routing alone yields GENERAL_MEDICAL_QA here (no symptoms),
    # so a LoRA override proves the layer actually fired.
    result = route_intent("最近身体不太舒服")
    assert result.route_source == "lora"
    assert result.primary_intent == Intent.SYMPTOM_ASSESSMENT
    assert result.confidence == 0.91


# -- a LoRA guess can never grant write capability -----------------------
def test_lora_guess_cannot_grant_write(monkeypatch):
    monkeypatch.setenv("HEALTHTRACE_INTENT_ROUTER_MODE", "lora")
    monkeypatch.setattr(
        intent_router,
        "_lora_route",
        lambda _: _ModelRoute(
            primary_intent=Intent.LIFESTYLE_GUIDANCE,
            complexity="simple",
            needs_patient_context=False,
            requires_write_confirmation=True,  # attempt to smuggle write capability
            confidence=0.8,
        ),
    )
    result = route_intent("忌口什么")
    assert result.route_source == "lora"
    assert result.primary_intent == Intent.LIFESTYLE_GUIDANCE
    assert result.requires_write_confirmation is False


# -- protected intents never reached a model layer -----------------------
def test_protected_intent_stays_deterministic_even_in_lora_mode(monkeypatch):
    monkeypatch.setenv("HEALTHTRACE_INTENT_ROUTER_MODE", "lora")
    # A protected intent must never reach the LoRA layer; make it explode if it
    # is called, mirroring the existing FastModel safety-preflight test.
    monkeypatch.setattr(
        intent_router,
        "_lora_route",
        lambda _: (_ for _ in ()).throw(AssertionError("LoRA must not see protected input")),
    )
    result = route_intent("提醒我明天测血压")
    assert result.primary_intent == Intent.HEALTH_TASK
    assert result.route_source == "deterministic"


# -- a real-inference test, valid only if peft is installed --------------
def test_inference_requires_peft_skipped_otherwise():
    pytest.importorskip("peft")
    pytest.importorskip("transformers")
    pytest.importorskip("torch")
    # Reaching here means peft/transformers/torch exist; with no adapter the
    # classifier still degrades to None rather than raising.
    assert IntentClassifier(adapter_path="").classify("高血压是什么") is None
