"""HTTP webhook delivery for matched messages."""

import logging
from typing import Any

import httpx

from .config import TargetWebhook
from .telegram_utils import get_message_url

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 10.0


def _sender_username(message: Any) -> str | None:
    sender = getattr(message, "sender", None)
    return getattr(sender, "username", None) if sender is not None else None


def _sender_full_name(message: Any) -> str:
    sender = getattr(message, "sender", None)
    if sender is None:
        return ""
    first = getattr(sender, "first_name", None) or ""
    last = getattr(sender, "last_name", None) or ""
    full = f"{first} {last}".strip()
    if full:
        return full
    title = getattr(sender, "title", None)
    if title:
        return str(title)
    username = getattr(sender, "username", None)
    return username or ""


def _message_text(message: Any) -> str:
    return getattr(message, "raw_text", None) or getattr(message, "message", "") or ""


def _chat_id(message: Any) -> int | None:
    peer = getattr(message, "peer_id", None)
    if peer is None:
        return None
    for attr in ("channel_id", "chat_id", "user_id"):
        value = getattr(peer, attr, None)
        if value is not None:
            return value
    return None


def _timestamp(message: Any) -> str | None:
    date = getattr(message, "date", None)
    if date is None:
        return None
    try:
        return date.isoformat()
    except Exception:  # pylint: disable=broad-except
        return str(date)


def format_text_payload(message: Any) -> str:
    """Return a single-line text payload describing the message."""
    username = _sender_username(message) or ""
    user_part = f"@{username}" if username else ""
    name = _sender_full_name(message)
    text = _message_text(message)
    return f"From: {user_part}, Name: {name}, Message: {text}"


def format_json_payload(message: Any, category: str | None = None) -> dict:
    """Return a dict payload describing the message."""
    return {
        "from_username": _sender_username(message),
        "from_name": _sender_full_name(message),
        "message_text": _message_text(message),
        "chat_id": _chat_id(message),
        "message_id": getattr(message, "id", None),
        "message_url": get_message_url(message),
        "timestamp": _timestamp(message),
        "category": category,
    }


async def _ensure_sender_loaded(message: Any) -> None:
    """Force Telethon to resolve ``message.sender`` if it's lazy-loaded."""
    if getattr(message, "sender", None) is not None:
        return
    getter = getattr(message, "get_sender", None)
    if not callable(getter):
        return
    try:
        await getter()
    except Exception:  # pylint: disable=broad-except
        logger.debug("Failed to resolve sender for webhook payload", exc_info=True)


async def send_webhook(
    target: TargetWebhook, message: Any, category: str | None = None
) -> None:
    """POST ``message`` to ``target.url`` using ``target.format``.

    Failures are logged and swallowed so the caller's flow is never broken.
    """
    try:
        await _ensure_sender_loaded(message)
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            if target.format == "json":
                response = await client.post(
                    target.url, json=format_json_payload(message, category)
                )
            else:
                response = await client.post(
                    target.url,
                    content=format_text_payload(message).encode("utf-8"),
                    headers={"Content-Type": "text/plain; charset=utf-8"},
                )
        if response.status_code >= 300:
            logger.warning(
                "Webhook %s returned status %s: %s",
                target.url,
                response.status_code,
                response.text[:500],
            )
    except Exception:  # pylint: disable=broad-except
        logger.exception("Webhook delivery to %s failed", target.url)
