# HealthTrace Agent 策略评测

## 目的

该评测验证 Health Agent 的工程决策是否按预期执行，不用于证明医学答案达到临床水平。数据集位于 `evaluation/healthtrace_agent_v1.jsonl`，共 42 条人工规则用例。

## 覆盖范围

| 类别 | 数量 | 验证内容 |
|---|---:|---|
| high_risk | 10 | 急危重症信号是否绕过 LLM 并升级就医 |
| missing_information | 8 | 个体化建议缺少必要病史时是否主动追问 |
| public_medical | 8 | 公共 RAG 与 KG 证据源规划 |
| patient_tool_routing | 8 | 白名单 Patient Tool 选择 |
| privacy | 4 | 手机、身份证、邮箱等字段脱敏 |
| boundary | 4 | 剂量、安全边界和普通问答分流 |

## 复现命令

```cmd
.venv\Scripts\python.exe scripts\evaluate_healthtrace_agent.py --output-id policy-v1
```

2026-07-22 本地结果为 42/42：Action、Evidence State、证据源、患者工具、隐私脱敏、高风险召回和缺信息召回均为 1.000。该结果来自确定性策略用例，必须与 RAGCare、RAGAS、MIRAGE 等医学检索/生成评测分开表述。

运行时 trace 也可单独聚合：

```cmd
.venv\Scripts\python.exe scripts\evaluate_healthtrace_agent.py --trace-results path\to\traces.jsonl
```

运行时报告统计 fallback、无证据率、工具成功率和 P50/P95 延迟，不保存隐藏推理过程。
