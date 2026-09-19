"""命令行入口：python -m agent.cli "做一个 XXX 命令行工具"。"""

from __future__ import annotations

import argparse
import sys

from rich.console import Console

from .config import Settings
from .sandbox import DockerUnavailable
from .task import Task
from .ui import Ui


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cloudide-agent", description="用一句话需求生成一个 Python 命令行工具并自测通过"
    )
    parser.add_argument("requirement", help="需求描述，比如：做一个统计文本词频的命令行工具")
    parser.add_argument("--name", help="项目名，默认从需求生成")
    parser.add_argument("--max-usd", type=float, help="单任务花费上限（美元）")
    parser.add_argument("--max-turns", type=int, help="单任务最大对话轮数")
    parser.add_argument("--keep-sandbox", action="store_true", help="任务结束后保留容器便于排查")
    parser.add_argument("--push", action="store_true", help="成功后推送到本地 Gitea 并等 CI 结果")
    parser.add_argument("-v", "--verbose", action="store_true", help="打印完整的工具输出")
    args = parser.parse_args(argv)

    settings = Settings.load()
    if args.max_usd:
        settings.max_usd = args.max_usd
    if args.max_turns:
        settings.max_turns = args.max_turns

    ui = Ui(Console(), verbose=args.verbose)
    task = Task(settings, args.requirement, args.name, ui)
    try:
        result = task.run(keep_sandbox=args.keep_sandbox)
    except DockerUnavailable as exc:
        ui.warn(str(exc))
        return 2
    ui.summary(result)

    if args.push:
        if not result.ok:
            ui.warn("任务没有全绿，跳过推送")
            return 1
        from .publish import GiteaError, publish

        try:
            pub = publish(settings, result.workspace, result.workspace.name, ui)
        except GiteaError as exc:
            ui.warn(str(exc))
            return 1
        ui.info(f"仓库：{pub.repo_url}")
        ui.info(f"CI：{pub.actions_url}（{pub.status}）")
        if pub.release_url:
            ui.info(f"制品：{pub.release_url}")
        return 0 if pub.status == "success" else 1

    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
