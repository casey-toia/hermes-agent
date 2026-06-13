import json
from datetime import datetime, timedelta, timezone

import pytest


def test_create_and_consume_action_button_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_ACTION_BUTTON_REGISTRY_PATH", str(tmp_path / "registry.json"))
    from tools.action_button_registry import create_action_button_entry, consume_action_button_choice

    entry = create_action_button_entry(
        platform="telegram",
        chat_id="123",
        thread_id=None,
        text_preview="Approve this?",
        choices={"approve": {"label": "Approve", "response_text": "Approve"}},
    )

    choice_id, choice = next(iter(entry["choices"].items()))
    assert choice["callback_data"].startswith(f"sm:{entry['request_id']}:")
    assert len(choice["callback_data"].encode()) <= 64

    result = consume_action_button_choice(entry["request_id"], choice_id, resolved_by="KC")
    assert result["status"] == "resolved"
    assert result["response_text"] == "Approve"

    again = consume_action_button_choice(entry["request_id"], choice_id, resolved_by="KC")
    assert again["status"] == "already_resolved"


def test_expired_action_button(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_ACTION_BUTTON_REGISTRY_PATH", str(tmp_path / "registry.json"))
    from tools.action_button_registry import create_action_button_entry, consume_action_button_choice

    entry = create_action_button_entry(
        platform="telegram",
        chat_id="123",
        thread_id=None,
        text_preview="Approve this?",
        choices={"deny": {"label": "Deny", "response_text": "Deny"}},
        ttl_seconds=-1,
    )
    choice_id = next(iter(entry["choices"]))
    assert consume_action_button_choice(entry["request_id"], choice_id)["status"] == "expired"
