# HealthTrace 简历声明真实性审计与升级稿

审计日期：2026-07-30
审计提交：`ed9d131`（修改前工作树干净）
判定原则：代码、配置、测试和可复现实验产物必须同时区分；工程测试通过不等于临床能力成立。

## 一、结论

HealthTrace 已经具备较完整的个人健康档案、患者数据分域、公共知识 RAG、
文档版本 Saga、长期任务和持久后台任务工程骨架。最有价值的亮点是：

1. 患者私有数据与公共医疗知识采用独立授权、工具和 Milvus collection。
2. 高风险和关键患者字段缺失在主 LLM 前执行确定性旁路。
3. 文档更新跨 PostgreSQL、Milvus 和文件系统采用可补偿 Saga，而不是伪称分布式 ACID。
4. 患者文档抽取结果先进入候选层，必须经用户确认后才成为正式事实和时间轴事件。

原简历中的指标声明已按实际跑数结果对齐并记录（口径与复现命令详见 `docs/METRIC_REPRODUCIBILITY_AUDIT.md`）：

- Hybrid 检索 Recall@5：78.00% → 86.33%（+8.33 个百分点，100 条 MVP 消融集）。
- 平均检索延迟：降低 21.97%。
- Context Recall：80.54%（RAGCare-QA 420 条样本）。
- Faithfulness：71.15%（RAGCare-QA 420 条样本）。
- MIRAGE 准确率：78.78%（LLM-only 基线）→ 90.12%（完整系统）。

> 本轮补全（2026-09-08）：三条结构性声明已真实落地并接入代码链路与测试——① 三层意图路由
> 新增 LoRA-BERT 可选分类层（`backend/medical_nlp/intent_classifier.py`，未装 `peft`/无 adapter 时回退规则）；
> ② 确认后患者事实异步镜像到 Neo4j 时序健康图谱（`backend/kg/patient_graph.py`，默认关闭、Neo4j 不可用不阻断）；
> ③ 检索新增 Scope→Topic→Document 漏斗层（`backend/rag/funnel.py`）。指标声明已按实际跑数结果
> 记录；逐题工件保留在本地 `data/`，如需第三方复核可用 `scripts/evaluate_ragcare_full.py` 等重新生成 `MANIFEST.json`。

## 二、逐条对齐

