"""Provider-neutral LLM step for both agents (Level 1 triage, Level 2 first-line).

One call = one model turn: system prompt + transcript + tools → text and/or tool calls.
The agents own the loop; providers only translate formats. `LLM_PROVIDER` picks the provider.

Transcript entries (provider-neutral, JSON-serialisable except `raw`):
  {"role": "user", "content": str}
  {"role": "assistant", "text": str | None, "tool_calls": [{"id", "name", "args"}], "raw": <provider object or None>}
  {"role": "tool", "tool_call_id": str, "name": str, "content": str, "is_error": bool}
A provider must replay `raw` unchanged when it is its own (Anthropic thinking blocks live there),
and rebuild from `text` / `tool_calls` otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

from pydantic import BaseModel

from resolve import config


@dataclass
class ToolSpec:
    name: str
    description: str
    params: type[BaseModel]  # arguments schema; every field is sent as required (nullable when Optional)


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class Step:
    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    # end = model finished; tool = wants tool results; max_tokens = cut off; refusal = declined
    stop: Literal["end", "tool", "max_tokens", "refusal"] = "end"
    raw: object | None = None
    provider: str = ""


class LLMError(Exception):
    """Provider/API failure (network, 4xx/5xx, refusal handling). Agents record it and stop."""


class LLM(Protocol):
    provider: str
    model: str

    def step(self, system: str, messages: list[dict], tools: list[ToolSpec]) -> Step: ...


def assistant_message(step: Step) -> dict:
    """The transcript entry to append after a step."""
    return {
        "role": "assistant",
        "text": step.text,
        "tool_calls": [{"id": c.id, "name": c.name, "args": c.args} for c in step.tool_calls],
        "raw": step.raw,
        "provider": step.provider,
    }


def tool_message(call: ToolCall, content: str, is_error: bool = False) -> dict:
    return {"role": "tool", "tool_call_id": call.id, "name": call.name, "content": content, "is_error": is_error}


class FakeLLM:
    """Scripted steps for tests: each `step()` pops the next Step (or raises it if it is an exception)."""

    provider = "fake"
    model = "fake"

    def __init__(self, steps):
        self.steps = list(steps)
        self.calls: list[dict] = []

    def step(self, system: str, messages: list[dict], tools: list[ToolSpec]) -> Step:
        self.calls.append({"system": system, "messages": [dict(m) for m in messages], "tools": [t.name for t in tools]})
        if not self.steps:
            raise AssertionError("FakeLLM: more calls than scripted steps")
        out = self.steps.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


def make_llm() -> LLM:
    """From the environment. Raises config.MissingKey when the provider's key or model is not set."""
    if config.LLM_PROVIDER == "openai":
        from resolve.llm_openai import OpenAILLM

        return OpenAILLM(api_key=config.openai_key(), model=config.openai_model(), base_url=config.OPENAI_BASE_URL or None)
    if config.LLM_PROVIDER == "anthropic":
        from resolve.llm_anthropic import AnthropicLLM

        return AnthropicLLM(api_key=config.anthropic_key(), model=config.ANTHROPIC_MODEL or None)
    raise config.MissingKey(f"LLM_PROVIDER must be 'openai' or 'anthropic', got {config.LLM_PROVIDER!r}")
