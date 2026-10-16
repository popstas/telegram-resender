import logging
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

from src import webhook
from src.config import TargetWebhook


def _make_message(**overrides):
    base = SimpleNamespace(
        id=42,
        raw_text="Hello, how are you?",
        peer_id=SimpleNamespace(channel_id=1001),
        sender=SimpleNamespace(
            username="alice",
            first_name="Alice",
            last_name="Doe",
        ),
        date=datetime(2026, 5, 15, 12, 30, 0, tzinfo=timezone.utc),
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def test_format_text_payload_full():
    msg = _make_message()
    payload = webhook.format_text_payload(msg)
    assert payload == "From: @alice, Name: Alice Doe, Message: Hello, how are you?"


def test_format_text_payload_no_username_no_lastname():
    msg = _make_message(
        sender=SimpleNamespace(username=None, first_name="John", last_name=None)
    )
    payload = webhook.format_text_payload(msg)
    assert payload == "From: , Name: John, Message: Hello, how are you?"


def test_format_json_payload():
    msg = _make_message()
    payload = webhook.format_json_payload(msg)
    assert payload["from_username"] == "alice"
    assert payload["from_name"] == "Alice Doe"
    assert payload["message_text"] == "Hello, how are you?"
    assert payload["chat_id"] == 1001
    assert payload["message_id"] == 42
    assert payload["message_url"] == "https://t.me/c/1001/42"
    assert payload["timestamp"] == "2026-05-15T12:30:00+00:00"


def test_json_payload_category():
    payload = webhook.format_json_payload(_make_message(), category="lead_direct")
    assert payload["category"] == "lead_direct"


def test_json_payload_category_none():
    assert webhook.format_json_payload(_make_message())["category"] is None


def test_text_payload_unchanged_with_category_support():
    assert "category" not in webhook.format_text_payload(_make_message())


def test_format_json_payload_handles_missing_sender():
    msg = _make_message(sender=None)
    payload = webhook.format_json_payload(msg)
    assert payload["from_username"] is None
    assert payload["from_name"] == ""


@pytest.mark.asyncio
async def test_send_webhook_text(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200
        text = "ok"

    class FakeClient:
        def __init__(self, *args, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, **kwargs):
            captured["url"] = url
            captured["kwargs"] = kwargs
            return FakeResponse()

    monkeypatch.setattr(webhook.httpx, "AsyncClient", FakeClient)
    target = TargetWebhook(url="http://localhost:8002/hook", format="text")
    msg = _make_message()
    await webhook.send_webhook(target, msg)

    assert captured["url"] == "http://localhost:8002/hook"
    assert (
        captured["kwargs"]["content"]
        == b"From: @alice, Name: Alice Doe, Message: Hello, how are you?"
    )
    assert captured["kwargs"]["headers"]["Content-Type"].startswith("text/plain")
    assert captured["client_kwargs"]["timeout"] == webhook.REQUEST_TIMEOUT


@pytest.mark.asyncio
async def test_send_webhook_json(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200
        text = "ok"

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, **kwargs):
            captured["url"] = url
            captured["kwargs"] = kwargs
            return FakeResponse()

    monkeypatch.setattr(webhook.httpx, "AsyncClient", FakeClient)
    target = TargetWebhook(url="http://localhost:8002/hook", format="json")
    msg = _make_message()
    await webhook.send_webhook(target, msg)

    assert captured["url"] == "http://localhost:8002/hook"
    body = captured["kwargs"]["json"]
    assert body["from_username"] == "alice"
    assert body["chat_id"] == 1001
    assert body["message_id"] == 42


@pytest.mark.asyncio
async def test_send_webhook_swallows_network_error(monkeypatch, caplog):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, **kwargs):
            raise httpx.ConnectError("boom")

    monkeypatch.setattr(webhook.httpx, "AsyncClient", FakeClient)
    target = TargetWebhook(url="http://localhost:8002/hook", format="text")
    msg = _make_message()

    with caplog.at_level(logging.ERROR):
        await webhook.send_webhook(target, msg)

    assert any("Webhook delivery" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_send_webhook_resolves_lazy_sender(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200
        text = "ok"

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, **kwargs):
            captured["kwargs"] = kwargs
            return FakeResponse()

    monkeypatch.setattr(webhook.httpx, "AsyncClient", FakeClient)

    msg = _make_message(sender=None)

    async def fake_get_sender():
        msg.sender = SimpleNamespace(
            username="lazyuser", first_name="Lazy", last_name="User"
        )
        return msg.sender

    msg.get_sender = fake_get_sender

    target = TargetWebhook(url="http://localhost:8002/hook", format="text")
    await webhook.send_webhook(target, msg)

    assert (
        captured["kwargs"]["content"]
        == b"From: @lazyuser, Name: Lazy User, Message: Hello, how are you?"
    )


@pytest.mark.asyncio
async def test_send_webhook_logs_non_2xx(monkeypatch, caplog):
    class FakeResponse:
        status_code = 500
        text = "server error"

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(webhook.httpx, "AsyncClient", FakeClient)
    target = TargetWebhook(url="http://localhost:8002/hook", format="text")
    msg = _make_message()

    with caplog.at_level(logging.WARNING):
        await webhook.send_webhook(target, msg)

    assert any("returned status 500" in rec.message for rec in caplog.records)
