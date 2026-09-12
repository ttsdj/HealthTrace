# HealthTrace 迁移台账

## 基线

- 迁移日期：2026-07-21
- 源目录：`D:\aicoding\00.project\medretieve\V3`
- 目标目录：`D:\aicoding\00.project\HealthTrace`
- 源提交：`15cab76189a2c1ba0b094ab91854344d766af70a`
- 基线标签：`medretrieve-v3-baseline`
- 迁移分支：`migration/healthtrace-phase0`
- Git remote：未配置

源仓库保持只读；迁移采用本地 `git clone --no-hardlinks`，因此保留历史但不共享可写 Git object。

## Phase 0 提交

| 顺序 | 提交 | 用途 |
|---|---|---|
| 1 | `117cb1f` | 建立 HealthTrace 迁移基线 |
| 2 | `5705e25` | 统一品牌、默认运行配置和双模式启动 |
| 3 | `d20d18b` | 当前实现与指标审计 |
| 4 | `test: add HealthTrace migration regression coverage` | 回归、安全和可运行性验证 |

## 本地运行兼容

本机 `.env` 使用：

```env
HEALTHTRACE_INFRA_MODE=external
```

该模式只读取 `DATABASE_URL`、`REDIS_URL`、Milvus 和 Neo4j 地址，不执行 `docker compose up`，不会重建、删除或迁移现有容器卷。仓库默认 `.env.example` 使用 `managed`，适合新机器启动全新 HealthTrace 基础设施。

## 数据状态

- PostgreSQL、Redis、Milvus、Neo4j：本阶段不迁移 schema 和数据。
- `.env`：从源工作区复制到目标，仅本地存在并受 `.gitignore` 保护。
- 模型、原始文档、评测集、逐题结果、Docker volumes：未复制到 Git。
- 新 HealthTrace managed 环境默认使用全新 `healthtrace` 数据库、容器和网络名称。

## 回滚

代码回滚可从 `medretrieve-v3-baseline` 创建分支。运行数据未被本阶段修改，因此不需要数据库回滚。不得删除源目录或现有 Docker volumes，直到后续数据分域迁移完成并经过校验。

## 后续阶段

1. 数据分域与可回滚 migration。
2. 患者结构化事实和时间轴。
3. Patient Context Planner 与 Typed Tools。
4. Health Agent 状态机和 Action Policy。
5. 多模态 POC（延期，默认关闭）。
6. 长期健康任务。
7. 全面评测与消融。

## 2026-07-22 患者数据基础升级

- 分支：`feature/healthtrace-phase1-data-domains`
- 迁移版本：`2026_07_22_phase1_document_domains`
- 方式：只增加 tenant/patient/document 字段、患者表和索引，不删除旧数据。
- 旧父块：回填为 `public_medical`。
- 新患者向量：独立 `healthtrace_patient_record_text_v1`，禁止回退公共 collection。
- 新权威数据：FHIR-like facts、timeline、health tasks 和 task runs。
- 回滚：迁移状态改为 `rolled_back` 并关闭功能开关，数据保留。
- 验证：后端测试全部通过，包含认证后的患者事实、时间轴、任务 HTTP 流程、患者检索降级和 Milvus flush。
- 真实迁移：已在 `medretrieve_v2` PostgreSQL 执行加法迁移；用户和父块计数保持不变，旧父块全部标记为 `public_medical`。
- Milvus：旧 collection 未重建；新增空的 `healthtrace_patient_record_text_v1`，包含 tenant/patient/domain/owner 隔离字段。
- 患者链路：使用本地 BGE-M3 和无隐私合成文档验证上传、Hybrid 召回、聊天上下文注入、跨患者无泄漏及协调删除；测试结束后无可查询残留。
- 运行验收：`/health` 返回 `ready=true`，PostgreSQL、Redis、Milvus、LLM 配置正常；Neo4j 当前未启动并按可选能力降级。
- 详细记录：见 `docs/PHASE1_REAL_INFRA_VALIDATION.md`。

## 2026-07-22 候选事实与健康档案工作台

- 迁移版本：`2026_07_22_phase2_fact_candidates`。
- 新表：`patient_fact_candidates`，只保存待确认、已确认或已拒绝的抽取候选。
- 安全边界：默认本地规则抽取；外部 LLM 增强要求 API 和前端双重显式同意。
- 权威写入：候选经患者确认后才写入 `patient_facts` 并创建 `patient_timeline_events`。
- 前端：新增患者资料、待确认、健康事实、时间轴、长期任务五个栏目，并在登录/登出时清空患者缓存。
- 真实迁移：执行前已生成本地忽略的 PostgreSQL 备份；迁移只新增一张表，重复执行 `changes=[]`，旧表行数保持不变。
- 端到端：本地 BGE-M3 合成病历完成上传、Hybrid 检索、跨患者无命中、规则候选、确认入档、时间轴和协调删除；测试记录无残留。
- 视觉验证：桌面 1280×720 与移动 390×844 均无横向溢出，移动端保留新建对话和健康档案入口。

## 2026-07-22 Agent 与长期任务闭环

