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


def test_claim_then_mark_action_button_enqueued(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_ACTION_BUTTON_REGISTRY_PATH", str(tmp_path / "registry.json"))
    from tools.action_button_registry import (
        claim_action_button_choice,
        create_action_button_entry,
        mark_action_button_enqueued,
    )

    entry = create_action_button_entry(
        platform="telegram",
        chat_id="123",
        thread_id=None,
        text_preview="Approve this?",
        choices={"approve": {"label": "Approve", "response_text": "Approve"}},
    )
    choice_id = next(iter(entry["choices"]))

    claimed = claim_action_button_choice(entry["request_id"], choice_id, resolved_by="KC")
    assert claimed["status"] == "claimed"
    assert claimed["response_text"] == "Approve"

    enqueued = mark_action_button_enqueued(
        entry["request_id"], choice_id, message_id="callback:456:request:approve"
    )
    assert enqueued["status"] == "enqueued"

    registry = json.loads((tmp_path / "registry.json").read_text())
    stored = registry["entries"][entry["request_id"]]
    assert stored["status"] == "enqueued"
    assert stored["resolved_choice"] == choice_id
    assert stored["enqueued_message_id"] == "callback:456:request:approve"


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
