# Phase 2 候选事实链路验收

日期：2026-07-22

## 目标

患者文档中的模型或规则抽取结果不能直接成为权威病历。Phase 2 引入候选事实层，让患者先核对资源类型、描述、结构化值和临床日期，再写入 FHIR-like 事实与时间轴。

## 数据流

```text
患者私有文档
  -> 本地规则抽取（默认）
  -> 外部 LLM 增强（仅显式同意后）
  -> patient_fact_candidates / pending
  -> 患者编辑并确认或拒绝
  -> patient_facts / user_confirmed
  -> patient_timeline_events / effective_at 排序
```

## 迁移验收

- 数据库：兼容模式下的现有 PostgreSQL。
- 迁移：`2026_07_22_phase2_fact_candidates`。
- 变更：仅新增 `patient_fact_candidates`。
- 幂等：第二次执行返回空变更集。
- 保护：迁移前已生成本地 PostgreSQL custom-format 备份，备份目录受 Git 忽略。
- 守恒：迁移前后 document、fact、timeline、task 行数一致。

## 功能验收

- 本地规则可抽取过敏、用药、诊断、操作、免疫、血压和血糖候选。
- 未确认候选不会生成正式事实或时间轴。
- 用户确认创建一条 `user_confirmed` 事实和对应时间轴事件，重复确认保持幂等。
- 另一 tenant/patient 无法读取或确认候选。
- 外部模型失败自动降级本地规则，并保存非敏感失败说明。
- 本地 BGE-M3 合成病历端到端测试通过，跨患者无向量与候选命中，清理后数据库无残留。

## 工程验证

- `python -m pytest -q`：全部通过。
- `npm run build`：通过。
- 桌面 1280×720、移动 390×844：无横向溢出。
- `/health`：核心 PostgreSQL、Redis、Milvus、LLM 正常；Neo4j 未启动时作为可选能力降级。

## 已知边界

- 当前是 FHIR-like canonical schema，不是完整 FHIR Server。
- 本地规则强调确定性和隐私，不代表临床级字段召回率。
- 外部 LLM 抽取仍需建立脱敏策略、临床 golden set 和字段级 Precision/Recall/F1。
- 患者时间轴使用已确认的 `effective_at`，不能用上传时间替代未知临床日期。
