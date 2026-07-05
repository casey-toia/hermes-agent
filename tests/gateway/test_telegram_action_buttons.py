import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import BadRequest

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


@pytest.mark.asyncio
async def test_sm_callback_answer_failure_still_injects_response_text(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_ACTION_BUTTON_REGISTRY_PATH", str(tmp_path / "registry.json"))
    from tools.action_button_registry import create_action_button_entry

    entry = create_action_button_entry(
        platform="telegram",
        chat_id="123",
        thread_id=None,
        text_preview="Approve?",
        choices={"approve": {"label": "Approve", "response_text": "approve salus"}},
    )
    choice_id = next(iter(entry["choices"]))

    monkeypatch.setenv("GATEWAY_ALLOW_ALL_USERS", "true")
    adapter = _make_adapter()
    seen = []

    async def handler(event):
        seen.append(event)
        return None

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
        answer=AsyncMock(
            side_effect=BadRequest("Query is too old and response timeout expired or query id is invalid")
        ),
        edit_message_text=AsyncMock(),
    )

    await adapter._handle_callback_query(SimpleNamespace(callback_query=query), MagicMock())

    assert [event.text for event in seen] == ["approve salus"]
    assert seen[0].queue_when_busy is True
    query.answer.assert_awaited()


@pytest.mark.asyncio
async def test_sm_callback_second_click_does_not_double_inject(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_ACTION_BUTTON_REGISTRY_PATH", str(tmp_path / "registry.json"))
    from tools.action_button_registry import create_action_button_entry

    entry = create_action_button_entry(
        platform="telegram",
        chat_id="123",
        thread_id=None,
        text_preview="Approve?",
        choices={"approve": {"label": "Approve", "response_text": "approve once"}},
    )
    choice_id = next(iter(entry["choices"]))

    monkeypatch.setenv("GATEWAY_ALLOW_ALL_USERS", "true")
    adapter = _make_adapter()
    seen = []

    async def handler(event):
        seen.append(event)
        return None

    adapter._message_handler = handler
    adapter._send_message_with_thread_fallback = AsyncMock()

    def make_query():
        return SimpleNamespace(
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

    first = make_query()
    second = make_query()

    await adapter._handle_callback_query(SimpleNamespace(callback_query=first), MagicMock())
    await adapter._handle_callback_query(SimpleNamespace(callback_query=second), MagicMock())

    assert [event.text for event in seen] == ["approve once"]
    assert "already" in second.answer.await_args.kwargs["text"].lower()


@pytest.mark.asyncio
async def test_sm_callbacks_return_before_slow_handler_finishes(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_ACTION_BUTTON_REGISTRY_PATH", str(tmp_path / "registry.json"))
    from tools.action_button_registry import create_action_button_entry

    monkeypatch.setenv("GATEWAY_ALLOW_ALL_USERS", "true")
    adapter = _make_adapter()
    release_first = asyncio.Event()
    started = []

    async def handler(event):
        started.append(event.text)
        if event.text == "approve first":
            await release_first.wait()
        return None

    adapter._message_handler = handler
    adapter._send_message_with_thread_fallback = AsyncMock()

    first_entry = create_action_button_entry(
        platform="telegram",
        chat_id="123",
        thread_id=None,
        text_preview="Approve first?",
        choices={"approve": {"label": "Approve first", "response_text": "approve first"}},
    )
    second_entry = create_action_button_entry(
        platform="telegram",
        chat_id="123",
        thread_id=None,
        text_preview="Approve second?",
        choices={"approve": {"label": "Approve second", "response_text": "approve second"}},
    )

    def make_query(entry, message_id):
        choice_id = next(iter(entry["choices"]))
        return SimpleNamespace(
            data=f"sm:{entry['request_id']}:{choice_id}",
            from_user=SimpleNamespace(id=111, first_name="KC", full_name="KC Toia"),
            message=SimpleNamespace(
                chat_id=123,
                message_id=message_id,
                message_thread_id=None,
                text="Approve?",
                caption=None,
                chat=SimpleNamespace(type="private", title=None, full_name="KC"),
            ),
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )

    first_task = asyncio.create_task(
        adapter._handle_callback_query(SimpleNamespace(callback_query=make_query(first_entry, 456)), MagicMock())
    )
    await asyncio.wait_for(asyncio.shield(first_task), timeout=0.01)
    for _ in range(5):
        if "approve first" in started:
            break
        await asyncio.sleep(0)

    assert "approve first" in started

    await adapter._handle_callback_query(SimpleNamespace(callback_query=make_query(second_entry, 457)), MagicMock())

    assert "approve second" in started
    release_first.set()
    await first_task
