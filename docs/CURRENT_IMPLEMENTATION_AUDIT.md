# HealthTrace 当前实现审计

审计基线：Git tag `medretrieve-v3-baseline`，源提交 `15cab76`。审计日期：2026-07-21。

状态定义：

- `IMPLEMENTED`：存在实际调用链，并至少有代码级验证。
- `PARTIAL`：存在代码，但缺少生产数据、完整集成、权限、真实模型或端到端测试。
- `DESIGN_ONLY`：只有目标设计或接口设想。
- `NOT_FOUND`：当前代码未发现实现。

## 审计结论

| 能力 | 状态 | 代码证据 | 限制 |
|---|---|---|---|
| FastAPI + Vue 工作台 | IMPLEMENTED | `backend/app.py`、`frontend/src/App.vue` | 当前是桌面 Web，不含移动端 |
| JWT 与会话隔离 | IMPLEMENTED | `backend/infra/auth.py`、`backend/chat/storage.py`、sessions 路由 | 隔离主体是 `user_id`，尚无 tenant/patient 维度 |
| LangGraph RAG 图 | IMPLEMENTED | `backend/rag/pipeline.py` 的 `build_rag_graph` 与子图 | 这是检索图，不是完整 Health Agent 咨询状态机 |
| Recent Messages | IMPLEMENTED | `backend/rag/context_compression.py`、`backend/chat/service.py` | 窗口策略较简单 |
| Persistent Note | PARTIAL | `backend/chat/service.py` 的笔记生成与注入 | LLM 摘要未结构化、无事实可信状态 |
| 语义/情景记忆 | PARTIAL | `backend/memory/service.py`、两类 Milvus collection | 语义事实由规则触发，未做用户确认、过期和撤回 |
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
| PostgreSQL 会话模型 | IMPLEMENTED | `users`、`chat_sessions`、`chat_messages`、`parent_chunks` | 无患者健康事实表 |
| Redis 缓存 | PARTIAL | `backend/infra/cache.py`、父块缓存 | 无完整任务状态、查询缓存治理和缓存一致性审计 |
| Neo4j 医疗 KG | PARTIAL | `backend/kg/client.py`、`search_medical_kg` | 查询工具已接入；实际图数据完整性取决于外部实例 |
| 医疗安全规则 | PARTIAL | `backend/medical_nlp/safety.py` | 规则型高风险与剂量提示，不是完整 Action Policy |
| PII 脱敏 | PARTIAL | 手机号、身份证、邮箱等规则 | 尚无 PII NER、全日志二次扫描和字段级加密 |
| 冲突提示 | PARTIAL | `backend/rag/conflict.py` | 能提示证据限制，尚无统一 Evidence State |
| 医院导航 | PARTIAL | `backend/care_navigation/` | 依赖定位授权和外部地图/搜索服务 |
| RAGCare 评测框架 | IMPLEMENTED | dataset、retrieval、metrics、judge、runner、测试 | 目标仓库不包含原始 420 条和正式结果 |
| 正式 RAGAS 代码 | IMPLEMENTED | `backend/evaluation/ragas_*`、评测脚本 | 当前目标仓库未发现可复现正式结果 |
| 运行时 RAGAS-lite | PARTIAL | `backend/chat/service.py` | 仅启发式监控，不是 RAGAS 模型评审 |
| MIRAGE 评测 | IMPLEMENTED | dataset、metrics、runner、CLI 与历史结果摘要 | 原始 7,663 条逐题结果不提交 Git |

## 当前真实上下文注入

当前每轮主要按以下顺序组装：

```text
System Prompt
→ Persistent Note
→ 相关语义/情景记忆
→ 定位授权与安全提示
→ 压缩后的近期对话
→ 当前用户问题
→ 工具返回的向量/KG/导航证据
```

这与目标 Health Agent 的差距是：尚未先规划 `required_patient_fields`，也没有从受控患者工具中查询已验证事实。

## 当前数据结构

PostgreSQL 当前只有用户、会话、消息和父块四类核心表。Milvus 使用统一 dense/sparse schema，并通过环境变量选择医疗问答、情景记忆和语义记忆 collection。当前没有 `document_domain`、`tenant_id`、`patient_id`、事实可信状态或患者专属 collection。

## 测试覆盖判断

迁移前基线为 35 项测试通过，覆盖 RAGCare 数据转换/指标、RAGAS 解析、MIRAGE 指标、OCR 路由和医院导航。缺口包括登录会话端到端、真实 Milvus/Neo4j 集成、患者数据隔离、安全攻击集、真实 OCR 准确率和 Health Agent 状态决策。

## 下一步

Phase 1 先做数据分域和 tenant/patient 权限边界；在此之前不得把当前系统描述为“完整个人健康管理 Agent”。
