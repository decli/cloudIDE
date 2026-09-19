"""命令行入口。模板里只是占位，实现由 agent 完成。"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    print("还没有实现任何功能", args)
    return 0


def _entry() -> None:
    sys.exit(main())


if __name__ == "__main__":
    _entry()
