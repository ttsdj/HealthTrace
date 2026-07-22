# HealthTrace 目标架构与当前落点

```text
Vue / Future Mobile
        |
FastAPI Auth + Patient Scope
        |
Consultation Orchestrator (下一阶段)
  |        |          |          |
Public RAG Patient Tools KG Tool  Health Tasks
  |        |          |          |
Milvus   PostgreSQL  Neo4j   PostgreSQL Scheduler
  |        |
Public   Facts / Timeline / Private Documents
```

当前已完成数据域、轻量 FHIR-like 事实、时间轴、类型化工具合约和长期任务基础。现有 LangGraph 仍是 RAG 图，尚未成为完整咨询 Orchestrator。

下一阶段增加 `required_patient_fields → missing information → evidence state → action policy`，并把现有 Public RAG、Patient Tools 和 KG 作为受控工具接入。

多模态默认关闭。优先保留 OCR/MinerU + BGE-M3 文本链路；视觉 collection 和 Qwen3-VL-Embedding 只在独立 POC 与 text-only 基线比较后启用。
