"""Convert chat messages to the OTel GenAI ``parts`` message schema.

``gen_ai.input.messages``, ``gen_ai.output.messages`` and
``gen_ai.system_instructions`` are JSON documents with a fixed schema
(semantic-conventions-genai, ``model/gen-ai/gen-ai-*-messages.json``):

    [{"role": "user", "parts": [{"type": "text", "content": "hi"}]}]

Hermes hands the hooks OpenAI-style messages instead
(``{"role": ..., "content": ..., "tool_calls": [...]}``). Backends that
validate against the schema (Phoenix converts ``gen_ai.*`` to OpenInference
at ingest) cannot read that shape, so the attributes are rewritten here.
``input.value`` / ``output.value`` keep the provider-native shape.

Pure functions over plain data: no tracer, no config, no package imports.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def _get(obj: Any, name: str) -> Any:
    """A field of a dict or of an SDK object (pydantic/dataclass)."""
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _maybe_json(value: Any) -> Any:
    """Tool-call arguments arrive as a JSON string; the schema allows any
    value, and a parsed object renders better. Unparseable strings stay."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value


def _text_part(text: Any) -> Dict[str, Any]:
    return {"type": "text", "content": text if isinstance(text, str) else str(text)}


def _content_parts(content: Any) -> List[Dict[str, Any]]:
    """``content`` as parts: a string, or an OpenAI/Anthropic content-part list."""
    if content is None or content == "":
        return []
    if isinstance(content, str):
        return [_text_part(content)]
    if not isinstance(content, list):
        return [_text_part(content)]
    parts: List[Dict[str, Any]] = []
    for item in content:
        kind = _get(item, "type")
        if isinstance(item, str):
            parts.append(_text_part(item))
        elif kind in ("text", "input_text", "output_text"):
            parts.append(_text_part(_get(item, "text") or _get(item, "content") or ""))
        elif kind in ("image_url", "input_image"):
            image = _get(item, "image_url")
            uri = _get(image, "url") if image is not None and not isinstance(image, str) else image
            if uri:
                parts.append({"type": "uri", "modality": "image", "uri": str(uri)})
        elif kind in ("thinking", "reasoning"):
            text = _get(item, "thinking") or _get(item, "text") or _get(item, "content")
            if text:
                parts.append({"type": "reasoning", "content": str(text)})
        elif kind == "tool_use":
            # Anthropic Messages: the assistant's tool call as a content block.
            parts.extend(_tool_call_parts([item]))
        elif kind == "tool_result":
            # Anthropic Messages: the tool's answer, inside a user message.
            parts.append(_tool_response_part(_get(item, "tool_use_id"), _get(item, "content")))
        elif kind:
            # Unknown part types are allowed by the schema (GenericPart).
            parts.append(
                {**item, "type": str(kind)} if isinstance(item, dict) else {"type": str(kind)}
            )
        else:
            parts.append(_text_part(item))
    return parts


def _tool_call_parts(tool_calls: Any) -> List[Dict[str, Any]]:
    parts: List[Dict[str, Any]] = []
    if not isinstance(tool_calls, list):
        return parts
    for call in tool_calls:
        function = _get(call, "function")
        name = _get(function, "name") if function is not None else _get(call, "name")
        if not name:
            continue
        part: Dict[str, Any] = {"type": "tool_call", "name": str(name)}
        call_id = _get(call, "id")
        if call_id:
            part["id"] = str(call_id)
        arguments = _get(function, "arguments") if function is not None else _get(call, "arguments")
        if arguments in (None, ""):
            arguments = _get(call, "input")  # Anthropic tool_use
        if arguments not in (None, ""):
            part["arguments"] = _maybe_json(arguments)
        parts.append(part)
    return parts


def _tool_response_part(call_id: Any, content: Any) -> Dict[str, Any]:
    """A ``tool_call_response`` part; list content is flattened to its text."""
    response: Any = content
    if isinstance(content, list):
        response = " ".join(
            p["content"] for p in _content_parts(content) if p.get("type") == "text"
        )
    part: Dict[str, Any] = {
        "type": "tool_call_response",
        "response": response if response is not None else "",
    }
    if call_id:
        part["id"] = str(call_id)
    return part


