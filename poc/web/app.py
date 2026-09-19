"""cloudIDE POC 的 Web 界面。

写一句需求，看着它写码、跑测试、推 Git、CI 部署，最后点开看成品。
和 CLI 共用 agent/ 里的同一套编排。
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
class Job:
    id: str
    requirement: str
    template: str
    created_at: str
    status: str = "running"  # running / done / failed
    events: list[dict] = field(default_factory=list)
    site_url: str | None = None
    repo_url: str | None = None
    ci_url: str | None = None
    workspace: str | None = None
    usd: float = 0.0
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
            "requirement": self.requirement,
            "template": self.template,
            "status": self.status,
            "created_at": self.created_at,
            "site_url": self.site_url,
            "repo_url": self.repo_url,
            "ci_url": self.ci_url,
            "usd": self.usd,
        }


JOBS: dict[str, Job] = {}


def _arg(payload: str, key: str) -> str:
    try:
        return str(json.loads(payload).get(key, ""))
    except Exception:
        return payload[:200]


class WebUi:
    """把编排过程中的事件转成前端能渲染的条目。"""

    def __init__(self, job: Job) -> None:
        self.job = job
        self._last_tool = ""

    def header(self, requirement: str, workspace: Path, model: str) -> None:
        self.job.workspace = str(workspace)
        self.job.emit("info", f"模型 {model}，工作区 {workspace.name}")

    def stage(self, text: str) -> None:
        self.job.emit("stage", text)

    def info(self, text: str) -> None:
        self.job.emit("info", text)

    def warn(self, text: str) -> None:
        self.job.emit("warn", text)

    def check(self, label: str, ok: bool) -> None:
        self.job.emit("check", label, ok=ok)

    def code(self, text: str, lang: str = "python") -> None:
        self.job.emit("code", text)

    def event(self, kind: str, payload: str) -> None:
        if kind == "say":
            self.job.emit("say", payload)
        elif kind == "run":
            self._last_tool = "run"
            self.job.emit("cmd", _arg(payload, "cmd"))
        elif kind == "write_file":
            self._last_tool = "write"
            self.job.emit("file", _arg(payload, "path"))
        elif kind in {"read_file", "list_files"}:
            self._last_tool = kind
        elif kind == "result" and self._last_tool == "run":
            text = payload.strip()
            if text and text != "[exit=0]":
                self.job.emit("out", text[:600])


def run_job(job: Job) -> None:
    spec = TEMPLATES[job.template]
    ui = WebUi(job)
    try:
        task = Task(SETTINGS, job.requirement, None, ui, template=spec)
        result = task.run()
        job.usd = result.usd
        if not result.ok:
            ui.warn(result.blocked or "检查没有全部通过")
            job.status = "failed"
            return
        pub = publish(SETTINGS, result.workspace, result.workspace.name, ui, spec.deploy)
        job.repo_url = pub.repo_url
        job.ci_url = pub.actions_url
        job.site_url = pub.site_url or pub.release_url
        job.status = "done" if pub.status == "success" else "failed"
        if job.status == "failed":
            ui.warn(f"CI 状态：{pub.status}")
    except Exception as exc:  # 任何异常都要落到前端，不能让任务卡在 running
        ui.warn(f"出错：{exc}")
        job.status = "failed"


class CreateJob(BaseModel):
    requirement: str
    template: str = DEFAULT_TEMPLATE


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (HERE / "index.html").read_text(encoding="utf-8")


@app.get("/api/templates")
def templates() -> list[dict]:
    return [{"key": s.key, "label": s.label, "deploy": s.deploy} for s in TEMPLATES.values()]


@app.get("/api/jobs")
def list_jobs() -> list[dict]:
    return [j.brief() for j in sorted(JOBS.values(), key=lambda x: x.created_at, reverse=True)]


@app.post("/api/jobs")
def create_job(body: CreateJob) -> dict:
    requirement = body.requirement.strip()
    if not requirement:
        raise HTTPException(400, "需求不能为空")
    if body.template not in TEMPLATES:
        raise HTTPException(400, f"未知模板：{body.template}")
    job = Job(
        id=uuid4().hex[:8],
        requirement=requirement,
        template=body.template,
        created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    JOBS[job.id] = job
    threading.Thread(target=run_job, args=(job,), daemon=True).start()
    return job.brief()


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    return {**job.brief(), "events": job.events, "workspace": job.workspace}


@app.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")

    async def stream():
        sent = 0
        while True:
            with job.lock:
                pending = job.events[sent:]
                sent = len(job.events)
            for event in pending:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if job.status != "running" and sent >= len(job.events):
                yield f"data: {json.dumps({'kind': 'end', **job.brief()}, ensure_ascii=False)}\n\n"
                return
            await asyncio.sleep(0.4)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
