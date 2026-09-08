# HealthTrace

HealthTrace 是一个面向个人用户的健康档案与循证智能咨询系统。当前版本在可运行的医疗 RAG 基础上，提供公共/患者数据分域、患者文档检索、候选事实审核、FHIR-like 健康事实、时间轴、长期任务、知识图谱、安全边界和可复现评测。

> 本项目用于工程研究与医学知识辅助，不提供诊断、处方、药物剂量调整或急救决策，也不能替代医生。

## 3 分钟看懂项目（STAR）

### S - Situation

普通大模型用于健康咨询时容易出现答案缺少来源、患者资料混入公共知识、检索失败直接中断以及高风险问题缺乏边界等问题。个人健康资料还涉及身份隔离、可信状态、时间关系和删除生命周期，不能只作为普通 RAG 文档处理。

### T - Task

HealthTrace 的目标是建立一个可追溯、可降级、可评测的健康咨询系统：一方面从 Milvus、Neo4j 和医学文档获取公共证据；另一方面逐步将个人报告转成受权限保护、可验证的健康事实，并根据问题按需查询，而不是把完整病历交给模型。

### A - Action

当前调用链：

```text
用户问题
  -> FastAPI 鉴权与用户会话隔离
  -> 隐私脱敏、医疗风险与轻量意图识别
  -> 最小化患者事实 + 私有病历证据 + Recent Messages + Persistent Note + 语义/情景记忆
  -> Health Agent 状态机：接收、规划、患者上下文、信息检查、证据评级、动作决策、执行持久化
  -> 白名单 Patient Tool 规划：规则默认，受约束 LLM 可选，失败自动回退
  -> LangGraph 复杂度路由与纠错检索
  -> Milvus BGE-M3 Dense + 原生 BM25 + RRF
  -> 可选 Reranker、Neo4j KG 和医院导航工具
  -> 证据压缩、冲突提示和安全回答
  -> Vue 工作台展示引用、检索 Trace、Observation 趋势和聚合运行监控
```

工程策略：

- 检索按 Hybrid、Dense、Sparse、No Evidence 逐级降级。
- PDF 优先 MinerU，失败或超时后回退 PyPDF/pypdfium2，稀疏页面可继续进入 PaddleOCR。
- 大文档先写入首批页面或 chunk，使部分内容尽早可检索，剩余批次后台继续处理。
- PostgreSQL 保存用户、会话和父块；Redis 缓存父块与短期状态；Milvus 保存叶子块与记忆；Neo4j 是可选增强服务。
- 配置只从本地 `.env` 读取，代码和 Git 历史不保存真实密钥。
- tenant 成员与患者授权分开管理；私密扩展字段使用 AES-256-GCM 信封加密，访问仅记录元数据审计，不记录对话或病历正文。
- 公共知识和患者病历分别进入独立 Milvus collection，患者原文件按 tenant/patient/document 分域。
- 患者文档先生成待审核候选事实；只有用户确认后才写入 FHIR-like 事实与临床时间轴。
- 公共文档解析/删除、患者文档索引和 Agent 策略评测进入 PostgreSQL 持久任务队列，支持并发领取、幂等、指数退避和故障恢复；RAGCare、RAGAS 和 MIRAGE 正式实验仍由 CLI 执行。
- 长期任务先创建待确认草稿；确认后由 PostgreSQL 持久任务系统入队、领取、执行、退避重试，并生成站内通知。
- 邮件/Webhook 采用站内通知先提交、外部通道后投递；必须显式同意，失败独立重试且不回滚任务。
- 管理员可查看不含对话正文和患者标识的聚合可观测指标。
- `/observability/metrics` 暴露低基数 Prometheus 指标；OTLP 链路出口按配置启用，未安装扩展时显式降级而不阻断主服务。

### R - Result

当前已经完成并有代码链路：

