# Web 界面 + 编排服务。它自己跑在容器里，通过宿主机的 docker socket 去起任务沙箱。
FROM docker:cli AS dockercli

FROM python:3.12-slim

COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY agent/ ./agent/
COPY web/ ./web/
COPY templates/ ./templates/

ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "8000"]
