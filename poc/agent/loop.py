"""编码 agent 的对话循环：调模型 → 执行工具 → 把结果喂回去，直到它不再调工具。"""

from __future__ import annotations

from typing import Callable

from .llm import LLM, assistant_to_dict
from .tools import TOOL_SCHEMAS, ToolBox


class TurnLimitExceeded(RuntimeError):
    pass


class CodingAgent:
    def __init__(
        self,
        llm: LLM,
        toolbox: ToolBox,
        system: str,
        on_event: Callable[[str, str], None],
    ) -> None:
        self.llm = llm
        self.toolbox = toolbox
        self.messages: list[dict] = [{"role": "system", "content": system}]
        self.on_event = on_event
        self.turns = 0

    def send(self, user_text: str) -> str:
        """发一条消息，跑完整个工具循环，返回 agent 最后说的话。"""
        self.messages.append({"role": "user", "content": user_text})
        while True:
            if self.turns >= self.llm.settings.max_turns:
                raise TurnLimitExceeded(f"超过单任务最大轮数 {self.llm.settings.max_turns}")
            self.turns += 1
            msg = self.llm.chat(self.messages, TOOL_SCHEMAS)
            self.messages.append(assistant_to_dict(msg))

            calls = getattr(msg, "tool_calls", None)
            text = (msg.content or "").strip()
            if text:
                self.on_event("say", text)
            if not calls:
                return text

            for call in calls:
                self.on_event(call.function.name, call.function.arguments)
                result = self.toolbox.dispatch(call.function.name, call.function.arguments)
                self.on_event("result", result)
                self.messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": result}
                )
