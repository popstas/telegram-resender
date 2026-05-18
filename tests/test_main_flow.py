import asyncio
import json
import logging
from types import SimpleNamespace

import pytest

import src.app as app
import src.config as config_module
import src.prompts as prompts
import src.stats as stats_module
import src.telegram_utils as tgu


class BreakLoop(Exception):
    pass


@pytest.mark.asyncio
async def test_rescan_loop(monkeypatch):
    sleep_calls = []
    load_calls = []

    async def fake_sleep(t):
        sleep_calls.append(t)
        return None

    async def fake_update(inst, fr):
        raise BreakLoop

    def fake_load_config():
        load_calls.append(True)
        return {}

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(app, "update_instance_chat_ids", fake_update)
    monkeypatch.setattr(app, "load_config", fake_load_config)

    inst = app.Instance(name="i", words=[], target_chat=0)
    with pytest.raises(BreakLoop):
        await app.rescan_loop(inst, interval=0)
    assert sleep_calls == [0]
    assert len(load_calls) == 1


@pytest.mark.asyncio
async def test_setup_logging(monkeypatch):
    recorded = {}

    def fake_basicConfig(**kwargs):
        recorded.update(kwargs)

    monkeypatch.setattr(logging, "basicConfig", fake_basicConfig)
    tele_logger = logging.getLogger("telethon")
    tele_logger.setLevel(logging.INFO)
    app.setup_logging("debug")
    assert recorded["level"] == logging.DEBUG
    assert tele_logger.level == logging.WARNING


@pytest.mark.asyncio
async def test_main_flow(monkeypatch, dummy_tg_client, dummy_message_cls, tmp_path):
    config = {"log_level": "info"}
    monkeypatch.setattr(app, "load_config", lambda: config)
    monkeypatch.setattr(app, "get_api_credentials", lambda cfg: (1, "h", "s"))

    dummy_client = dummy_tg_client
    monkeypatch.setattr(app, "TelegramClient", lambda s, a, b, proxy=None: dummy_client)

    stats_path = tmp_path / "stats.json"
    monkeypatch.setattr(
        app, "stats", stats_module.StatsTracker(str(stats_path), flush_interval=0)
    )

    async def fake_rescan(inst):
        return None

    monkeypatch.setattr(app, "rescan_loop", fake_rescan)

    async def fake_update(inst, fr):
        inst.chat_ids = {1}

    monkeypatch.setattr(app, "update_instance_chat_ids", fake_update)

    async def fake_load_instances(cfg):
        return [
            app.Instance(name="i", words=["hi"], target_chat=99, target_entity="name")
        ]

    monkeypatch.setattr(app, "load_instances", fake_load_instances)

    async def fake_get_message_source(m):
        return "URL"

    monkeypatch.setattr(tgu, "get_message_source", fake_get_message_source)

    async def fake_get_chat_name(v, safe=False):
        return "name"

    monkeypatch.setattr(tgu, "get_chat_name", fake_get_chat_name)

    await app.main()
    assert app.config is config

    handler = dummy_client.on_handler
    msg = dummy_message_cls(SimpleNamespace(channel_id=1), msg_id=5, text="hi there")
    event = SimpleNamespace(message=msg, chat_id=1)
    await handler(event)
    assert msg.forwarded == [99, "name"]
    assert dummy_client.sent[0][0][0] == 99
    assert dummy_client.sent[1][0][0] == "name"
    data = json.loads(stats_path.read_text())
    assert data["stats"]["total"] == 1
    assert data["stats"]["forwarded_total"] == 1
    assert data["stats"]["forwarded_words"] == 1
    inst = data["instances"][0]
    assert inst["name"] == "i"
    assert inst["stats"]["total"] == 1
    assert inst["stats"]["forwarded_total"] == 1
    assert inst["stats"]["forwarded_words"] == 1


