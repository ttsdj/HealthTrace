# HealthTrace 当前实现审计

审计基线：Git tag `healthtrace-reversible-incremental-v1`，源提交 `ed9d131`。最近更新：2026-07-30。

状态定义：

- `IMPLEMENTED`：存在实际调用链，并至少有代码级验证。
- `PARTIAL`：存在代码，但缺少生产数据、完整集成、权限、真实模型或端到端测试。
- `DESIGN_ONLY`：只有目标设计或接口设想。
- `NOT_FOUND`：当前代码未发现实现。

## 审计结论

| 能力 | 状态 | 代码证据 | 限制 |
|---|---|---|---|
| FastAPI + Vue 工作台 | IMPLEMENTED | `backend/app.py`、`frontend/src/App.vue`、`HealthRecordWorkspace.vue` | 桌面与移动端核心布局已验证；不是原生 App |
| JWT、tenant 与患者授权 | IMPLEMENTED | `backend/infra/auth.py`、`backend/patient/scope.py`、`access_control.py` | 支持多成员 tenant 与患者级 read/write/manage 授权；真实机构身份源和 SSO 尚未接入 |
| 私密扩展字段加密 | IMPLEMENTED | `backend/security/crypto.py`、`patient_sensitive_records` | AES-256-GCM 信封加密已验证；生产 KMS、密钥轮换和透明数据库加密仍属于部署责任 |
| Metadata-only 审计 | IMPLEMENTED | `backend/security/middleware.py`、`audit_events` | 记录路由、身份、scope、状态和延迟，不记录正文；集中日志留存策略尚需部署配置 |
| 公共/患者文档分域 | IMPLEMENTED | `patient_documents.py`、`migrate_phase1_domains.py`、`smoke_patient_rag.py` | 已用本地 BGE-M3 验证上传、Hybrid 召回、聊天注入、跨患者隔离和删除；真实患者 PDF/OCR 质量仍需评测 |
| FHIR-like 患者事实 | IMPLEMENTED | `patient_facts`、`backend/patient/facts.py` | 当前是轻量 canonical schema，不是完整 FHIR Server |
| 文档候选事实审核 | IMPLEMENTED | `patient_fact_candidates`、`backend/patient/fact_candidates.py`、`HealthRecordWorkspace.vue` | 本地规则默认运行；外部 LLM 增强必须显式同意，仍需临床抽取 golden set |
| 患者健康时间轴 | IMPLEMENTED | `patient_timeline_events`、`get_patient_timeline` | 按 effective_at 排序；自然语言模糊时间抽取尚未实现 |
| Typed Patient Tools | IMPLEMENTED | `backend/patient/tools.py`、`patient/planner.py`、`patient/trends.py` | 白名单规则规划默认启用，可选受约束 LLM 规划并自动回退；趋势仅使用已核验 Observation |
| 长期健康任务 | IMPLEMENTED | `health_tasks`、`health_task_runs`、`health_notifications`、scheduler 与 API | 五类任务、确认、持久化入队、领取、退避重试、等待输入、周期摘要和站内通知已实现 |
| 外部通知 | PARTIAL | `health_notification_deliveries`、`backend/tasks/notifications.py` | 邮件/Webhook 适配、同意门槛、幂等和独立重试已实现；尚未用真实供应商执行验收 |
| LangGraph RAG 图 | IMPLEMENTED | `backend/rag/pipeline.py` 的 `build_rag_graph` 与子图 | 负责公共证据检索，作为咨询状态机内的检索能力运行 |
| Scope→Topic→Document 漏斗检索 | IMPLEMENTED | `backend/rag/funnel.py`、`backend/rag/pipeline.py` | 按患者标记选择公共/患者收集域、复用药学主题词收窄、再做文档级候选与叶子块检索；记录 `funnel_trace` 并逐级降级 |
| 咨询 Orchestrator | IMPLEMENTED | `backend/agent/orchestrator.py`、`chat/service.py` | RECEIVE 到 COMPLETED 的八阶段轨迹、Evidence State、Action Policy、无证据/冲突/高风险策略和动态白名单 Patient Tool 已接入 |
| 三层意图路由（规则→LoRA-BERT→LLM） | IMPLEMENTED | `backend/medical_nlp/intent_classifier.py`、`backend/agent/intent_router.py` | 规则层确定性优先；LoRA-BERT 为可选层，默认关闭且无 adapter/peft 时自动回退规则；模型层永不授予写能力 |
| Recent Messages | IMPLEMENTED | `backend/rag/context_compression.py`、`backend/chat/service.py` | 窗口策略较简单 |
| Persistent Note | PARTIAL | `backend/chat/service.py` 的笔记生成与注入 | LLM 摘要未结构化、无事实可信状态 |
| 语义/情景记忆 | PARTIAL | `backend/memory/service.py`、两类 Milvus collection | 已强制 tenant/patient 过滤并标记未核验；仍缺过期、确认和撤回流程 |
| BGE-M3 Dense | IMPLEMENTED | `backend/indexing/embedding.py` | 首次加载模型成本较高，主要按 CPU 环境验证 |
| Milvus 原生 BM25 | IMPLEMENTED | `backend/indexing/milvus_client.py` 的 BM25 Function | 依赖 Milvus 2.5+ |
| Hybrid + RRF | IMPLEMENTED | `hybrid_retrieve` 与 `RRFRanker` | 生产权重仍需消融验证 |
| Reranker | PARTIAL | `backend/rag/utils.py` 与本地评测 reranker | 生产链路依赖外部 endpoint/key；未默认启用 |
| 检索降级 | IMPLEMENTED | `backend/rag/utils.py` | 顺序为 Hybrid → Dense → Sparse → No Evidence |
| Corrective RAG | IMPLEMENTED | 初检、grader、rewrite、HyDE/Step-back、重试节点 | grader 和重写质量依赖模型配置 |
| 复杂问题拆分 | IMPLEMENTED | LangGraph `Send` 并行子问题与 synthesis | 已增加全局分支舱壁、排队超时、错误隔离和 partial synthesis；尚缺下游取消传播、负载验证和正式复杂医学问题评测 |
| 三级父子分块 | IMPLEMENTED | `backend/indexing/document_loader.py`、`parent_chunk_store.py` | 默认约 L1=2400、L2=1600、L3=800 字符；不是严格 token 计量 |
| 父块按阈值恢复 | IMPLEMENTED | `backend/rag/utils.py` | 默认同父命中阈值为 2，仍需数据驱动调参 |
| MinerU | PARTIAL | `backend/indexing/mineru_parser.py` | 可选 CLI/API token；超时后降级，非所有机器默认可用 |
| PyPDF/pypdfium2 fallback | IMPLEMENTED | `backend/indexing/document_loader.py` | 复杂表格结构可能丢失 |
| PaddleOCR / PP-Structure | PARTIAL | `backend/indexing/ocr.py` | 可选重依赖；测试主要验证路由与容错，不是临床字段准确率 |
| VLM fallback | NOT_FOUND | 无 VLM 调用实现 | 仅在目标架构中规划 |
| 多模态向量 | NOT_FOUND | 无视觉 collection 或跨模态 embedding | 4GB GPU 环境下延期 POC |
| PostgreSQL 数据模型 | IMPLEMENTED | 会话、文档、患者事实、时间轴、目标、任务、授权、审计与 golden review 模型 | 持久任务使用 `SKIP LOCKED`；当前是 FHIR-like schema，不是完整 FHIR Server |
| 通用持久后台任务 | IMPLEMENTED | `backend/jobs/queue.py`、`worker.py`、`background_jobs` | 文档、患者索引和 Agent 策略评测具备幂等、并发、退避和 stale recovery；RAGCare/RAGAS/MIRAGE 尚未接入，且未使用独立分布式队列集群 |
| Redis 缓存 | PARTIAL | `backend/infra/cache.py`、父块缓存 | 无完整任务状态、查询缓存治理和缓存一致性审计 |
| Neo4j 医疗 KG | PARTIAL | `backend/kg/client.py`、`search_medical_kg` | 查询工具已接入；实际图数据完整性取决于外部实例 |
| Neo4j 患者时序健康图谱 | IMPLEMENTED | `backend/kg/patient_graph.py`、`backend/patient/facts.py` | 确认后事实异步镜像到 Patient→资源节点→TimelineEvent 链，按 effective_at 排序；默认关闭，Neo4j 不可用时患者事实入库不受影响 |
| 医疗安全规则 | PARTIAL | `backend/medical_nlp/safety.py`、`backend/agent/orchestrator.py` | 高风险、缺失信息、无证据和冲突已进入 Action Policy；规则覆盖仍不是临床决策系统 |
| PII 脱敏 | PARTIAL | 手机号、身份证、邮箱规则与私密字段 AES-GCM 加密 | 尚无医学 PII NER、全日志二次扫描和集中式 KMS |
| 冲突提示 | IMPLEMENTED | `backend/rag/conflict.py`、`backend/agent/orchestrator.py` | KG/向量冲突进入统一 `CONFLICTING` Evidence State 并强制披露；医学冲突检测仍以规则为主 |
| 医院导航 | PARTIAL | `backend/care_navigation/` | 依赖定位授权和外部地图/搜索服务 |
| RAGCare 评测框架 | IMPLEMENTED | dataset、retrieval、metrics、judge、runner、测试 | 420 条样本防泄漏数据处理完成；实际指标 Context Recall 80.54%、Faithfulness 71.15% 记录于指标复现审计，逐题工件保留在本地 |
| 正式 RAGAS 代码 | IMPLEMENTED | `backend/evaluation/ragas_*`、评测脚本 | 实际指标 Context Recall 80.54%、Faithfulness 71.15% 已记录于指标复现审计；逐题输出保留在本地 |
| 运行时 RAGAS-lite | PARTIAL | `backend/chat/service.py`、`backend/observability/service.py` | 已进入管理员聚合监控；仅启发式信号，不是 RAGAS 模型评审 |
| 全局可观测性 | IMPLEMENTED | `/observability/summary`、`/alerts`、`/metrics`、`metrics.py`、`telemetry.py` | Prometheus 指标已提供，OTLP 按配置启用；当前没有随仓库部署 Grafana/Collector |
| Golden 审核门禁 | IMPLEMENTED | `golden_review.py`、`golden_evaluation.py`、`golden_evaluation_*` 表 | 双人独立审核与 clinician 门禁已实现；当前全部用例均为 draft，尚未获得临床批准 |
| Health Agent 策略评测 | IMPLEMENTED | `evaluation/healthtrace_agent_v1.jsonl`、`scripts/evaluate_healthtrace_agent.py` | 确定性工程用例当前全部通过；不是临床医学答案评测 |
| Liveness/readiness 与容器发布 | IMPLEMENTED | `/health/live`、`/health/ready`、`Dockerfile`、`container.yml` | 配置和自动化已完成；仍需在目标云平台完成真实发布与恢复演练 |
| MIRAGE 评测 | IMPLEMENTED | dataset、metrics、runner、CLI 与历史结果摘要 | 实际指标 78.78% → 90.12% 记录于指标复现审计；原始逐题结果不提交 Git |
| 请求级运行上下文隔离 | IMPLEMENTED | `rag_context.py`、`streaming.py`、`knowledge.py` 的 `ContextVar` | 已覆盖协程隔离与跨线程传播；仍需真实 SSE 并发和客户端取消压力测试 |
| 前端按需加载与语法高亮裁剪 | IMPLEMENTED | `App.vue`、`utils/markdown.ts` | 本地生产构建入口 JS 体积已大幅裁剪；不是网络性能或用户体验压测结果 |

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

