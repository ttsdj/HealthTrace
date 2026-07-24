# HealthTrace Golden Set 审核工作流

## 原则

自动转换的数据、规则生成用例和公开 benchmark 不是天然的临床 golden set。HealthTrace 将“数据导入”和“被允许用于正式结论”分开。

## 流程

```text
JSONL import
  -> draft
  -> independent reviews
  -> approved / rejected
  -> readiness gate
  -> frozen export
  -> official evaluation
```

- 数据包含潜在 PHI 标记时拒绝导入。
- 内容发生变化时重新变为 `draft`，历史批准不再沿用。
- 同一审核人只能贡献一份有效审核。
- 默认至少需要两份独立批准。
- `clinical_review_required=true` 的用例至少需要一名 clinician 角色审核。
- 正式后台评测在数据集未 ready 时返回冲突，不偷偷使用 draft。

## 当前真实状态

内置 `healthtrace_agent/v1` 共 42 条，已导入本地数据库：

- draft：42
- approved：0
- clinical claim allowed：false

因此当前 `42/42` 只表示确定性工程策略测试通过，不代表临床答案准确率或临床安全认证。

## 命令

```cmd
.venv\Scripts\python.exe scripts\golden_review.py import --path evaluation\healthtrace_agent_v1.jsonl --dataset healthtrace_agent --version v1
.venv\Scripts\python.exe scripts\golden_review.py status --dataset healthtrace_agent --version v1
.venv\Scripts\python.exe scripts\golden_review.py export --dataset healthtrace_agent --version v1 --output data\evaluations\golden\approved_healthtrace_agent_v1.jsonl
```

审核通过后的导出文件属于评测产物，不应默认提交包含敏感内容的真实数据。