@pytest.mark.asyncio
async def test_process_message_prompt(monkeypatch, dummy_message_cls, tmp_path):
    sent = []

    class DummyClient:
        async def send_message(self, *a, **k):
            sent.append((a, k))

    app.client = DummyClient()
    tgu.client = app.client
    app.stats = stats_module.StatsTracker(
        str(tmp_path / "stats.json"), flush_interval=0
    )

    inst = app.Instance(
        name="p",
        words=[],
        prompts=[prompts.Prompt(name="hi", prompt="hi", threshold=4)],
        target_chat=1,
    )

    async def fake_match(prompt, text, inst_name, chat_name):
        assert prompt.prompt == "hi"
        assert inst_name == "p"
        assert chat_name == "n"
        return prompts.MatchPromptResult(score=5, reasoning="", quote="", trace_id=None)

    async def fake_get_message_source(msg):
        return "src"

    async def fake_get_chat_name(v, safe=False):
        return "n"

    monkeypatch.setattr(app, "match_prompt", fake_match)
    monkeypatch.setattr(tgu, "get_message_source", fake_get_message_source)
    monkeypatch.setattr(tgu, "get_chat_name", fake_get_chat_name)
    monkeypatch.setattr(app, "get_chat_name", fake_get_chat_name)

    msg = dummy_message_cls(SimpleNamespace(channel_id=1), msg_id=7, text="hi")
    event = SimpleNamespace(message=msg, chat_id=1)
    await app.process_message(inst, event)

    assert sent[0][0][0] == 1
    assert msg.forwarded == [1]
    assert app.stats.data["stats"]["forwarded_total"] == 1
    assert app.stats.data["stats"]["forwarded_prompt"] == 1
    inst_data = app.stats.data["instances"][0]
    assert inst_data["stats"]["forwarded_total"] == 1
    assert inst_data["stats"]["forwarded_prompt"] == 1


@pytest.mark.asyncio
async def test_process_message_target_webhook(monkeypatch, dummy_message_cls, tmp_path):
    sent = []

    class DummyClient:
        async def send_message(self, *a, **k):
            sent.append((a, k))

    app.client = DummyClient()
    tgu.client = app.client
    app.stats = stats_module.StatsTracker(
        str(tmp_path / "stats.json"), flush_interval=0
    )

    target_webhook = config_module.TargetWebhook(
        url="http://localhost:8002/hook", format="json"
    )
    inst = app.Instance(
        name="w",
        words=["hi"],
        target_chat=1,
        target_webhook=target_webhook,
    )

    called = []

    async def fake_send_webhook(target, message):
        called.append((target, message))

    monkeypatch.setattr(app.webhook, "send_webhook", fake_send_webhook)

    async def fake_get_message_source(msg):
        return "src"

    async def fake_get_chat_name(v, safe=False):
        return "n"

    monkeypatch.setattr(tgu, "get_message_source", fake_get_message_source)
    monkeypatch.setattr(tgu, "get_chat_name", fake_get_chat_name)
    monkeypatch.setattr(app, "get_chat_name", fake_get_chat_name)

    msg = dummy_message_cls(SimpleNamespace(channel_id=1), msg_id=9, text="hi")
    event = SimpleNamespace(message=msg, chat_id=1)
    await app.process_message(inst, event)

    assert msg.forwarded == [1]
    assert len(called) == 1
    assert called[0][0] is target_webhook
    assert called[0][1] is msg


@pytest.mark.asyncio
async def test_process_message_no_forward_message(
    monkeypatch, dummy_message_cls, tmp_path
):
    sent = []

    class DummyClient:
        async def send_message(self, *a, **k):
            sent.append((a, k))

    app.client = DummyClient()
    tgu.client = app.client
    app.stats = stats_module.StatsTracker(
        str(tmp_path / "stats.json"), flush_interval=0
    )

    inst = app.Instance(name="n", words=["hi"], target_chat=1, no_forward_message=True)

    async def fake_get_message_source(msg):
        return "src"

    async def fake_get_chat_name(v, safe=False):
        return "n"

    monkeypatch.setattr(tgu, "get_message_source", fake_get_message_source)
    monkeypatch.setattr(tgu, "get_chat_name", fake_get_chat_name)
    monkeypatch.setattr(app, "get_chat_name", fake_get_chat_name)

    msg = dummy_message_cls(SimpleNamespace(channel_id=1), msg_id=8, text="hi")
    event = SimpleNamespace(message=msg, chat_id=1)
    await app.process_message(inst, event)

    assert sent == []
    assert msg.forwarded == [1]


