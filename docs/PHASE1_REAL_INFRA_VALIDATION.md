# HealthTrace Phase 1 真实基础设施验收

验收日期：2026-07-22  
分支：`feature/healthtrace-phase1-data-domains`  
迁移版本：`2026_07_22_phase1_document_domains`

## 验收范围

- 复用旧 V2 的 PostgreSQL、Redis、Milvus、MinIO 和 etcd 容器及绑定卷。
- 不重建、不移动、不删除旧容器卷。
- PostgreSQL 只执行加法迁移；Milvus 旧 collection 保持原样。
- Neo4j 不在本次迁移范围内，未启动时作为可选增强能力降级。

## 迁移前保护

- PostgreSQL 数据库：`medretrieve_v2`，迁移前大小约 14 MB。
- 已生成 PostgreSQL custom-format 逻辑备份，保存在被 Git 忽略的 `data/backups/`。
- `pg_restore -l` 可读取 50 个备份目录条目。
- 迁移前计数：用户 10，父块 6650，公共 schema 表 4。
- Milvus 迁移前核心计数：公共医疗问答 5056，情景记忆 55，语义记忆 6。

## 迁移结果

- 新增 `users.tenant_id`。
- `parent_chunks` 新增 `document_id`、`document_domain`、`tenant_id`、`patient_id`、`owner_user_id`。
- 新增 tenant、patient profile、document、patient fact、timeline、health goal、health task 和 task run 等表。
- 10 个旧用户全部获得独立 tenant 和 patient profile。
- 6650 个旧父块全部保留并标记为 `public_medical`。
- 新建独立患者 collection：`healthtrace_patient_record_text_v1`，初始行数 0。
- 患者 collection 包含 `document_domain`、`tenant_id`、`patient_id` 和 `owner_user_id` 字段。
- 第二次执行迁移返回 `changes=[]`，用户、父块和患者计数不变，证明迁移幂等。

## 运行验证

- `/health` 返回 `service=HealthTrace`、`infra_mode=external`、`ready=true`。
- PostgreSQL、Redis、Milvus 和 LLM 配置检查通过。
- `phase1_migration=applied`，`patient_domains_enabled=true`。
- Neo4j 当前端口不可达；系统将其报告为 optional service，不阻塞核心问答链路。
- Python 自动化测试：60 passed。
- Vue/TypeScript 生产构建：通过。
- 仓库密钥与大文件安全检查：通过。

## 仍未覆盖

- 尚未把真实患者 PDF 写入患者 collection，以免在验收期间引入隐私或测试脏数据。
- 尚未对患者文档执行真实 BGE-M3 召回质量评测。
- 尚未验证外部 Neo4j 图数据完整性。
- 多模态向量检索仍为延期能力，默认关闭。
- 当前咨询 Orchestrator 仍是规则预检加现有 RAG 图，不是完整自主 Health Agent。
