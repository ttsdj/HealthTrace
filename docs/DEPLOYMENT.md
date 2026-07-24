# HealthTrace 部署手册

## 本地开发

```cmd
setup.bat
start.bat
```

`setup.bat` 创建虚拟环境、安装依赖、创建本地 `.env` 并生成随机 JWT/字段加密密钥。`start.bat` 根据 `HEALTHTRACE_INFRA_MODE` 使用 managed 或 external 基础设施。

## 容器部署

```cmd
docker compose -f docker-compose.yml -f docker-compose.app.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.app.yml up -d --build
```

若本机 Docker Hub mirror 不可用，可以临时覆盖为等价的公共官方镜像，而不修改仓库默认值：

```cmd
docker build --build-arg PYTHON_IMAGE=public.ecr.aws/docker/library/python:3.12-slim --build-arg NODE_IMAGE=public.ecr.aws/docker/library/node:20-alpine -t healthtrace:local .
```

应用镜像：

- Node 20 阶段构建 Vue。
- Python 3.12 slim 阶段安装后端。
- 默认从 PyTorch CPU wheel 索引安装 embedding 运行时，避免把无用 CUDA 库打入通用 API 镜像；GPU 部署可通过构建参数覆盖。
- 以非 root `healthtrace` 用户运行。
- `/health/live` 用于进程存活检查。
- `/health/ready` 用于流量接入检查。

## 上线前检查

```cmd
.venv\Scripts\python.exe scripts\production_preflight.py
.venv\Scripts\python.exe scripts\deployment_smoke.py --base-url http://127.0.0.1:8000
```

生产环境还应：

1. 把密钥放入平台 Secret Manager，不挂载仓库 `.env`。
2. 使用托管 PostgreSQL、Redis、对象存储和 Milvus，并启用备份。
3. 在数据库备份校验后依次运行 Phase 1-6 加法迁移。
4. 配置 Prometheus 抓取 `/observability/metrics`，需要跨服务 trace 时安装 observability extra 并配置 OTLP。
5. 为 readiness、API 5xx、任务失败、工具成功率和通知失败建立告警。
6. 使用滚动或蓝绿发布；新版本异常时回退镜像，不回滚破坏性数据操作。

## CI/CD

- `ci.yml`：后端测试、Python 编译、前端构建、仓库安全扫描和 Compose 校验。
- `container.yml`：`v*` 标签触发 GHCR 镜像发布，包含 provenance、SBOM 和构建缓存。

当前仓库未配置 remote，因此发布工作流只有推送到 GitHub 后才会运行。