@pytest.mark.asyncio
async def test_ignore_usernames(
    monkeypatch, dummy_tg_client, dummy_message_cls, tmp_path
):
    config = {"log_level": "info", "ignore_usernames": ["bad"]}
    monkeypatch.setattr(app, "load_config", lambda: config)
    monkeypatch.setattr(app, "get_api_credentials", lambda cfg: (1, "h", "s"))

    dummy_client = dummy_tg_client
    monkeypatch.setattr(app, "TelegramClient", lambda s, a, b, proxy=None: dummy_client)

    stats_path = tmp_path / "stats.json"
    monkeypatch.setattr(
        app, "stats", stats_module.StatsTracker(str(stats_path), flush_interval=0)
    )

    async def fake_rescan(inst):
        return None

    monkeypatch.setattr(app, "rescan_loop", fake_rescan)

    async def fake_update(inst, fr):
        inst.chat_ids = {1}

    monkeypatch.setattr(app, "update_instance_chat_ids", fake_update)

    async def fake_load_instances(cfg):
        return [app.Instance(name="i", words=["hi"], target_chat=99)]

    monkeypatch.setattr(app, "load_instances", fake_load_instances)

    async def fake_get_message_source(m):
        return "URL"

    monkeypatch.setattr(tgu, "get_message_source", fake_get_message_source)

    async def fake_get_chat_name(v, safe=False):
        return "name"

    monkeypatch.setattr(tgu, "get_chat_name", fake_get_chat_name)

    await app.main()

    handler = dummy_client.on_handler
    msg = dummy_message_cls(SimpleNamespace(channel_id=1), msg_id=5, text="hi")
    msg.sender = SimpleNamespace(username="bad")
    event = SimpleNamespace(message=msg, chat_id=1)
    await handler(event)
    assert msg.forwarded == []
    assert dummy_client.sent == []
    assert app.stats.data["stats"]["total"] == 0


@pytest.mark.asyncio
async def test_ignore_usernames_override_empty(
    monkeypatch, dummy_tg_client, dummy_message_cls, tmp_path
):
    """An empty ignore_usernames_override on an instance disables the global list."""
    config = {"log_level": "info", "ignore_usernames": ["bad"]}
    monkeypatch.setattr(app, "load_config", lambda: config)
    monkeypatch.setattr(app, "get_api_credentials", lambda cfg: (1, "h", "s"))

    dummy_client = dummy_tg_client
    monkeypatch.setattr(app, "TelegramClient", lambda s, a, b, proxy=None: dummy_client)

    stats_path = tmp_path / "stats.json"
    monkeypatch.setattr(
        app, "stats", stats_module.StatsTracker(str(stats_path), flush_interval=0)
    )

    async def fake_rescan(inst):
        return None

    monkeypatch.setattr(app, "rescan_loop", fake_rescan)

    async def fake_update(inst, fr):
        inst.chat_ids = {1}

    monkeypatch.setattr(app, "update_instance_chat_ids", fake_update)

    async def fake_load_instances(cfg):
        return [
            app.Instance(
                name="i",
                words=["hi"],
                target_chat=99,
                ignore_usernames_override=[],
            )
        ]

    monkeypatch.setattr(app, "load_instances", fake_load_instances)

    async def fake_get_message_source(m):
        return "URL"

    monkeypatch.setattr(tgu, "get_message_source", fake_get_message_source)

    async def fake_get_chat_name(v, safe=False):
        return "name"

    monkeypatch.setattr(tgu, "get_chat_name", fake_get_chat_name)
    monkeypatch.setattr(app, "get_chat_name", fake_get_chat_name)

    await app.main()

    handler = dummy_client.on_handler
    msg = dummy_message_cls(SimpleNamespace(channel_id=1), msg_id=5, text="hi")
    msg.sender = SimpleNamespace(username="bad")
    event = SimpleNamespace(message=msg, chat_id=1)
    await handler(event)
    assert msg.forwarded == [99]
    assert app.stats.data["stats"]["forwarded_total"] == 1


