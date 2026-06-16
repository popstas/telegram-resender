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
