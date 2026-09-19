"""Agent 能调用的四个工具。

文件读写在宿主机的工作目录里做（容器用 bind mount 看到同一份），
命令在沙箱容器里跑。锁定目录（验收测试、CI 配置）拒绝写入。
"""

from __future__ import annotations

import json
from pathlib import Path

from .sandbox import Sandbox

MAX_READ_CHARS = 40_000

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "列出项目里的文件（相对路径）",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "相对目录，默认项目根"}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取一个文本文件的完整内容",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "相对路径"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "写入文件，覆盖原内容，父目录自动创建。必须写完整文件，不能写片段或省略号。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对路径"},
                    "content": {"type": "string", "description": "完整文件内容"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run",
            "description": "在沙箱容器里执行 shell 命令，工作目录是项目根目录，没有网络",
            "parameters": {
                "type": "object",
                "properties": {"cmd": {"type": "string", "description": "shell 命令"}},
                "required": ["cmd"],
            },
        },
    },
]

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".ruff_cache", "dist", ".venv"}


class ToolBox:
    def __init__(
        self,
        workspace: Path,
        sandbox: Sandbox,
        locked: tuple[str, ...] = (),
        max_output: int = 6000,
        cmd_timeout: int = 120,
    ) -> None:
        self.workspace = workspace.resolve()
        self.sandbox = sandbox
        self.locked = locked
        self.max_output = max_output
        self.cmd_timeout = cmd_timeout
        self.commands: list[tuple[str, int]] = []

    # --- 内部 ---

    def _safe(self, rel: str) -> Path:
        target = (self.workspace / rel).resolve()
        if target != self.workspace and self.workspace not in target.parents:
            raise ValueError(f"路径越界，只能在项目目录内操作：{rel}")
        return target

    def _is_locked(self, rel: str) -> bool:
        norm = rel.replace("\\", "/").lstrip("./")
        return any(norm == p.rstrip("/") or norm.startswith(p) for p in self.locked)

    def _truncate(self, text: str) -> str:
        if len(text) <= self.max_output:
            return text
        half = self.max_output // 2
        return f"{text[:half]}\n…（省略 {len(text) - self.max_output} 字）…\n{text[-half:]}"

    # --- 工具实现 ---

    def list_files(self, path: str = ".") -> str:
        base = self._safe(path)
        if not base.exists():
            return f"目录不存在：{path}"
        lines = []
        for p in sorted(base.rglob("*")):
            if any(part in SKIP_DIRS for part in p.parts):
                continue
            if p.is_file():
                lines.append(f"{p.relative_to(self.workspace)}  ({p.stat().st_size} B)")
        return "\n".join(lines) or "（空目录）"

    def read_file(self, path: str) -> str:
        target = self._safe(path)
        if not target.is_file():
            return f"文件不存在：{path}"
        text = target.read_text(encoding="utf-8", errors="replace")
        if len(text) > MAX_READ_CHARS:
            text = text[:MAX_READ_CHARS] + "\n…（文件过大，已截断）"
        return text

    def write_file(self, path: str, content: str) -> str:
        if self._is_locked(path):
            return (
                f"拒绝写入：{path} 属于锁定目录（验收测试和 CI 配置不允许修改）。"
                "如果测试不通过，请改源码。"
            )
        target = self._safe(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"已写入 {path}（{len(content)} 字）"

    def run(self, cmd: str) -> str:
        code, out = self.sandbox.exec(cmd, timeout=self.cmd_timeout)
        self.commands.append((cmd, code))
        return f"[exit={code}]\n{self._truncate(out.strip())}"

    # --- 分发 ---

    def dispatch(self, name: str, arguments: str) -> str:
        try:
            args = json.loads(arguments or "{}")
        except json.JSONDecodeError as exc:
            return f"参数不是合法 JSON：{exc}"
        try:
            if name == "list_files":
                return self.list_files(args.get("path", "."))
            if name == "read_file":
                return self.read_file(args["path"])
            if name == "write_file":
                return self.write_file(args["path"], args.get("content", ""))
            if name == "run":
                return self.run(args["cmd"])
            return f"未知工具：{name}"
        except KeyError as exc:
            return f"缺少参数：{exc}"
        except Exception as exc:  # 工具出错也要把信息喂回模型，让它自己纠正
            return f"工具执行出错：{exc}"