@pytest.mark.asyncio
async def test_ignore_usernames_override_replaces_global(
    monkeypatch, dummy_tg_client, dummy_message_cls, tmp_path
):
    """A non-empty override replaces the global list entirely."""
    config = {"log_level": "info", "ignore_usernames": ["bad"]}
    monkeypatch.setattr(app, "load_config", lambda: config)
    monkeypatch.setattr(app, "get_api_credentials", lambda cfg: (1, "h", "s"))

    dummy_client = dummy_tg_client
    monkeypatch.setattr(app, "TelegramClient", lambda s, a, b, proxy=None: dummy_client)

    stats_path = tmp_path / "stats.json"
    monkeypatch.setattr(
        app, "stats", stats_module.StatsTracker(str(stats_path), flush_interval=0)
    )

    async def fake_rescan(inst):
        return None

    monkeypatch.setattr(app, "rescan_loop", fake_rescan)

    async def fake_update(inst, fr):
        inst.chat_ids = {1}

    monkeypatch.setattr(app, "update_instance_chat_ids", fake_update)

    async def fake_load_instances(cfg):
        return [
            app.Instance(
                name="i",
                words=["hi"],
                target_chat=99,
                ignore_usernames_override=["other"],
            )
        ]

    monkeypatch.setattr(app, "load_instances", fake_load_instances)

    async def fake_get_message_source(m):
        return "URL"

    monkeypatch.setattr(tgu, "get_message_source", fake_get_message_source)

    async def fake_get_chat_name(v, safe=False):
        return "name"

    monkeypatch.setattr(tgu, "get_chat_name", fake_get_chat_name)
    monkeypatch.setattr(app, "get_chat_name", fake_get_chat_name)

    await app.main()

    handler = dummy_client.on_handler

    # Sender on global list but NOT on the instance override → forwarded.
    msg_bad = dummy_message_cls(SimpleNamespace(channel_id=1), msg_id=5, text="hi")
    msg_bad.sender = SimpleNamespace(username="bad")
    await handler(SimpleNamespace(message=msg_bad, chat_id=1))
    assert msg_bad.forwarded == [99]

    # Sender on instance override → blocked even though not on global list.
    msg_other = dummy_message_cls(SimpleNamespace(channel_id=1), msg_id=6, text="hi")
    msg_other.sender = SimpleNamespace(username="other")
    await handler(SimpleNamespace(message=msg_other, chat_id=1))
    assert msg_other.forwarded == []

    assert app.stats.data["stats"]["forwarded_total"] == 1


@pytest.mark.asyncio
async def test_ignore_usernames_override_lazy_sender(
    monkeypatch, dummy_tg_client, dummy_message_cls, tmp_path
):
    """Handler must await ``event.get_sender()`` when ``message.sender`` is None.

    Reproduces the case where the session owner's own messages arrive with
    ``message.sender`` not yet resolved by Telethon — username must still be
    matched against ``ignore_usernames_override`` after a lazy fetch.
    """
    config = {"log_level": "info", "ignore_usernames": []}
    monkeypatch.setattr(app, "load_config", lambda: config)
    monkeypatch.setattr(app, "get_api_credentials", lambda cfg: (1, "h", "s"))

    dummy_client = dummy_tg_client
    monkeypatch.setattr(app, "TelegramClient", lambda s, a, b, proxy=None: dummy_client)

    stats_path = tmp_path / "stats.json"
    monkeypatch.setattr(
        app, "stats", stats_module.StatsTracker(str(stats_path), flush_interval=0)
    )

    async def fake_rescan(inst):
        return None

    monkeypatch.setattr(app, "rescan_loop", fake_rescan)

    async def fake_update(inst, fr):
        inst.chat_ids = {1}

    monkeypatch.setattr(app, "update_instance_chat_ids", fake_update)

    async def fake_load_instances(cfg):
        return [
            app.Instance(
                name="i",
                words=["hi"],
                target_chat=99,
                ignore_usernames_override=["popstas"],
            )
        ]

    monkeypatch.setattr(app, "load_instances", fake_load_instances)

    async def fake_get_message_source(m):
        return "URL"

    monkeypatch.setattr(tgu, "get_message_source", fake_get_message_source)

    async def fake_get_chat_name(v, safe=False):
        return "name"

    monkeypatch.setattr(tgu, "get_chat_name", fake_get_chat_name)

    await app.main()

    handler = dummy_client.on_handler
    msg = dummy_message_cls(SimpleNamespace(channel_id=1), msg_id=7, text="hi")
    msg.sender = None
    lazy_calls = []

    async def lazy_get_sender():
        lazy_calls.append(True)
        return SimpleNamespace(username="popstas", id=12345)

    event = SimpleNamespace(message=msg, chat_id=1, get_sender=lazy_get_sender)
    await handler(event)

    assert lazy_calls, "handler must await event.get_sender() when sender is None"
    assert msg.forwarded == []
    assert dummy_client.sent == []
    assert app.stats.data["stats"].get("forwarded_total", 0) == 0


