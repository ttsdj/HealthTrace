# HealthTrace Agent 状态机

## 当前咨询链路

```text
RECEIVE
→ AUTHENTICATE + REDACT
→ CLASSIFY_AND_PLAN（确定性安全前置 + 结构化 Intent Router）
   ├─ HIGH_RISK → ESCALATE_URGENT（不调用 LLM）
   ├─ PATIENT_DATA_MISSING → ASK
   └─ PREFLIGHT_PASSED → CONTEXT + TOOL AGENT
→ Public RAG / KG / Navigation
→ FINALIZE_EVIDENCE_STATE
→ ANSWER / REFUSE
→ PERSIST MESSAGE + TRACE + MEMORY
```

当前定义的 Evidence State：`SUFFICIENT / PARTIAL / CONFLICTING / NO_EVIDENCE / PATIENT_DATA_MISSING / LOW_CONFIDENCE_INPUT / HIGH_RISK`。

当前定义的 Action：`ANSWER / ASK / CREATE_REMINDER / RECOMMEND_ROUTINE_VISIT / ESCALATE_URGENT / REFUSE`。

`LOW_CONFIDENCE_INPUT → ASK`，`CREATE_REMINDER` 会生成确认前草稿，
`RECOMMEND_ROUTINE_VISIT` 会返回常规就医建议；三者均进入统一 Action Policy。

## 已实现的确定性策略

- 胸痛、呼吸困难等高风险模式直接返回急诊分流，绕过 LLM。
- 个人用药问题强制检查 allergies、current medications 和 conditions；缺失时先追问。
- RAG、KG 命中、证据冲突和无结果会映射为明确 Evidence State。
- Trace 保存 required fields、missing fields、evidence sources、state、action 和原因。

## 尚未完成

- 将预检图和最终证据图合并成支持中途挂起/恢复的单一持久化 LangGraph。
- FastModel 已使用冻结集完成一次线上兼容性验收；其超时/异常必须降级到规则路径。详见 `docs/evidence/INTENT_ROUTER_ACCEPTANCE_20260730.md`。
- Patient Tool 已支持白名单规则动态选择和受约束 LLM fallback；仍缺统一的跨公共/患者工具回放协议。
