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
- 公共知识和患者病历分别进入独立 Milvus collection，患者原文件按 tenant/patient/document 分域。
- 患者文档先生成待审核候选事实；只有用户确认后才写入 FHIR-like 事实与临床时间轴。
- 长期任务先创建待确认草稿；确认后由 PostgreSQL 持久队列入队、领取、执行、退避重试，并生成站内通知。
- 邮件/Webhook 采用站内通知先提交、外部通道后投递；必须显式同意，失败独立重试且不回滚任务。
- 管理员可查看不含对话正文和患者标识的聚合可观测指标。

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
- 42 条 Health Agent 策略集覆盖高风险、缺信息、证据源、工具路由、隐私和边界，当前 42/42 通过。
- RAGCare-QA、RAGAS 和 MIRAGE 评测代码；当前仓库测试为 90 项通过。

尚未完成、不得对外宣称已实现：

- 短信、原生移动推送、机构级多成员 tenant 权限和外部通道真实供应商验收。
- 临床人员审核的 Agent 安全集与患者 Observation 趋势 golden set。
- 多模态向量、Any-to-Any 检索和临床级医学影像理解。

详细证据见 `docs/CURRENT_IMPLEMENTATION_AUDIT.md` 和 `docs/METRIC_REPRODUCIBILITY_AUDIT.md`。

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

需要 Python 3.12、Node.js、Docker Desktop。Docker 仅在 `managed` 模式下必须由本项目启动。

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

Phase 4 外部通知表为加法迁移，既有部署升级前执行：

```cmd
.venv\Scripts\python.exe scripts\migrate_phase4_notifications.py apply
```

## 数据与安全

- 不提交 `.env`、API key、token、数据库密码和精确定位信息。
- 不提交 `data/`、`volumes/`、模型权重、上传文件和原始评测集。
- 技术 Markdown 可以进入 `docs/`；`docs/private/`、`docs/interview/` 和 `docs/local/` 永久忽略。
- Neo4j、地图和 Reranker 是可选增强能力，失败时不得阻断普通知识检索。
- 运行时 `ragas_lite` 是低成本启发式监控信号，不等于正式 RAGAS 评测结果。
- 没有 gold evidence 的数据集不得报告 Recall@K。

## 迭代路线

1. 患者文档抽取 golden set 与字段级准确率评测。
2. 用真实患者纵向数据验证 Observation 趋势与主动追问策略。
3. 短信/移动推送适配器、真实邮件/Webhook 供应商验收和通知回执。
4. 在硬件或外部推理资源满足后开展多模态 POC。
5. 检索、生成、Agent、抽取、安全和性能消融评测。

## License

MIT
