from __future__ import annotations

import logging
import time
from typing import Any

from litellm import completion

from builder_runtime.tools import build_tools, run_tools

log = logging.getLogger(__name__)


def assistant_message_to_dict(message: Any) -> dict[str, Any]:
    if hasattr(message, "model_dump"):
        return message.model_dump()
    output: dict[str, Any] = {"role": "assistant", "content": getattr(message, "content", None)}
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        serialized = []
        for call in tool_calls:
            if hasattr(call, "model_dump"):
                serialized.append(call.model_dump())
            else:
                function = getattr(call, "function", call)
                serialized.append(
                    {
                        "id": getattr(call, "id", ""),
                        "type": "function",
                        "function": {
                            "name": getattr(function, "name", ""),
                            "arguments": getattr(function, "arguments", "{}"),
                        },
                    }
                )
        output["tool_calls"] = serialized
    return output


def litellm_completion_with_retries(**kwargs: Any) -> Any:
    for attempt in range(3):
        try:
            return completion(**kwargs)
        except Exception as exc:
            if attempt >= 2:
                raise
            wait = 2.0 * (attempt + 1)
            log.warning("LLM completion failed (attempt %s/3), retrying in %ss: %s", attempt + 1, wait, exc)
            time.sleep(wait)


def tool_choice_for_round(cfg: dict[str, Any], round_index: int) -> str | dict[str, Any]:
    raw = cfg.get("tool_choice", "auto")
    if round_index > 0:
        return "auto"
    if isinstance(raw, dict):
        return raw
    value = str(raw).strip()
    return value if value else "auto"


def agent_turn(
    telos,
    cfg: dict[str, Any],
    messages: list[dict[str, Any]],
    model: str,
) -> None:
    tools = build_tools(cfg)
    max_rounds = int(cfg["max_tool_rounds"])
    parallel = cfg.get("parallel_tool_calls", True)
    if not isinstance(parallel, bool):
        parallel = True

    for round_index in range(max_rounds):
        tool_choice = tool_choice_for_round(cfg, round_index)
        response = litellm_completion_with_retries(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            parallel_tool_calls=parallel,
        )
        choice = response.choices[0]
        message = choice.message
        serialized = assistant_message_to_dict(message)
        messages.append(serialized)

        tool_calls = getattr(message, "tool_calls", None) or serialized.get("tool_calls")
        if not tool_calls:
            log.info("assistant: %s", (serialized.get("content") or "")[:500])
            return

        for call in tool_calls:
            if isinstance(call, dict):
                tool_call_id = call.get("id", "")
                function = call.get("function", {})
                name = function.get("name", "")
                arguments = function.get("arguments", "{}")
            else:
                tool_call_id = getattr(call, "id", "")
                function = getattr(call, "function", None)
                name = getattr(function, "name", "") if function else ""
                arguments = getattr(function, "arguments", "{}") if function else "{}"

            payload = run_tools(telos, cfg, name, arguments)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": payload,
                }
            )
        log.debug("tool round %s done", round_index + 1)

    log.warning("reached max_tool_rounds (%s)", max_rounds)
