from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.gateway.test_telegram_approval_buttons import _make_adapter


@pytest.mark.asyncio
async def test_sm_callback_injects_response_text(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_ACTION_BUTTON_REGISTRY_PATH", str(tmp_path / "registry.json"))
    from tools.action_button_registry import create_action_button_entry

    entry = create_action_button_entry(
        platform="telegram",
        chat_id="123",
        thread_id=None,
        text_preview="Approve?",
        choices={"approve": {"label": "Approve", "response_text": "Approve"}},
    )
    choice_id = next(iter(entry["choices"]))

    monkeypatch.setenv("GATEWAY_ALLOW_ALL_USERS", "true")
    adapter = _make_adapter()
    seen = []

    async def handler(event):
        seen.append(event)
        return "handled"

    adapter._message_handler = handler
    adapter._send_message_with_thread_fallback = AsyncMock()

    query = SimpleNamespace(
        data=f"sm:{entry['request_id']}:{choice_id}",
        from_user=SimpleNamespace(id=111, first_name="KC", full_name="KC Toia"),
        message=SimpleNamespace(
            chat_id=123,
            message_id=456,
            message_thread_id=None,
            text="Approve?",
            caption=None,
            chat=SimpleNamespace(type="private", title=None, full_name="KC"),
        ),
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    update = SimpleNamespace(callback_query=query)

    await adapter._handle_callback_query(update, MagicMock())

    assert [event.text for event in seen] == ["Approve"]
    assert seen[0].queue_when_busy is True
    query.answer.assert_awaited()
    query.edit_message_text.assert_awaited()
    adapter._send_message_with_thread_fallback.assert_awaited()


@pytest.mark.asyncio
async def test_sm_callback_rejects_second_click(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_ACTION_BUTTON_REGISTRY_PATH", str(tmp_path / "registry.json"))
    from tools.action_button_registry import create_action_button_entry, consume_action_button_choice

    entry = create_action_button_entry(
        platform="telegram",
        chat_id="123",
        thread_id=None,
        text_preview="Approve?",
        choices={"approve": {"label": "Approve", "response_text": "Approve"}},
    )
    choice_id = next(iter(entry["choices"]))
    consume_action_button_choice(entry["request_id"], choice_id)

    monkeypatch.setenv("GATEWAY_ALLOW_ALL_USERS", "true")
    adapter = _make_adapter()
    adapter._message_handler = AsyncMock()
    query = SimpleNamespace(
        data=f"sm:{entry['request_id']}:{choice_id}",
        from_user=SimpleNamespace(id=111, first_name="KC", full_name="KC Toia"),
        message=SimpleNamespace(chat_id=123, message_thread_id=None, chat=SimpleNamespace(type="private")),
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )

    await adapter._handle_callback_query(SimpleNamespace(callback_query=query), MagicMock())

    adapter._message_handler.assert_not_awaited()
    assert "already" in query.answer.await_args.kwargs["text"]