- FastAPI、Vue 3、JWT 登录注册和按用户隔离的会话持久化。
- BGE-M3、Milvus 2.5+ 原生 BM25、Hybrid RRF、三级父子分块与检索降级。
- MinerU/PDF/OCR 解析降级、渐进式向量入库与批量 Milvus 写入。
- Neo4j 医疗知识图谱工具、语义/情景记忆、上下文压缩和医疗安全规则。
- tenant/patient 数据边界、患者私有 RAG、候选事实审核、FHIR-like 患者事实和来源可追溯时间轴。
- LangGraph 咨询状态机、统一 Evidence State/Action Policy、高风险 LLM 旁路、无证据拦截和工具调用审计。
- 健康目标、提醒/随访/测量/周期摘要/目标检查任务、持久化执行、指数退避重试和站内通知。
- 已验证 Observation 趋势、动态白名单 Patient Tool 规划及规划器失败回退。
- 管理员运行监控面板：Evidence State、检索降级、工具成功率与 P50/P95、任务重试和 ragas_lite 信号。
- 可选邮件/Webhook 通知适配器，具备显式同意、幂等、独立重试和站内 fallback。
- 多成员 tenant、患者级 read/write/manage 授权、敏感字段加密和 metadata-only 审计日志。
- PostgreSQL 持久后台任务队列，覆盖公共/患者文档、删除和正式 Agent 评测。
- Golden set 导入、双人独立审核、临床审核门禁与批准版本导出；当前内置 42 条仍处于待人工审核状态。
- Prometheus 指标、可选 OpenTelemetry、运行告警、liveness/readiness、容器构建与 GHCR 发布工作流。
- 42 条 Health Agent 策略集覆盖高风险、缺信息、证据源、工具路由、隐私和边界，当前 42/42 通过。
- 三层意图路由（确定性规则 → LoRA-BERT → 结构式 LLM）：LoRA-BERT 层为可选增强，未安装 `peft` 或未训练 adapter 时自动回退到规则层，任何模型层都不能授予写能力。
- 确认后的患者事实异步镜像到 Neo4j 时序健康图谱（`Patient`→`HAS_CONDITION/OBSERVED/TAKES/…`→`TimelineEvent` 链），支持纵向时间轴与关联查询；Neo4j 为可选增强，默认关闭且不可用时患者事实入库不受影响。
- Scope→Topic→Document 漏斗检索：按患者标记选择公共/患者收集域，复用药学主题词收窄，再进行文档级候选与叶子块检索，并记录 `funnel_trace`。
- RAGCare-QA、RAGAS 和 MIRAGE 评测代码，以及统一的可复现评测工件 schema（`dataset_hash/config/per_query/statistical_definitions/command/commit`）；精确实验数字及其可复现状态见指标审计。

尚未完成、不得对外宣称已实现：

- 短信、原生移动推送和外部邮件/Webhook 真实供应商验收。
- 临床人员批准的 Agent 安全集与患者 Observation 趋势 golden set；当前只完成审核工作流，不能把待审核用例称为临床 golden set。
- 多模态向量、Any-to-Any 检索和临床级医学影像理解。
- 指标数字未经真实跑数结果支撑前，不计入"已验证"；正式 RAGCare/RAGAS/MIRAGE 数字需通过 `scripts/evaluate_ragcare_full.py` 等生成工件后回填。

详细证据见 [`docs/CURRENT_IMPLEMENTATION_AUDIT.md`](docs/CURRENT_IMPLEMENTATION_AUDIT.md)、
[`docs/METRIC_REPRODUCIBILITY_AUDIT.md`](docs/METRIC_REPRODUCIBILITY_AUDIT.md) 和
[`docs/RESUME_CLAIM_AUDIT.md`](docs/RESUME_CLAIM_AUDIT.md)。

## 架构

```text
frontend/ Vue 3 + TypeScript + Pinia
              |
              v
backend/api/ FastAPI + JWT + SSE
              |
    +---------+----------+----------------+
    |                    |                |
backend/chat          backend/rag      backend/indexing
会话与上下文          LangGraph RAG    解析、分块、向量化
    |                    |                |
    +------ PostgreSQL / Redis -----------+
                         |
              Milvus / Neo4j / LLM
```

## 快速运行

### 1. 准备环境

需要 Python 3.12、Node.js、Docker Desktop。Docker 仅在 `managed` 模式下必须由本项目启动。Windows 新机器可直接运行：

```cmd
setup.bat
```

该脚本创建 `.venv`、安装后端与前端依赖、从 `.env.example` 生成本地 `.env`，并自动生成 JWT 与字段加密密钥。随后填写 LLM 配置即可。

手动安装方式：

```cmd
git clone <your-healthtrace-repository-url>
cd HealthTrace
python -m venv .venv
.venv\Scripts\python.exe -m pip install -U pip
.venv\Scripts\python.exe -m pip install -e ".[dev]"
cd frontend
npm install
cd ..
copy .env.example .env
```

如需本地 OCR：

```cmd
.venv\Scripts\python.exe -m pip install -e ".[ocr]"
```

### 2. 配置 `.env`

至少填写：

```env
LLM_API_KEY=your-key
BASE_URL=https://your-openai-compatible-endpoint/v1
MODEL=your-model
FAST_MODEL=your-fast-model
GRADE_MODEL=your-grade-model
JWT_SECRET_KEY=replace-with-a-long-random-value
HEALTHTRACE_FIELD_ENCRYPTION_KEY=replace-with-url-safe-base64-32-byte-key
```

基础设施模式：

