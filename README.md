# MedRetrieveV2.0

MedRetrieveV2.0 是一个面向医疗知识问答的 RAG 工程项目。它把 Milvus 混合检索、Neo4j 医疗知识图谱、会话记忆、医疗安全边界、文档解析入库和 RAG 评测整合成一个可运行的医疗检索工作台。

> 免责声明：本项目用于学习、工程展示和医学知识检索辅助，不提供诊断、处方或急救决策。任何医疗建议都应以专业医生意见为准。

## 3 分钟看懂项目（STAR）

### S - Situation：为什么做

普通聊天模型在医疗问答中容易出现三个问题：回答没有来源、检索证据不稳定、对高风险医学问题缺少边界。MedRetrieveV2.0 的目标是做一个“可追溯、可降级、可评测”的医疗 RAG 系统，让每次回答尽量绑定检索证据、结构化知识和安全提示。

### T - Task：要解决什么

项目第一阶段聚焦五件事：

- 用 Milvus 建立医疗文档/问答向量库，并支持 Dense、BM25、Hybrid RRF 检索。
- 接入 Neo4j 医疗知识图谱，作为结构化医学证据补充。
- 增加情景记忆和语义记忆，让多轮问答保留上下文。
- 增加医疗安全边界，包括高风险症状、药品剂量、隐私脱敏。
- 建立 RAGCare-QA / RAGAS 评测流程，量化 Recall@K、Faithfulness、Answer Relevance 等指标。

### A - Action：怎么实现

核心链路如下：

```text
用户问题
  -> FastAPI 鉴权与会话隔离
  -> 医疗安全边界与隐私脱敏
  -> 轻量 NER / 意图识别
  -> 情景记忆 + 语义记忆召回
  -> Milvus Hybrid RAG 检索
  -> Neo4j KG 结构化证据查询
  -> 证据压缩与冲突提示
  -> OpenAI 兼容 LLM 生成回答
  -> 前端展示回答、检索路径、证据、RAGAS-lite 和安全状态
```

工程上做了这些取舍：

- 检索层优先 Hybrid RAG，失败时按 Dense / Sparse / 无检索证据逐级降级，避免系统直接退出。
- 文档解析优先 MinerU，超时或失败时回退 PyPDF / pypdfium2，必要时再走 OCR。
- 大 PDF 入库采用“前若干页/前若干 chunk 先可检索，剩余后台继续处理”的渐进式策略。
- 模型配置只从 `.env` 读取，仓库只提交 `.env.example`。
- Neo4j 被视为增强能力，未启动时 KG 降级，不阻断普通 RAG 问答。

### R - Result：当前完成度

已完成：

- FastAPI 后端、Vue 3 前端、JWT 登录注册、会话隔离。
- Docker Compose 一键启动 PostgreSQL、Redis、Milvus、etcd、MinIO、Attu。
- Milvus 三类 collection 设计：医疗问答库、情景记忆、语义记忆。
- BGE-M3 embedding、Milvus 2.5+ 原生 BM25、Hybrid RRF、可选 rerank。
- Neo4j 医疗 KG 查询工具和 KG 降级健康检查。
- 医疗安全边界、隐私脱敏、药品剂量提示。
- RAGCare-QA 评测脚本和 RAGAS 评测脚本。
- `start.bat` 一行启动本地开发环境。

待继续完善：

- 大规模中文医疗评测集和人工审核 golden set。
- 生产级任务队列、后台 worker、对象存储和监控告警。
- 更严格的药品剂量知识库校验和医生审核流程。

## 架构

```text
MedRetrieveV2.0/
  backend/                  FastAPI 后端
    api/routes/             鉴权、会话、聊天、文档、健康检查、评测 API
    chat/                   聊天编排、SSE 流式输出、会话存储
    rag/                    检索、RRF 融合、降级策略、上下文压缩
    indexing/               文档解析、分块、embedding、Milvus 写入
    kg/                     Neo4j 医疗知识图谱查询
    memory/                 情景记忆和语义记忆
    medical_nlp/            医疗安全边界、隐私脱敏、轻量规则
    evaluation/             RAGCare-QA、RAGAS、检索指标
    infra/                  数据库、Redis、JWT 等基础设施
  frontend/                 Vue 3 + TypeScript + Pinia + Vite
  scripts/                  初始化、导入、评测和仓库检查脚本
  docker-compose.yml        PostgreSQL + Redis + Milvus 基础服务
  .env.example              本地配置模板，不含真实密钥
  start.bat                 Windows 一行启动脚本
```

## 快速运行

### 1. 克隆项目

```cmd
git clone https://github.com/ttsdj/MedRetrieveV2.0.git
cd MedRetrieveV2.0
```

### 2. 创建 Python 环境

推荐 Python 3.12：

