"""DeepSeek 调用：流式输出、思考过程、用量统计。走 OpenAI 兼容协议。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from openai import OpenAI

from .config import Settings, price_table


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass
class AssistantMessage:
    content: str = ""
    reasoning: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass
class Usage:
    calls: int = 0
    cache_hit: int = 0
    cache_miss: int = 0
    output: int = 0
    estimated: bool = False

    def add(self, raw: Any) -> None:
        if raw is None:
            return
        prompt = getattr(raw, "prompt_tokens", 0) or 0
        hit = getattr(raw, "prompt_cache_hit_tokens", None)
        miss = getattr(raw, "prompt_cache_miss_tokens", None)
        if hit is None and miss is None:
            hit, miss = 0, prompt
        elif hit is None:
            hit = max(prompt - (miss or 0), 0)
        elif miss is None:
            miss = max(prompt - hit, 0)
        self.cache_hit += hit
        self.cache_miss += miss
        self.output += getattr(raw, "completion_tokens", 0) or 0

    def add_estimate(self, sent_chars: int, got_chars: int) -> None:
        """接口没回用量时的兜底估算，宁可高估也不要显示成 0。"""
        self.estimated = True
        self.cache_miss += sent_chars // 3
        self.output += got_chars // 3

    @property
    def usd(self) -> float:
        p = price_table()
        total = (
            self.cache_hit * p["cache_hit"]
            + self.cache_miss * p["cache_miss"]
            + self.output * p["output"]
        )
        return total / 1_000_000

    def summary(self) -> str:
        prefix = "约" if self.estimated else ""
        return (
            f"{self.calls} 次调用 · 输入 {self.cache_hit + self.cache_miss} token"
            f"（缓存命中 {self.cache_hit}）· 输出 {self.output} token · {prefix}花费 ${self.usd:.4f}"
        )


class LLM:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = OpenAI(api_key=settings.api_key, base_url=settings.base_url, timeout=600)
        self.usage = Usage()

    def check_budget(self) -> None:
        if self.usage.usd >= self.settings.max_usd:
            raise BudgetExceeded(
                f"已花费约 ${self.usage.usd:.4f}，达到上限 ${self.settings.max_usd:.2f}"
            )

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        on_delta: Callable[[str, str], None] | None = None,
    ) -> AssistantMessage:
        """流式调用。on_delta(kind, text) 会被不断回调，kind 是 thinking 或 say。"""
        self.check_budget()
        last: Exception | None = None
        for attempt in range(3):
            started = False
            try:
                kwargs: dict[str, Any] = {
                    "model": self.settings.model,
                    "messages": messages,
                    "stream": True,
                    "stream_options": {"include_usage": True},
                }
                if tools:
                    kwargs["tools"] = tools
                    kwargs["tool_choice"] = "auto"

                msg = AssistantMessage()
                calls: dict[int, dict] = {}
                got_usage = False

                for chunk in self.client.chat.completions.create(**kwargs):
                    if getattr(chunk, "usage", None):
                        self.usage.add(chunk.usage)
                        got_usage = True
                    for choice in chunk.choices or []:
                        delta = getattr(choice, "delta", None)
                        if delta is None:
                            continue
                        reasoning = getattr(delta, "reasoning_content", None)
                        if reasoning:
                            started = True
                            msg.reasoning += reasoning
                            if on_delta:
                                on_delta("thinking", reasoning)
                        if delta.content:
                            started = True
                            msg.content += delta.content
                            if on_delta:
                                on_delta("say", delta.content)
                        for tc in delta.tool_calls or []:
                            slot = calls.setdefault(
                                tc.index, {"id": "", "name": "", "arguments": ""}
                            )
                            started = True
                            if tc.id:
                                slot["id"] = tc.id
                            if tc.function and tc.function.name:
                                slot["name"] += tc.function.name
                            if tc.function and tc.function.arguments:
                                slot["arguments"] += tc.function.arguments

                self.usage.calls += 1
                if not got_usage:
                    sent = sum(len(str(m.get("content") or "")) for m in messages)
                    self.usage.add_estimate(sent, len(msg.content) + len(msg.reasoning))
                msg.tool_calls = [ToolCall(**calls[i]) for i in sorted(calls)]
                return msg
            except Exception as exc:  # 网络、限流、5xx 退避重试；已经吐过内容就不重试，避免重复
                last = exc
                if started or attempt == 2:
                    break
                time.sleep(2**attempt)
        raise RuntimeError(f"模型调用失败：{last}")


def assistant_to_dict(msg: AssistantMessage) -> dict:
    """转成可回传的 dict。思考内容不回传——DeepSeek 明确要求不要把 reasoning 发回去。"""
    out: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
    if msg.tool_calls:
        out["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            for call in msg.tool_calls
        ]
    return out
