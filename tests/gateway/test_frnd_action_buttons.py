"""Durable FRND Telegram cards re-enter the ordinary FIFO without a blocking clarify."""

import asyncio
import json
import stat
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.session_context import clear_session_vars, set_session_vars
from plugins.platforms.telegram.adapter import TelegramAdapter
from tools import telegram_action_buttons as buttons
from tools.registry import discover_builtin_tools, registry
from toolsets import resolve_toolset


def _adapter():
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="test-token", extra={}))
    adapter._bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=42)))
    adapter._is_callback_user_authorized = lambda *a, **kw: True
    return adapter


def test_tool_is_telegram_only_and_sends_bound_card(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    discover_builtin_tools()
    assert registry.get_entry("send_action_buttons")
    assert "send_action_buttons" in resolve_toolset("hermes-telegram")
    assert "send_action_buttons" not in resolve_toolset("hermes-discord")
    from hermes_cli.tools_config import _get_platform_tools
    assert "telegram_action_buttons" in _get_platform_tools(
        {"platform_toolsets": {"telegram": ["file"]}}, "telegram")
    assert "telegram_action_buttons" not in _get_platform_tools(
        {"platform_toolsets": {"discord": ["file"]}}, "discord")
    adapter = _adapter()
    from plugins.platforms.telegram import adapter as telegram_module
    class Button:
        def __init__(self, text, callback_data):
            self.text, self.callback_data = text, callback_data
    class Markup:
        def __init__(self, inline_keyboard):
            self.inline_keyboard = inline_keyboard
    monkeypatch.setattr(telegram_module, "InlineKeyboardButton", Button)
    monkeypatch.setattr(telegram_module, "InlineKeyboardMarkup", Markup)
    monkeypatch.setattr(telegram_module.ParseMode, "HTML", "HTML", raising=False)
    loop = asyncio.new_event_loop()
    runner = SimpleNamespace(_gateway_loop=loop)
    # Run the real async prompt in a loop owned by this test thread, while tool dispatch
    # runs in an executor (the production shape).
    async def exercise():
        with patch("tools.send_message_senders._live_adapter", return_value=(runner, adapter)):
            return await asyncio.to_thread(registry.dispatch, "send_action_buttons", {
                "proposal_id": "frnd-a1", "message": "Review Salus refresh"})
    import threading
    thread = threading.Thread(target=loop.run_forever)
    thread.start()
    tokens = set_session_vars(platform="telegram", chat_id="123", session_key="lane", profile="default")
    try:
        result = json.loads(asyncio.run(exercise()))
        assert result == {"success": True, "message_id": "42", "proposal_id": "frnd-a1"}
        call = adapter._bot.send_message.call_args.kwargs
        assert call["chat_id"] == 123
        assert "frnd-a1" in call["text"]
        assert call["reply_markup"].inline_keyboard[0][0].callback_data.startswith("ab:")
        button_id = call["reply_markup"].inline_keyboard[0][0].callback_data.split(":")[1]
        assert buttons.claim(button_id, "123", "", "lane", "42") == "frnd-a1"
        assert buttons.claim(button_id, "123", "", "lane", "42") is None
        assert stat.S_IMODE((tmp_path / "data" / "telegram_action_buttons.sqlite3").stat().st_mode) == 0o600
    finally:
        clear_session_vars(tokens)
        loop.call_soon_threadsafe(loop.stop)
        thread.join()
        loop.close()


@pytest.mark.asyncio
async def test_independent_clicks_queue_without_interrupt_and_failures_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    adapter = _adapter()
    from gateway.run import GatewayRunner
    runner = GatewayRunner.__new__(GatewayRunner)
    runner._queued_events = {}
    runner.adapters = {Platform.TELEGRAM: adapter}
    queued = []

    async def admit(event):
        queued.append(event)
        runner._queue_or_replace_pending_event("lane", event)

    adapter.handle_message = admit
    adapter._event_session_key = lambda event: "lane"
    adapter.build_source = lambda **kw: SimpleNamespace(platform=Platform.TELEGRAM, profile=None, **kw)
    cb = {"chat_id": 123, "chat_type": "private", "thread_id": None, "user_name": "Owner"}
    for button_id, proposal in (("one", "frnd-a1"), ("two", "frnd-a2")):
        buttons._insert(button_id, "123", "", "lane", proposal)
        buttons._bind(button_id, "42" if button_id == "one" else "43")

    def query(message_id):
        return SimpleNamespace(
            message=SimpleNamespace(chat_id=123, message_id=message_id),
            from_user=SimpleNamespace(id=77), answer=AsyncMock(), edit_message_reply_markup=AsyncMock())

    first, second = query(42), query(43)
    await adapter._handle_frnd_action_callback(first, "ab:one:a", cb)
    await adapter._handle_frnd_action_callback(second, "ab:two:d", cb)
    assert [event.text for event in queued] == ["approve frnd-a1", "deny frnd-a2"]
    assert all(event.internal and not event.allow_gateway_control for event in queued)
    assert runner._queued_events["lane"][0].text == "deny frnd-a2"
    assert adapter._pending_messages["lane"].text == "approve frnd-a1"
    await adapter._handle_frnd_action_callback(first, "ab:one:d", cb)
    assert len(queued) == 2  # first card consumed, second remains independent
    assert buttons.claim("two", "999", "", "lane", "43") is None

    buttons._insert("three", "123", "", "lane", "frnd-a3")
    buttons._bind("three", "44")
    async def refused(event):
        event._gateway_accepted = False
    adapter.handle_message = refused
    await adapter._handle_frnd_action_callback(query(44), "ab:three:a", cb)
    assert buttons.claim("three", "123", "", "lane", "44") == "frnd-a3"
