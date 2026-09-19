"""一次任务的完整编排。

两种模式：
- create：用模板初始化一个新项目
- iterate：在已有项目上继续改，历史验收测试继续生效，新要求追加新的验收测试
"""

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
from .templates import TEMPLATES, TemplateSpec
from .tools import ToolBox

# 只锁住已有文件还不够：新增一个优先级更高的配置文件同样能架空锁定配置，
# 比如 ruff.toml 会盖掉 pyproject.toml，根目录的 conftest.py 能劫持验收测试的夹具。
CONFIG_TRAPS = {
    "ruff.toml", ".ruff.toml", "setup.cfg", "tox.ini", "pytest.ini", ".editorconfig",
    "conftest.py",
}
# conftest.py 放在 tests/unit/ 下只影响它自己的单元测试，是正当用法；
# 放在根目录或 tests/ 下会一并作用到验收测试，能劫持夹具，必须拦。
CONFTEST_ALLOWED_PREFIX = "tests/unit/"

ITERATE_MESSAGE = """这是一个已经做好并上线的项目，用户提出了新的要求。

项目此前的需求：
{history}

本次要求：
{requirement}

针对本次要求新增的验收测试 {test_path}（只读，不能改）：
```python
{tests}
```

注意：
- 先用 list_files 和 read_file 看清现有代码再动手，在现有基础上改，不要推倒重来。
- 以前的验收测试也必须继续通过，别改坏已有功能。
- 改完跑 ./run.sh lint、./run.sh test、./run.sh acceptance，全绿后总结两三句就停。"""


def slugify(text: str, limit: int = 28) -> str:
    text = unicodedata.normalize("NFKD", text)
    ascii_only = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return (ascii_only or "site")[:limit].strip("-") or "site"


def extract_code(text: str) -> str:
    match = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.S)
    return (match.group(1) if match else text).strip() + "\n"


