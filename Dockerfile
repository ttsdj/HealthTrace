ARG NODE_IMAGE=node:20-alpine
ARG PYTHON_IMAGE=python:3.12-slim
ARG TORCH_VERSION=2.13.0
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
FROM ${NODE_IMAGE} AS frontend-builder
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM ${PYTHON_IMAGE} AS runtime
ARG TORCH_VERSION
ARG TORCH_INDEX_URL
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY backend/ ./backend/
RUN python -m pip install --upgrade pip \
    && python -m pip install "torch==${TORCH_VERSION}" --index-url "${TORCH_INDEX_URL}" \
    && python -m pip install .
COPY scripts/ ./scripts/
COPY --from=frontend-builder /build/frontend/dist ./frontend/dist
RUN groupadd --system healthtrace \
    && useradd --system --gid healthtrace --home-dir /app healthtrace \
    && mkdir -p /app/data /app/logs \
    && chown -R healthtrace:healthtrace /app
USER healthtrace
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=8s --start-period=60s --retries=5 CMD curl -fsS http://127.0.0.1:8000/health/ready || exit 1
CMD ["python", "-m", "uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000"]
