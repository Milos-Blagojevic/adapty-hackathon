"""OpenAI Chat Completions behind `llm.LLM`. Only translates formats; the agents own the loop."""

from __future__ import annotations

import json

import openai

from resolve.llm import LLMError, Step, ToolCall, ToolSpec
from resolve.llm_schema import error_message, strict_schema

MAX_TOKENS = 16000
STOPS = {"stop": "end", "tool_calls": "tool", "length": "max_tokens", "content_filter": "refusal"}  # others → end


class OpenAILLM:
    provider = "openai"

    def __init__(self, api_key: str, model: str, base_url: str | None = None, client=None,
                 extra_body: dict | None = None, strict: bool = True, max_tokens: int = MAX_TOKENS):
        self._key = api_key
        self.model = model
        self.extra_body = extra_body
        self.strict = strict
        self.max_tokens = max_tokens
        self.client = client or openai.OpenAI(api_key=api_key, base_url=base_url, max_retries=1, timeout=120)

    def step(self, system: str, messages: list[dict], tools: list[ToolSpec]) -> Step:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system}, *to_openai(messages)],
                tools=[
                    {
                        "type": "function",
                        "function": {"name": t.name, "description": t.description, "parameters": strict_schema(t.params), "strict": self.strict},
                    }
                    for t in tools
                ]
                or openai.omit,
                max_completion_tokens=self.max_tokens,
                extra_body=self.extra_body,
            )
        except openai.APIError as e:
            raise LLMError(error_message("openai", e, self._key)) from e
        return from_openai(response)


def to_openai(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    for m in messages:
        if m["role"] == "user":
            out.append({"role": "user", "content": m["content"]})
        elif m["role"] == "assistant":
            msg = {"role": "assistant", "content": m.get("text") or None}
            calls = [
                {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": json.dumps(c["args"])}}
                for c in m.get("tool_calls", [])
            ]
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
        elif m["role"] == "tool":
            out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
        else:
            raise ValueError(f"unknown transcript role {m['role']!r}")
    return out


def from_openai(response) -> Step:
    choice = response.choices[0]
    message = choice.message
    calls = []
    for c in message.tool_calls or []:
        try:
            args = json.loads(c.function.arguments)
        except json.JSONDecodeError:
            args = None
        if not isinstance(args, dict):  # the agent rejects the call
            args = {"_invalid_json": c.function.arguments}
        calls.append(ToolCall(id=c.id, name=c.function.name, args=args))
    stop = "refusal" if message.refusal else STOPS.get(choice.finish_reason, "end")
    return Step(text=message.content or message.refusal, tool_calls=calls, stop=stop, raw=message.model_dump(), provider="openai")
