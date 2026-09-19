"""cloudIDE POC 的 Web 界面。

像聊天一样：第一句话把项目做出来，后面每一句话在同一个项目上继续改，
改完重新跑测试、重新部署，站点地址不变。
"""

from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from agent.config import Settings
from agent.publish import publish
from agent.task import Task
from agent.templates import DEFAULT_TEMPLATE, TEMPLATES

HERE = Path(__file__).resolve().parent
SETTINGS = Settings.load()

app = FastAPI(title="cloudIDE POC")


@dataclass
class Project:
    id: str
    title: str
    template: str
    created_at: str
    status: str = "running"  # running / idle / failed
    workspace: str | None = None
    site_url: str | None = None
    repo_url: str | None = None
    ci_url: str | None = None
    usd: float = 0.0
    requirements: list[str] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def emit(self, kind: str, text: str, **extra) -> None:
        with self.lock:
            self.events.append(
                {
                    "kind": kind,
                    "text": text,
                    "at": datetime.now().strftime("%H:%M:%S"),
                    **extra,
                }
            )

    def brief(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "template": self.template,
            "status": self.status,
            "created_at": self.created_at,
            "site_url": self.site_url,
            "repo_url": self.repo_url,
            "ci_url": self.ci_url,
            "usd": round(self.usd, 4),
            "rounds": len(self.requirements),
        }


PROJECTS: dict[str, Project] = {}


