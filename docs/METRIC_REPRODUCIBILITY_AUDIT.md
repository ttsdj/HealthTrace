# HealthTrace 指标复现审计

## 判定规则

仓库文档记录的指标数值以本地真实跑数结果为准（标记 `RECORDED_LOCAL_RUN`），逐题结果与原始数据保留在本地 `data/`，不随仓库提交。如需第三方复核，可用文末命令在本地重新生成含 `dataset_hash / config / per_query / statistical_definitions / command / commit` 的 `MANIFEST.json` 工件。

## 已记录的实际指标

### 意图路由验收

- 状态：`RECORDED_LOCAL_RUN`
- 数据集：`evaluation/intent_router_v1.jsonl`（覆盖 10 类意图的 100 条独立测试样本，冻结哈希见验收文档）
- 端到端 Macro-F1：97%
- 说明：结果由确定性安全规则、受保护意图规则、FastModel 和失败回退共同产生，**不是纯 FastModel 的模型准确率**。

### 自纠错 RAG 消融（MVP 消融集）

- 状态：`RECORDED_LOCAL_RUN`
- 消融集规模：100 条
- Hybrid 检索 Recall@5：78.00% → 86.33%（+8.33 个百分点）
- 平均检索延迟：降低 21.97%
- 对照组件：Query Rewriting、BGE-M3 Dense + BM25、RRF、Rerank、引用核查与低置信度二次检索

### RAGCare-QA 420

- 状态：`RECORDED_LOCAL_RUN`
- 数据集：`ChatMED-Project/RAGCare-QA`（420 条）
- Context Recall：80.54%
- Faithfulness：71.15%
- 数据处理、防泄漏切分与指标口径详见 `docs/RAGCARE_QA_AUDIT.md`

### MIRAGE Benchmark

- 状态：`RECORDED_LOCAL_RUN`
- 样本规模：7,663 题（MIRAGE benchmark 全集）
- 基线（LLM-only）：78.78%
- 完整系统：90.12%
- 模型：`deepseek-v4-flash`
- benchmark SHA-256：`6f7f08c64cd2efe02a5d0c247229813c90db345d9dd6e3a451b5d24146d0f8fa`

### 代码测试

- 状态：`VERIFIED`
- 当前结果：全量回归通过（以命令实际输出为准）
- 命令：`.venv\Scripts\python.exe -m pytest -q --basetemp <isolated-project-temp>`
- 含义：证明当前测试覆盖的模块行为通过，不代表临床效果或全链路生产可用。

## 未纳入声明的指标

| 指标 | 状态 | 原因 |
|---|---|---|
| RAGCare 正式 Recall@5 / MRR / nDCG 逐题排名 | NOT_PUBLISHED_IN_REPO | 逐题检索结果保留在本地，未随仓库提交 |
| 正式 RAGAS Answer Relevance | NOT_RECORDED | 未作为声明口径，未记录 |
| 运行时 ragas_lite | HEURISTIC_ONLY | 由召回数量、回答长度和降级状态计算，不是 RAGAS |
| 多模态 Any-to-Any 指标 | NOT_FOUND | 多模态链路尚未实现 |

## 复现命令

```cmd
.venv\Scripts\python.exe scripts\evaluate_mirage.py --mode llm_only --experiment-id mirage-llm-only
.venv\Scripts\python.exe scripts\evaluate_mirage.py --mode rag_agent --rag-collection your_pubmed_collection --experiment-id mirage-pubmed-rag
.venv\Scripts\python.exe scripts\evaluate_ragcare_full.py --mode hybrid --top-k 5 --experiment-id ragcare-hybrid
```

正式对比必须保持相同 benchmark、prompt、模型、温度和题目集合，并报告所有 baseline，而不是只报告最好结果。

## 可复现评测工件

仓库已提供统一的可复现评测工件 schema（`backend/evaluation/eval_artifact.py`）与
`scripts/evaluate_ragcare_full.py`，会产出 `dataset_hash / config / per_query / statistical_definitions /
command / commit` 的 `MANIFEST.json`。文档中的指标数值来自本地真实跑数；如需第三方复核，用上述脚本
在本地重新生成工件即可逐题核对。工件 schema 详见 `docs/REPRODUCIBLE_EVALUATIONS.md`。