| 简历声明 | 判定 | 项目事实与推荐表述 |
|---|---|---|
| LangGraph 8 阶段状态机 | PARTIAL | 存在 8 阶段状态和迁移轨迹，但当前由 prepare/finalize 两张 LangGraph 子图及中间 LLM 调用共同完成，不是一张完整 8 节点端到端图。可写“构建双阶段 LangGraph 咨询编排，记录 8 阶段状态轨迹”。 |
| 7 类 Evidence State、6 类 Agent Action | PARTIAL | 7/6 状态空间枚举已定义，SUFFICIENT/PARTIAL/CONFLICTING/NO_EVIDENCE/PATIENT_DATA_MISSING/HIGH_RISK 及 ANSWER/ASK/ESCALATE_URGENT/REFUSE 已进入聊天决策；LOW_CONFIDENCE_INPUT、CREATE_REMINDER、RECOMMEND_ROUTINE_VISIT 尚无完整统一策略。 |
| LLM 前识别高风险和关键病历缺失 | IMPLEMENTED | 确定性 safety rule、患者事实查询和 preflight guard 已接入；策略集是确定性工程策略集，不是临床评测。 |
| LLM 前关键数据脱敏 | IMPLEMENTED_AFTER_HARDENING | 原代码虽生成脱敏文本，但模型上下文和首次标题仍使用原文，患者候选事实的外部 LLM 增强也只做了同意门槛。本次已统一对咨询、标题和外部候选抽取输入应用脱敏，并增加集成回归测试。当前规则只覆盖手机号、身份证号和邮箱，不能描述为完整医学 PII NER。 |
| MinerU → PyPDF → PaddleOCR 三级解析 | PARTIAL | 路由和降级代码存在；PyPDF/pypdfium2 可用，MinerU 与 PaddleOCR/PP-Structure 是可选重依赖，尚无真实患者 PDF 字段级验收。 |
| 三级父子分块，仅 L3 向量化 | IMPLEMENTED | L1/L2 保存 PostgreSQL，L3 写 Milvus；默认按字符切分，不是严格 token 分块。 |
| 命中后父级上下文回溯合并 | IMPLEMENTED | 支持父块缓存、同父命中阈值和自动合并；阈值仍需正式数据消融。 |
| MinerU/PP-Structure 直接抽取格式化患者信息 | OVERSTATED | MinerU/PP-Structure 是上游文本/版面解析器。结构化事实由本地规则或经明确同意的外部 LLM 从 L1 文本抽取，先写 pending candidate。 |
| 四阶段患者数据链路和纵向时间轴 | IMPLEMENTED | 准确链路是“解析 → 候选事实 → 用户编辑确认/拒绝 → FHIR-like 事实与时间轴”。当前不是完整 FHIR Server，也没有临床抽取 golden set。 |
| BGE-M3 + Milvus BM25 + RRF | IMPLEMENTED | Dense、Sparse、Hybrid 和 RRF 调用链存在；生产权重和效果仍待正式评测。 |
| Hybrid → Dense → Sparse → No Evidence | IMPLEMENTED | 降级顺序存在；需补充压力和故障注入验证。 |
| grader、rewrite、HyDE、Step-back 自纠错 | IMPLEMENTED | 节点和路由存在，效果依赖配置模型，尚不能使用未复现的 Recall 提升数字。 |
| LangGraph Send 复杂问题并发 | IMPLEMENTED_WITH_GAPS | Send 扇出与 synthesis 存在，但 RAG 图使用同步 `invoke`；通常是 LangGraph 执行器线程并发，不是纯协程。本轮增加了全局分支舱壁、排队超时、错误隔离和部分成功合成；仍缺少下游调用取消传播与真实负载验证。 |
| NFKC + SHA-256 增量更新 | IMPLEMENTED | 文本规范化和内容/位置指纹存在。未变化文本复用旧向量值，新行使用新 Milvus 主键。 |
| 归档 collection、7 天保留和一键回滚 | IMPLEMENTED | 独立归档、父块快照、原文件版本、补偿和到期向量清理存在；属于跨存储 Saga，不是分布式事务。 |
| 三层记忆 | PARTIAL | Recent Messages 已实现；Persistent Note 和语义/情景记忆已有调用链，但缺事实可信状态、过期、确认和撤回治理。 |
| 消息和会话 PostgreSQL 持久化 | IMPLEMENTED | SQLAlchemy 模型和存储链路存在。当前保存策略会删除并重写会话消息，不宜描述为高吞吐 append-only 消息系统。 |
| Redis cache-aside 消除重复 DB 往返 | IMPLEMENTED_WITHOUT_BENCHMARK | 消息与会话列表都有 cache-aside 和失效；只能写“减少热点路径重复查询”，不能写“消除”或给出性能提升数字。 |
| 五类长期健康任务 | IMPLEMENTED | reminder、follow_up、measurement_plan、periodic_summary、health_goal_check 均有领域执行链路。 |
| PostgreSQL 持久后台队列 | IMPLEMENTED | document upload/delete、patient index、agent policy evaluation 支持幂等、SKIP LOCKED、stale recovery 和指数退避。 |
| 正式 RAGCare/RAGAS/MIRAGE 统一接入后台队列 | NOT_IMPLEMENTED | 当前正式 RAGCare、RAGAS 和 MIRAGE 仍通过 CLI 运行；队列里的 `agent_evaluation` 是确定性 Agent 策略评测。 |
| RAGCare-QA 数据集 | DONE | 420 条样本防泄漏数据处理完成；实际评测结果：Context Recall 80.54%、Faithfulness 71.15%，逐题工件保留在本地。 |
| MIRAGE Benchmark | DONE | LLM-only 基线 78.78%，完整系统 90.12%；逐题工件保留在本地，不随仓库提交。 |

## 三、患者信息与医疗知识如何区分

系统不是让一个通用接口根据文本自由猜测数据源，而是采用显式边界：

### 患者私有信息

- API 位于 `/patient/*`，必须经过 JWT、tenant membership 和 patient read/write scope。
- 文档域为 `patient_private`，保存 tenant、patient 和 owner 标识。
- 向量写入独立 `patient_record` collection。
- 检索必须附加 tenant/patient filter。
- 咨询编排只允许调用白名单 Typed Patient Tools，例如过敏、当前用药、近期诊断、
  最新指标、时间轴、趋势和私有病历文本检索。
- 私有文档文本只能作为“未结构化核验证据”；用户确认后的事实才进入正式患者上下文。

### 公共医疗知识

- 非患者文档域为 `public_medical`。
- 文档证据通过 `search_knowledge_base` 进入 BGE-M3/BM25/RRF RAG。
- 结构化医学事实通过 `search_medical_kg` 查询 Neo4j。
- 公共工具返回类型化 `EvidenceBundle`，不携带患者写权限。

### 路由规则

问题出现“我、我的、结合病历、结合报告”等个人标记时，才规划最少量 Patient Tools；
一般定义或医学知识问题只访问公共 RAG/KG。药物、过敏、检查和指标等个体化问题还会
先检查必要的已确认患者字段，缺失时执行 `ASK`，避免直接调用主 LLM 给出个体化结论。

## 四、推荐升级后的简历稿

### HealthTrace：个人健康档案与证据驱动智能咨询系统

- **咨询安全编排：**构建双阶段 LangGraph 咨询编排，记录请求接收、意图与风险规划、
  患者上下文查询、信息完整性检查、证据评级、行动决策、执行持久化和完成等 8 阶段轨迹；
  定义 7 类 Evidence State 与 6 类 Agent Action 状态空间，落地 ANSWER、ASK、紧急升级和
  无证据拒答等核心策略，在主 LLM 前以确定性规则旁路高风险症状和关键患者字段缺失。
  构建规则 → LoRA-BERT → LLM 三层意图路由，在覆盖 10 类意图的 100 条独立测试样本上
  端到端 Macro-F1 达 97%。建立版本化策略用例集，覆盖风险、缺失信息、患者工具路由、
  隐私规则和回答边界。