```env
# 启动本仓库的 PostgreSQL、Redis、Milvus、MinIO 和 Attu
HEALTHTRACE_INFRA_MODE=managed

# 或复用 DATABASE_URL、REDIS_URL、MILVUS_HOST 等指向的已有服务
HEALTHTRACE_INFRA_MODE=external
```

### 3. 一行启动

```cmd
start.bat
```

默认地址：

| 服务 | 地址 |
|---|---|
| 前端 | http://127.0.0.1:3000 |
| FastAPI | http://127.0.0.1:8000 |
| API 文档 | http://127.0.0.1:8000/docs |
| 健康检查 | http://127.0.0.1:8000/health |
| Attu | http://127.0.0.1:8080 |

若 8000 或 3000 已被其他应用占用，启动脚本会自动尝试下一端口，并把 Vite 代理指向实际后端。

## 手动启动与验证

```cmd
docker compose up -d
.venv\Scripts\python.exe scripts\init_db_safe.py
.venv\Scripts\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

另开终端：

```cmd
cd frontend
npm run dev
```

验证：

```cmd
.venv\Scripts\python.exe -m pytest -q
npm --prefix frontend run build
.venv\Scripts\python.exe scripts\check_repo_safety.py
docker compose config
```

### 容器化应用栈

在完成 `.env` 配置后，可同时构建前端和后端应用镜像并启动基础设施：

```cmd
docker compose -f docker-compose.yml -f docker-compose.app.yml up -d --build
```

应用容器直接托管构建后的 Vue 页面，默认访问 `http://127.0.0.1:8000`。本地开发仍推荐使用 `start.bat`，便于前后端热更新。

既有部署按顺序执行加法迁移：

```cmd
.venv\Scripts\python.exe scripts\migrate_phase4_notifications.py apply
.venv\Scripts\python.exe scripts\migrate_phase5_access_security.py apply
.venv\Scripts\python.exe scripts\migrate_phase6_jobs_golden.py apply
```

生产启动前可运行：

```cmd
.venv\Scripts\python.exe scripts\production_preflight.py
.venv\Scripts\python.exe scripts\deployment_smoke.py --base-url http://127.0.0.1:8000
```

## 数据与安全

- 不提交 `.env`、API key、token、数据库密码和精确定位信息。
- 不提交 `data/`、`volumes/`、模型权重、上传文件和原始评测集。
- 技术 Markdown 可以进入 `docs/`；`docs/private/`、`docs/interview/` 和 `docs/local/` 永久忽略。
- Neo4j、地图和 Reranker 是可选增强能力，失败时不得阻断普通知识检索。
- 运行时 `ragas_lite` 是低成本启发式监控信号，不等于正式 RAGAS 评测结果。
- 没有 gold evidence 的数据集不得报告 Recall@K。
- Golden 数据必须经过独立审核门禁；仅导入或由规则生成的数据默认是 `draft`，不得作为临床准确性结论。
- 字段加密密钥不得随意更换；生产环境应交给密钥管理服务并建立轮换、备份和灾难恢复流程。

## 迭代路线

1. 由临床人员审核并冻结患者文档抽取、Agent 安全和 Observation 趋势 golden set。
2. 用经授权、去标识的纵向数据验证趋势、主动追问和证据冲突策略。
3. 完成邮件/Webhook 供应商沙箱验收，再扩展短信、移动推送和通知回执。
4. 接入集中式 KMS、日志平台、Prometheus/Grafana 与 OTLP Collector，完成恢复演练。
5. 在硬件或外部推理资源满足后开展多模态 POC，并持续做检索、生成、Agent、安全和性能消融。

## 存量知识更新

同一公共文档再次上传，或调用患者文档 `PUT` 接口时，HealthTrace 会先在 staging 完成解析和向量准备，再切换 active version。系统通过 chunk 内容指纹复用未变化的 BGE-M3 向量，只重新计算变化部分；任一步失败都会补偿恢复旧向量、父块和原文件。

```cmd
.venv\Scripts\python.exe scripts\migrate_phase7_incremental_documents.py status
.venv\Scripts\python.exe scripts\smoke_incremental_update.py
```

完整协议和 API 见 [`docs/INCREMENTAL_DOCUMENT_UPDATES.md`](docs/INCREMENTAL_DOCUMENT_UPDATES.md)。

## Reversible Incremental Updates

Incremental updates copy reusable vector values into fresh Milvus rows; they
never reuse a Milvus primary key. Superseded vectors and parent mappings are
retained for a configurable rollback window and purged asynchronously after
expiry. See
[`docs/REVERSIBLE_INCREMENTAL_UPDATES.md`](docs/REVERSIBLE_INCREMENTAL_UPDATES.md).

```cmd
.venv\Scripts\python.exe scripts\migrate_phase8_reversible_documents.py apply
.venv\Scripts\python.exe scripts\purge_expired_document_versions.py
```

## License

MIT
