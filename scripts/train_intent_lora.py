"""Train a LoRA adapter on the intent-classification dataset.

This is a standalone training script: it is NOT imported by the app, so an
absent torch / peft / transformers here must not affect the application.  The
heavy imports therefore live inside ``main`` and are guarded; if any are
missing the script prints a clear message and returns a non-zero exit code.

The trained adapter is written under ``data/models/intent_lora/`` (gitignored).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.medical_nlp.intent_classifier import INTENT_LABELS


def _load_examples(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"dataset not found: {path}")
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _check_train_deps() -> list[str]:
    """Return a human-readable list of missing training dependencies."""
    import importlib.util

    missing = [name for name in ("torch", "transformers", "peft", "datasets") if importlib.util.find_spec(name) is None]
    return missing


def main() -> int:
    parser = argparse.ArgumentParser(description="Train a LoRA intent classifier.")
    parser.add_argument("--train-file", type=Path, default=Path("data/evaluations/intent_lora/v1/train.jsonl"))
    parser.add_argument("--eval-file", type=Path, default=Path("data/evaluations/intent_lora/v1/eval.jsonl"))
    parser.add_argument("--base-model", default="hfl/chinese-roberta-wwm-ext")
    parser.add_argument("--out", type=Path, default=Path("data/models/intent_lora/v1"))
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--max-seq-len", type=int, default=256)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    missing = _check_train_deps()
    if missing:
        print(
            "Training dependencies are not installed. Missing: "
            + ", ".join(missing)
            + ".\nInstall with: pip install torch transformers peft datasets",
            file=sys.stderr,
        )
        return 1

    # Everything below is only reached when torch/transformers/peft are present.
    import torch
    import torch.nn.functional as F  # noqa: F401  (kept for clarity)
    from datasets import Dataset
    from peft import LoraConfig
    from peft import get_peft_model
    from peft import PeftModel
    from transformers import AutoModelForSequenceClassification, AutoTokenizer, Trainer, TrainingArguments

    train_examples = _load_examples(args.train_file)
    eval_examples = _load_examples(args.eval_file)
    label2id = {label: i for i, label in enumerate(INTENT_LABELS)}

    tokenizer = AutoTokenizer.from_pretrained(args.base_model)

    def _tokenize(batch: dict) -> dict:
        texts = batch["query"]
        if isinstance(batch["query"], list):
            enc = tokenizer(
                texts,
                truncation=True,
                max_length=args.max_seq_len,
                padding="max_length",
            )
        else:
            enc = tokenizer(
                [texts],
                truncation=True,
                max_length=args.max_seq_len,
                padding="max_length",
            )
        enc["labels"] = [label2id[item] for item in batch["label"]]
        return enc

    train_ds = Dataset.from_list(train_examples).map(_tokenize, batched=True)
    eval_ds = Dataset.from_list(eval_examples).map(_tokenize, batched=True)

    base = AutoModelForSequenceClassification.from_pretrained(
        args.base_model,
        num_labels=len(INTENT_LABELS),
        id2label={i: label for i, label in enumerate(INTENT_LABELS)},
        label2id=label2id,
    )
    lora_config = LoraConfig(
        r=8,
        lora_alpha=16,
        target_modules=["query", "key", "value", "dense"],
        lora_dropout=0.1,
        bias="none",
        task_type="SEQ_CLS",
    )
    model = get_peft_model(base, lora_config)

    training_args = TrainingArguments(
        output_dir=str(args.out),
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.batch_size,
        num_train_epochs=args.epochs,
        learning_rate=args.lr,
        evaluation_strategy="epoch",
        save_strategy="epoch",
        load_best_model_at_end=True,
        metric_for_best_model="accuracy",
        logging_steps=10,
        seed=args.seed,
        report_to=[],
    )

    def _metrics(pred):
        logits, labels = pred
        preds = logits.argmax(axis=-1)
        import numpy as np

        accuracy = (preds == labels).mean()
        return {"accuracy": float(accuracy)}

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_ds,
        eval_dataset=eval_ds,
        compute_metrics=_metrics,
    )
    metrics = trainer.evaluate()
    trainer.train()
    final_metrics = trainer.evaluate()

    args.out.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(args.out))
    tokenizer.save_pretrained(str(args.out))
    adapter_info = {"base_model": args.base_model, "out": str(args.out), "labels": list(INTENT_LABELS)}
    (args.out / "adapter_config.json").write_text(
        json.dumps(adapter_info, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    report = {
        "train_count": len(train_examples),
        "eval_count": len(eval_examples),
        "pre_train_eval": metrics,
        "final_eval": final_metrics,
        "adapter_path": str(args.out),
        "base_model": args.base_model,
        "epochs": args.epochs,
        "learning_rate": args.lr,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