@pytest.mark.asyncio
async def test_ignore_user_ids(
    monkeypatch, dummy_tg_client, dummy_message_cls, tmp_path
):
    config = {"log_level": "info", "ignore_user_ids": [42]}
    monkeypatch.setattr(app, "load_config", lambda: config)
    monkeypatch.setattr(app, "get_api_credentials", lambda cfg: (1, "h", "s"))

    dummy_client = dummy_tg_client
    monkeypatch.setattr(app, "TelegramClient", lambda s, a, b, proxy=None: dummy_client)

    stats_path = tmp_path / "stats.json"
    monkeypatch.setattr(
        app, "stats", stats_module.StatsTracker(str(stats_path), flush_interval=0)
    )

    async def fake_rescan(inst):
        return None

    monkeypatch.setattr(app, "rescan_loop", fake_rescan)

    async def fake_update(inst, fr):
        inst.chat_ids = {1}

    monkeypatch.setattr(app, "update_instance_chat_ids", fake_update)

    async def fake_load_instances(cfg):
        return [app.Instance(name="i", words=["hi"], target_chat=99)]

    monkeypatch.setattr(app, "load_instances", fake_load_instances)

    async def fake_get_message_source(m):
        return "URL"

    monkeypatch.setattr(tgu, "get_message_source", fake_get_message_source)

    async def fake_get_chat_name(v, safe=False):
        return "name"

    monkeypatch.setattr(tgu, "get_chat_name", fake_get_chat_name)

    await app.main()

    handler = dummy_client.on_handler
    msg = dummy_message_cls(SimpleNamespace(channel_id=1), msg_id=5, text="hi")
    msg.sender = SimpleNamespace(id=42)
    event = SimpleNamespace(message=msg, chat_id=1)
    await handler(event)
    assert msg.forwarded == []
    assert dummy_client.sent == []
    assert app.stats.data["stats"]["total"] == 0


@pytest.mark.asyncio
async def test_false_positive_reaction(monkeypatch, dummy_message_cls):
    msg = dummy_message_cls(SimpleNamespace(channel_id=77), msg_id=5, text="hi")

    class DummyClient:
        async def get_messages(self, peer, ids):
            return msg

        async def get_entity(self, ident):
            return SimpleNamespace(channel_id=77)

    app.client = DummyClient()
    tgu.client = app.client
    inst = app.Instance(
        name="i",
        words=[],
        target_entity="t",
        false_positive_entity="fp",
    )
    app.instances = [inst]

    update = tgu.types.UpdateMessageReactions(
        peer=tgu.types.PeerChannel(77),
        msg_id=5,
        reactions=tgu.types.MessageReactions(
            results=[tgu.types.ReactionCount(tgu.types.ReactionEmoji("\U0001f44e"), 1)]
        ),
    )

    async def fake_to_event_chat_id(peer):
        return 77

    async def fake_get_forward_message_text(m, **kwargs):
        return "src"

    monkeypatch.setattr(tgu, "to_event_chat_id", fake_to_event_chat_id)
    monkeypatch.setattr(tgu, "get_forward_message_text", fake_get_forward_message_text)

    await app.handle_reaction(update)

    assert msg.forwarded == ["fp"]


