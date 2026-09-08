# HealthTrace 指标复现审计

## 判定规则

一个指标只有同时具备数据版本、实验配置、逐题或逐样本结果、统计口径、复现命令和 Git commit，才可标记为 `VERIFIED`。只有代码或 README 描述的数字均不得写入简历。

## 当前可验证证据

### 代码测试

- 状态：`VERIFIED`
- 当前结果：117 passed（本轮升级前基线为 111）
- 命令：`.venv\Scripts\python.exe -m pytest -q --basetemp <isolated-project-temp>`
- 含义：证明当前测试覆盖的模块行为通过，不代表临床效果或全链路生产可用。

### MIRAGE LLM-only

- 状态：`VERIFIED_LOCAL_ARTIFACT`
- 样本数：7,663
- Overall Accuracy：78.7812%
- Macro Accuracy：78.5602%
- Failure Rate：0%
- Mean Latency：0.769746 秒/题
- 模型：`deepseek-v4-flash`
- benchmark SHA-256：`6f7f08c64cd2efe02a5d0c247229813c90db345d9dd6e3a451b5d24146d0f8fa`

### MIRAGE + 小规模 PubMed RAG

- 状态：`VERIFIED_LOCAL_ARTIFACT`
- 样本数：7,663
- Corpus：MedRAG PubMed 抽样 300 条，切成 462 chunks
- Top-K：5
- Overall Accuracy：73.0132%
- Macro Accuracy：66.7463%
- Failure Rate：0%
- Mean Latency：2.242744 秒/题

该实验比 LLM-only 低 5.768 个百分点（overall），不能表述为 RAG 带来提升。合理解释是 corpus 只有 300 条，覆盖不足且会把无关上下文带入回答；它证明了全链路可运行，也暴露了“语料质量和覆盖比是否接 RAG 更重要”。

精简证据保存在 `docs/evidence/MIRAGE_VERIFIED_RESULTS.json`。逐题结果和原始 benchmark 因体积与许可证管理要求保留在本地 `data/`，不提交 Git。

## 当前不可作为简历数字

| 指标 | 状态 | 原因 |
|---|---|---|
| RAGCare Recall@5 / MRR | NOT_REPRODUCIBLE_HERE | 本地已有原始/处理后 420 条，但无正式逐题检索结果 |
| 正式 RAGAS 三项分数 | NOT_REPRODUCIBLE_HERE | 目标仓库无对应实验输出 |
| 运行时 ragas_lite | HEURISTIC_ONLY | 由召回数量、回答长度和降级状态计算，不是 RAGAS |
| 复杂回答延迟下降 21.97% | UNVERIFIED | 当前未发现完整前后配置和原始 latency 结果 |
| Hybrid RRF Recall@5 90.31% | UNVERIFIED | 当前未发现可对应的数据版本和逐题排名 |
| 上下文召回率 80.54% | UNVERIFIED | 指标定义和原始结果不完整 |
| 忠实度 71.15% | UNVERIFIED | Judge、prompt、样本和逐题结果不完整 |
| Query Rewrite Recall@5 90.00%→93.33% | UNVERIFIED | 缺少同数据同配置消融证据 |
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
command / commit` 的 `MANIFEST.json`。规则：**指标数字只从真实跑数生成的工件读取**；未跑数前，上表
"当前不可作为简历数字"的各项不动，不得用手填或推算数字代替工件。工件 schema 详见
`docs/REPRODUCIBLE_EVALUATIONS.md`。
