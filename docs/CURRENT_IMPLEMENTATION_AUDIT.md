# HealthTrace 当前实现审计

审计基线：Git tag `medretrieve-v3-baseline`，源提交 `15cab76`。最近更新：2026-07-22。

状态定义：

- `IMPLEMENTED`：存在实际调用链，并至少有代码级验证。
- `PARTIAL`：存在代码，但缺少生产数据、完整集成、权限、真实模型或端到端测试。
- `DESIGN_ONLY`：只有目标设计或接口设想。
- `NOT_FOUND`：当前代码未发现实现。

## 审计结论

| 能力 | 状态 | 代码证据 | 限制 |
|---|---|---|---|
| FastAPI + Vue 工作台 | IMPLEMENTED | `backend/app.py`、`frontend/src/App.vue`、`HealthRecordWorkspace.vue` | 桌面与移动端核心布局已验证；不是原生 App |
| JWT 与会话隔离 | IMPLEMENTED | `backend/infra/auth.py`、`backend/patient/scope.py`、sessions 路由 | 每名现有用户迁移到独立 tenant/patient；尚未实现机构多成员租户 |
| 公共/患者文档分域 | IMPLEMENTED | `patient_documents.py`、`migrate_phase1_domains.py`、`smoke_patient_rag.py` | 已用本地 BGE-M3 验证上传、Hybrid 召回、聊天注入、跨患者隔离和删除；真实患者 PDF/OCR 质量仍需评测 |
| FHIR-like 患者事实 | IMPLEMENTED | `patient_facts`、`backend/patient/facts.py` | 当前是轻量 canonical schema，不是完整 FHIR Server |
| 文档候选事实审核 | IMPLEMENTED | `patient_fact_candidates`、`backend/patient/fact_candidates.py`、`HealthRecordWorkspace.vue` | 本地规则默认运行；外部 LLM 增强必须显式同意，仍需临床抽取 golden set |
| 患者健康时间轴 | IMPLEMENTED | `patient_timeline_events`、`get_patient_timeline` | 按 effective_at 排序；自然语言模糊时间抽取尚未实现 |
| Typed Patient Tools | IMPLEMENTED | `backend/patient/tools.py`、`patient/planner.py`、`patient/trends.py` | 白名单规则规划默认启用，可选受约束 LLM 规划并自动回退；趋势仅使用已核验 Observation |
| 长期健康任务 | IMPLEMENTED | `health_tasks`、`health_task_runs`、`health_notifications`、scheduler 与 API | 五类任务、确认、持久化入队、领取、退避重试、等待输入、周期摘要和站内通知已实现 |
| 外部通知 | PARTIAL | `health_notification_deliveries`、`backend/tasks/notifications.py` | 邮件/Webhook 适配、同意门槛、幂等和独立重试已实现；尚未用真实供应商执行验收 |
| LangGraph RAG 图 | IMPLEMENTED | `backend/rag/pipeline.py` 的 `build_rag_graph` 与子图 | 负责公共证据检索，作为咨询状态机内的检索能力运行 |
| 咨询 Orchestrator | IMPLEMENTED | `backend/agent/orchestrator.py`、`chat/service.py` | RECEIVE 到 COMPLETED 的八阶段轨迹、Evidence State、Action Policy、无证据/冲突/高风险策略和动态白名单 Patient Tool 已接入 |
| Recent Messages | IMPLEMENTED | `backend/rag/context_compression.py`、`backend/chat/service.py` | 窗口策略较简单 |
| Persistent Note | PARTIAL | `backend/chat/service.py` 的笔记生成与注入 | LLM 摘要未结构化、无事实可信状态 |
| 语义/情景记忆 | PARTIAL | `backend/memory/service.py`、两类 Milvus collection | 已强制 tenant/patient 过滤并标记未核验；仍缺过期、确认和撤回流程 |
| BGE-M3 Dense | IMPLEMENTED | `backend/indexing/embedding.py` | 首次加载模型成本较高，主要按 CPU 环境验证 |
| Milvus 原生 BM25 | IMPLEMENTED | `backend/indexing/milvus_client.py` 的 BM25 Function | 依赖 Milvus 2.5+ |
| Hybrid + RRF | IMPLEMENTED | `hybrid_retrieve` 与 `RRFRanker` | 生产权重仍需消融验证 |
| Reranker | PARTIAL | `backend/rag/utils.py` 与本地评测 reranker | 生产链路依赖外部 endpoint/key；未默认启用 |
| 检索降级 | IMPLEMENTED | `backend/rag/utils.py` | 顺序为 Hybrid → Dense → Sparse → No Evidence |
| Corrective RAG | IMPLEMENTED | 初检、grader、rewrite、HyDE/Step-back、重试节点 | grader 和重写质量依赖模型配置 |
| 复杂问题拆分 | IMPLEMENTED | LangGraph `Send` 并行子问题与 synthesis | 尚缺覆盖复杂医学问题的正式 Agent 评测 |
| 三级父子分块 | IMPLEMENTED | `backend/indexing/document_loader.py`、`parent_chunk_store.py` | 默认约 L1=2400、L2=1600、L3=800 字符；不是严格 token 计量 |
| 父块按阈值恢复 | IMPLEMENTED | `backend/rag/utils.py` | 默认同父命中阈值为 2，仍需数据驱动调参 |
| MinerU | PARTIAL | `backend/indexing/mineru_parser.py` | 可选 CLI/API token；超时后降级，非所有机器默认可用 |
| PyPDF/pypdfium2 fallback | IMPLEMENTED | `backend/indexing/document_loader.py` | 复杂表格结构可能丢失 |
| PaddleOCR / PP-Structure | PARTIAL | `backend/indexing/ocr.py` | 可选重依赖；测试主要验证路由与容错，不是临床字段准确率 |
| VLM fallback | NOT_FOUND | 无 VLM 调用实现 | 仅在目标架构中规划 |
| 多模态向量 | NOT_FOUND | 无视觉 collection 或跨模态 embedding | 4GB GPU 环境下延期 POC |
| PostgreSQL 数据模型 | IMPLEMENTED | 会话、文档、患者事实、时间轴、目标、任务运行和通知模型 | 任务领取使用 PostgreSQL `SKIP LOCKED`；字段加密与完整 FHIR 映射尚未实现 |
| Redis 缓存 | PARTIAL | `backend/infra/cache.py`、父块缓存 | 无完整任务状态、查询缓存治理和缓存一致性审计 |
| Neo4j 医疗 KG | PARTIAL | `backend/kg/client.py`、`search_medical_kg` | 查询工具已接入；实际图数据完整性取决于外部实例 |
| 医疗安全规则 | PARTIAL | `backend/medical_nlp/safety.py`、`backend/agent/orchestrator.py` | 高风险、缺失信息、无证据和冲突已进入 Action Policy；规则覆盖仍不是临床决策系统 |
| PII 脱敏 | PARTIAL | 手机号、身份证、邮箱等规则 | 尚无 PII NER、全日志二次扫描和字段级加密 |
| 冲突提示 | IMPLEMENTED | `backend/rag/conflict.py`、`backend/agent/orchestrator.py` | KG/向量冲突进入统一 `CONFLICTING` Evidence State 并强制披露；医学冲突检测仍以规则为主 |
| 医院导航 | PARTIAL | `backend/care_navigation/` | 依赖定位授权和外部地图/搜索服务 |
| RAGCare 评测框架 | IMPLEMENTED | dataset、retrieval、metrics、judge、runner、测试 | 目标仓库不包含原始 420 条和正式结果 |
| 正式 RAGAS 代码 | IMPLEMENTED | `backend/evaluation/ragas_*`、评测脚本 | 当前目标仓库未发现可复现正式结果 |
| 运行时 RAGAS-lite | PARTIAL | `backend/chat/service.py`、`backend/observability/service.py` | 已进入管理员聚合监控；仅启发式信号，不是 RAGAS 模型评审 |
| 全局可观测性 | IMPLEMENTED | `/observability/summary`、`ObservabilityWorkspace.vue` | 只输出聚合计数，不返回问题、回答或患者标识；尚未接 Prometheus/OpenTelemetry |
| Health Agent 策略评测 | IMPLEMENTED | `evaluation/healthtrace_agent_v1.jsonl`、`scripts/evaluate_healthtrace_agent.py` | 42 条人工规则用例当前 42/42；不是临床医学答案评测 |
| MIRAGE 评测 | IMPLEMENTED | dataset、metrics、runner、CLI 与历史结果摘要 | 原始 7,663 条逐题结果不提交 Git |