PostgreSQL 已增加 tenant、patient、document、fact candidate、FHIR-like fact、timeline、health task/run、成员授权、审计、持久任务与 golden review 表；父块增加 document domain 与患者 scope。Milvus 新增独立 `patient_record` collection，患者检索必须带 tenant/patient 过滤。旧公共 collection 不做破坏性改造。候选事实不会直接成为权威事实，必须经患者确认。

## 测试覆盖判断

迁移前基线规模较小；最近一次在项目内隔离临时目录重新验证为全量通过。新增覆盖六阶段加法迁移、跨患者数据/API 隔离、成员授权与加密、持久任务故障恢复、Golden 审核门禁、八阶段咨询状态机、Agent 策略评测、Observation 趋势、Patient Tool fallback、Prometheus 聚合监控、任务幂等、外部通知同意与独立重试，以及请求级 RAG/SSE 上下文隔离、LLM 前脱敏集成和复杂 Send 分支舱壁/错误合成。2026-07-24 已在真实旧 PostgreSQL 上完成 Phase 5/6 迁移，迁移前备份和恢复目录校验通过。

## 下一步

下一步不再以增加接口数量为目标，而是完成外部验收：临床人员审核患者文档/安全/趋势 golden set，邮件与 Webhook 供应商沙箱验证，生产 KMS 与备份恢复演练，以及目标平台的 Prometheus/OTLP 看板和发布回滚演练。在完成临床数据验证前仍不得描述为临床诊疗系统。
