# HealthTrace

HealthTrace 是一个面向个人用户的健康档案与循证智能咨询系统。当前版本以可运行的医疗 RAG 工作为基础，提供文档入库、混合检索、知识图谱、会话记忆、安全边界和可复现评测；后续将逐步加入个人健康事实、时间轴、主动追问和长期健康任务。

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
  -> Recent Messages + Persistent Note + 语义/情景记忆
  -> LangGraph 复杂度路由与纠错检索
  -> Milvus BGE-M3 Dense + 原生 BM25 + RRF
  -> 可选 Reranker、Neo4j KG 和医院导航工具
  -> 证据压缩、冲突提示和安全回答
  -> Vue 工作台展示引用、检索 Trace 和运行质量信号
```

工程策略：

- 检索按 Hybrid、Dense、Sparse、No Evidence 逐级降级。
- PDF 优先 MinerU，失败或超时后回退 PyPDF/pypdfium2，稀疏页面可继续进入 PaddleOCR。
- 大文档先写入首批页面或 chunk，使部分内容尽早可检索，剩余批次后台继续处理。
- PostgreSQL 保存用户、会话和父块；Redis 缓存父块与短期状态；Milvus 保存叶子块与记忆；Neo4j 是可选增强服务。
- 配置只从本地 `.env` 读取，代码和 Git 历史不保存真实密钥。

### R - Result

当前已经完成并有代码链路：

- FastAPI、Vue 3、JWT 登录注册和按用户隔离的会话持久化。
- BGE-M3、Milvus 2.5+ 原生 BM25、Hybrid RRF、三级父子分块与检索降级。
- MinerU/PDF/OCR 解析降级、渐进式向量入库与批量 Milvus 写入。
- Neo4j 医疗知识图谱工具、语义/情景记忆、上下文压缩和医疗安全规则。
- RAGCare-QA、RAGAS 和 MIRAGE 评测代码；仓库测试基线为 35 项通过。

尚未完成、不得对外宣称已实现：

- 公共医学知识与个人病历的完整数据分域。
- FHIR-like 患者事实表、可信状态、健康时间轴和 Typed Patient Tools。
- 完整咨询 Agent 的 Evidence State、Action Policy 和长期健康任务。
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

## 数据与安全

- 不提交 `.env`、API key、token、数据库密码和精确定位信息。
- 不提交 `data/`、`volumes/`、模型权重、上传文件和原始评测集。
- 技术 Markdown 可以进入 `docs/`；`docs/private/`、`docs/interview/` 和 `docs/local/` 永久忽略。
- Neo4j、地图和 Reranker 是可选增强能力，失败时不得阻断普通知识检索。
- 运行时 `ragas_lite` 是低成本启发式监控信号，不等于正式 RAGAS 评测结果。
- 没有 gold evidence 的数据集不得报告 Recall@K。

## 迭代路线

1. 公共知识与个人病历数据分域。
2. 患者结构化事实、可信状态和健康时间轴。
3. Patient Context Planner、Typed Tools 和主动追问。
4. Evidence State、Action Policy 与咨询状态机。
5. 在硬件或外部推理资源满足后开展多模态 POC。
6. 长期提醒、健康目标和周期摘要。
7. 检索、生成、Agent、抽取、安全和性能消融评测。

## License

MIT