```cmd
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install -U pip
pip install -e ".[dev]"
```

如需 OCR 能力：

```cmd
pip install -e ".[ocr]"
```

### 3. 安装前端依赖

```cmd
cd frontend
npm install
cd ..
```

### 4. 配置环境变量

```cmd
copy .env.example .env
```

然后编辑 `.env`，至少填写：

```env
LLM_API_KEY=你的模型服务密钥
BASE_URL=你的 OpenAI 兼容服务地址
MODEL=你的主模型名
FAST_MODEL=你的快速模型名
GRADE_MODEL=你的评分模型名
JWT_SECRET_KEY=换成随机长字符串
```

可选配置：

- `MINERU_TOKEN`：MinerU 文档解析服务 token。
- `NEO4J_URL` / `NEO4J_USER` / `NEO4J_PASSWORD`：医疗知识图谱。
- `AMAP_MAPS_API_KEY`：附近医院和地图服务。

### 5. 启动 Docker 基础服务

```cmd
docker compose up -d
```

服务端口：

| 服务 | 地址 |
|---|---|
| FastAPI | http://127.0.0.1:8000 |
| API Docs | http://127.0.0.1:8000/docs |
| Frontend | http://localhost:3000 |
| Attu | http://localhost:8080 |
| Milvus | 127.0.0.1:19530 |
| PostgreSQL | 127.0.0.1:5432 |
| Redis | 127.0.0.1:6379 |

### 6. 一行启动开发环境

Windows：

```cmd
start.bat
```

这个脚本会：

1. 启动 Docker Compose 服务。
2. 初始化数据库表。
3. 启动 FastAPI 后端。
4. 启动 Vue 前端。
5. 打开浏览器访问前端。

如果想手动启动：

```cmd
.\.venv\Scripts\activate
python -m backend.infra.database
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000 --reload
```

另开一个终端：

```cmd
cd frontend
npm run dev
```

## 常用命令

```cmd
docker compose ps
docker compose up -d
docker compose down

python -m pytest
npm --prefix frontend run build

python scripts/check_repo_safety.py
python scripts/evaluate_ragcare.py --stage pilot --baselines bm25,dense,hybrid
python scripts/evaluate_ragas.py --concurrency 2 --output-id pilot-ragas
```

## 环境变量说明

仓库不会提交真实 `.env`。请以 `.env.example` 为模板配置本地环境。

核心变量：

| 变量 | 用途 |
|---|---|
| `LLM_API_KEY` | OpenAI 兼容模型服务密钥 |
| `BASE_URL` | OpenAI 兼容 API 地址 |
| `MODEL` | 主回答模型 |
| `FAST_MODEL` | 快速分类/标题/轻量任务模型 |
| `GRADE_MODEL` | 文档相关性或评估模型 |
| `EMBEDDING_MODEL` | 默认 `BAAI/bge-m3` |
| `MILVUS_MEDICAL_QA_COLLECTION` | 医疗问答/文档知识库 |
| `MILVUS_EPISODIC_MEMORY_COLLECTION` | 情景记忆 collection |
| `MILVUS_SEMANTIC_MEMORY_COLLECTION` | 语义记忆 collection |
| `MINERU_TOKEN` | 可选，MinerU 解析服务 |
| `NEO4J_PASSWORD` | 可选，Neo4j 医疗知识图谱 |
| `JWT_SECRET_KEY` | JWT 签名密钥，必须本地更换 |

## 数据与安全

为了保持仓库干净：

- 不提交 `.env`、真实 API key、token、数据库密码。
- 不提交 `docs/` 内部面试资料。
- 不提交 `data/`、`volumes/`、`logs/`、模型缓存、上传文档。
- 不提交 RAGCare-QA 原始数据和真实医疗数据。
- 提交前运行：

```cmd
python scripts/check_repo_safety.py
```

## 评测设计

当前评测分两类：

1. 检索指标：Recall@5、Precision@5、F1@5、MRR、nDCG。
2. 生成质量：RAGAS / RAGAS-lite，关注上下文相关性、忠实度、答案相关性。

RAGCare-QA 用作公开英文医疗 RAG 评测集。项目默认不把数据集提交到仓库；新机器需要自行下载或按脚本准备。

## 面试表述示例

可以这样概括：

> 我做了一个医疗 RAG 工作台 MedRetrieveV2.0，后端用 FastAPI，前端用 Vue 3，检索层用 Milvus 的 BGE-M3 Dense + 原生 BM25 做 Hybrid RRF，并接入 Neo4j 医疗知识图谱、会话记忆和医疗安全边界。工程上实现了文档解析降级、检索降级、KG 降级、隐私脱敏和 RAGCare-QA/RAGAS 评测，让系统不仅能回答，还能展示证据链和质量指标。

## License

MIT
