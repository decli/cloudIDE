#!/bin/sh
# 一键拉起整套本地环境：Gitea、Actions runner、站点服务器、Web 界面。
# 幂等：重复执行不会破坏已有数据。
set -e
cd "$(dirname "$0")"

POC_DIR=$(cd .. && pwd)
PASSWORD="${GITEA_PASSWORD:-cloudide-poc-2026}"
USERNAME="${GITEA_USER:-cloudide}"

if [ ! -f ../.env ]; then
  echo "缺少 ../.env，先 cp .env.example .env 并填入 LLM_API_KEY" >&2
  exit 1
fi

get() { grep -E "^$1=" ../.env | head -1 | cut -d= -f2- ; }

mkdir -p gitea-data runner-data ../workspaces

echo "==> 构建沙箱镜像"
docker build -q -t cloudide-sandbox-py:latest -f ../docker/sandbox-py.Dockerfile .. >/dev/null

echo "==> 启动 Gitea"
docker compose up -d gitea

printf "==> 等待 Gitea 就绪"
for _ in $(seq 1 60); do
  if curl -sf http://localhost:3000/api/healthz >/dev/null 2>&1; then echo " OK"; break; fi
  printf "."
  sleep 2
done

echo "==> 创建管理员 $USERNAME（已存在则跳过）"
docker compose exec -T -u git gitea gitea admin user create \
  --admin --username "$USERNAME" --password "$PASSWORD" \
  --email "$USERNAME@cloudide.local" --must-change-password=false 2>/dev/null \
  || echo "    用户已存在，跳过"

echo "==> 生成凭证"
API_TOKEN=$(docker compose exec -T -u git gitea gitea admin user generate-access-token \
  --username "$USERNAME" --token-name "poc-$(date +%s)" \
  --scopes write:repository,write:user --raw | tr -d '\r\n ')
RUNNER_TOKEN=$(docker compose exec -T -u git gitea gitea actions generate-runner-token \
  | tr -d '\r\n ')

cat > .env <<EOF
RUNNER_TOKEN=$RUNNER_TOKEN
LLM_API_KEY=$(get LLM_API_KEY)
LLM_BASE_URL=$(get LLM_BASE_URL)
LLM_MODEL=$(get LLM_MODEL)
DEEPSEEK_API_KEY=$(get DEEPSEEK_API_KEY)
DEEPSEEK_BASE_URL=$(get DEEPSEEK_BASE_URL)
DEEPSEEK_MODEL=$(get DEEPSEEK_MODEL)
MAX_USD=$(get MAX_USD)
GITEA_USER=$USERNAME
GITEA_TOKEN=$API_TOKEN
WORKSPACES_HOST_DIR=$POC_DIR/workspaces
EOF

echo "==> 把 Gitea 凭证写回 ../.env"
python3 - "$USERNAME" "$API_TOKEN" <<'PY'
import sys
from pathlib import Path

user, token = sys.argv[1], sys.argv[2]
path = Path("../.env").resolve()
lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
out, seen = [], set()
for line in lines:
    key = line.split("=", 1)[0].strip()
    if key == "GITEA_TOKEN":
        out.append(f"GITEA_TOKEN={token}")
    elif key == "GITEA_USER":
        out.append(f"GITEA_USER={user}")
    else:
        out.append(line)
    seen.add(key)
if "GITEA_TOKEN" not in seen:
    out.append(f"GITEA_TOKEN={token}")
if "GITEA_USER" not in seen:
    out.append(f"GITEA_USER={user}")
path.write_text("\n".join(out) + "\n", encoding="utf-8")
print(f"    已写入 {path}")
PY

echo "==> 启动 runner、站点服务器、Web 界面"
docker compose up -d --force-recreate runner
docker compose up -d pages
docker compose up -d --build web

echo
echo "完成："
echo "  Web 界面   http://localhost:8000"
echo "  站点入口   http://localhost:8080"
echo "  Gitea      http://localhost:3000   $USERNAME / $PASSWORD"
echo
echo "看日志：docker compose -f infra/docker-compose.yml logs -f web"
