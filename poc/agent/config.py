"""配置：环境变量、模型、价格、各类上限。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# DeepSeek 价格（美元 / 百万 token），只用于成本估算
PRICE_PEAK = {"cache_hit": 0.006, "cache_miss": 0.30, "output": 1.20}
PRICE_OFFPEAK = {"cache_hit": 0.003, "cache_miss": 0.15, "output": 0.60}


def load_env(path: Path | None = None) -> None:
    """极简 .env 读取，不引第三方依赖。已存在的环境变量优先。"""
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def price_table(now: datetime | None = None) -> dict[str, float]:
    """高峰时段为周一至周五 UTC 01:00-04:00 和 06:00-10:00，其余时段半价。"""
    now = now or datetime.now(timezone.utc)
    peak = now.weekday() < 5 and (1 <= now.hour < 4 or 6 <= now.hour < 10)
    return PRICE_PEAK if peak else PRICE_OFFPEAK


@dataclass
class Settings:
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-flash"
    sandbox_image: str = "cloudide-sandbox-py:latest"
    workspaces: Path = ROOT / "workspaces"
    templates: Path = ROOT / "templates"
    template: str = "static-site"

    # 编排服务自己跑在容器里时，创建沙箱要用宿主机上的路径
    workspaces_host: Path | None = None

    # 上限：任何一层循环都必须有边界
    max_turns: int = 40
    max_usd: float = 0.50
    max_repair_rounds: int = 3
    cmd_timeout: int = 120
    max_tool_output: int = 6000

    # 本地 Gitea：gitea_url 是服务之间互访的地址，public 是给用户点的
    gitea_url: str = "http://localhost:3000"
    gitea_public_url: str = ""
    gitea_user: str = "cloudide"
    gitea_token: str = ""

    # 部署好的站点入口
    pages_url: str = "http://localhost:8080"

    @classmethod
    def load(cls) -> Settings:
        load_env()
        key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not key:
            raise SystemExit("缺少 DEEPSEEK_API_KEY，请在 poc/.env 里配置（参考 .env.example）")
        s = cls(api_key=key)
        s.base_url = os.environ.get("DEEPSEEK_BASE_URL", s.base_url)
        s.model = os.environ.get("DEEPSEEK_MODEL", s.model)
        s.gitea_url = os.environ.get("GITEA_URL", s.gitea_url).rstrip("/")
        s.gitea_public_url = os.environ.get("GITEA_PUBLIC_URL", s.gitea_url).rstrip("/")
        s.gitea_user = os.environ.get("GITEA_USER", s.gitea_user)
        s.gitea_token = os.environ.get("GITEA_TOKEN", s.gitea_token)
        s.pages_url = os.environ.get("PAGES_URL", s.pages_url).rstrip("/")
        s.template = os.environ.get("TEMPLATE", s.template)
        host = os.environ.get("WORKSPACES_HOST_DIR", "").strip()
        s.workspaces_host = Path(host) if host else None
        s.max_usd = float(os.environ.get("MAX_USD", s.max_usd))
        s.max_turns = int(os.environ.get("MAX_TURNS", s.max_turns))
        s.workspaces.mkdir(parents=True, exist_ok=True)
        return s
