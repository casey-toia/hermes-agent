"""Durable registry for Telegram action-button callbacks.

The send_message tool and Telegram callback handler can run in different
processes. This JSON registry stores only routing metadata and the text that
should be injected back into the normal Hermes chat flow when a button is
clicked; callbacks do not perform business side effects directly.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    import fcntl
except Exception:  # pragma: no cover - non-POSIX fallback
    fcntl = None  # type: ignore[assignment]

_DEFAULT_TTL_SECONDS = 7 * 24 * 3600
_MAX_CALLBACK_BYTES = 64


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _registry_path() -> Path:
    override = os.getenv("HERMES_ACTION_BUTTON_REGISTRY_PATH", "").strip()
    if override:
        return Path(override).expanduser()
    from hermes_constants import get_hermes_home

    return get_hermes_home() / "runtime" / "action-buttons.json"


@contextlib.contextmanager
def _locked_registry():
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+") as lock_fh:
        if fcntl is not None:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        try:
            data = _read_registry(path)
            yield data
            _write_registry(path, data)
        finally:
            if fcntl is not None:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)


def _read_registry(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"entries": {}}
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {"entries": {}}
    if not isinstance(data, dict):
        return {"entries": {}}
    if not isinstance(data.get("entries"), dict):
        data["entries"] = {}
    return data


def _write_registry(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp_name, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_name)


def _clean_choice_id(raw: str, used: set[str]) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in raw.strip())
    base = "-".join(part for part in cleaned.split("-") if part)[:18] or "choice"
    choice_id = base
    idx = 2
    while choice_id in used:
        suffix = f"-{idx}"
        choice_id = f"{base[:18 - len(suffix)]}{suffix}"
        idx += 1
    used.add(choice_id)
    return choice_id


def _preview(text: str, limit: int = 500) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def create_action_button_entry(
    *,
    platform: str,
    chat_id: str,
    thread_id: str | None,
    text_preview: str,
    choices: dict[str, dict[str, Any]],
    ttl_seconds: int = _DEFAULT_TTL_SECONDS,
) -> dict[str, Any]:
    request_id = "ab_" + secrets.token_urlsafe(8).rstrip("=")
    created_at = _now()
    used: set[str] = set()
    normalized: dict[str, dict[str, Any]] = {}

    for raw_key, raw_choice in choices.items():
        label = str(raw_choice.get("label") or raw_choice.get("text") or raw_key).strip()
        response_text = str(raw_choice.get("response_text") or "").strip()
        if not label:
            raise ValueError("action button label cannot be empty")
        if not response_text:
            raise ValueError(f"action button {label!r} missing response_text")
        choice_id = _clean_choice_id(str(raw_choice.get("id") or raw_key or label), used)
        callback_data = f"sm:{request_id}:{choice_id}"
        if len(callback_data.encode("utf-8")) > _MAX_CALLBACK_BYTES:
            raise ValueError("generated Telegram callback_data exceeds 64 bytes")
        normalized[choice_id] = {
            "label": _preview(label, 80),
            "response_text": response_text,
            "consume": bool(raw_choice.get("consume", True)),
            "callback_data": callback_data,
        }

    if not normalized:
        raise ValueError("action_buttons must include at least one button")

    entry = {
        "request_id": request_id,
        "created_at": _iso(created_at),
        "expires_at": _iso(created_at + timedelta(seconds=ttl_seconds)),
        "platform": platform,
        "target": {"chat_id": str(chat_id), "thread_id": str(thread_id) if thread_id is not None else None},
        "message": {"text_preview": _preview(text_preview)},
        "choices": normalized,
        "status": "pending",
        "resolved_by": None,
        "resolved_at": None,
        "resolved_choice": None,
    }
    with _locked_registry() as data:
        data.setdefault("entries", {})[request_id] = entry
    return entry


def consume_action_button_choice(request_id: str, choice_id: str, *, resolved_by: str | None = None) -> dict[str, Any]:
    now = _now()
    with _locked_registry() as data:
        entry = data.setdefault("entries", {}).get(request_id)
        if not isinstance(entry, dict):
            return {"status": "not_found"}
        if entry.get("status", "pending") != "pending":
            return {"status": "already_resolved", "request_id": request_id, "resolved_choice": entry.get("resolved_choice")}
        expires_at = _parse_iso(entry.get("expires_at"))
        if expires_at is not None and expires_at <= now:
            entry["status"] = "expired"
            return {"status": "expired", "request_id": request_id}
        raw_choices = entry.get("choices")
        choices = raw_choices if isinstance(raw_choices, dict) else {}
        choice = choices.get(choice_id)
        if not isinstance(choice, dict):
            return {"status": "not_found", "request_id": request_id}
        if choice.get("consume", True):
            entry["status"] = "resolved"
            entry["resolved_by"] = resolved_by
            entry["resolved_at"] = _iso(now)
            entry["resolved_choice"] = choice_id
        return {
            "status": "resolved",
            "request_id": request_id,
            "choice_id": choice_id,
            "label": choice.get("label") or choice_id,
            "response_text": choice.get("response_text") or "",
            "entry": entry,
        }
