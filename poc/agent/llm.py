"""DeepSeek 调用与用量统计，走 OpenAI 兼容协议。"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from openai import OpenAI

from .config import Settings, price_table


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class Usage:
    calls: int = 0
    cache_hit: int = 0
    cache_miss: int = 0
    output: int = 0

    def add(self, raw: Any) -> None:
        if raw is None:
            return
        self.calls += 1
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
        return (
            f"{self.calls} 次调用 · 输入 {self.cache_hit + self.cache_miss} token"
            f"（缓存命中 {self.cache_hit}）· 输出 {self.output} token · 约 ${self.usd:.4f}"
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

    def chat(self, messages: list[dict], tools: list[dict] | None = None):
        self.check_budget()
        last: Exception | None = None
        for attempt in range(3):
            try:
                kwargs: dict[str, Any] = {"model": self.settings.model, "messages": messages}
                if tools:
                    kwargs["tools"] = tools
                    kwargs["tool_choice"] = "auto"
                resp = self.client.chat.completions.create(**kwargs)
                self.usage.add(resp.usage)
                return resp.choices[0].message
            except Exception as exc:  # 网络、限流、5xx 统一退避重试
                last = exc
                if attempt < 2:
                    time.sleep(2**attempt)
        raise RuntimeError(f"模型调用失败：{last}")


def assistant_to_dict(msg: Any) -> dict:
    """把 SDK 返回的 assistant 消息转成可回传的 dict，丢掉 reasoning_content 等不该回传的字段。"""
    out: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
    if getattr(msg, "tool_calls", None):
        out["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in msg.tool_calls
        ]
    return out
