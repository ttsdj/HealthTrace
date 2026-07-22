# HealthTrace 工具合约

## 医学检索

现有 LangGraph RAG 由 `retrieve_public_medical_evidence` 包装为 `EvidenceBundle`。工具返回 evidence、来源、页码、分数、检索模式和失败状态，不直接决定最终回答。

## 患者工具

`PatientTools` 构造时由后端注入数据库 Session 与 `PatientScope`：

- `get_patient_allergies`
- `get_current_medications`
- `get_recent_conditions`
- `get_latest_observations`
- `get_patient_timeline`
- `search_patient_record_text`
- `create_health_reminder_draft`

模型不可传入 `patient_id`。尚未实现的趋势工具返回 `capability_unavailable`，不得伪造空结果。

## 写工具约束

提醒写工具只创建 `waiting_confirmation` 草稿。用户确认后才进入 `active`；`idempotency_key` 防止重放。取消和运行记录均持久化，跨会话恢复。
