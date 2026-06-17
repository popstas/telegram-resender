from types import SimpleNamespace

import pytest
from telethon import types

import src.telegram_utils as tgu


@pytest.mark.asyncio
async def test_warm_entity_cache_calls_get_dialogs(monkeypatch):
    calls = []

    async def fake_get_dialogs(*args, **kwargs):
        calls.append((args, kwargs))
        return []

    monkeypatch.setattr(tgu, "client", SimpleNamespace(get_dialogs=fake_get_dialogs))

    await tgu.warm_entity_cache()

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_warm_entity_cache_swallows_exceptions(monkeypatch, caplog):
    async def boom(*args, **kwargs):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(tgu, "client", SimpleNamespace(get_dialogs=boom))

    # Must not raise — a warming failure cannot crash startup.
    await tgu.warm_entity_cache()

    assert any("Failed to warm entity cache" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_to_event_chat_id_resolves_user_to_positive_id(monkeypatch):
    """A bare positive user id resolves to a positive id once the entity is known.

    Regression for the ``-user_id`` fallback that broke private-chat matching:
    when ``get_input_entity`` succeeds (entity cache warmed), ``to_event_chat_id``
    returns the positive ``user_id`` (the real ``event.chat_id`` for that chat).
    """

    user_id = 1042930695

    async def fake_get_input_entity(peer):
        assert peer == user_id
        return types.InputPeerUser(user_id=user_id, access_hash=12345)

    monkeypatch.setattr(
        tgu, "client", SimpleNamespace(get_input_entity=fake_get_input_entity)
    )

    result = await tgu.to_event_chat_id(user_id)

    assert result == user_id


@pytest.mark.asyncio
async def test_to_event_chat_id_falls_back_when_unresolved(monkeypatch):
    """When resolution fails, the bare positive id falls back to ``-peer``."""

    async def fake_get_input_entity(peer):
        raise ValueError("Could not find the input entity for PeerUser")

    monkeypatch.setattr(
        tgu, "client", SimpleNamespace(get_input_entity=fake_get_input_entity)
    )

    result = await tgu.to_event_chat_id(898532232)

    assert result == -898532232


def _make_private_message(username="alice", first="Alice", last="Smith"):
    """Build a minimal private message for source-rendering tests."""
    return SimpleNamespace(
        id=10,
        peer_id=types.PeerUser(user_id=555),
        sender=SimpleNamespace(username=username, first_name=first, last_name=last),
    )


@pytest.mark.asyncio
async def test_forward_message_default_matches_today(monkeypatch):
    """With no template/flags, output is the byte-identical ``{reason}\\n\\n{source}``."""

    async def fake_source(m):
        return "Forwarded from: private @alice"

    monkeypatch.setattr(tgu, "get_message_source", fake_source)

    msg = _make_private_message()
    text = await tgu.get_forward_message_text(msg, word="hello")

    assert text == "word: hello\n\nForwarded from: private @alice"


@pytest.mark.asyncio
async def test_forward_message_default_no_reason(monkeypatch):
    """No reason => just the source (no leading separators), as today."""

    async def fake_source(m):
        return "Forwarded from: private @alice"

    monkeypatch.setattr(tgu, "get_message_source", fake_source)

    msg = _make_private_message()
    text = await tgu.get_forward_message_text(msg)

    assert text == "Forwarded from: private @alice"


@pytest.mark.asyncio
async def test_forward_message_template_placeholders(monkeypatch):
    """A template fills ``{trigger}``, ``{source}``, ``{username}``, ``{name}``, ``{chat}``."""

    async def fake_source(m):
        return "SRC"

    async def fake_chat_name(peer, safe=False):
        return "Alice Chat"

    monkeypatch.setattr(tgu, "get_message_source", fake_source)
    monkeypatch.setattr(tgu, "get_chat_name", fake_chat_name)

    msg = _make_private_message()
    text = await tgu.get_forward_message_text(
        msg,
        word="hi",
        message_template="{trigger} | {source} | {username} | {name} | {chat}",
    )

    assert text == "word: hi | SRC | @alice | Alice Smith | Alice Chat"


@pytest.mark.asyncio
async def test_forward_message_template_unknown_placeholder(monkeypatch):
    """Unknown placeholders render as empty string, never raise."""

    async def fake_source(m):
        return "SRC"

    async def fake_chat_name(peer, safe=False):
        return "C"

    monkeypatch.setattr(tgu, "get_message_source", fake_source)
    monkeypatch.setattr(tgu, "get_chat_name", fake_chat_name)

    msg = _make_private_message()
    text = await tgu.get_forward_message_text(
        msg, word="hi", message_template="{trigger}[{nope}]"
    )

    assert text == "word: hi[]"


@pytest.mark.asyncio
async def test_forward_message_empty_template(monkeypatch):
    """An empty template renders to an empty string."""

    async def fake_source(m):
        return "SRC"

    async def fake_chat_name(peer, safe=False):
        return "C"

    monkeypatch.setattr(tgu, "get_message_source", fake_source)
    monkeypatch.setattr(tgu, "get_chat_name", fake_chat_name)

    msg = _make_private_message()
    text = await tgu.get_forward_message_text(msg, word="hi", message_template="")

    assert text == ""


@pytest.mark.asyncio
async def test_forward_message_flags_drop_trigger_and_source(monkeypatch):
    """``show_trigger``/``show_source`` flags drop their parts."""

    async def fake_source(m):
        return "SRC"

    monkeypatch.setattr(tgu, "get_message_source", fake_source)

    msg = _make_private_message()

    only_source = await tgu.get_forward_message_text(msg, word="hi", show_trigger=False)
    assert only_source == "SRC"

    only_trigger = await tgu.get_forward_message_text(msg, word="hi", show_source=False)
    assert only_trigger == "word: hi"

    neither = await tgu.get_forward_message_text(
        msg, word="hi", show_trigger=False, show_source=False
    )
    assert neither == ""


@pytest.mark.asyncio
async def test_forward_message_prefix_suffix_wrap(monkeypatch):
    """``prefix``/``suffix`` wrap the assembled body."""

    async def fake_source(m):
        return "SRC"

    monkeypatch.setattr(tgu, "get_message_source", fake_source)

    msg = _make_private_message()
    text = await tgu.get_forward_message_text(
        msg, word="hi", prefix=">> ", suffix=" <<"
    )

    assert text == ">> word: hi\n\nSRC <<"


@pytest.mark.asyncio
async def test_forward_message_template_none_source_fields(monkeypatch):
    """Missing/None source fields render as empty strings."""

    async def fake_source(m):
        return "SRC"

    async def fake_chat_name(peer, safe=False):
        return ""

    monkeypatch.setattr(tgu, "get_message_source", fake_source)
    monkeypatch.setattr(tgu, "get_chat_name", fake_chat_name)

    msg = SimpleNamespace(
        id=10,
        peer_id=types.PeerUser(user_id=555),
        sender=SimpleNamespace(username=None, first_name=None, last_name=None),
    )
    text = await tgu.get_forward_message_text(
        msg, message_template="[{username}][{name}][{chat}]"
    )

    assert text == "[][][]"
