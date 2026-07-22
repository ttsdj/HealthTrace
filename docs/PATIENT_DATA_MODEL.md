# HealthTrace 患者数据模型

## 四个存储投影

1. 原始患者文件：`data/documents/private/<tenant>/<patient>/<document>/<filename>`。
2. PostgreSQL 权威数据：文档元数据、父块、FHIR-like 事实、时间轴、任务与来源。
3. Milvus 派生索引：`healthtrace_patient_record_text_v1`，只保存患者叶子块向量。
4. Redis 派生缓存：父块和短期状态，缓存键不得替代 PostgreSQL 权威记录。

公共医学知识继续使用 `medical_qa` collection。患者 collection 永远不能回退到公共 collection。

## 身份边界

```text
authenticated user
→ tenant_id
→ patient_id
→ document/fact/timeline/task
```

`patient_id` 由后端登录态注入，API 和模型均不得指定其他患者。

## FHIR-like 事实

`patient_facts` 统一保存 `resource_type / code / value / effective time / verification / provenance`。当前支持 Condition、Observation、MedicationStatement、AllergyIntolerance、DiagnosticReport、Procedure 和 Immunization。

这是一套受 FHIR 启发的轻量 schema，不声称通过 FHIR 一致性认证。

## 时间轴

`patient_timeline_events` 是事实的读取投影。排序依据是 `effective_at`，`source_chunk_id` 和页码只负责证据回溯。录入时间 `recorded_at` 不替代临床事件时间。

## 迁移与回滚

```cmd
.venv\Scripts\python.exe scripts\migrate_phase1_domains.py apply
.venv\Scripts\python.exe scripts\migrate_phase1_domains.py status
.venv\Scripts\python.exe scripts\migrate_phase1_domains.py rollback
```

迁移只增加字段、表和索引。rollback 将迁移标记为停用并要求设置 `HEALTHTRACE_PATIENT_DOMAIN_ENABLED=false`，不会删除患者数据、旧父块或 collection。

代码默认不自动迁移旧数据库。全新部署的 `.env.example` 可启用安全加法迁移；复用旧基础设施时应先备份并显式运行 `apply`。
