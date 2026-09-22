"""Current-chat FRND proposal buttons; durable, one-shot callbacks per proposal."""

import asyncio
import json
import os
import re
import secrets
import sqlite3
from contextlib import closing

from gateway.session_context import get_session_env
from hermes_constants import get_process_hermes_home
from tools.registry import registry, tool_error

_PROPOSAL = re.compile(r"frnd-[A-Za-z0-9][A-Za-z0-9-]*\Z")


def _db():
    path = get_process_hermes_home() / "data" / "telegram_action_buttons.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)
    conn = sqlite3.connect(path, timeout=10)
    conn.execute("""CREATE TABLE IF NOT EXISTS buttons (
        id TEXT PRIMARY KEY, chat_id TEXT NOT NULL, thread_id TEXT NOT NULL,
        session_key TEXT NOT NULL, message_id TEXT NOT NULL DEFAULT '',
        proposal_id TEXT NOT NULL, consumed INTEGER NOT NULL DEFAULT 0)""")
    return conn


def _insert(button_id, chat_id, thread_id, session_key, proposal_id):
    with closing(_db()) as db, db:
        db.execute("INSERT INTO buttons(id,chat_id,thread_id,session_key,proposal_id) VALUES (?,?,?,?,?)",
                   (button_id, chat_id, thread_id, session_key, proposal_id))


def _bind(button_id, message_id):
    with closing(_db()) as db, db:
        db.execute("UPDATE buttons SET message_id=? WHERE id=?", (str(message_id), button_id))


def _remove(button_id):
    with closing(_db()) as db, db:
        db.execute("DELETE FROM buttons WHERE id=?", (button_id,))


def claim(button_id, chat_id, thread_id, session_key, message_id):
    """Atomically claim this exact sent card; a duplicate or wrong lane cannot act."""
    with closing(_db()) as db, db:
        row = db.execute("""UPDATE buttons SET consumed=1 WHERE id=? AND chat_id=? AND thread_id=?
                            AND session_key=? AND message_id=? AND consumed=0 RETURNING proposal_id""",
                         (button_id, chat_id, thread_id, session_key, message_id)).fetchone()
        return row[0] if row else None


def release(button_id):
    """A refused gateway admission must leave the button tappable."""
    with closing(_db()) as db, db:
        db.execute("UPDATE buttons SET consumed=0 WHERE id=?", (button_id,))


def send_action_buttons_tool(args, **_kw):
    proposal_id = args.get("proposal_id")
    message = args.get("message")
    if not isinstance(proposal_id, str) or not _PROPOSAL.fullmatch(proposal_id):
        return tool_error("proposal_id must be a literal frnd- identifier")
    if not isinstance(message, str) or not message.strip() or len(message) > 3000:
        return tool_error("message must contain 1–3000 characters")
    if get_session_env("HERMES_SESSION_PLATFORM") != "telegram" or get_session_env("HERMES_SESSION_PROFILE") not in ("", "default"):
        return tool_error("Only the default-profile live Telegram session may send these buttons")
    chat_id = get_session_env("HERMES_SESSION_CHAT_ID")
    thread_id = get_session_env("HERMES_SESSION_THREAD_ID") or ""
    session_key = get_session_env("HERMES_SESSION_KEY")
    if not chat_id or not session_key:
        return tool_error("A current Telegram chat and session are required")
    from gateway.config import Platform
    from tools.send_message_senders import _live_adapter
    runner, adapter = _live_adapter(Platform.TELEGRAM)
    loop = getattr(runner, "_gateway_loop", None)
    if adapter is None or loop is None or not loop.is_running():
        return tool_error("Live Telegram gateway adapter unavailable")
    button_id = secrets.token_hex(8)
    _insert(button_id, chat_id, thread_id, session_key, proposal_id)
    try:
        result = asyncio.run_coroutine_threadsafe(
            adapter.send_frnd_action_buttons(chat_id, thread_id, message, button_id, proposal_id), loop
        ).result(timeout=40)
        if not result.success or not result.message_id:
            _remove(button_id)
            return tool_error(result.error or "Telegram did not confirm button delivery")
        _bind(button_id, result.message_id)
        return json.dumps({"success": True, "message_id": result.message_id, "proposal_id": proposal_id})
    except Exception:
        # A timed-out send may still land: retain the unbound row (inert) instead of
        # allowing a retry that could authorize a duplicate action.
        raise


registry.register(
    name="send_action_buttons", toolset="telegram_action_buttons",
    schema={"name": "send_action_buttons",
            "description": "Send durable Approve/Deny buttons for a FRND proposal in this Telegram chat. Each card is independent; clicks queue as separate user turns.",
            "parameters": {"type": "object", "properties": {
                "proposal_id": {"type": "string", "description": "Exact FRND ledger ID, e.g. frnd-123."},
                "message": {"type": "string", "description": "Proposal summary displayed with its exact ID."}},
                "required": ["proposal_id", "message"]}},
    handler=send_action_buttons_tool,
)
