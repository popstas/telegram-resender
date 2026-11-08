"""Bot API delivery: a Telegram bot sends the match, so the account owner gets a
notification (messages sent from the owner's own account do not notify them).

A bot is not a member of the source chat and cannot forward the original, so it
sends one plain-text message per batch: the header, then each message's text
and a link to it.
"""

import logging
from typing import Any

import httpx

from .config import TargetBot
from .telegram_utils import get_message_url

logger = logging.getLogger(__name__)

API_URL = "https://api.telegram.org/bot{token}/sendMessage"
REQUEST_TIMEOUT = 10.0
MAX_LEN = 4096  # Telegram's limit for a message text


def message_link(message: Any) -> str | None:
    """Return ``t.me/<username>/<id>`` for a public chat, else the ``t.me/c`` link."""
    username = getattr(getattr(message, "chat", None), "username", None)
    msg_id = getattr(message, "id", None)
    if username and msg_id:
        return f"https://t.me/{username}/{msg_id}"
    return get_message_url(message)


def format_batch(header: str | None, messages: list) -> str:
    """Join the header and each message's text and link into one text."""
    parts = [header] if header else []
    for message in messages:
        lines = [
            getattr(message, "raw_text", None) or "",
            message_link(message) or "",
        ]
        part = "\n".join(line for line in lines if line)
        if part:
            parts.append(part)
    text = "\n\n".join(parts)
    if len(text) > MAX_LEN:
        text = text[: MAX_LEN - 1] + "…"
    return text


async def send_batch(
    target: TargetBot,
    header: str | None,
    messages: list,
    proxy_url: str | None = None,
) -> None:
    """Send the batch through the Bot API ``sendMessage``.

    Failures are logged and swallowed so the caller's flow is never broken.
    The token is masked in every log line.
    """
    payload = {
        "chat_id": target.chat_id,
        "text": format_batch(header, messages),
        "disable_web_page_preview": True,
    }
    try:
        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT, proxy=proxy_url
        ) as client:
            response = await client.post(
                API_URL.format(token=target.token), json=payload
            )
        if response.status_code >= 300:
            try:
                description = response.json().get("description")
            except Exception:  # pylint: disable=broad-except
                description = response.text[:500]
            logger.warning(
                "Bot API returned status %s for chat %s: %s",
                response.status_code,
                target.chat_id,
                _mask(str(description), target.token),
            )
        else:
            logger.info("Sent batch via bot to chat %s", target.chat_id)
    except Exception as exc:  # pylint: disable=broad-except
        logger.error(
            "Bot delivery to chat %s failed: %s: %s",
            target.chat_id,
            type(exc).__name__,
            _mask(str(exc), target.token),
        )


def _mask(text: str, token: str) -> str:
    return text.replace(token, "***") if token else text
