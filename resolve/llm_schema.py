"""Provider-neutral helpers for both providers: the strict tool-argument schema, the short error text.

From the pydantic schema: `$ref`/`$defs` inlined, every object closed (`additionalProperties: false`)
with every property in `required`; Optional fields stay nullable (pydantic's `anyOf` with null).
Keywords that one of the strict modes rejects (numeric bounds, string/array lengths, pattern, format)
are moved into the description as text: the model still reads them, pydantic enforces them in the agent.
"""

from __future__ import annotations

from pydantic import BaseModel

_DROP = ("title", "default")
_TO_DESCRIPTION = (
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
    "minLength", "maxLength", "minItems", "maxItems", "pattern", "format",
)  # fmt: skip


def strict_schema(model: type[BaseModel]) -> dict:
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})
    return _strict(schema, defs, ())


def _strict(node: dict, defs: dict, seen: tuple[str, ...]) -> dict:
    if "$ref" in node:
        name = node["$ref"].removeprefix("#/$defs/")
        if name in seen:
            raise ValueError(f"recursive model {name} cannot be inlined")
        seen = (*seen, name)
        node = {**defs[name], **{k: v for k, v in node.items() if k != "$ref"}}
    if node.get("additionalProperties", False) is not False:
        raise ValueError("free-form dicts are not allowed in strict tool arguments")

    out: dict = {}
    notes = []
    for key, value in node.items():
        if key in _DROP:
            continue
        if key in _TO_DESCRIPTION:
            notes.append(f"{key}: {value}")
        elif key == "properties":
            out[key] = {name: _strict(sub, defs, seen) for name, sub in value.items()}
        elif key == "items":
            out[key] = _strict(value, defs, seen)
        elif key in ("anyOf", "oneOf", "allOf"):
            out["allOf" if key == "allOf" else "anyOf"] = [_strict(sub, defs, seen) for sub in value]
        else:
            out[key] = value

    if out.get("type") == "object":
        out.setdefault("properties", {})
        out["additionalProperties"] = False
        out["required"] = list(out["properties"])
    if notes:
        out["description"] = (out.get("description", "") + " {" + ", ".join(notes) + "}").strip()
    return out


def error_message(provider: str, e: Exception, api_key: str) -> str:
    """Short LLMError text from an SDK error: class, status, the API's message; the key is masked."""
    status = getattr(e, "status_code", None)
    message = str(getattr(e, "message", e))[:300]
    if api_key:
        message = message.replace(api_key, "***")
    return f"{provider} {type(e).__name__}" + (f" {status}" if status else "") + f": {message}"
