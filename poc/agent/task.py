"""一次任务的完整编排：脚手架 → 生成并锁定验收测试 → 编码循环 → 验证 → git 提交。"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import Settings
from .llm import LLM, BudgetExceeded
from .loop import CodingAgent, TurnLimitExceeded
from .sandbox import Sandbox
from .tools import ToolBox

# 这些路径 agent 不许改：验收测试、命令契约、CI 配置、lint 配置
LOCKED = ("tests/acceptance/", "run.sh", "pyproject.toml", ".gitea/")

# 只锁住已有文件还不够：新增一个优先级更高的配置文件同样能架空锁定配置，
# 比如 ruff.toml 会盖掉 pyproject.toml，根目录的 conftest.py 能劫持验收测试的夹具。
CONFIG_TRAPS = {
    "ruff.toml", ".ruff.toml", "setup.cfg", "tox.ini", "pytest.ini",
    "conftest.py", ".editorconfig",
}

CHECKS = (("lint", "静态检查"), ("test", "单元测试"), ("acceptance", "验收测试"))

TESTER_SYSTEM = """你是测试工程师，负责把用户需求翻译成可执行的验收测试。

被测对象是一个 Python 命令行工具，入口为 `python -m app.cli`。

写 pytest 测试，约束如下：
- 只用标准库和 pytest，不要 import 被测项目的内部模块，只测命令行的外部行为。
- 用 conftest 提供的 run_cli fixture 调用：result = run_cli("--help") 或 run_cli("add", "3", stdin="x")。
  result 是 subprocess.CompletedProcess，可以断言 result.returncode、result.stdout、result.stderr。
- 需要临时文件就用 pytest 的 tmp_path fixture。
- 写 5 到 8 个测试函数，覆盖主要功能、一个边界情况、一个错误处理。
- 断言要宽容：判断输出里是否包含关键内容，不要写死无关的格式、空格和标点。
- 每个测试函数上面用一行中文注释写清它对应的验收标准。
- 只输出一个 python 代码块，不要任何解释文字。"""

CODER_SYSTEM = """你是一个编码 agent，在断网的 Docker 沙箱里完成一个 Python 命令行工具项目。

环境：
- 当前目录就是项目根目录，模板已经初始化好，先读 AGENTS.md 了解约定。
- 沙箱没有网络，pip 装不了任何东西。只能用 Python 3.12 标准库；pytest 和 ruff 已经预装。
- 用 run 工具执行命令，比如 ./run.sh lint、./run.sh test、./run.sh acceptance。

规则：
1. 源码写在 src/app/ 下，入口是 src/app/cli.py 里的 main(argv) -> int。
2. tests/acceptance/ 是锁定的验收测试，不能改、也不要绕过。测试不通过就改源码。
3. run.sh、pyproject.toml、.gitea/ 同样不能改。
4. write_file 一次写一个完整文件，不要写省略号或代码片段。
5. 每改一轮就自己跑 ./run.sh lint、./run.sh test、./run.sh acceptance，按报错继续修。
6. 三项全绿后，用两三句话总结你做了什么，然后停止调用工具。

