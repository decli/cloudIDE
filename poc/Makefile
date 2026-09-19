VENV := .venv
PY := $(VENV)/bin/python

.PHONY: help setup image run clean

help:
	@echo "make setup            建虚拟环境、装依赖"
	@echo "make image            构建沙箱镜像"
	@echo 'make run REQ="需求"   跑一个任务'
	@echo "make clean            清理工作区和残留容器"

setup:
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install -q --upgrade pip
	$(VENV)/bin/pip install -q -r requirements.txt
	@echo "OK: $(VENV) 就绪"

image:
	docker build -t cloudide-sandbox-py:latest -f docker/sandbox-py.Dockerfile .

run:
	@test -n "$(REQ)" || (echo 'usage: make run REQ="需求描述"' >&2; exit 2)
	$(PY) -m agent.cli "$(REQ)"

clean:
	rm -rf workspaces/*
	@docker ps -aq --filter "name=cloudide-task-" | while read -r id; do docker rm -f "$$id"; done
