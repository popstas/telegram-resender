"""Inbox-file delivery: one Markdown file per forwarded batch.

The resender only creates new files and never edits or removes them, so every
file has a single writer. A file appears under its final name only when it is
complete: it is written to a dot-prefixed temp file in the same directory,
flushed to disk and renamed.
"""

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

from . import telegram_utils
from .config import TargetFile

logger = logging.getLogger(__name__)

_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def sanitize(value: str | None) -> str:
    """Keep ``[A-Za-z0-9_.-]`` only; never start with a dot or underscore."""
    cleaned = _UNSAFE.sub("_", value or "")
    cleaned = re.sub(r"_+", "_", cleaned)
    return cleaned.lstrip("._").rstrip("_")


def _label(message: Any) -> str:
    sender = getattr(message, "sender", None)
    chat = getattr(message, "chat", None)
    for candidate in (getattr(sender, "username", None), getattr(chat, "username", None)):
        label = sanitize(candidate)
        if label:
            return label
    chat_id = getattr(message, "chat_id", None)
    if chat_id is None:
        peer = getattr(message, "peer_id", None)
        for attr in ("channel_id", "chat_id", "user_id"):
            chat_id = getattr(peer, attr, None)
            if chat_id is not None:
                break
    return f"chat{abs(chat_id)}" if chat_id is not None else "unknown"


def build_filename(trigger: Any) -> str:
    """Return ``YYYY-MM-DD_HH-MM-SS_<login or chat>_<msg_id>.md`` (time in UTC)."""
    date = getattr(trigger, "date", None) or datetime.now(timezone.utc)
    stamp = date.astimezone(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    msg_id = sanitize(str(getattr(trigger, "id", ""))) or "0"
    return f"{stamp}_{_label(trigger)}_{msg_id}.md"


def message_body(message: Any) -> str:
    """Return the text of ``message``, a note for media without text, or ``""``."""
    text = getattr(message, "message", None) or getattr(message, "raw_text", None)
    if text:
        return text
    if getattr(message, "media", None) is not None:
        return telegram_utils.PROTECTED_MEDIA_NOTE
    return ""


def _free_path(directory: str, filename: str) -> str:
    stem, ext = os.path.splitext(filename)
    path = os.path.join(directory, filename)
    n = 2
    while os.path.exists(path):
        path = os.path.join(directory, f"{stem}_{n}{ext}")
        n += 1
    return path


def _write_atomic(path: str, content: str) -> None:
    directory, name = os.path.split(path)
    tmp = os.path.join(directory, f".{name}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


async def write_batch(target: TargetFile, messages: list, trigger: Any) -> str | None:
    """Write one file for the batch; return its path, or ``None`` on failure.

    The first line is the ``Forwarded from: …`` source of ``trigger``, then the
    text of every message in order, separated by blank lines. Failures are
    logged and swallowed so the other targets keep working.
    """
    try:
        await telegram_utils._get_full_sender(trigger)
        source = await telegram_utils.get_message_source(trigger)
        parts = [source] + [b for b in (message_body(m) for m in messages) if b]
        content = "\n\n".join(parts) + "\n"
        if not os.path.isdir(target.dir):
            raise FileNotFoundError(f"target_file.dir does not exist: {target.dir}")
        path = _free_path(target.dir, build_filename(trigger))
        _write_atomic(path, content)
    except Exception:  # pylint: disable=broad-except
        logger.exception("Failed to write batch to %s", target.dir)
        return None
    logger.info("Wrote message %s to %s", getattr(trigger, "id", None), path)
    return path
