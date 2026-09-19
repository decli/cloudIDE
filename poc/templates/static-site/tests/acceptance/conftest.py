"""验收测试夹具：先构建，再起一个本地静态服务器，通过 HTTP 访问站点。

只测用户能看到的东西，不碰实现细节。
"""

from __future__ import annotations

import functools
import http.server
import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import pytest

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "dist"


@dataclass
class Page:
    """一次 HTTP 访问的结果。"""

    status: int
    text: str
    url: str

    def links(self) -> list[str]:
        """页面里的站内链接（去掉外链、锚点、mailto 之类）。"""
        found = re.findall(r'href\s*=\s*["\']([^"\']+)["\']', self.text, re.I)
        out = []
        for href in found:
            if href.startswith(("http://", "https://", "//", "#", "mailto:", "tel:")):
                continue
            out.append(href.split("#", 1)[0])
        return [h for h in out if h]


class Site:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.dist = DIST

    def get(self, path: str = "/") -> Page:
        if not path.startswith("/"):
            path = "/" + path
        url = self.base_url + path
        try:
            with urlopen(url, timeout=10) as resp:
                return Page(resp.status, resp.read().decode("utf-8", "replace"), url)
        except HTTPError as exc:
            return Page(exc.code, exc.read().decode("utf-8", "replace"), url)
        except URLError as exc:  # 连不上也当成一次失败的访问，让断言给出可读信息
            return Page(0, f"无法访问 {url}: {exc}", url)


@pytest.fixture(scope="session")
def site():
    proc = subprocess.run(
        ["./run.sh", "build"], cwd=ROOT, capture_output=True, text=True, timeout=180
    )
    assert proc.returncode == 0, f"构建失败：\n{proc.stdout}\n{proc.stderr}"
    assert (DIST / "index.html").is_file(), "构建产物里没有 dist/index.html"

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(DIST))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    httpd.RequestHandlerClass.log_message = lambda *args, **kwargs: None  # 别刷屏
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield Site(f"http://127.0.0.1:{httpd.server_address[1]}")
    finally:
        httpd.shutdown()
