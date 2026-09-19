"""Docker 沙箱：每个任务一个容器，默认断网，工作目录以 bind mount 挂进去。

git 操作、凭证都留在宿主机，容器里看不到。
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class DockerUnavailable(RuntimeError):
    pass


def ensure_daemon() -> str:
    proc = subprocess.run(
        ["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise DockerUnavailable("Docker 守护进程没在运行，请先启动 Docker Desktop 再试")
    return proc.stdout.strip()


def image_exists(image: str) -> bool:
    proc = subprocess.run(["docker", "image", "inspect", image], capture_output=True, text=True)
    return proc.returncode == 0


@dataclass
class Sandbox:
    name: str
    workspace: Path
    image: str
    network: str = "none"
    memory: str = "2g"
    cpus: str = "2"

    def start(self) -> Sandbox:
        ensure_daemon()
        if not image_exists(self.image):
            raise DockerUnavailable(
                f"沙箱镜像 {self.image} 不存在，请先执行：make image"
            )
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True, text=True)
        proc = subprocess.run(
            [
                "docker", "run", "-d",
                "--name", self.name,
                "--network", self.network,
                "--memory", self.memory,
                "--cpus", self.cpus,
                "--pids-limit", "512",
                "-v", f"{self.workspace}:/workspace",
                "-w", "/workspace",
                self.image, "sleep", "infinity",
            ],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise DockerUnavailable(f"启动沙箱失败：{proc.stderr.strip()}")
        return self

    def exec(self, cmd: str, timeout: int = 120) -> tuple[int, str]:
        try:
            proc = subprocess.run(
                ["docker", "exec", "-w", "/workspace", self.name, "sh", "-c", cmd],
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return 124, f"[命令超时 {timeout}s] {cmd}"
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")

    def stop(self) -> None:
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True, text=True)

    def __enter__(self) -> Sandbox:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()