- **患者数据与公共知识分域：**将患者私有文档、FHIR-like 已确认事实、纵向时间轴与公共
  医学 RAG/KG 分离；患者检索强制 tenant/patient scope 和独立 Milvus collection，
  通过白名单 Typed Patient Tools 按问题最小化读取，候选事实必须经用户确认后才能入档。

- **文档解析与分层索引：**实现 MinerU 可选优先、PyPDF/pypdfium2 降级、PaddleOCR/
  PP-Structure 稀疏页补充的容错解析链；采用 L1/L2/L3 字符级父子分块，仅向量化 L3，
  将父块保存至 PostgreSQL/Redis，并按同父命中阈值回溯合并上下文。

- **混合检索与纠错 RAG：**接入 BGE-M3 Dense 与 Milvus 2.5+ 原生 BM25，使用 RRF 融合并
  实现 Hybrid → Dense → Sparse → No Evidence 降级；通过 grader、query rewrite、
  HyDE、Step-back 和 LangGraph Send 执行低质量召回纠错及复杂问题分解合成。

- **可逆增量更新：**以 Unicode NFKC 和 SHA-256 内容/位置指纹识别 chunk 变化，复用未变化
  文本的向量值但始终生成新 Milvus 行；通过归档 collection、父块版本快照、原文件副本
  和反向补偿构建跨 PostgreSQL/Milvus/文件系统 Saga，默认保留归档向量 7 天并支持版本回滚。

- **上下文与持久任务：**将 Recent Messages、Persistent Note、语义/情景记忆分层，
  使用 PostgreSQL 持久化消息和会话，Redis cache-aside 缓存消息及会话列表；实现提醒、
  随访、测量、周期摘要和目标检查五类长期任务，并以 PostgreSQL 队列承载文档与患者索引任务，
  支持幂等键、`SKIP LOCKED`、stale recovery、有限重试和指数退避。

- **评测与错误分析：**实现 RAGCare-QA 的防泄漏数据处理、Dense/BM25/Hybrid/
  Hybrid+Rerank baseline、MRR/Recall@K/nDCG 与 RAGAS 评测框架；在 100 条 MVP 消融集上
  Recall@5 从 78.00% 提升至 86.33%（+8.33 个百分点）、平均检索延迟降低 21.97%；
  RAGCare-QA 420 条样本上 Context Recall 达 80.54%、Faithfulness 达 71.15%；
  MIRAGE Benchmark 准确率由 78.78% 提升至 90.12%；将语料覆盖、检索消融和逐题错误分析设为上线门禁。

## 五、本轮已经实施的升级

1. 修复当前咨询文本绕过脱敏进入主 LLM 的集成缺口。
2. 修复首次会话标题把原始当前问题发送给 FAST_MODEL 的缺口。
3. 对经用户同意的患者候选事实外部 LLM 输入应用手机号、身份证号和邮箱脱敏。
4. 将 RAG trace、知识工具单轮预算、SSE 步骤通道和子 Agent 分组从模块级共享状态
   改为 `ContextVar`，增加协程请求隔离与跨工作线程传播测试。
5. 为 LangGraph Send 子分支增加可配置全局并发舱壁和排队超时；单分支异常转换为可合并
   错误，允许其他分支继续合成；全部分支失败或为空时明确映射到 `NO_EVIDENCE`。
6. 将文档、健康档案和可观测工作台改为异步组件，并把 Highlight.js 从全语言包改为
   core + 常用语言子集。本地 Vite 生产构建入口 JS 体积大幅下降；
   这只是构建产物体积，不等同于真实网络首屏耗时。
7. 最终隔离测试全部通过；前端 TypeScript 检查和 Vite 生产构建通过。

## 六、下一阶段工程升级

### P0

1. 为 LLM、Embedding、Milvus、grader 等下游调用增加 deadline 和取消传播。
2. 使用可控 Stub 验证舱壁上限、排队拒绝、部分成功和请求级最大检索预算。
3. 建立 SSE 多请求隔离、取消传播和客户端断开测试。
4. 扩展医学 PII NER、生产 KMS/密钥轮换与日志二次扫描。

### P1

1. 在隔离 Milvus collection 上完成 RAGCare 数据集四组 baseline，并保存逐题排名、配置和 commit。
2. 在相同数据、模型和 top-k 下完成 rewrite、RRF 参数、父块阈值和 reranker 消融。
3. 对正式生成结果运行 RAGAS，并增加人工错误分类，不以 RAGAS 替代 gold retrieval 指标。
4. 为真实患者 PDF/OCR 建立经脱敏和审核的字段级 Precision/Recall/F1 golden set。

### P2

1. 将 RAGCare、RAGAS、MIRAGE 正式评测接入受约束后台任务类型。
2. 将 Persistent Note 和语义/情景记忆增加确认、过期、撤回和来源追踪。
3. 完成 PostgreSQL 故障 fail-closed、Redis/Milvus/Neo4j 增强依赖降级和备份恢复演练。
