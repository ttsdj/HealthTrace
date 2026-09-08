"""Expand the 10 intent strata of intent_router_v1 into a larger LoRA dataset.

Deterministic (seeded) template expansion so the generated data is reproducible.
Records carry an ``id`` and a ``provenance`` field describing how the query was
constructed, which satisfies the LoRA training pipeline's provenance contract.

Output goes to ``data/evaluations/intent_lora/<version>/train.jsonl`` and
``eval.jsonl``.  ``data/`` is gitignored, so this data is never committed.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.medical_nlp.intent import (
    DISEASE_HINTS,
    DRUG_SUFFIXES,
    INTENT_KEYWORDS,
    SYMPTOM_ALIASES,
)
from backend.medical_nlp.safety import DOSAGE_PATTERNS, HIGH_RISK_PATTERNS

# The strata are the frozen intent_router_v1 label space (== Intent values).
STRATA = [
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
]

_DRUGS = [
    "布洛芬",
    "阿莫西林",
    "对乙酰氨基酚",
    "奥美拉唑",
    "阿司匹林",
    "二甲双胍",
    "维生素C",
    "红霉素",
]


def _sentence(parts: list[str]) -> str:
    return "".join(parts)


def _symptom_terms() -> list[str]:
    """Canonical symptom terms plus aliases, for navigation-style queries."""
    terms: list[str] = []
    for canonical, aliases in SYMPTOM_ALIASES.items():
        terms.append(canonical)
        terms.extend(aliases[:2])
    return terms


# -- per-stratum generator functions -------------------------------------
# Each generator cross-multiplies a set of entity terms with a set of natural
# templates.  This yields a few thousand synthetic, de-identified examples
# spread across the 10 strata with an explicit provenance on every record.

_DEPARTMENTS = ["呼吸科", "心内科", "消化科", "神经内科", "皮肤科", "内分泌科", "骨科", "急诊科"]
_RECORD_TERMS = ["我的病史", "既往病史", "我的过敏", "我的用药", "我的病历", "我的档案", "我的检查历史", "我的住院记录"]
_PATIENT_ACTIONS = ["查一下", "我想看", "帮我找找", "告诉我", "调出来", "列出", "回顾一下", "检索"]
_METRICS = ["血压", "血糖", "体重", "心率", "体温", "血红蛋白", "胆固醇", "空腹血糖", "甘油三酯", "血尿酸"]
_LIFESTYLE_KEYWORDS = ["饮食", "忌口", "吃什么", "运动", "睡眠", "减重", "作息", "情绪", "抽烟", "喝酒"]
_HEALTH_TASKS = ["测血压", "测血糖", "测量体重", "吃药", "复诊", "打疫苗", "复查", "运动", "泡脚", "按摩"]
_GENERAL_QA_VERBS = ["是什么", "是什么病", "病因是什么", "怎么护理", "介绍下", "了解一下", "怎么预防", "危险吗", "能治好吗", "要注意什么"]


def _urgent_care(seed: random.Random) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for risk in HIGH_RISK_PATTERNS:
        for template in (
            f"我突然{risk}，怎么办",
            f"现在{risk}，应该立即怎么办",
            f"家人{risk}了，要马上送医吗",
            f"突然{risk}，还能等吗",
            f"{risk}非常难受，需要急诊吗",
            f"{risk}的时候我该立即做什么",
            f"我现在{risk}，很害怕",
            f"这种情况{risk}，要不要打急救电话",
        ):
            out.append((_sentence([template]), f"template:urgent_high_risk:{risk}"))
    return out


def _symptom_assessment(seed: random.Random) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for top_s, aliases in SYMPTOM_ALIASES.items():
        for a in aliases[:3]:
            for template in (
                f"我最近总是{a}，是怎么回事",
                f"{a}好几天了，需要看什么科",
                f"一直{a}会不会很严重",
                f"最近{a}，需要注意什么",
                f"我{a}，要不要去医院",
                f"为什么我会{a}",
                f"反复{a}是什么原因",
                f"我这样的{a}正常吗",
                f"从上个月开始{a}，没见好",
            ):
                out.append((_sentence([template]), f"template:symptom_assessment:{top_s}"))
    return out


def _medication_safety(seed: random.Random) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for drug in _DRUGS:
        for template in (
            f"{drug}一次吃多少",
            f"{drug}的剂量是多少",
            f"{drug}能和{drug}一起吃吗",
            f"我吃{drug}需要注意什么",
            f"{drug}吃多了怎么办",
            f"{drug}有副作用吗",
            f"饭后还是空腹吃{drug}",
            f"我吃了{drug}还是不舒服",
        ):
            out.append((_sentence([template]), f"template:medication_safety:{drug}"))
    for pattern in DOSAGE_PATTERNS:
        for word in ("这个药", "布洛芬", "阿莫西林", "我的药"):
            out.append((_sentence([f"{word}{pattern}"]), f"template:medication_dosage:{pattern}"))
    return out


def _report_interpretation(seed: random.Random) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    reports = ["体检报告", "检查报告", "化验报告", "影像检查报告", "尿检报告", "检查单", "检验单", "CT报告", "血常规报告"]
    issues = ["偏高", "偏低", "有箭头", "数值异常", "阳性", "结节", "阴影", "交界区", "不明原因升高"]
    for r in reports:
        for template in (
            f"我的{r}怎么看",
            f"帮我看看{r}结果",
            f"这份{r}上几项{seed.choice(issues)}要紧吗",
            f"看不懂我的{r}",
            f"{r}里的异常项严重吗",
            f"能把{r}解释给我听吗",
        ):
            out.append((_sentence([template]), f"template:report_interpretation:{r}"))
    for issue in issues:
        for template in (
            f"体检报告里有一项{issue}",
            f"我这次的报告显示{issue}",
            f"{issue}是什么意思",
            f"报告上{issue}怎么办",
        ):
            out.append((_sentence([template]), f"template:report_interpretation:{issue}"))
    return out


def _patient_record_query(seed: random.Random) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for term in _RECORD_TERMS:
        for action in _PATIENT_ACTIONS:
            out.append((_sentence([f"{action}{term}"]), f"template:patient_record:{term}:{action}"))
        for template in (
            f"{term}里有哪些记录",
            f"把{term}给我看看",
            f"我的{term}能查到吗",
        ):
            out.append((_sentence([template]), f"template:patient_record:{term}"))
    return out


def _timeline_trend(seed: random.Random) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for m in _METRICS:
        for template in (
            f"我最近的{m}趋势怎么样",
            f"我的{m}有什么变化",
            f"看看我{m}的历史记录",
            f"我的{m}是不是一直在升高",
            f"对比一下我的{m}前后变化",
            f"最近几个月我的{m}波动大吗",
            f"我想复盘一下我的{m}",
            f"{m}的曲线能画出来吗",
            f"把我的{m}按时间排一下",
            f"今年我的{m}变化趋势",
        ):
            out.append((_sentence([template]), f"template:timeline_trend:{m}"))
    return out


def _lifestyle_guidance(seed: random.Random) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for keyword in _LIFESTYLE_KEYWORDS:
        for template in (
            f"我这个情况{keyword}要注意什么",
            f"{keyword}上有什么建议",
            f"平时{keyword}怎么安排比较好",
            f"{keyword}方面我能做些什么",
            f"给我点{keyword}方面的建议",
        ):
            out.append((_sentence([template]), f"template:lifestyle:{keyword}"))
    for disease in DISEASE_HINTS:
        for verb in ("忌口什么", "能吃什么", "适合什么运动", "作息该怎么调整", "有什么生活建议"):
            out.append((_sentence([f"有{disease}的话我该{verb}"]), f"template:lifestyle:{disease}"))
    return out


def _care_navigation(seed: random.Random) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for keyword in ("附近医院", "哪里就医", "去哪里看", "去哪个科", "挂什么号", "哪个医院好", "就近看诊"):
        for template in (
            f"{keyword}好",
            f"请问{keyword}",
            f"还不知道{keyword}",
            f"帮我看看{keyword}",
            f"{keyword}需要预约吗",
        ):
            out.append((_sentence([template]), f"template:care_navigation:{keyword}"))
    for dept in _DEPARTMENTS:
        for template in (
            f"这个情况是不是挂{dept}",
            f"{dept}怎么预约",
            f"我应该挂{dept}吗",
            f"今天能挂{dept}吗",
        ):
            out.append((_sentence([template]), f"template:care_navigation:{dept}"))
    # Symptom-driven department queries: a common navigation ask.
    for symptom in _symptom_terms():
        for template in (
            f"{symptom}该挂什么科",
            f"{symptom}去哪个科看",
            f"{symptom}的话挂{seed.choice(_DEPARTMENTS)}合适吗",
        ):
            out.append((_sentence([template]), f"template:care_navigation:symptom:{symptom}"))
    return out


def _health_task(seed: random.Random) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    times = ["明天", "每天早上", "每晚", "每周五", "明天下午三点", "月底"]
    for action in _HEALTH_TASKS:
        for template in (
            f"提醒我明天{action}",
            f"创建任务：每天{action}",
            f"帮我记一下每周{action}",
            f"设置一个提醒，提醒我{action}",
            f"我要个每天{action}的打卡",
            f"记得叫我早晚{action}",
            f"设一个每日{action}的提醒",
            f"帮我安排定期{action}",
        ):
            out.append((_sentence([template]), f"template:health_task:{action}"))
        for time in times:
            out.append((_sentence([f"{time}提醒我{action}"]), f"template:health_task:{action}:{time}"))
    return out


def _general_medical_qa(seed: random.Random) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for disease in DISEASE_HINTS:
        for verb in _GENERAL_QA_VERBS:
            out.append((_sentence([f"{disease}{verb}"]), f"template:general_qa:{disease}"))
    for keyword in INTENT_KEYWORDS["drug_info"]:
        out.append((_sentence([f"这个{keyword}"]), f"template:general_qa:{keyword}"))
    return out


GENERATORS = {
    "urgent_care": _urgent_care,
    "symptom_assessment": _symptom_assessment,
    "medication_safety": _medication_safety,
    "report_interpretation": _report_interpretation,
    "patient_record_query": _patient_record_query,
    "timeline_trend": _timeline_trend,
    "lifestyle_guidance": _lifestyle_guidance,
    "care_navigation": _care_navigation,
    "health_task": _health_task,
    "general_medical_qa": _general_medical_qa,
}


def build_examples(seed_value: int, train_ratio: float = 0.85) -> list[dict]:
    seed = random.Random(seed_value)
    examples: list[dict] = []
    for stratum in STRATA:
        generated = GENERATORS[stratum](seed)
        for n, (query, provenance) in enumerate(generated):
            examples.append(
                {
                    "id": f"lora-{stratum}-{n:04d}",
                    "stratum": stratum,
                    "query": query.strip(),
                    "label": stratum,
                    "provenance": provenance,
                    "split": "train",  # assigned below
                }
            )
    # Assign split deterministically so train/eval are disjoint and stratified.
    split_perm = random.Random(seed_value + 1)
    for example in examples:
        example["split"] = "eval" if split_perm.random() > train_ratio else "train"
    # De-duplicate on (query) while keeping the first example of each group.
    seen: set[str] = set()
    deduped: list[dict] = []
    for example in examples:
        if example["query"] in seen:
            continue
        seen.add(example["query"])
        deduped.append(example)
    return deduped


def write_split(examples: list[dict], split: str, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [example for example in examples if example["split"] == split]
    with path.open("w", encoding="utf-8") as fh:
        for example in lines:
            fh.write(json.dumps(example, ensure_ascii=False) + "\n")
    return len(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a LoRA intent dataset.")
    parser.add_argument("--out-dir", type=Path, default=Path("data/evaluations/intent_lora"))
    parser.add_argument("--version", default="v1")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--train-ratio", type=float, default=0.85)
    args = parser.parse_args()

    examples = build_examples(args.seed, args.train_ratio)
    out_dir = args.out_dir / args.version
    train_path = out_dir / "train.jsonl"
    eval_path = out_dir / "eval.jsonl"

    train_count = write_split(examples, "train", train_path)
    eval_count = write_split(examples, "eval", eval_path)

    summary = {
        "total": len(examples),
        "train": train_count,
        "eval": eval_count,
        "out_dir": str(out_dir),
        "seed": args.seed,
        "strata": {stratum: {"count": sum(e["stratum"] == stratum for e in examples)} for stratum in STRATA},
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
