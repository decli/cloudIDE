"""把 dist/app.pyz 发布成 Gitea Release，在 CI 里执行，只用标准库。"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BASE = os.environ.get("GITEA_INTERNAL_URL", "http://gitea:3000").rstrip("/")
REPO = os.environ.get("GITHUB_REPOSITORY", "")
SHA = os.environ.get("GITHUB_SHA", "dev")
TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("GITEA_TOKEN") or ""
ASSET = Path("dist/app.pyz")


def api(path: str, data: dict | None = None, method: str = "GET") -> dict:
    req = urllib.request.Request(
        f"{BASE}/api/v1{path}",
        data=json.dumps(data).encode() if data is not None else None,
        method=method,
        headers={"Authorization": f"token {TOKEN}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read()
    return json.loads(body) if body else {}


def upload(release_id: int, path: Path) -> dict:
    boundary = uuid.uuid4().hex
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="attachment"; filename="{path.name}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode()
    body = head + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{BASE}/api/v1/repos/{REPO}/releases/{release_id}/assets?name={path.name}",
        data=body,
        method="POST",
        headers={
            "Authorization": f"token {TOKEN}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read() or b"{}")


def main() -> int:
    if not TOKEN or not REPO:
        print("缺少 GITHUB_TOKEN 或 GITHUB_REPOSITORY，跳过发布", file=sys.stderr)
        return 1
    if not ASSET.exists():
        print(f"制品不存在：{ASSET}", file=sys.stderr)
        return 1

    tag = f"build-{SHA[:7]}"
    try:
        release = api(
            f"/repos/{REPO}/releases",
            {"tag_name": tag, "name": tag, "body": f"CI 自动发布，commit {SHA[:7]}",
             "target_commitish": "main"},
            method="POST",
        )
    except urllib.error.HTTPError as exc:
        if exc.code != 409:
            print(f"创建 Release 失败：{exc.code} {exc.read().decode(errors='replace')}",
                  file=sys.stderr)
            return 1
        release = api(f"/repos/{REPO}/releases/tags/{tag}")

    asset = upload(release["id"], ASSET)
    print(f"已发布 {tag}：{asset.get('browser_download_url', '(无下载地址)')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
