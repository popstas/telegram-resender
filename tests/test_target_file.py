import os
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src import target_file
from src import telegram_utils as tgu
from src.config import TargetFile

DATE = datetime(2026, 11, 5, 14, 3, 9, tzinfo=timezone.utc)


def _msg(msg_id, text=None, *, media=None, username="ivan_p", date=DATE, **extra):
    base = SimpleNamespace(
        id=msg_id,
        message=text,
        raw_text=text,
        media=media,
        date=date,
        chat_id=555,
        peer_id=SimpleNamespace(user_id=555),
        sender=SimpleNamespace(
            username=username, first_name="Иван", last_name="Петров"
        ),
    )
    for key, value in extra.items():
        setattr(base, key, value)
    return base


@pytest.fixture
def fake_source(monkeypatch):
    async def fake_get_message_source(message):
        return "Forwarded from: private @ivan_p, Name: Иван Петров"

    monkeypatch.setattr(tgu, "get_message_source", fake_get_message_source)


def test_filename_uses_login_and_trigger_id():
    assert (
        target_file.build_filename(_msg(4242))
        == "2026-11-05_14-03-09_ivan_p_4242.md"
    )


def test_filename_strips_unsafe_characters():
    msg = _msg(7, username=None, chat=SimpleNamespace(username="my chat/../x"))
    name = target_file.build_filename(msg)
    assert name == "2026-11-05_14-03-09_my_chat_.._x_7.md"
    assert all(c.isalnum() or c in "_.-" for c in name)


def test_filename_falls_back_to_chat_id_for_cyrillic_or_missing_login():
    msg = _msg(8, username=None, chat=SimpleNamespace(username=None))
    msg.chat_id = -1001234
    assert target_file.build_filename(msg) == "2026-11-05_14-03-09_chat1001234_8.md"


def test_filename_without_date_uses_current_utc_time():
    name = target_file.build_filename(_msg(9, date=None))
    assert name.endswith("_ivan_p_9.md")
    assert name[:4].isdigit()


def test_sanitize_never_starts_with_dot():
    assert target_file.sanitize("...hidden") == "hidden"
    assert target_file.sanitize("Сотрудники") == ""


@pytest.mark.asyncio
async def test_write_batch_content(tmp_path, fake_source):
    messages = [
        _msg(100, "Привет!"),
        _msg(101, None, media=object()),
        _msg(102, "Подскажите по договору"),
        _msg(103, None),  # service message: no text, no media -> skipped
    ]
    path = await target_file.write_batch(
        TargetFile(dir=str(tmp_path)), messages, messages[0]
    )

    assert path == str(tmp_path / "2026-11-05_14-03-09_ivan_p_100.md")
    with open(path, encoding="utf-8") as f:
        content = f.read()
    assert content == (
        "Forwarded from: private @ivan_p, Name: Иван Петров\n"
        "\n"
        "Привет!\n"
        "\n"
        f"{tgu.PROTECTED_MEDIA_NOTE}\n"
        "\n"
        "Подскажите по договору\n"
    )
    assert "do telegram-message-analyze" not in content
    assert sorted(os.listdir(tmp_path)) == ["2026-11-05_14-03-09_ivan_p_100.md"]
    assert oct(os.stat(path).st_mode & 0o777) == "0o644"


@pytest.mark.asyncio
async def test_write_batch_is_atomic(tmp_path, fake_source, monkeypatch):
    calls = []

    def failing_replace(src, dst):
        calls.append((src, dst))
        with open(src, encoding="utf-8") as f:
            calls.append(f.read())
        raise OSError("disk full")

    monkeypatch.setattr(target_file.os, "replace", failing_replace)

    path = await target_file.write_batch(
        TargetFile(dir=str(tmp_path)), [_msg(100, "Привет!")], _msg(100, "Привет!")
    )

    assert path is None
    src, dst = calls[0]
    assert os.path.dirname(src) == str(tmp_path)
    assert os.path.basename(src) == ".2026-11-05_14-03-09_ivan_p_100.md.tmp"
    assert dst == str(tmp_path / "2026-11-05_14-03-09_ivan_p_100.md")
    # The temp file was complete before the rename.
    assert calls[1].endswith("Привет!\n")
    # Nothing is left behind: no final file, no temp file.
    assert os.listdir(tmp_path) == []


@pytest.mark.asyncio
async def test_write_batch_never_overwrites(tmp_path, fake_source):
    existing = tmp_path / "2026-11-05_14-03-09_ivan_p_100.md"
    existing.write_text("old", encoding="utf-8")

    path = await target_file.write_batch(
        TargetFile(dir=str(tmp_path)), [_msg(100, "new")], _msg(100, "new")
    )

    assert existing.read_text(encoding="utf-8") == "old"
    assert path == str(tmp_path / "2026-11-05_14-03-09_ivan_p_100_2.md")


@pytest.mark.asyncio
async def test_write_batch_missing_dir_is_logged(tmp_path, fake_source, caplog):
    target = TargetFile(dir=str(tmp_path / "missing"))
    path = await target_file.write_batch(target, [_msg(1, "x")], _msg(1, "x"))
    assert path is None
    assert "Failed to write" in caplog.text
