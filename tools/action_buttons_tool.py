"""Current-chat Telegram action button tool.

This is intentionally narrower than ``send_message``: the agent can only send a
button prompt back to the Telegram chat that invoked the current turn. Button
clicks inject their configured response text into the normal gateway flow, so
approval replies queue behind any work already in progress.
"""

from __future__ import annotations

import json
from typing import Any

from tools.registry import registry, tool_error


SEND_ACTION_BUTTONS_SCHEMA = {
    "name": "send_action_buttons",
    "description": (
        "Send a Telegram action-button prompt to the current chat only. "
        "Use this for approval/deny/edit prompts when multiple independent "
        "approvals should be sent and handled asynchronously. Each button's "
        "response_text is injected back into the normal chat flow when clicked."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "message": {
                "type": "string",
                "description": "Prompt text to send above the buttons. Include concise typed fallbacks when useful.",
            },
            "action_buttons": {
                "type": "array",
                "description": "Rows of Telegram buttons. Each button is {text, response_text, id?, consume?}.",
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "response_text": {"type": "string"},
                            "id": {"type": "string"},
                            "consume": {"type": "boolean"},
                        },
                        "required": ["text", "response_text"],
                    },
                },
            },
        },
        "required": ["message", "action_buttons"],
    },
}


def _current_telegram_target() -> str | None:
    try:
        from gateway.session_context import get_session_env

        platform = get_session_env("HERMES_SESSION_PLATFORM", "")
        chat_id = get_session_env("HERMES_SESSION_CHAT_ID", "")
        thread_id = get_session_env("HERMES_SESSION_THREAD_ID", "")
    except Exception:
        return None

    if str(platform).lower() != "telegram" or not chat_id:
        return None
    if thread_id:
        return f"telegram:{chat_id}:{thread_id}"
    return f"telegram:{chat_id}"


def check_send_action_buttons_requirements() -> bool:
    """Expose only inside a routed Telegram session."""
    return _current_telegram_target() is not None


# Availability depends on the current gateway session ContextVars. Do not let
# the registry's process-wide check_fn cache pin a prior non-Telegram/empty
# context and hide this tool from later Telegram turns.
setattr(check_send_action_buttons_requirements, "_hermes_skip_check_cache", True)


def send_action_buttons_tool(args: dict[str, Any], **_kw) -> str:
    message = str(args.get("message") or "").strip()
    action_buttons = args.get("action_buttons")
    if not message:
        return tool_error("message is required")
    if not isinstance(action_buttons, list) or not action_buttons:
        return tool_error("action_buttons must be a non-empty list of rows")

    target = _current_telegram_target()
    if not target:
        return tool_error("send_action_buttons is only available in a Telegram chat session")

    from tools.send_message_tool import send_message_tool

    result = send_message_tool({
        "action": "send",
        "target": target,
        "message": message,
        "action_buttons": action_buttons,
    })
    try:
        payload = json.loads(result)
    except Exception:
        return result
    if isinstance(payload, dict) and payload.get("success"):
        payload["target"] = "current Telegram chat"
        payload["queued_button_responses"] = True
        return json.dumps(payload)
    return result


registry.register(
    name="send_action_buttons",
    toolset="messaging",
    schema=SEND_ACTION_BUTTONS_SCHEMA,
    handler=lambda args, **kw: send_action_buttons_tool(args, **kw),
    check_fn=check_send_action_buttons_requirements,
    description="Send current-chat Telegram action buttons whose clicks queue response_text back into chat",
    emoji="🔘",
)
