import json


def test_send_action_buttons_requires_telegram_session(monkeypatch):
    from gateway.session_context import clear_session_vars, set_session_vars
    from tools.action_buttons_tool import send_action_buttons_tool

    tokens = set_session_vars(platform="telegram", chat_id="")
    try:
        result = json.loads(send_action_buttons_tool({"message": "Approve?", "action_buttons": [[{"text": "Approve", "response_text": "approve x"}]]}))
    finally:
        clear_session_vars(tokens)

    assert "only available in a Telegram chat session" in result["error"]


def test_send_action_buttons_targets_current_telegram_chat(monkeypatch):
    from gateway.session_context import clear_session_vars, set_session_vars
    import tools.send_message_tool as send_message_tool
    from tools.action_buttons_tool import send_action_buttons_tool

    seen = []

    def fake_send_message_tool(args):
        seen.append(args)
        return json.dumps({"success": True, "platform": "telegram", "message_id": "42"})

    monkeypatch.setattr(send_message_tool, "send_message_tool", fake_send_message_tool)
    tokens = set_session_vars(platform="telegram", chat_id="123", thread_id="456")
    try:
        result = json.loads(send_action_buttons_tool({
            "message": "Approve?",
            "action_buttons": [[
                {"text": "Approve", "response_text": "approve proposal-1"},
                {"text": "Deny", "response_text": "deny proposal-1"},
            ]],
        }))
    finally:
        clear_session_vars(tokens)

    assert seen == [{
        "action": "send",
        "target": "telegram:123:456",
        "message": "Approve?",
        "action_buttons": [[
            {"text": "Approve", "response_text": "approve proposal-1"},
            {"text": "Deny", "response_text": "deny proposal-1"},
        ]],
    }]
    assert result["success"] is True
    assert result["target"] == "current Telegram chat"
    assert result["queued_button_responses"] is True


def test_send_action_buttons_registered_in_core_toolset():
    import tools.action_buttons_tool  # noqa: F401 - import triggers registration
    from hermes_cli.tools_config import _get_platform_tools
    from tools.registry import registry
    from toolsets import TOOLSETS

    entry = registry.get_entry("send_action_buttons")
    assert entry is not None
    assert entry.toolset == "messaging"
    assert "send_action_buttons" in TOOLSETS["hermes-telegram"]["tools"]
    assert "messaging" in _get_platform_tools({}, "telegram", include_default_mcp_servers=False)


def test_send_action_buttons_availability_is_session_scoped_not_stale_cached():
    from gateway.session_context import clear_session_vars, set_session_vars
    from hermes_cli.tools_config import _get_platform_tools
    from model_tools import _clear_tool_defs_cache, get_tool_definitions
    from tools.registry import invalidate_check_fn_cache

    toolsets = sorted(_get_platform_tools({}, "telegram", include_default_mcp_servers=False))
    _clear_tool_defs_cache()
    invalidate_check_fn_cache()

    tokens = set_session_vars(platform="discord", chat_id="123")
    try:
        discord_tools = get_tool_definitions(enabled_toolsets=toolsets, quiet_mode=True)
    finally:
        clear_session_vars(tokens)
    assert "send_action_buttons" not in {t["function"]["name"] for t in discord_tools}

    tokens = set_session_vars(platform="telegram", chat_id="123")
    try:
        telegram_tools = get_tool_definitions(enabled_toolsets=toolsets, quiet_mode=True)
    finally:
        clear_session_vars(tokens)
    assert "send_action_buttons" in {t["function"]["name"] for t in telegram_tools}
