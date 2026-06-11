import atexit
import json
import logging
import os
import time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

SEEN_CHATS_PATH = os.path.join("data", "seen_chats.json")


def _reset_boundary(now: datetime, reset_hour: int) -> datetime:
    """Most recent local occurrence of ``reset_hour:00``.

    Today's ``reset_hour:00`` when ``now`` is at or past it, otherwise
    yesterday's.
    """

    boundary = now.replace(hour=reset_hour, minute=0, second=0, microsecond=0)
    if now.hour < reset_hour:
        boundary -= timedelta(days=1)
    return boundary


class SeenChatStore:
    """Track the last per-instance, per-chat forward time for ``once_per_chat``.

    Mirrors :class:`src.trace_ids.TraceStore`: dirty-flag with interval flush and
    an ``atexit`` hook. Stored at ``data/seen_chats.json`` as
    ``{instance_name: {chat_id: last_forward_epoch}}``.
    """

    def __init__(self, path: str, flush_interval: int = 60) -> None:
        self.path = path
        self.flush_interval = flush_interval
        self.last_flush = time.monotonic()
        self.dirty = False
        self.data: dict[str, dict[str, float]] = {}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    self.data = loaded
            except Exception:  # pragma: no cover - corrupt file
                self.data = {}

    def should_forward(
        self, instance: str, chat_id: int | str, now: datetime, reset_hour: int
    ) -> bool:
        """True when this chat has no record or its record predates the reset boundary."""

        record = self.data.get(instance, {}).get(str(chat_id))
        if record is None:
            return True
        return record < _reset_boundary(now, reset_hour).timestamp()

    def record(self, instance: str, chat_id: int | str, now: datetime) -> None:
        chat = self.data.setdefault(instance, {})
        chat[str(chat_id)] = now.timestamp()
        self.dirty = True
        if time.monotonic() - self.last_flush >= self.flush_interval:
            self.flush()

    def flush(self) -> None:
        if not self.dirty:
            return
        logger.debug("Flushing seen chats to %s", self.path)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=4)
        self.last_flush = time.monotonic()
        self.dirty = False


seen_chats = SeenChatStore(SEEN_CHATS_PATH)
atexit.register(seen_chats.flush)