## 当前真实上下文注入

当前每轮主要按以下顺序组装：

```text
System Prompt
→ Persistent Note
→ 按问题最小化查询的已确认患者事实
→ 本人私有病历的 top-3 检索证据（明确标记为未结构化核验）
→ 相关语义/情景记忆
→ 定位授权与安全提示
→ 压缩后的近期对话
→ 当前用户问题
→ 工具返回的向量/KG/导航证据
```

当前已有规则版 `required_patient_fields`、最小化患者上下文查询、确定性高风险旁路、统一 Evidence State 和 Action Policy。患者上下文由白名单工具规划器选择；默认规则模式，可切换受约束 LLM，任何解析/超时失败都回退规则。Observation 趋势按患者 scope、核验状态和临床时间计算。

## 当前数据结构

PostgreSQL 已增加 tenant、patient、document、fact candidate、FHIR-like fact、timeline、health task/run 等表；父块增加 document domain 与患者 scope。Milvus 新增独立 `patient_record` collection，患者检索必须带 tenant/patient 过滤。旧公共 collection 不做破坏性改造。候选事实不会直接成为权威事实，必须经患者确认。

## 测试覆盖判断

迁移前基线为 35 项，Phase 0 为 39 项；当前为 90 项通过。新增覆盖四阶段加法迁移、跨患者数据/API 隔离、八阶段咨询状态机、Agent 策略评测、Observation 趋势、Patient Tool fallback、聚合可观测性、任务幂等、外部通知同意与独立重试。2026-07-22 已在真实旧 PostgreSQL/Milvus 上完成四阶段迁移、备份校验、独立患者 collection、本地 BGE-M3 患者文档和候选事实冒烟验收。

## 下一步

下一步优先建设患者文档检索 golden set、临床审核安全攻击集、真实通知供应商验收、OpenTelemetry 指标出口和多成员 tenant 权限；在完成临床数据验证前仍不得描述为临床诊疗系统。