@pytest.mark.asyncio
async def test_negative_reaction_twice(monkeypatch, dummy_message_cls):
    msg = dummy_message_cls(SimpleNamespace(channel_id=77), msg_id=5, text="hi")

    class DummyClient:
        async def get_messages(self, peer, ids):
            return msg

        async def get_entity(self, ident):
            return SimpleNamespace(channel_id=77)

    app.client = DummyClient()
    tgu.client = app.client
    app.forwarded_positive.clear()
    app.forwarded_negative.clear()
    inst = app.Instance(
        name="i",
        words=[],
        target_entity="t",
        false_positive_entity="fp",
    )
    app.instances = [inst]

    update = tgu.types.UpdateMessageReactions(
        peer=tgu.types.PeerChannel(77),
        msg_id=5,
        reactions=tgu.types.MessageReactions(
            results=[tgu.types.ReactionCount(tgu.types.ReactionEmoji("\U0001f44e"), 1)]
        ),
    )

    async def fake_to_event_chat_id(peer):
        return 77

    async def fake_get_forward_message_text(m, **kwargs):
        return "src"

    monkeypatch.setattr(tgu, "to_event_chat_id", fake_to_event_chat_id)
    monkeypatch.setattr(tgu, "get_forward_message_text", fake_get_forward_message_text)

    await app.handle_reaction(update)
    await app.handle_reaction(update)

    assert msg.forwarded == ["fp"]


@pytest.mark.asyncio
async def test_true_positive_reaction(monkeypatch, dummy_message_cls):
    msg = dummy_message_cls(SimpleNamespace(channel_id=77), msg_id=5, text="hi")

    class DummyClient:
        async def get_messages(self, peer, ids):
            return msg

        async def get_entity(self, ident):
            return SimpleNamespace(channel_id=77)

    app.client = DummyClient()
    inst = app.Instance(
        name="i",
        words=[],
        target_entity="t",
        true_positive_entity="tp",
    )
    app.instances = [inst]

    update = tgu.types.UpdateMessageReactions(
        peer=tgu.types.PeerChannel(77),
        msg_id=5,
        reactions=tgu.types.MessageReactions(
            results=[tgu.types.ReactionCount(tgu.types.ReactionEmoji("\U0001f44d"), 1)]
        ),
    )

    async def fake_to_event_chat_id(peer):
        return 77

    async def fake_get_forward_message_text(m, **kwargs):
        return "src"

    monkeypatch.setattr(tgu, "to_event_chat_id", fake_to_event_chat_id)
    monkeypatch.setattr(tgu, "get_forward_message_text", fake_get_forward_message_text)

    await app.handle_reaction(update)

    assert msg.forwarded == ["tp"]


@pytest.mark.asyncio
async def test_positive_reaction_twice(monkeypatch, dummy_message_cls):
    msg = dummy_message_cls(SimpleNamespace(channel_id=77), msg_id=5, text="hi")

    class DummyClient:
        async def get_messages(self, peer, ids):
            return msg

        async def get_entity(self, ident):
            return SimpleNamespace(channel_id=77)

    app.client = DummyClient()
    app.forwarded_positive.clear()
    app.forwarded_negative.clear()
    inst = app.Instance(
        name="i",
        words=[],
        target_entity="t",
        true_positive_entity="tp",
    )
    app.instances = [inst]

    update = tgu.types.UpdateMessageReactions(
        peer=tgu.types.PeerChannel(77),
        msg_id=5,
        reactions=tgu.types.MessageReactions(
            results=[tgu.types.ReactionCount(tgu.types.ReactionEmoji("\U0001f44d"), 1)]
        ),
    )

    async def fake_to_event_chat_id(peer):
        return 77

    async def fake_get_forward_message_text(m, **kwargs):
        return "src"

    monkeypatch.setattr(tgu, "to_event_chat_id", fake_to_event_chat_id)
    monkeypatch.setattr(tgu, "get_forward_message_text", fake_get_forward_message_text)

    await app.handle_reaction(update)
    await app.handle_reaction(update)

    assert msg.forwarded == ["tp"]


