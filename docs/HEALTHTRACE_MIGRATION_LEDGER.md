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
