import logging
from types import SimpleNamespace

import httpx
import pytest

from src import target_bot
from src.config import TargetBot


def _msg(msg_id=7, text="Вещание началось", username="radio_t_chat", channel_id=100):
    return SimpleNamespace(
        id=msg_id,
        raw_text=text,
        peer_id=SimpleNamespace(channel_id=channel_id),
        chat=SimpleNamespace(username=username) if username else None,
    )


def test_message_link_prefers_public_username():
    assert target_bot.message_link(_msg()) == "https://t.me/radio_t_chat/7"


def test_message_link_falls_back_to_private_link():
    assert target_bot.message_link(_msg(username=None)) == "https://t.me/c/100/7"


def test_format_batch_header_texts_and_links():
    text = target_bot.format_batch("HEADER", [_msg(1, "one"), _msg(2, "two")])
    assert text == (
        "HEADER\n\n"
        "one\nhttps://t.me/radio_t_chat/1\n\n"
        "two\nhttps://t.me/radio_t_chat/2"
    )


def test_format_batch_without_header_and_text():
    text = target_bot.format_batch(None, [_msg(3, None)])
    assert text == "https://t.me/radio_t_chat/3"


def test_format_batch_truncates_to_telegram_limit():
    text = target_bot.format_batch(None, [_msg(text="x" * 5000)])
    assert len(text) == target_bot.MAX_LEN
    assert text.endswith("…")


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {"ok": True}
        self.text = str(self._payload)

    def json(self):
        return self._payload


def _fake_client(captured, response=None, error=None):
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
            if error:
                raise error
            return response or _FakeResponse()

    return FakeClient


@pytest.mark.asyncio
async def test_send_batch_posts_send_message(monkeypatch):
    captured = {}
    monkeypatch.setattr(target_bot.httpx, "AsyncClient", _fake_client(captured))
    target = TargetBot(chat_id=241225329, token="123:abc")

    await target_bot.send_batch(target, "HEADER", [_msg(1, "one")])

    assert captured["url"] == "https://api.telegram.org/bot123:abc/sendMessage"
    body = captured["kwargs"]["json"]
    assert body["chat_id"] == 241225329
    assert body["text"] == "HEADER\n\none\nhttps://t.me/radio_t_chat/1"
    assert body["disable_web_page_preview"] is True
    assert "parse_mode" not in body
    assert captured["client_kwargs"]["timeout"] == target_bot.REQUEST_TIMEOUT
    assert captured["client_kwargs"].get("proxy") is None


@pytest.mark.asyncio
async def test_send_batch_uses_proxy(monkeypatch):
    captured = {}
    monkeypatch.setattr(target_bot.httpx, "AsyncClient", _fake_client(captured))
    await target_bot.send_batch(
        TargetBot(chat_id=1, token="t"),
        None,
        [_msg()],
        proxy_url="socks5://127.0.0.1:1080",
    )
    assert captured["client_kwargs"]["proxy"] == "socks5://127.0.0.1:1080"


@pytest.mark.asyncio
async def test_send_batch_logs_api_error_without_token(monkeypatch, caplog):
    captured = {}
    response = _FakeResponse(400, {"ok": False, "description": "chat not found"})
    monkeypatch.setattr(
        target_bot.httpx, "AsyncClient", _fake_client(captured, response=response)
    )
    with caplog.at_level(logging.WARNING):
        await target_bot.send_batch(
            TargetBot(chat_id=1, token="123:secret"), None, [_msg()]
        )
    assert "chat not found" in caplog.text
    assert "123:secret" not in caplog.text


@pytest.mark.asyncio
async def test_send_batch_swallows_network_error_without_token(monkeypatch, caplog):
    captured = {}
    error = httpx.ConnectError(
        "boom https://api.telegram.org/bot123:secret/sendMessage"
    )
    monkeypatch.setattr(
        target_bot.httpx, "AsyncClient", _fake_client(captured, error=error)
    )
    with caplog.at_level(logging.ERROR):
        await target_bot.send_batch(
            TargetBot(chat_id=1, token="123:secret"), None, [_msg()]
        )
    assert "Bot delivery" in caplog.text
    assert "123:secret" not in caplog.text
