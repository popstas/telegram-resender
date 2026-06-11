import json
from datetime import datetime, timedelta

from src.seen_chats import SeenChatStore, _reset_boundary


def test_reset_boundary_after_reset_hour():
    now = datetime(2026, 6, 11, 10, 0, 0)
    assert _reset_boundary(now, 6) == datetime(2026, 6, 11, 6, 0, 0)


def test_reset_boundary_before_reset_hour():
    now = datetime(2026, 6, 11, 3, 0, 0)
    assert _reset_boundary(now, 6) == datetime(2026, 6, 10, 6, 0, 0)


def test_reset_boundary_at_reset_hour():
    now = datetime(2026, 6, 11, 6, 0, 0)
    assert _reset_boundary(now, 6) == datetime(2026, 6, 11, 6, 0, 0)


def test_first_then_suppressed_same_day(tmp_path):
    store = SeenChatStore(str(tmp_path / "seen.json"), flush_interval=0)
    now = datetime(2026, 6, 11, 10, 0, 0)
    assert store.should_forward("inst", 1, now, 6) is True
    store.record("inst", 1, now)
    later = datetime(2026, 6, 11, 23, 0, 0)
    assert store.should_forward("inst", 1, later, 6) is False


def test_rearms_after_next_reset_boundary(tmp_path):
    store = SeenChatStore(str(tmp_path / "seen.json"), flush_interval=0)
    now = datetime(2026, 6, 11, 10, 0, 0)
    store.record("inst", 1, now)
    # Next day past the reset hour crosses the boundary.
    next_day = datetime(2026, 6, 12, 7, 0, 0)
    assert store.should_forward("inst", 1, next_day, 6) is True


def test_instances_and_chats_isolated(tmp_path):
    store = SeenChatStore(str(tmp_path / "seen.json"), flush_interval=0)
    now = datetime(2026, 6, 11, 10, 0, 0)
    store.record("inst_a", 1, now)
    assert store.should_forward("inst_a", 1, now, 6) is False
    # Different chat in same instance.
    assert store.should_forward("inst_a", 2, now, 6) is True
    # Different instance, same chat.
    assert store.should_forward("inst_b", 1, now, 6) is True


def test_persistence_reload(tmp_path):
    path = tmp_path / "seen.json"
    store = SeenChatStore(str(path), flush_interval=0)
    now = datetime(2026, 6, 11, 10, 0, 0)
    store.record("inst", 1, now)
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "inst": {"1": now.timestamp()}
    }
    reloaded = SeenChatStore(str(path))
    assert reloaded.should_forward("inst", 1, now, 6) is False


def test_missing_file_empty_store(tmp_path):
    store = SeenChatStore(str(tmp_path / "nope.json"))
    assert store.data == {}
    assert store.should_forward("inst", 1, datetime(2026, 6, 11, 10), 6) is True


def test_corrupt_file_empty_store(tmp_path):
    path = tmp_path / "seen.json"
    path.write_text("{not valid json", encoding="utf-8")
    store = SeenChatStore(str(path))
    assert store.data == {}


def test_non_dict_json_empty_store(tmp_path):
    path = tmp_path / "seen.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    store = SeenChatStore(str(path))
    assert store.data == {}
