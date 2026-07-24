# HealthTrace 持久后台任务

## 为什么不使用进程内 BackgroundTasks

大 PDF 解析、向量化和评测可能持续数分钟。任务只放在 FastAPI 进程内时，重启会丢失状态，也无法可靠重试。HealthTrace 将任务状态保存到 PostgreSQL，再由后台 worker 领取。

## 状态机

```text
queued
  -> running
  -> completed
  -> retry_wait -> running
  -> failed
```

- `idempotency_key` 防止重复提交产生重复任务。
- PostgreSQL 使用 `FOR UPDATE SKIP LOCKED` 防止多个 worker 重复领取。
- 每次进度更新独立短事务提交，前端轮询不依赖 worker 内存。
- worker 崩溃后，超过 stale timeout 的任务会重新入队。
- 失败使用有限次数指数退避，达到上限后进入 `failed`。
- `HEALTHTRACE_JOB_CONCURRENCY` 控制单进程线程池并发。

## 当前任务类型

- `document_upload`
- `document_delete`
- `patient_document_index`
- `agent_evaluation`

长期健康任务仍使用自己的领域队列表，因为它包含确认、等待用户输入和周期调度等业务状态。

## 运维

- `/jobs`：管理员查看任务。
- `/jobs/{job_id}`：管理员查看单个任务。
- `/patient/jobs/{job_id}`：按 patient scope 查询，防止跨患者读取。
- `/observability/summary`：任务状态和失败计数。
- `/observability/alerts`：失败任务告警。

当前队列适合单数据库、中等任务量部署。大规模部署可保留相同幂等与状态协议，将执行层迁移到 Celery、Dramatiq 或云队列。

