from types import SimpleNamespace
from unittest.mock import MagicMock
import sys

import pytest


def _ensure_telegram_mock():
    mod = MagicMock()
    mod.InlineKeyboardButton.side_effect = lambda text, callback_data=None: SimpleNamespace(text=text, callback_data=callback_data)
    mod.InlineKeyboardMarkup.side_effect = lambda rows: SimpleNamespace(inline_keyboard=rows)
    sys.modules["telegram"] = mod


def test_build_action_button_markup_registers_sm_callbacks(tmp_path, monkeypatch):
    _ensure_telegram_mock()
    monkeypatch.setenv("HERMES_ACTION_BUTTON_REGISTRY_PATH", str(tmp_path / "registry.json"))
    from tools.send_message_tool import _build_action_button_markup

    markup = _build_action_button_markup(
        platform_name="telegram",
        chat_id="123",
        thread_id=None,
        message="Do it?",
        action_buttons=[[{"text": "Approve", "response_text": "Approve"}, {"text": "Deny", "response_text": "Deny"}]],
    )

    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert callbacks[0].startswith("sm:ab_")
    assert callbacks[1].startswith("sm:ab_")
    assert all(len(cb.encode()) <= 64 for cb in callbacks)


def test_send_message_schema_exposes_action_buttons():
    from tools.send_message_tool import SEND_MESSAGE_SCHEMA

    props = SEND_MESSAGE_SCHEMA["parameters"]["properties"]
    assert "action_buttons" in props
    button_schema = props["action_buttons"]["items"]["items"]
    assert set(button_schema["required"]) == {"text", "response_text"}


@pytest.mark.asyncio
async def test_send_to_platform_attaches_action_buttons_to_telegram_message(tmp_path, monkeypatch):
    _ensure_telegram_mock()
    monkeypatch.setenv("HERMES_ACTION_BUTTON_REGISTRY_PATH", str(tmp_path / "registry.json"))

    import tools.send_message_tool as send_tool
    from gateway.config import Platform, PlatformConfig

    calls = []

    async def fake_send_telegram(*args, **kwargs):
        calls.append(kwargs)
        return {"success": True, "message_id": "42"}

    monkeypatch.setattr(send_tool, "_send_telegram", fake_send_telegram)

    result = await send_tool._send_to_platform(
        Platform.TELEGRAM,
        PlatformConfig(enabled=True, token="token"),
        "123",
        "Approve this?",
        action_buttons=[[{"text": "Approve", "response_text": "Approve this"}]],
    )

    assert result == {"success": True, "message_id": "42"}
    assert len(calls) == 1
    reply_markup = calls[0].get("reply_markup")
    assert reply_markup is not None
    callback = reply_markup.inline_keyboard[0][0].callback_data
    assert callback.startswith("sm:ab_")
