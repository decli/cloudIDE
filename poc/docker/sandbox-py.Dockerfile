# 任务沙箱镜像：Python + pytest + ruff，预装好依赖，容器运行时可以完全断网。
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir "pytest>=8,<9" "ruff>=0.6,<1"

WORKDIR /workspace
