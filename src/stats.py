import atexit
import datetime
import json
import logging
import os
import time
from dataclasses import asdict, dataclass

logger = logging.getLogger(__name__)

STATS_PATH = os.path.join("data", "stats.json")


def current_day() -> str:
    """Return the current day string in UTC."""
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")


@dataclass
class Stats:
    """Statistics counters for processed messages."""

    total: int = 0
    forwarded_total: int = 0
    forwarded_words: int = 0
    forwarded_prompt: int = 0
    forwarded_category: int = 0
    tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    @classmethod
    def from_dict(cls, data: dict | None) -> "Stats":
        data = data or {}
        return cls(
            total=data.get("total", 0),
            forwarded_total=data.get("forwarded_total", 0),
            forwarded_words=data.get("forwarded_words", 0),
            forwarded_prompt=data.get("forwarded_prompt", 0),
            forwarded_category=data.get("forwarded_category", 0),
            tokens=data.get("tokens", 0),
            input_tokens=data.get("input_tokens", 0),
            output_tokens=data.get("output_tokens", 0),
        )

    def to_dict(self) -> dict:
        return asdict(self)


def convert(data: dict) -> dict:
    """Convert stats from the old format to the new one."""

    if "stats" in data:
        return data

    new_data = {
        "stats": Stats(
            total=data.get("total", 0), tokens=data.get("tokens", 0)
        ).to_dict(),
        "instances": [],
    }

    for inst in data.get("instances", []):
        inst_data = {
            "name": inst.get("name"),
            "stats": Stats(
                total=inst.get("total", 0), tokens=inst.get("tokens", 0)
            ).to_dict(),
            "days": {},
            "tokens": inst.get("tokens", 0),
            "input_tokens": 0,
            "output_tokens": 0,
        }
        for day, val in inst.get("days", {}).items():
            if isinstance(val, dict) and "stats" in val:
                inst_data["days"][day] = val
            else:
                inst_data["days"][day] = {"stats": Stats(total=val).to_dict()}
        new_data["instances"].append(inst_data)

    return new_data


class StatsTracker:
    """Collect and periodically flush statistics about processed messages."""

    def __init__(self, path: str, flush_interval: int = 60) -> None:
        self.path = path
        self.flush_interval = flush_interval
        self.last_flush = time.monotonic()
        self.dirty = False
        self.data = {"stats": Stats().to_dict(), "instances": []}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
                if "stats" not in self.data:
                    self.data = convert(self.data)
            except Exception:  # pragma: no cover - corrupt file
                self.data = {"stats": Stats().to_dict(), "instances": []}

    def _get_inst(self, name: str) -> dict:
        for inst in self.data.get("instances", []):
            if inst.get("name") == name:
                inst.setdefault("stats", Stats().to_dict())
                inst.setdefault("tokens", 0)
                inst.setdefault("input_tokens", 0)
                inst.setdefault("output_tokens", 0)
                return inst
        inst = {
            "name": name,
            "stats": Stats().to_dict(),
            "days": {},
            "tokens": 0,
            "input_tokens": 0,
            "output_tokens": 0,
        }
        self.data.setdefault("instances", []).append(inst)
        return inst

    def increment(
        self,
        name: str,
        forwarded: bool = False,
        used_word: bool = False,
        used_prompt: bool = False,
        used_category: bool = False,
    ) -> None:
        inst = self._get_inst(name)
        day = current_day()
        day_stat = inst["days"].setdefault(day, {"stats": Stats().to_dict()})
        for scope in (self.data["stats"], inst["stats"], day_stat["stats"]):
            scope["total"] = scope.get("total", 0) + 1
            if forwarded:
                scope["forwarded_total"] = scope.get("forwarded_total", 0) + 1
                if used_word:
                    scope["forwarded_words"] = scope.get("forwarded_words", 0) + 1
                if used_prompt:
                    scope["forwarded_prompt"] = scope.get("forwarded_prompt", 0) + 1
                if used_category:
                    scope["forwarded_category"] = scope.get("forwarded_category", 0) + 1
        self.dirty = True
        if time.monotonic() - self.last_flush >= self.flush_interval:
            self.flush()

    def add_tokens(
        self,
        name: str,
        input_tokens: int,
        output_tokens: int,
        *,
        total_tokens: int | None = None,
    ) -> None:
        in_t = max(0, input_tokens)
        out_t = max(0, output_tokens)
        tot = total_tokens if total_tokens is not None else None
        if tot is not None:
            tot = max(0, tot)
        if in_t <= 0 and out_t <= 0 and (tot is None or tot <= 0):
            return
        delta_total = tot if tot is not None and tot > 0 else in_t + out_t
        if delta_total <= 0:
            return
        inst = self._get_inst(name)
        day = current_day()
        day_stat = inst["days"].setdefault(day, {"stats": Stats().to_dict()})
        for scope in (self.data["stats"], inst["stats"], day_stat["stats"]):
            scope["input_tokens"] = scope.get("input_tokens", 0) + in_t
            scope["output_tokens"] = scope.get("output_tokens", 0) + out_t
            scope["tokens"] = scope.get("tokens", 0) + delta_total
        inst["input_tokens"] = inst.get("input_tokens", 0) + in_t
        inst["output_tokens"] = inst.get("output_tokens", 0) + out_t
        inst["tokens"] = inst.get("tokens", 0) + delta_total
        self.dirty = True
        if time.monotonic() - self.last_flush >= self.flush_interval:
            self.flush()

    def set_folder_chats(self, name: str, chat_ids: list[int]) -> None:
        """Store normalized folder chat IDs for an instance (sibling of per-instance stats)."""
        inst = self._get_inst(name)
        inst["chats"] = chat_ids
        self.dirty = True
        if time.monotonic() - self.last_flush >= self.flush_interval:
            self.flush()

    def clear_folder_chats(self, name: str) -> None:
        """Remove folder chat list for an instance when it no longer uses folders."""
        for inst in self.data.get("instances", []):
            if inst.get("name") == name and inst.pop("chats", None) is not None:
                self.dirty = True
                if time.monotonic() - self.last_flush >= self.flush_interval:
                    self.flush()
                break

    def flush(self) -> None:
        if not self.dirty:
            return
        logger.debug("Flushing stats to %s", self.path)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=4)
        self.last_flush = time.monotonic()
        self.dirty = False


stats = StatsTracker(STATS_PATH)
atexit.register(stats.flush)