def git(workspace: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """在工作区执行 git；提交身份显式指定，避免依赖宿主机的全局配置。"""
    cmd = [
        "git", "-C", str(workspace),
        "-c", "user.name=codeless agent",
        "-c", "user.email=agent@codeless.local",
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
    usd: float = 0.0
    blocked: str | None = None
    tampered: list[str] = field(default_factory=list)
    removed_configs: list[str] = field(default_factory=list)


class Task:
    def __init__(
        self,
        settings: Settings,
        requirement: str,
        ui,
        *,
        template: TemplateSpec | str | None = None,
        name: str | None = None,
        workspace: Path | None = None,
        history: list[str] | None = None,
    ) -> None:
        self.settings = settings
        self.requirement = requirement.strip()
        self.ui = ui
        self.history = history or []
        if isinstance(template, str) or template is None:
            template = TEMPLATES[template or settings.template]
        self.spec = template
        if workspace is not None:
            self.mode = "iterate"
            self.workspace = workspace
            self.slug = workspace.name
        else:
            self.mode = "create"
            stamp = datetime.now().strftime("%m%d-%H%M%S")
            self.slug = slugify(name or requirement)
            self.workspace = settings.workspaces / f"{self.slug}-{stamp}"
        self.llm = LLM(settings)
        self.locked_snapshot: dict[str, tuple[str, bytes]] = {}
        self.initial_files: set[str] = set()

    @property
    def mount_source(self) -> Path:
        """传给 docker -v 的路径。编排服务自己跑在容器里时，必须用宿主机上的路径。"""
        if self.settings.workspaces_host:
            return self.settings.workspaces_host / self.workspace.name
        return self.workspace

    # --- 各阶段 ---

    def scaffold(self) -> None:
        shutil.copytree(self.settings.templates / self.spec.dir_name, self.workspace)
        (self.workspace / "REQUIREMENT.md").write_text(
            f"# 需求\n\n{self.requirement}\n", encoding="utf-8"
        )
        git(self.workspace, "init", "-q", "-b", "main")
        git(self.workspace, "add", "-A")
        git(self.workspace, "commit", "-q", "-m", "chore: 用模板初始化项目")

    def acceptance_path(self) -> Path:
        base = self.workspace / "tests" / "acceptance"
        base.mkdir(parents=True, exist_ok=True)
        if self.mode == "create":
            return base / "test_acceptance.py"
        existing = len(list(base.glob("test_*.py")))
        return base / f"test_iteration_{existing + 1}.py"

    def tests_runnable(self, sandbox: Sandbox, rel: Path) -> tuple[bool, str]:
        """确认这份测试本身跑得起来：没有语法错、没有未定义的名字、能被 pytest 收集。"""
        code, out = sandbox.exec(
            f"ruff check --isolated --select E9,F821,F811 {rel} "
            f"&& python -m pytest --collect-only -q {rel}",
            timeout=120,
        )
        return code == 0, out.strip()

    def generate_acceptance(self, sandbox: Sandbox) -> tuple[str, Path]:
        """生成验收测试，并且在锁定之前先确认它自己跑得起来。

        测试一旦锁定 agent 就改不了。如果这份测试本身有语法错误、或者把夹具当全局变量用，
        agent 会被困在一个无解的任务里反复烧钱——必须在这一步挡住。
        """
        if self.mode == "create":
            user = f"用户需求：\n{self.requirement}"
        else:
            history = "\n".join(f"{i + 1}. {h}" for i, h in enumerate(self.history)) or "（无）"
            user = (
                f"这是一个已有项目，此前的需求：\n{history}\n\n"
                f"用户这次提出的新要求：\n{self.requirement}\n\n"
                "只针对这次的新要求写 2 到 5 个测试，不要重复已有功能的测试。"
            )
        messages = [
            {"role": "system", "content": self.spec.tester_system},
            {"role": "user", "content": user},
        ]
        target = self.acceptance_path()
        rel = target.relative_to(self.workspace)
        code = ""
        for attempt in range(1, 4):
            msg = self.llm.chat(messages)
            code = extract_code(msg.content or "")
            target.write_text(code, encoding="utf-8")
            ok, problem = self.tests_runnable(sandbox, rel)
            if ok:
                break
            self.ui.warn(f"生成的验收测试自己跑不起来，重新生成（第 {attempt} 次）")
            self.ui.detail(problem[-800:])
            messages.append({"role": "assistant", "content": msg.content or ""})
            messages.append(
                {
                    "role": "user",
                    "content": "这份测试跑不起来，报错如下。修正后重新输出完整的测试文件：\n"
                    + problem[-1500:],
                }
            )
        git(self.workspace, "add", "-A")
        git(self.workspace, "commit", "-q", "-m", f"test: 生成并锁定验收测试（{target.name}）")
        return code, target

    def _files(self) -> list[Path]:
        return [
            p for p in sorted(self.workspace.rglob("*"))
            if p.is_file() and ".git" not in p.relative_to(self.workspace).parts
        ]

    def snapshot_locked(self) -> None:
        for rel in self.spec.locked:
            base = self.workspace / rel
            paths = sorted(base.rglob("*")) if base.is_dir() else [base]
            for path in paths:
                if path.is_file():
                    key = str(path.relative_to(self.workspace))
                    self.locked_snapshot[key] = (sha256(path), path.read_bytes())
        self.initial_files = {str(p.relative_to(self.workspace)) for p in self._files()}

    def restore_tampered(self) -> list[str]:
        """锁定文件被改就还原——run 工具能绕过 write_file 的限制，所以必须查。"""
        tampered = []
        for key, (digest, blob) in self.locked_snapshot.items():
            path = self.workspace / key
            if not path.exists() or sha256(path) != digest:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(blob)
                tampered.append(key)
        return tampered

    def sweep_config_traps(self) -> list[str]:
        """删掉 agent 新增的配置文件——它们能架空锁定的 lint / pytest 配置。"""
        removed = []
        for path in self._files():
            rel = str(path.relative_to(self.workspace))
            if rel in self.initial_files or path.name not in CONFIG_TRAPS:
                continue
            if path.name == "conftest.py" and rel.startswith(CONFTEST_ALLOWED_PREFIX):
                continue
            path.unlink()
            removed.append(rel)
        return removed

    def verify(self, sandbox: Sandbox) -> dict[str, tuple[bool, str]]:
        results: dict[str, tuple[bool, str]] = {}
        for step, label in self.spec.checks:
            code, out = sandbox.exec(f"./run.sh {step}", timeout=300)
            results[step] = (code == 0, out.strip())
            self.ui.check(label, code == 0)
            if code != 0:
                # 失败原因也要给用户看见，不能只喂回给模型
                self.ui.detail(out.strip()[-1200:])
        return results

    # --- 主流程 ---

    def run(self, keep_sandbox: bool = False) -> TaskResult:
        self.ui.header(self.requirement, self.workspace, self.settings.model)
        if self.mode == "create":
            self.scaffold()

        # 沙箱要先起来，验收测试得在里面先跑一遍确认可用，才能锁定
        sandbox = Sandbox(
            name=f"codeless-task-{self.slug[:30]}-{datetime.now().strftime('%H%M%S')}",
            workspace=self.workspace,
            image=self.settings.sandbox_image,
            mount_source=self.mount_source,
        ).start()
        self.ui.info(f"沙箱已启动：{sandbox.name}（断网，内存 {sandbox.memory}）")

        self.ui.stage("生成验收测试")
        try:
            tests, test_path = self.generate_acceptance(sandbox)
        except Exception:
            sandbox.stop()
            raise
        self.ui.code(tests)
        self.snapshot_locked()

        toolbox = ToolBox(
            workspace=self.workspace,
            sandbox=sandbox,
            locked=self.spec.locked,
            max_output=self.settings.max_tool_output,
            cmd_timeout=self.settings.cmd_timeout,
        )
        agent = CodingAgent(self.llm, toolbox, self.spec.coder_system, self.ui)

        result = TaskResult(ok=False, workspace=self.workspace)
        try:
            rel_test = test_path.relative_to(self.workspace)
            if self.mode == "create":
                message = self.spec.first_message.format(
                    requirement=self.requirement, tests=tests
                )
            else:
                history = "\n".join(f"{i + 1}. {h}" for i, h in enumerate(self.history)) or "（无）"
                message = ITERATE_MESSAGE.format(
                    history=history,
                    requirement=self.requirement,
                    tests=tests,
                    test_path=rel_test,
                )

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
                        "验收测试和命令契约不可改，请改实现来满足它们。"
                    )
                traps = self.sweep_config_traps()
                if traps:
                    result.removed_configs.extend(traps)
                    self.ui.warn(f"检测到新增配置文件，已删除：{', '.join(traps)}")
                    notes.append(
                        f"你新增了 {', '.join(traps)}，这类文件会覆盖锁定的 lint / 测试配置，"
                        "已被删除。不要通过改配置让检查变绿。如果你是为了给验收测试补夹具才建它，"
                        "说明验收测试本身有问题——直接在回复里说清楚卡在哪，不要反复尝试。"
                    )

                self.ui.stage(f"第 {round_no} 轮：验证")
                checks = self.verify(sandbox)
                result.checks = {k: v[0] for k, v in checks.items()}
                git(self.workspace, "add", "-A")
                git(
                    self.workspace, "commit", "-q", "--allow-empty",
                    "-m", f"feat: {self.requirement[:40]}"
                          f"（第 {round_no} 轮，{sum(result.checks.values())}"
                          f"/{len(self.spec.checks)} 通过）",
                )

                if all(result.checks.values()):
                    result.ok = True
                    break

                failed = [
                    f"### {label}\n{checks[step][1][-2500:]}"
                    for step, label in self.spec.checks
                    if not checks[step][0]
                ]
                extra = ("\n\n注意：" + " ".join(notes)) if notes else ""
                message = (
                    "以下检查没有通过，请修复后再自测一遍：\n\n" + "\n\n".join(failed) + extra
                )
            else:
                result.blocked = f"修复 {self.settings.max_repair_rounds} 轮后仍未全绿"
        except (BudgetExceeded, TurnLimitExceeded) as exc:
            # 触达上限不代表活没干完。最后再验一次，全绿就照样算成功、照样发布，
            # 不然就是白烧了钱又把做好的东西扔掉。
            result.blocked = str(exc)
            self.ui.warn(f"{exc}，先做最后一次验证")
            self.restore_tampered()
            self.sweep_config_traps()
            checks = self.verify(sandbox)
            result.checks = {k: v[0] for k, v in checks.items()}
            if all(result.checks.values()):
                result.ok = True
                result.blocked = f"{exc}（但检查已经全绿，按完成处理）"
            git(self.workspace, "add", "-A", check=False)
            git(self.workspace, "commit", "-q", "--allow-empty",
                "-m", f"feat: {self.requirement[:40]}（触达上限时的最终状态）", check=False)
        except KeyboardInterrupt:
            result.blocked = "被用户中断"
        finally:
            result.usage = self.llm.usage.summary()
            result.usd = round(self.llm.usage.usd, 4)
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
        meta_path = self.workspace / ".codeless"
        meta_path.mkdir(exist_ok=True)
        file = meta_path / "task.json"
        history = []
        if file.exists():
            try:
                history = json.loads(file.read_text(encoding="utf-8")).get("runs", [])
            except json.JSONDecodeError:
                history = []
        history.append(
            {
                "mode": self.mode,
                "requirement": self.requirement,
                "rounds": result.rounds,
                "checks": result.checks,
                "ok": result.ok,
                "blocked": result.blocked,
                "tampered": result.tampered,
                "removed_configs": result.removed_configs,
                "usage": result.usage,
                "usd": result.usd,
                "finished_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        file.write_text(
            json.dumps(
                {"template": self.spec.key, "model": self.settings.model, "runs": history},
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
