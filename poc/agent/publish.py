"""推送到本地 Gitea 并等 CI 结果。

凭证只在宿主机这一侧使用，推送 URL 一次性拼出来，不写进仓库的 remote 配置。
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .config import Settings
from .task import git


class GiteaError(RuntimeError):
    pass


@dataclass
class PublishResult:
    repo_url: str
    actions_url: str
    status: str
    release_url: str | None = None


class Gitea:
    def __init__(self, settings: Settings) -> None:
        if not settings.gitea_token:
            raise GiteaError("缺少 GITEA_TOKEN，请先执行 infra/setup.sh")
        self.base = settings.gitea_url.rstrip("/")
        self.user = settings.gitea_user
        self.token = settings.gitea_token

    def api(self, path: str, data: dict | None = None, method: str = "GET") -> dict:
        req = urllib.request.Request(
            f"{self.base}/api/v1{path}",
            data=json.dumps(data).encode() if data is not None else None,
            method=method,
            headers={
                "Authorization": f"token {self.token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read()
        except urllib.error.HTTPError as exc:
            raise GiteaError(f"{method} {path} 失败：{exc.code} {exc.read().decode(errors='replace')}")
        return json.loads(body) if body else {}

    def ensure_repo(self, name: str) -> dict:
        try:
            return self.api(f"/repos/{self.user}/{name}")
        except GiteaError:
            return self.api(
                "/user/repos",
                {"name": name, "private": False, "default_branch": "main", "auto_init": False},
                method="POST",
            )

    def push(self, workspace: Path, name: str) -> str:
        quoted = urllib.parse.quote(self.token, safe="")
        host = self.base.split("://", 1)[1]
        url = f"http://{self.user}:{quoted}@{host}/{self.user}/{name}.git"
        proc = subprocess.run(
            ["git", "-C", str(workspace), "push", "--quiet", "--force", url, "main"],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise GiteaError(f"推送失败：{proc.stderr.strip().replace(self.token, '***')}")
        return git(workspace, "rev-parse", "HEAD").stdout.strip()

    def wait_ci(self, name: str, sha: str, timeout: int = 900) -> str:
        """轮询 commit status，Gitea Actions 会把每次运行的结果写在这里。"""
        deadline = time.time() + timeout
        last = "pending"
        while time.time() < deadline:
            data = self.api(f"/repos/{self.user}/{name}/commits/{sha}/status")
            last = data.get("state") or "pending"
            if last in {"success", "failure", "error"}:
                return last
            time.sleep(4)
        return f"timeout（最后状态 {last}）"

    def latest_release(self, name: str) -> str | None:
        try:
            releases = self.api(f"/repos/{self.user}/{name}/releases")
        except GiteaError:
            return None
        if isinstance(releases, list) and releases:
            assets = releases[0].get("assets") or []
            if assets:
                return assets[0].get("browser_download_url")
            return releases[0].get("html_url")
        return None


def publish(settings: Settings, workspace: Path, name: str, ui) -> PublishResult:
    gitea = Gitea(settings)
    ui.stage("推送到 Gitea 并触发 CI")
    gitea.ensure_repo(name)
    sha = gitea.push(workspace, name)
    repo_url = f"{gitea.base}/{gitea.user}/{name}"
    ui.info(f"已推送 {sha[:7]} → {repo_url}")

    status = gitea.wait_ci(name, sha)
    ui.check("CI", status == "success")
    release = gitea.latest_release(name) if status == "success" else None
    if release:
        ui.info(f"制品：{release}")
    return PublishResult(
        repo_url=repo_url,
        actions_url=f"{repo_url}/actions",
        status=status,
        release_url=release,
    )
