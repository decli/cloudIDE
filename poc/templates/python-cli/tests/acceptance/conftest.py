"""验收测试的公共夹具：只通过命令行外部行为测试，不碰内部实现。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def run_cli():
    """调用被测 CLI，返回 subprocess.CompletedProcess。"""

    def _run(*args: object, stdin: str = "", timeout: int = 30, cwd: Path | None = None):
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
        return subprocess.run(
            [sys.executable, "-m", "app.cli", *[str(a) for a in args]],
            input=stdin,
            capture_output=True,
            text=True,
            cwd=str(cwd or ROOT),
            env=env,
            timeout=timeout,
        )

    return _run