def _responses_item(item: Any) -> Optional[Dict[str, Any]]:
    """A Responses API input item that is not a role message (``function_call``,
    ``function_call_output``, ``reasoning``) as a ``ChatMessage``. None for any
    other item: it is converted as a message instead."""
    kind = _get(item, "type")
    if kind == "function_call":
        part: Dict[str, Any] = {"type": "tool_call", "name": str(_get(item, "name") or "")}
        call_id = _get(item, "call_id") or _get(item, "id")
        if call_id:
            part["id"] = str(call_id)
        arguments = _get(item, "arguments")
        if arguments not in (None, ""):
            part["arguments"] = _maybe_json(arguments)
        return {"role": "assistant", "parts": [part]} if part["name"] else None
    if kind == "function_call_output":
        return {
            "role": "tool",
            "parts": [_tool_response_part(_get(item, "call_id"), _get(item, "output"))],
        }
    if kind == "reasoning":
        summary = _get(item, "summary")
        texts = [str(_get(x, "text")) for x in summary or [] if _get(x, "text")]
        if texts:
            return {
                "role": "assistant",
                "parts": [{"type": "reasoning", "content": "\n".join(texts)}],
            }
    return None


def to_genai_message(message: Any) -> Optional[Dict[str, Any]]:
    """One message as a schema ``ChatMessage``, or None if empty. Accepts
    OpenAI Chat Completions messages, Anthropic Messages content blocks and
    Responses API input items."""
    role = _get(message, "role")
    if not role and _get(message, "type") in ("function_call", "function_call_output", "reasoning"):
        return _responses_item(message)
    role = "system" if role == "developer" else (str(role) if role else "user")
    parts: List[Dict[str, Any]] = []
    reasoning = _get(message, "reasoning_content") or _get(message, "reasoning")
    if isinstance(reasoning, str) and reasoning:
        parts.append({"type": "reasoning", "content": reasoning})
    if role == "tool":
        parts.append(_tool_response_part(_get(message, "tool_call_id"), _get(message, "content")))
    else:
        parts.extend(_content_parts(_get(message, "content")))
        parts.extend(_tool_call_parts(_get(message, "tool_calls")))
    if not parts:
        return None
    out: Dict[str, Any] = {"role": role, "parts": parts}
    name = _get(message, "name")
    if isinstance(name, str) and name and role != "tool":
        out["name"] = name
    return out


def to_genai_messages(messages: Any) -> Optional[List[Dict[str, Any]]]:
    """A message list in the schema shape, or None when nothing converts.

    Anything that is not a list of messages (a bare string, say) becomes one
    user message, so the attribute is always schema-shaped."""
    if messages is None or messages == "" or messages == []:
        return None
    if not isinstance(messages, list):
        return [{"role": "user", "parts": [_text_part(messages)]}]
    out = [m for m in (to_genai_message(msg) for msg in messages) if m is not None]
    return out or None


def genai_messages_json(messages: Any) -> Optional[str]:
    """``to_genai_messages`` serialised, or None."""
    converted = to_genai_messages(messages)
    if converted is None:
        return None
    try:
        return json.dumps(converted, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return None


def single_message_json(role: str, content: Any) -> Optional[str]:
    """A one-message ``gen_ai.*.messages`` payload, or None for empty content."""
    if content is None or content == "":
        return None
    return genai_messages_json([{"role": role, "content": content}])


def output_message_json(
    content: Any, tool_calls: Any = None, finish_reason: Any = None
) -> Optional[str]:
    """``gen_ai.output.messages``: one assistant ``OutputMessage``, or None."""
    message = to_genai_message({"role": "assistant", "content": content, "tool_calls": tool_calls})
    if message is None:
        return None
    if finish_reason:
        message["finish_reason"] = str(finish_reason)
    try:
        return json.dumps([message], ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return None


def system_instructions_json(system_prompt: Any) -> Optional[str]:
    """``gen_ai.system_instructions``: a list of text parts, or None."""
    if system_prompt is None or system_prompt == "":
        return None
    return json.dumps([_text_part(system_prompt)], ensure_ascii=False)
