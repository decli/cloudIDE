"""编码 agent 的对话循环：调模型 → 执行工具 → 把结果喂回去，直到它不再调工具。"""

from __future__ import annotations

from .llm import LLM, assistant_to_dict
from .tools import TOOL_SCHEMAS, ToolBox


class TurnLimitExceeded(RuntimeError):
    pass


class CodingAgent:
    def __init__(self, llm: LLM, toolbox: ToolBox, system: str, ui) -> None:
        self.llm = llm
        self.toolbox = toolbox
        self.ui = ui
        self.messages: list[dict] = [{"role": "system", "content": system}]
        self.turns = 0

    def send(self, user_text: str) -> str:
        """发一条消息，跑完整个工具循环，返回 agent 最后说的话。"""
        self.messages.append({"role": "user", "content": user_text})
        while True:
            if self.turns >= self.llm.settings.max_turns:
                raise TurnLimitExceeded(f"超过单任务最大轮数 {self.llm.settings.max_turns}")
            self.turns += 1

            msg = self.llm.chat(self.messages, TOOL_SCHEMAS, on_delta=self.ui.delta)
            self.ui.flush()
            self.messages.append(assistant_to_dict(msg))

            if not msg.tool_calls:
                return msg.content.strip()

            for call in msg.tool_calls:
                self.ui.tool(call.name, call.arguments)
                result = self.toolbox.dispatch(call.name, call.arguments)
                self.ui.tool_result(call.name, result)
                self.messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": result}
                )