@pytest.mark.asyncio
async def test_ignore_words(monkeypatch, dummy_tg_client, dummy_message_cls, tmp_path):
    config = {"log_level": "info"}
    monkeypatch.setattr(app, "load_config", lambda: config)
    monkeypatch.setattr(app, "get_api_credentials", lambda cfg: (1, "h", "s"))

    dummy_client = dummy_tg_client
    monkeypatch.setattr(app, "TelegramClient", lambda s, a, b, proxy=None: dummy_client)

    stats_path = tmp_path / "stats.json"
    monkeypatch.setattr(
        app, "stats", stats_module.StatsTracker(str(stats_path), flush_interval=0)
    )

    async def fake_rescan(inst):
        return None

    monkeypatch.setattr(app, "rescan_loop", fake_rescan)

    async def fake_update(inst, fr):
        inst.chat_ids = {1}

    monkeypatch.setattr(app, "update_instance_chat_ids", fake_update)

    async def fake_load_instances(cfg):
        return [
            app.Instance(name="i", words=["hi"], ignore_words=["bad"], target_chat=99)
        ]

    monkeypatch.setattr(app, "load_instances", fake_load_instances)

    async def fake_get_message_source(m):
        return "URL"

    monkeypatch.setattr(tgu, "get_message_source", fake_get_message_source)

    async def fake_get_chat_name(v, safe=False):
        return "name"

    monkeypatch.setattr(tgu, "get_chat_name", fake_get_chat_name)

    await app.main()

    handler = dummy_client.on_handler
    msg = dummy_message_cls(SimpleNamespace(channel_id=1), msg_id=5, text="bad hi")
    event = SimpleNamespace(message=msg, chat_id=1)
    await handler(event)
    assert msg.forwarded == []
    assert dummy_client.sent == []
    assert app.stats.data["stats"]["total"] == 0


@pytest.mark.asyncio
async def test_negative_words(
    monkeypatch, dummy_tg_client, dummy_message_cls, tmp_path
):
    config = {"log_level": "info"}
    monkeypatch.setattr(app, "load_config", lambda: config)
    monkeypatch.setattr(app, "get_api_credentials", lambda cfg: (1, "h", "s"))

    dummy_client = dummy_tg_client
    monkeypatch.setattr(app, "TelegramClient", lambda s, a, b, proxy=None: dummy_client)

    stats_path = tmp_path / "stats.json"
    monkeypatch.setattr(
        app, "stats", stats_module.StatsTracker(str(stats_path), flush_interval=0)
    )

    async def fake_rescan(inst):
        return None

    monkeypatch.setattr(app, "rescan_loop", fake_rescan)

    async def fake_update(inst, fr):
        inst.chat_ids = {1}

    monkeypatch.setattr(app, "update_instance_chat_ids", fake_update)

    async def fake_load_instances(cfg):
        return [
            app.Instance(name="i", words=["hi"], negative_words=["bad"], target_chat=99)
        ]

    monkeypatch.setattr(app, "load_instances", fake_load_instances)

    async def fake_get_message_source(m):
        return "URL"

    monkeypatch.setattr(tgu, "get_message_source", fake_get_message_source)

    async def fake_get_chat_name(v, safe=False):
        return "name"

    monkeypatch.setattr(tgu, "get_chat_name", fake_get_chat_name)

    await app.main()

    handler = dummy_client.on_handler
    msg = dummy_message_cls(SimpleNamespace(channel_id=1), msg_id=5, text="bad hi")
    event = SimpleNamespace(message=msg, chat_id=1)
    await handler(event)
    assert msg.forwarded == []
    assert dummy_client.sent == []
    assert app.stats.data["stats"]["total"] == 0
