"""Anthropic Messages API behind `llm.LLM`. Only translates formats; the agents own the loop."""

from __future__ import annotations

import anthropic

from resolve.llm import LLMError, Step, ToolCall, ToolSpec
from resolve.llm_schema import error_message, strict_schema

MAX_TOKENS = 16000
STOPS = {"end_turn": "end", "tool_use": "tool", "max_tokens": "max_tokens", "refusal": "refusal"}  # others → end


class AnthropicLLM:
    provider = "anthropic"

    def __init__(self, api_key: str, model: str | None = None, client=None):
        self._key = api_key
        self._model = model
        self.client = client or anthropic.Anthropic(api_key=api_key, max_retries=1, timeout=120)

    @property
    def model(self) -> str:
        """The given model, else the newest model whose id contains 'opus' (the owner's choice)."""
        if not self._model:
            try:
                ids = [m.id for m in self.client.models.list(limit=50)]
            except anthropic.APIError as e:
                raise LLMError(error_message("anthropic", e, self._key)) from e
            self._model = next((i for i in ids if "opus" in i), None)
            if not self._model:
                raise LLMError("no Opus model in the models list; set ANTHROPIC_MODEL")
        return self._model

    def step(self, system: str, messages: list[dict], tools: list[ToolSpec]) -> Step:
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=system,
                tools=[
                    {"name": t.name, "description": t.description, "input_schema": strict_schema(t.params), "strict": True}
                    for t in tools
                ]
                or anthropic.omit,
                messages=to_anthropic(messages),
            )
        except anthropic.APIError as e:
            raise LLMError(error_message("anthropic", e, self._key)) from e
        return from_anthropic(response)


def to_anthropic(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    prev = None
    for m in messages:
        if m["role"] == "user":
            out.append({"role": "user", "content": m["content"]})
        elif m["role"] == "assistant":
            if m.get("provider") == "anthropic" and m.get("raw") is not None:
                out.append({"role": "assistant", "content": m["raw"]})
            else:
                blocks = [{"type": "text", "text": m["text"]}] if m.get("text") else []
                blocks += [{"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["args"]} for c in m.get("tool_calls", [])]
                out.append({"role": "assistant", "content": blocks})
        elif m["role"] == "tool":
            result = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"], "is_error": m.get("is_error", False)}
            if prev == "tool":  # consecutive results go into one user message
                out[-1]["content"].append(result)
            else:
                out.append({"role": "user", "content": [result]})
        else:
            raise ValueError(f"unknown transcript role {m['role']!r}")
        prev = m["role"]
    return out


def from_anthropic(response) -> Step:
    texts = [b.text for b in response.content if b.type == "text"]
    calls = [ToolCall(id=b.id, name=b.name, args=dict(b.input)) for b in response.content if b.type == "tool_use"]
    return Step(
        text="".join(texts) if texts else None,
        tool_calls=calls,
        stop=STOPS.get(response.stop_reason, "end"),
        raw=response.content,
        provider="anthropic",
    )

