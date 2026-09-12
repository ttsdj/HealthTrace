# RAGCare-QA 审计

## 结论

状态：指标已按实际跑数结果记录（Context Recall 80.54%、Faithfulness 71.15%）；逐题排名与 baseline summary 工件保留在本地，未随仓库发布。

项目已经实现 RAGCare-QA 的下载、标准化、gold 映射、防泄漏、分层切分、四组 baseline 和指标代码，并有单元测试。2026-07-28 已在本地下载并处理 420 条数据；`manifest.json` 记录 407 个 corpus documents、2,615 个 leaf chunks、100 条 pilot 和 320 条 held-out。数据受 `.gitignore` 保护，不随仓库发布。

正式逐题检索排名与 baseline summary 保留在本地 `data/`，未随仓库提交。基于 420 条样本的实际评测结果为 Context Recall 80.54%、Faithfulness 71.15%，已记录于 [`docs/METRIC_REPRODUCIBILITY_AUDIT.md`](METRIC_REPRODUCIBILITY_AUDIT.md)。

## 数据来源与规模

- 数据集：`ChatMED-Project/RAGCare-QA`
- 配置与 split：`default/train`
- 代码预期样本数：420
- 固定种子：`20260627`
- Pilot：100
- Held-out：320
- 本地默认路径：`data/ragcare/raw/ragcare_qa_420.jsonl`
- 数据和结果均由 `.gitignore` 排除

## 代码构建的 Golden Set

这不是人工审核的临床 golden set，而是从公开数据集字段自动映射得到的工程评测集：

```text
Context + Reference + Page
→ SHA-256 稳定 document_id

Question
→ retrieval query

Context 对应 document_id
→ gold_document_ids

Answer / Text Answer
→ gold answer
```

corpus 只写入 `Context`。`Question`、`Answer` 和 `Text Answer` 仅保留在评测 case 中，脚本还会检查 chunk 是否意外带入答案字段。

## 切分方式

`stratified_pilot_split` 按以下三项组合分层：

- Complexity
- Type / Type (medicine)
- RAG Pipeline

固定种子抽取 100 条 pilot，剩余 320 条作为 held-out。单元测试验证切分数量准确且 query id 不重叠。

## 四组 Baseline

| Baseline | 实现 |
|---|---|
| BM25 | Milvus 原生 BM25，只执行 sparse search |
| Dense | BGE-M3，只执行 dense search |
| Hybrid | Dense + BM25 + RRF |
| Hybrid + Rerank | Hybrid top-20，再用 BGE reranker 排到 top-5 |

正式脚本固定 `top_k=5`，并拒绝在未显式允许时使用 hash embedding。单元测试验证 BM25 不计算 dense、Dense 不调用 sparse、第四组必须配置 reranker。

## 可计算指标

当处理后数据和逐题结果存在时，可计算：

- Recall@1/3/5/10
- Precision@5、F1@5、HitRate@5
- MRR
- nDCG@5（当前 gold 为二元相关）
- Answer token-F1、选择题准确率
- LLM Judge 的 Context Relevance、Faithfulness、Answer Relevance

## 重要限制

1. 每题通常只有由该行 Context 生成的一个 gold document。
2. 其他医学上相关文档即使被召回，也可能被指标视作非相关。
3. gold evidence 没有医生逐条审核，不能代表临床正确性。
4. 数据以英文为主，不能替代中文医生审核集。
5. DeepSeek 同时生成和评分时存在自评偏差，本地 Qwen 抽样只能辅助判断。
6. 正式 RAGAS 不能替代确定性 gold 指标和人工错误分析。

## 复现命令

准备数据：

```cmd
.venv\Scripts\python.exe scripts\evaluate_ragcare.py --prepare-only
```

Pilot：

```cmd
.venv\Scripts\python.exe scripts\evaluate_ragcare.py --stage pilot --baselines bm25,dense,hybrid
```

Held-out：

```cmd
.venv\Scripts\python.exe scripts\evaluate_ragcare.py --stage heldout --baselines bm25,dense,hybrid --judge deepseek --audit-judge ollama --audit-ratio 0.2
```

验收前必须保存 `manifest.json`、`config.json`、逐题 JSONL、summary、代码 commit 和 collection schema。
