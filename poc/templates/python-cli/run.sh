#!/bin/sh
# 统一命令契约：编排服务只认这几个命令，不关心项目内部怎么组织。
set -e
export PYTHONPATH=src

cmd="${1:-help}"
[ $# -gt 0 ] && shift

case "$cmd" in
  # 只检查 agent 写的代码；tests/acceptance 是生成的，风格问题不该算在它头上
  lint)       ruff check src tests/unit ;;
  test)       pytest -q tests/unit ;;
  acceptance) pytest -q tests/acceptance ;;
  build)      mkdir -p dist && python -m zipapp src -m "app.cli:_entry" -o dist/app.pyz \
                  -p "/usr/bin/env python3" && echo "已生成 dist/app.pyz" ;;
  run)        python -m app.cli "$@" ;;
  all)        "$0" lint && "$0" test && "$0" acceptance ;;
  *)          echo "用法: ./run.sh {lint|test|acceptance|build|run|all}" >&2; exit 2 ;;
esac
