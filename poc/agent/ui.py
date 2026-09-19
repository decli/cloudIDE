"""终端输出：让每一步都看得见——模型在说什么、执行了什么命令、花了多少钱。"""

from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax


class Ui:
    def __init__(self, console: Console | None = None, verbose: bool = False) -> None:
        self.console = console or Console()
        self.verbose = verbose

    def header(self, requirement: str, workspace: Path, model: str) -> None:
        self.console.print(
            Panel(
                f"[bold]需求[/bold]：{requirement}\n"
                f"[bold]模型[/bold]：{model}\n"
                f"[bold]工作区[/bold]：{workspace}",
                title="cloudIDE POC",
                border_style="cyan",
            )
        )

    def stage(self, text: str) -> None:
        self.console.rule(f"[bold cyan]{text}")

    def info(self, text: str) -> None:
        self.console.print(f"[dim]· {text}[/dim]")

    def warn(self, text: str) -> None:
        self.console.print(f"[yellow]! {text}[/yellow]")

    def check(self, label: str, ok: bool) -> None:
        mark = "[green]✓[/green]" if ok else "[red]✗[/red]"
        self.console.print(f"  {mark} {label}")

    def code(self, text: str, lang: str = "python") -> None:
        self.console.print(Syntax(text, lang, theme="ansi_dark", word_wrap=True))

    def event(self, kind: str, payload: str) -> None:
        if kind == "say":
            self.console.print(f"[white]{payload}[/white]")
        elif kind == "run":
            self.console.print(f"[magenta]$ {self._arg(payload, 'cmd')}[/magenta]")
        elif kind == "write_file":
            path = self._arg(payload, "path")
            self.console.print(f"[green]写入[/green] {path}")
        elif kind == "read_file":
            self.console.print(f"[dim]读取 {self._arg(payload, 'path')}[/dim]")
        elif kind == "list_files":
            self.console.print("[dim]列目录[/dim]")
        elif kind == "result":
            first = payload.strip().splitlines()[0] if payload.strip() else ""
            if self.verbose:
                self.console.print(f"[dim]{payload[:2000]}[/dim]")
            elif first and first != "[exit=0]":
                self.console.print(f"[dim]  {first[:160]}[/dim]")

    @staticmethod
    def _arg(payload: str, key: str) -> str:
        try:
            return str(json.loads(payload).get(key, ""))[:200]
        except Exception:
            return payload[:200]

    def summary(self, result) -> None:
        status = "[green]交付成功[/green]" if result.ok else "[red]未完成[/red]"
        lines = [
            f"结果：{status}",
            f"轮次：{result.rounds}",
            "检查：" + "  ".join(
                f"{k}={'✓' if v else '✗'}" for k, v in (result.checks or {}).items()
            ),
            f"用量：{result.usage}",
            f"工作区：{result.workspace}",
        ]
        if result.tampered:
            lines.append(f"[yellow]曾试图修改锁定文件：{', '.join(result.tampered)}[/yellow]")
        if result.removed_configs:
            lines.append(
                f"[yellow]曾新增配置文件绕过检查：{', '.join(result.removed_configs)}[/yellow]"
            )
        if result.blocked:
            lines.append(f"[red]阻塞原因：{result.blocked}[/red]")
        self.console.print(Panel("\n".join(lines), title="任务总结", border_style="cyan"))