- 迁移版本：`2026_07_22_phase3_long_term_tasks`。
- 迁移前备份：`data/backups/healthtrace_pre_phase3_20260722.dump`，已通过 `pg_restore -l` 校验；该目录受 Git 忽略。
- 咨询状态机：新增 RECEIVE、CLASSIFY_AND_PLAN、QUERY_PATIENT_CONTEXT、CHECK_INFORMATION、RETRIEVE_AND_GRADE_EVIDENCE、DECIDE_ACTION、EXECUTE_AND_PERSIST、COMPLETED 八阶段轨迹。
- 证据策略：高风险和缺失信息在模型前拦截；No Evidence 不输出无依据确定答案；Conflict 强制披露；每次工具调用记录脱敏参数、耗时、尝试次数和失败类型。
- 长期任务：新增健康目标、任务运行领取、指数退避、最大重试、等待用户输入、周期摘要、目标检查和站内通知。
- 一致性：`health_task_runs(task_id, run_key)` 与通知 `dedup_key` 保证重复调度不会重复执行或重复通知；PostgreSQL 使用 `FOR UPDATE SKIP LOCKED` 领取任务。
- 前端：健康任务页展示目标、通知、五类任务和运行记录，支持确认、取消、重试、补录与归档。
- 真实迁移：已在 `medretrieve_v2` PostgreSQL 执行，只新增 11 个列、索引和 `health_notifications` 表，未删除旧数据。
- 验证：后端测试全部通过，`npm run build` 通过；新增覆盖故障注入、跨患者通知隔离和 Phase 3 幂等迁移。

## 2026-07-22 Agent 评测、趋势、可观测性与通知投递

- 迁移版本：`2026_07_22_phase4_notification_delivery`。
- 迁移前备份：`data/backups/healthtrace-before-phase4-20260722.dump`，custom format，已通过 `pg_restore -l` 校验；SHA-256 为 `4EF26910595357D68DADA0CFE1DD025E3D7E7E2F854677B2E3F529E8E9FA1E21`。
- 新表：`health_notification_deliveries`；没有删除、重命名或回填既有业务表。
- Patient Tools：新增 Observation 趋势与白名单工具规划；默认规则模式，可选受约束 LLM，任何模型/解析失败回退规则。
- Agent 评测：人工规则策略集覆盖高风险、缺失信息、证据源、工具路由、隐私和边界，当前全部通过；该结果不代表临床答案准确率。
- 可观测性：管理员聚合查看证据状态、检索降级、工具成功率/延迟、任务重试和 ragas_lite 信号；接口不返回问题、回答和患者标识。
- 外部通知：站内通知先提交，邮件/Webhook 后投递；显式同意、幂等、指数退避，外部失败不会回滚任务。
- 验证：后端测试全部通过，前端 TypeScript/Vite 构建通过，桌面和移动视口通过，真实 `/health` 为 `ready=true`；Neo4j 保持可选降级。
- 容器化：Compose 合并配置通过；应用镜像构建被本机 Docker 腾讯镜像源 EOF 阻塞，未发现 Dockerfile 语法错误，需修复 Docker Desktop registry mirror 后重试。

## 2026-07-24 权限、安全、持久任务与正式评测门禁

- 迁移版本：`2026_07_24_phase5_access_security`、`2026_07_24_phase6_jobs_golden_review`。
- 迁移前备份：`data/backups/healthtrace-pre-phase5-phase6-20260724.dump`，custom format，已通过 `pg_restore -l` 校验；SHA-256 为 `4E9F6ADE811B34A2E74FB319DE2F688821100F82A83D534C7E7E22782FEFE9AF`。
- Phase 5 新增：tenant 成员、患者授权、AES-256-GCM 私密记录和 metadata-only 审计表。
- Phase 6 新增：PostgreSQL 持久后台任务、Golden case 和独立审核表。由于 Phase 5 启动时的 metadata 初始化已经创建当前模型表，Phase 6 在真实库登记为 applied 且 `changes=[]`；没有删除或改写已有业务数据。
- 真实库迁移后计数：用户 10、tenant membership 10、patient grant 10、audit 0、background job 0、golden case 0。
- 评测导入：随后导入 `healthtrace_agent/v1` 策略用例，全部为 draft、均未批准、clinical claim allowed=false；没有伪造临床审核。
- 持久任务：公共文档上传/删除、患者文档索引和正式 Agent 评测进入数据库队列，支持并发领取、幂等键、短事务进度、stale recovery 和有限指数退避。
- 可观测性：增加 Prometheus 文本指标、可选 OTLP、聚合告警、liveness/readiness 和通知诊断。
- 部署：增加新机器 `setup.bat`、生产 preflight、部署 smoke、非 root 多阶段镜像以及 tag 触发的 GHCR 发布工作流。
- 容器：默认 PyTorch CPU wheel，避免通用 API 镜像携带 CUDA 运行库；本地镜像体积大幅缩减。
- 容器冒烟：临时应用容器在 external 模式连接既有 PostgreSQL、Redis 与 Milvus，`/health/live`、`/health/ready`、前端静态资源和 deployment smoke 全部通过；容器状态为 healthy。Neo4j 未启动并按 optional service 正常降级。
- 网络说明：本机 Docker Desktop 的腾讯镜像源在 Docker Hub 元数据请求时返回 EOF；本地验收通过 Dockerfile 构建参数使用 AWS 公共仓库中的等价官方 Python/Node 镜像完成，默认配置未写死本机绕行地址。
- 验证：后端测试全部通过，Python compileall、前端生产构建、仓库安全检查、生产 preflight、Compose 配置和真实容器冒烟通过。
