#!/bin/sh
# 统一命令契约：编排服务只认这几个命令，不关心站点内部怎么组织。
set -e

cmd="${1:-help}"
[ $# -gt 0 ] && shift

case "$cmd" in
  build)      python3 scripts/build.py ;;
  lint)       ruff check scripts tests/unit ;;
  test)       pytest -q tests/unit ;;
  acceptance) pytest -q tests/acceptance ;;
  serve)      python3 -m http.server -d dist "${1:-8000}" ;;
  all)        "$0" lint && "$0" test && "$0" acceptance ;;
  *)          echo "用法: ./run.sh {build|lint|test|acceptance|serve|all}" >&2; exit 2 ;;
esac
