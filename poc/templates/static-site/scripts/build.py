"""构建脚本：把 src/ 产出到 dist/。

默认只是原样复制。如果需要从数据生成页面（比如用统一模板渲染多篇文章），
可以在这里加逻辑，但必须保证最终产出 dist/index.html。
"""

from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
DIST = ROOT / "dist"


def build() -> None:
    if DIST.exists():
        shutil.rmtree(DIST)
    shutil.copytree(SRC, DIST)
    print(f"已构建 {DIST.relative_to(ROOT)}")


if __name__ == "__main__":
    build()
