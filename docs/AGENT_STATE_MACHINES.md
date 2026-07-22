# HealthTrace Agent 状态机

## 当前咨询链路

```text
RECEIVE
→ AUTHENTICATE + REDACT
→ PLAN_CONSULTATION
   ├─ HIGH_RISK → ESCALATE_URGENT（不调用 LLM）
   ├─ PATIENT_DATA_MISSING → ASK
   └─ PREFLIGHT_PASSED → CONTEXT + TOOL AGENT
→ Public RAG / KG / Navigation
→ FINALIZE_EVIDENCE_STATE
→ ANSWER / REFUSE
→ PERSIST MESSAGE + TRACE + MEMORY
```

当前 Evidence State：`SUFFICIENT / PARTIAL / CONFLICTING / NO_EVIDENCE / PATIENT_DATA_MISSING / LOW_CONFIDENCE_INPUT / HIGH_RISK`。

当前 Action：`ANSWER / ASK / CREATE_REMINDER / RECOMMEND_ROUTINE_VISIT / ESCALATE_URGENT / REFUSE`。

## 已实现的确定性策略

- 胸痛、呼吸困难等高风险模式直接返回急诊分流，绕过 LLM。
- 个人用药问题强制检查 allergies、current medications 和 conditions；缺失时先追问。
- RAG、KG 命中、证据冲突和无结果会映射为明确 Evidence State。
- Trace 保存 required fields、missing fields、evidence sources、state、action 和原因。

## 尚未完成

- 将咨询图本身改为完整 LangGraph 节点，而不是当前预检外壳。
- 模型规划字段与后端强制字段的结构化合并。
- Patient Record Text、Patient Fact、Guideline 等工具的动态选择和回放。
- LOW_CONFIDENCE_INPUT、CREATE_REMINDER 和 ROUTINE_VISIT 的完整 Action Policy。
- Agent 级工具选择、参数和动作准确率评测集。