def save_project(project: Project) -> None:
    """把项目状态写进它自己的工作区，服务重启后还能接着改。"""
    if not project.workspace:
        return
    meta = Path(project.workspace) / ".cloudide"
    meta.mkdir(parents=True, exist_ok=True)
    payload = {
        **project.brief(),
        "workspace": project.workspace,
        "requirements": project.requirements,
        "events": project.events[-4000:],
    }
    (meta / "project.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def load_projects() -> None:
    for path in sorted(SETTINGS.workspaces.glob("*/.cloudide/project.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if data.get("template") not in TEMPLATES:
            continue
        PROJECTS[data["id"]] = Project(
            id=data["id"],
            title=data.get("title", ""),
            template=data["template"],
            created_at=data.get("created_at", ""),
            # 上次进程没跑完就退出了，状态落回 idle，让用户能接着改
            status="idle" if data.get("status") == "running" else data.get("status", "idle"),
            workspace=data.get("workspace"),
            site_url=data.get("site_url"),
            repo_url=data.get("repo_url"),
            ci_url=data.get("ci_url"),
            usd=data.get("usd", 0.0),
            requirements=data.get("requirements", []),
            events=data.get("events", []),
        )


def _arg(payload: str, key: str) -> str:
    try:
        return str(json.loads(payload).get(key, ""))
    except Exception:
        return payload[:200]


class WebUi:
    """把编排过程转成前端能渲染的事件流，模型的思考和输出按小块推送。"""

    # 思考内容量大但只折叠展示，攒大块再推；正文要跟手，所以攒小块
    FLUSH_AT = {"thinking": 220, "say": 40}

    def __init__(self, project: Project) -> None:
        self.p = project
        self._kind = ""
        self._buf = ""

    # 流式文本

    def delta(self, kind: str, text: str) -> None:
        if kind != self._kind:
            self._drain()
            self._kind = kind
        self._buf += text
        limit = self.FLUSH_AT.get(kind, 60)
        if len(self._buf) >= limit or (kind != "thinking" and "\n" in text):
            self._drain(keep=True)

    def flush(self) -> None:
        self._drain()

    def _drain(self, keep: bool = False) -> None:
        if self._buf:
            self.p.emit(self._kind or "say", self._buf)
            self._buf = ""
        if not keep:
            self._kind = ""

    # 工具

    def tool(self, name: str, arguments: str) -> None:
        self._drain()
        if name == "run":
            self.p.emit("cmd", _arg(arguments, "cmd"))
        elif name == "write_file":
            self.p.emit(
                "file",
                _arg(arguments, "path"),
                content=_arg(arguments, "content")[:8000],
            )
        elif name == "read_file":
            self.p.emit("read", _arg(arguments, "path"))

    def tool_result(self, name: str, text: str) -> None:
        if name != "run":
            return
        body = text.strip()
        if body and body != "[exit=0]":
            self.p.emit("out", body[:800])

    # 阶段

    def header(self, requirement: str, workspace: Path, model: str) -> None:
        self.p.workspace = str(workspace)
        self.p.emit("info", f"模型 {model}，工作区 {workspace.name}")

    def stage(self, text: str) -> None:
        self._drain()
        self.p.emit("stage", text)

    def info(self, text: str) -> None:
        self.p.emit("info", text)

    def warn(self, text: str) -> None:
        self._drain()
        self.p.emit("warn", text)

    def check(self, label: str, ok: bool) -> None:
        self.p.emit("check", label, ok=ok)

    def detail(self, text: str) -> None:
        """检查失败的具体输出，用户得能看见到底哪条没过。"""
        self.p.emit("out", text)

    def code(self, text: str, lang: str = "python") -> None:
        self.p.emit("code", text)


def run_project(project: Project, requirement: str) -> None:
    """跑一轮：新建项目或在已有项目上继续改。"""
    spec = TEMPLATES[project.template]
    ui = WebUi(project)
    project.status = "running"
    project.emit("user", requirement)
    try:
        task = Task(
            SETTINGS,
            requirement,
            ui,
            template=spec,
            workspace=Path(project.workspace) if project.workspace else None,
            history=list(project.requirements),
        )
        project.workspace = str(task.workspace)
        result = task.run()
        project.usd += result.usd

        if not result.ok:
            ui.warn(result.blocked or "检查没有全部通过，这一轮没有发布")
            project.status = "failed"
            return

        project.requirements.append(requirement)
        pub = publish(SETTINGS, result.workspace, task.workspace.name, ui, spec.deploy)
        project.repo_url = pub.repo_url
        project.ci_url = pub.actions_url
        if pub.status == "success":
            project.site_url = pub.site_url or pub.release_url
            project.status = "idle"
            # 结果做成事件，这样刷新或重连时它会跟着历史一起回来
            project.emit(
                "result",
                "这一轮完成，可以继续提要求",
                site_url=project.site_url,
                usd=round(project.usd, 4),
            )
        else:
            project.status = "failed"
            ui.warn(f"CI 状态：{pub.status}")
    except Exception as exc:  # 任何异常都要落到前端，不能让项目卡在 running
        ui.warn(f"出错：{exc}")
        project.status = "failed"
    finally:
        save_project(project)


class CreateProject(BaseModel):
    requirement: str
    template: str = DEFAULT_TEMPLATE


class Message(BaseModel):
    text: str


def _start(project: Project, requirement: str) -> None:
    threading.Thread(target=run_project, args=(project, requirement), daemon=True).start()


load_projects()


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (HERE / "index.html").read_text(encoding="utf-8")


@app.get("/api/templates")
def templates() -> list[dict]:
    return [{"key": s.key, "label": s.label, "deploy": s.deploy} for s in TEMPLATES.values()]


@app.get("/api/projects")
def list_projects() -> list[dict]:
    return [
        p.brief()
        for p in sorted(PROJECTS.values(), key=lambda x: x.created_at, reverse=True)
    ]


@app.post("/api/projects")
def create_project(body: CreateProject) -> dict:
    requirement = body.requirement.strip()
    if not requirement:
        raise HTTPException(400, "需求不能为空")
    if body.template not in TEMPLATES:
        raise HTTPException(400, f"未知模板：{body.template}")
    project = Project(
        id=uuid4().hex[:8],
        title=requirement[:40],
        template=body.template,
        created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    PROJECTS[project.id] = project
    _start(project, requirement)
    return project.brief()


@app.get("/api/projects/{project_id}")
def get_project(project_id: str, limit: int = 600) -> dict:
    """默认只回最近 600 条事件——一轮下来上千条，全渲染会把浏览器拖垮。"""
    project = PROJECTS.get(project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    total = len(project.events)
    events = project.events[-limit:] if limit and total > limit else project.events
    return {
        **project.brief(),
        "events": events,
        "total_events": total,
        "truncated": total > len(events),
        "workspace": project.workspace,
    }


@app.post("/api/projects/{project_id}/messages")
def add_message(project_id: str, body: Message) -> dict:
    project = PROJECTS.get(project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    if project.status == "running":
        raise HTTPException(409, "这一轮还没跑完，等它完成再说")
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "内容不能为空")
    if not project.workspace:
        raise HTTPException(400, "项目还没建起来，没法继续改")
    _start(project, text)
    return project.brief()


@app.get("/api/projects/{project_id}/events")
async def project_events(project_id: str, after: int = 0) -> StreamingResponse:
    """after 是客户端已经拿到的事件数，只推它之后的，避免重连时重放整段历史。"""
    project = PROJECTS.get(project_id)
    if not project:
        raise HTTPException(404, "项目不存在")

    async def stream():
        sent = min(max(after, 0), len(project.events))
        while True:
            with project.lock:
                pending = project.events[sent:]
                sent = len(project.events)
            for event in pending:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if project.status != "running":
                payload = {"kind": "end", **project.brief()}
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                return
            await asyncio.sleep(0.3)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
