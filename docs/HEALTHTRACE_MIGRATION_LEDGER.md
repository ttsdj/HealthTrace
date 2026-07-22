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
- 验证：70 项测试通过，包含认证后的患者事实、时间轴、任务 HTTP 流程、患者检索降级和 Milvus flush。
- 真实迁移：已在 `medretrieve_v2` PostgreSQL 执行加法迁移；用户和父块计数保持不变，旧父块全部标记为 `public_medical`。
- Milvus：旧 collection 未重建；新增空的 `healthtrace_patient_record_text_v1`，包含 tenant/patient/domain/owner 隔离字段。
- 患者链路：使用本地 BGE-M3 和无隐私合成文档验证上传、Hybrid 召回、聊天上下文注入、跨患者零泄漏及协调删除；测试结束后可查询残留为 0。
- 运行验收：`/health` 返回 `ready=true`，PostgreSQL、Redis、Milvus、LLM 配置正常；Neo4j 当前未启动并按可选能力降级。
- 详细记录：见 `docs/PHASE1_REAL_INFRA_VALIDATION.md`。

## 2026-07-22 候选事实与健康档案工作台

- 迁移版本：`2026_07_22_phase2_fact_candidates`。
- 新表：`patient_fact_candidates`，只保存待确认、已确认或已拒绝的抽取候选。
- 安全边界：默认本地规则抽取；外部 LLM 增强要求 API 和前端双重显式同意。
- 权威写入：候选经患者确认后才写入 `patient_facts` 并创建 `patient_timeline_events`。
- 前端：新增患者资料、待确认、健康事实、时间轴、长期任务五个栏目，并在登录/登出时清空患者缓存。
- 真实迁移：执行前已生成本地忽略的 PostgreSQL 备份；迁移只新增一张表，重复执行 `changes=[]`，旧表行数保持不变。
- 端到端：本地 BGE-M3 合成病历完成上传、Hybrid 检索、跨患者零命中、规则候选、确认入档、时间轴和协调删除；测试记录残留为 0。
- 视觉验证：桌面 1280×720 与移动 390×844 均无横向溢出，移动端保留新建对话和健康档案入口。