风格：少说多做，直接调用工具，不要复述计划。"""


def slugify(text: str, limit: int = 28) -> str:
    text = unicodedata.normalize("NFKD", text)
    ascii_only = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return (ascii_only or "task")[:limit].strip("-") or "task"


def extract_code(text: str) -> str:
    match = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.S)
    return (match.group(1) if match else text).strip() + "\n"


def git(workspace: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """在工作区执行 git；提交身份显式指定，避免依赖宿主机的全局配置。"""
    cmd = [
        "git", "-C", str(workspace),
        "-c", "user.name=cloudIDE agent",
        "-c", "user.email=agent@cloudide.local",
        *args,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败：{proc.stderr.strip()}")
    return proc


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass
class TaskResult:
    ok: bool
    workspace: Path
    rounds: int = 0
    checks: dict[str, bool] = field(default_factory=dict)
    usage: str = ""
    blocked: str | None = None
    tampered: list[str] = field(default_factory=list)
    removed_configs: list[str] = field(default_factory=list)


class Task:
    def __init__(self, settings: Settings, requirement: str, name: str | None, ui) -> None:
        self.settings = settings
        self.requirement = requirement.strip()
        self.ui = ui
        stamp = datetime.now().strftime("%m%d-%H%M%S")
        self.slug = slugify(name or requirement)
        self.workspace = settings.workspaces / f"{self.slug}-{stamp}"
        self.llm = LLM(settings)
        self.locked_snapshot: dict[str, tuple[str, bytes]] = {}
        self.initial_files: set[str] = set()

    # --- 各阶段 ---

    def scaffold(self) -> None:
        shutil.copytree(self.settings.templates / "python-cli", self.workspace)
        (self.workspace / "REQUIREMENT.md").write_text(
            f"# 需求\n\n{self.requirement}\n", encoding="utf-8"
        )
        git(self.workspace, "init", "-q", "-b", "main")
        git(self.workspace, "add", "-A")
        git(self.workspace, "commit", "-q", "-m", "chore: 用模板初始化项目")

    def generate_acceptance(self) -> str:
        msg = self.llm.chat(
            [
                {"role": "system", "content": TESTER_SYSTEM},
                {"role": "user", "content": f"用户需求：\n{self.requirement}"},
            ]
        )
        code = extract_code(msg.content or "")
        target = self.workspace / "tests" / "acceptance" / "test_acceptance.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(code, encoding="utf-8")
        git(self.workspace, "add", "-A")
        git(self.workspace, "commit", "-q", "-m", "test: 生成并锁定验收测试")
        return code

    def _files(self) -> list[Path]:
        return [
            p for p in sorted(self.workspace.rglob("*"))
            if p.is_file() and ".git" not in p.relative_to(self.workspace).parts
        ]

    def snapshot_locked(self) -> None:
        for rel in LOCKED:
            base = self.workspace / rel
            paths = sorted(base.rglob("*")) if base.is_dir() else [base]
            for path in paths:
                if path.is_file():
                    key = str(path.relative_to(self.workspace))
                    self.locked_snapshot[key] = (sha256(path), path.read_bytes())
        self.initial_files = {str(p.relative_to(self.workspace)) for p in self._files()}

    def sweep_config_traps(self) -> list[str]:
        """删掉 agent 新增的配置文件——它们能架空锁定的 lint / pytest 配置。"""
        removed = []
        for path in self._files():
            rel = str(path.relative_to(self.workspace))
            if path.name in CONFIG_TRAPS and rel not in self.initial_files:
                path.unlink()
                removed.append(rel)
        return removed

    def restore_tampered(self) -> list[str]:
        """检查锁定文件有没有被改（run 工具可以绕过 write_file 的限制），改了就还原。"""
        tampered = []
        for key, (digest, blob) in self.locked_snapshot.items():
            path = self.workspace / key
            if not path.exists() or sha256(path) != digest:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(blob)
                tampered.append(key)
        return tampered

    def verify(self, sandbox: Sandbox) -> dict[str, tuple[bool, str]]:
        results: dict[str, tuple[bool, str]] = {}
        for step, label in CHECKS:
            code, out = sandbox.exec(f"./run.sh {step}", timeout=240)
            results[step] = (code == 0, out.strip())
            self.ui.check(label, code == 0)
        return results

    # --- 主流程 ---

    def run(self, keep_sandbox: bool = False) -> TaskResult:
        self.ui.header(self.requirement, self.workspace, self.settings.model)
        self.scaffold()

        self.ui.stage("生成验收测试")
        tests = self.generate_acceptance()
        self.ui.code(tests)
        self.snapshot_locked()

        sandbox = Sandbox(
            name=f"cloudide-task-{self.slug}-{datetime.now().strftime('%H%M%S')}",
            workspace=self.workspace,
            image=self.settings.sandbox_image,
        ).start()
        self.ui.info(f"沙箱已启动：{sandbox.name}（断网，内存 {sandbox.memory}）")

        toolbox = ToolBox(
            workspace=self.workspace,
            sandbox=sandbox,
            locked=LOCKED,
            max_output=self.settings.max_tool_output,
            cmd_timeout=self.settings.cmd_timeout,
        )
        agent = CodingAgent(self.llm, toolbox, CODER_SYSTEM, self.ui.event)

        result = TaskResult(ok=False, workspace=self.workspace)
        try:
            first = (
                f"需求：\n{self.requirement}\n\n"
                f"验收测试 tests/acceptance/test_acceptance.py（只读）：\n"
                f"```python\n{tests}\n```\n\n"
                "请实现功能，让 lint、单元测试、验收测试全部通过。"
            )
            message = first
            for round_no in range(1, self.settings.max_repair_rounds + 2):
                result.rounds = round_no
                self.ui.stage(f"第 {round_no} 轮：编码")
                agent.send(message)

                notes = []
                tampered = self.restore_tampered()
                if tampered:
                    result.tampered.extend(tampered)
                    self.ui.warn(f"检测到锁定文件被修改，已还原：{', '.join(tampered)}")
                    notes.append(
                        f"你修改了锁定文件（{', '.join(tampered)}），已自动还原。"
                        "验收测试和命令契约不可改，请改源码来满足它们。"
                    )
                traps = self.sweep_config_traps()
                if traps:
                    result.removed_configs.extend(traps)
                    self.ui.warn(f"检测到新增配置文件，已删除：{', '.join(traps)}")
                    notes.append(
                        f"你新增了 {', '.join(traps)}，这类文件会覆盖锁定的 lint / 测试配置，"
                        "已被删除。不要通过改配置让检查变绿。"
                    )

                self.ui.stage(f"第 {round_no} 轮：验证")
                checks = self.verify(sandbox)
                result.checks = {k: v[0] for k, v in checks.items()}
                git(self.workspace, "add", "-A")
                git(
                    self.workspace, "commit", "-q", "--allow-empty",
                    "-m", f"feat: 第 {round_no} 轮实现（{sum(result.checks.values())}/3 通过）",
                )

                if all(result.checks.values()):
                    result.ok = True
                    break

                failed = [f"### {label}\n{checks[step][1][-2500:]}" for step, label in CHECKS
                          if not checks[step][0]]
                extra = ("\n\n注意：" + " ".join(notes)) if notes else ""
                message = (
                    "以下检查没有通过，请修复后再自测一遍：\n\n" + "\n\n".join(failed) + extra
                )
            else:
                result.blocked = f"修复 {self.settings.max_repair_rounds} 轮后仍未全绿"
        except (BudgetExceeded, TurnLimitExceeded) as exc:
            result.blocked = str(exc)
        except KeyboardInterrupt:
            result.blocked = "被用户中断"
        finally:
            result.usage = self.llm.usage.summary()
            self.write_meta(result)
            git(self.workspace, "add", "-A", check=False)
            git(self.workspace, "commit", "-q", "--allow-empty", "-m", "chore: 记录任务元数据",
                check=False)
            if keep_sandbox:
                self.ui.info(f"沙箱保留：docker exec -it {sandbox.name} sh")
            else:
                sandbox.stop()
        return result

    def write_meta(self, result: TaskResult) -> None:
        meta = {
            "requirement": self.requirement,
            "model": self.settings.model,
            "rounds": result.rounds,
            "checks": result.checks,
            "ok": result.ok,
            "blocked": result.blocked,
            "tampered": result.tampered,
            "removed_configs": result.removed_configs,
            "usage": result.usage,
            "usd": round(self.llm.usage.usd, 4),
            "finished_at": datetime.now().isoformat(timespec="seconds"),
        }
        meta_path = self.workspace / ".cloudide"
        meta_path.mkdir(exist_ok=True)
        (meta_path / "task.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
