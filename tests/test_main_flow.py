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
async def test_forward_messages_single_header_batch(
    monkeypatch, dummy_message_cls, tmp_path
):
    """_forward_messages sends one header then forwards all messages in order."""
    sent = []

    class DummyClient:
        async def send_message(self, *a, **k):
            sent.append((a, k))

    app.client = DummyClient()
    tgu.client = app.client

    inst = app.Instance(name="b", words=["hi"], target_chat=1, target_entity="ent")

    forwarded_log = []

    async def fake_text(message, **kwargs):
        return "HEADER"

    async def fake_get_chat_name(v, safe=False):
        return "name"

    monkeypatch.setattr(app, "get_forward_message_text", fake_text)
    monkeypatch.setattr(app, "get_chat_name", fake_get_chat_name)

    m1 = dummy_message_cls(SimpleNamespace(channel_id=1), msg_id=1, text="a")
    m2 = dummy_message_cls(SimpleNamespace(channel_id=1), msg_id=2, text="hi")
    m3 = dummy_message_cls(SimpleNamespace(channel_id=1), msg_id=3, text="c")

    await app._forward_messages(
        inst,
        [m1, m2, m3],
        trigger_message=m2,
        used_word="hi",
        used_prompt=None,
        used_score=0,
        used_quote=None,
        used_reasoning=None,
        used_trace_id=None,
    )

    # Two destinations -> header sent twice.
    assert [s[0][0] for s in sent] == [1, "ent"]
    # Each message forwarded to both destinations in chronological order.
    assert m1.forwarded == [1, "ent"]
    assert m2.forwarded == [1, "ent"]
    assert m3.forwarded == [1, "ent"]


@pytest.mark.asyncio
async def test_forward_messages_no_forward_message(
    monkeypatch, dummy_message_cls, tmp_path
):
    """_forward_messages honors no_forward_message (forwards without header)."""
    sent = []

    class DummyClient:
        async def send_message(self, *a, **k):
            sent.append((a, k))

    app.client = DummyClient()
    tgu.client = app.client

    inst = app.Instance(name="b", words=["hi"], target_chat=1, no_forward_message=True)

    async def fake_get_chat_name(v, safe=False):
        return "name"

    monkeypatch.setattr(app, "get_chat_name", fake_get_chat_name)

    m1 = dummy_message_cls(SimpleNamespace(channel_id=1), msg_id=1, text="hi")

    await app._forward_messages(
        inst,
        [m1],
        trigger_message=m1,
        used_word="hi",
        used_prompt=None,
        used_score=0,
        used_quote=None,
        used_reasoning=None,
        used_trace_id=None,
    )

    assert sent == []
    assert m1.forwarded == [1]


@pytest.mark.asyncio
async def test_forward_messages_sets_trace_id_on_trigger(
    monkeypatch, dummy_message_cls, tmp_path
):
    """trace_id is set only on the trigger's forwarded copy."""
    sent = []

    class DummyClient:
        async def send_message(self, *a, **k):
            sent.append((a, k))

    app.client = DummyClient()
    tgu.client = app.client

    inst = app.Instance(name="b", words=["hi"], target_chat=1)

    trace_calls = []

    def fake_set(chat_id, msg_id, trace_id):
        trace_calls.append((chat_id, msg_id, trace_id))

    monkeypatch.setattr(app.trace_ids, "set", fake_set)

    async def fake_text(message, **kwargs):
        return "HEADER"

    async def fake_get_chat_name(v, safe=False):
        return "name"

    monkeypatch.setattr(app, "get_forward_message_text", fake_text)
    monkeypatch.setattr(app, "get_chat_name", fake_get_chat_name)
    monkeypatch.setattr(app, "get_message_url", lambda f: "url")

    class ForwardingMessage(dummy_message_cls):
        async def forward_to(self, target):
            self.forwarded.append(target)
            return SimpleNamespace(chat_id=500, id=self.id + 100)

    m1 = ForwardingMessage(SimpleNamespace(channel_id=1), msg_id=1, text="ctx")
    m2 = ForwardingMessage(SimpleNamespace(channel_id=1), msg_id=2, text="hi")

    await app._forward_messages(
        inst,
        [m1, m2],
        trigger_message=m2,
        used_word="hi",
        used_prompt=None,
        used_score=0,
        used_quote=None,
        used_reasoning=None,
        used_trace_id="trace-xyz",
    )

    # Only the trigger (m2 -> forwarded id 102) gets the trace_id.
    assert trace_calls == [(500, 102, "trace-xyz")]


@pytest.mark.asyncio
async def test_once_per_chat_first_forwards_then_suppresses(
    monkeypatch, dummy_message_cls, tmp_path
):
    """once_per_chat forwards the first match and suppresses later ones."""
    from datetime import datetime

    import src.seen_chats as seen_chats_module

    class DummyClient:
        async def send_message(self, *a, **k):
            pass

    app.client = DummyClient()
    tgu.client = app.client
    app.stats = stats_module.StatsTracker(
        str(tmp_path / "stats.json"), flush_interval=0
    )

    store = seen_chats_module.SeenChatStore(str(tmp_path / "seen.json"))
    monkeypatch.setattr(app, "seen_chats", store)

    fixed_now = datetime(2026, 6, 11, 12, 0, 0)
    monkeypatch.setattr(app, "datetime", SimpleNamespace(now=lambda: fixed_now))

    async def fake_text(message, **kwargs):
        return "HEADER"

    async def fake_get_chat_name(v, safe=False):
        return "name"

    monkeypatch.setattr(app, "get_forward_message_text", fake_text)
    monkeypatch.setattr(app, "get_chat_name", fake_get_chat_name)

    inst = app.Instance(name="o", words=["hi"], target_chat=1, once_per_chat=True)

    m1 = dummy_message_cls(SimpleNamespace(channel_id=1), msg_id=1, text="hi")
    await app.process_message(inst, SimpleNamespace(message=m1, chat_id=10))
    assert m1.forwarded == [1]

    m2 = dummy_message_cls(SimpleNamespace(channel_id=1), msg_id=2, text="hi")
    await app.process_message(inst, SimpleNamespace(message=m2, chat_id=10))
    assert m2.forwarded == []

    # Different chat is independent.
    m3 = dummy_message_cls(SimpleNamespace(channel_id=1), msg_id=3, text="hi")
    await app.process_message(inst, SimpleNamespace(message=m3, chat_id=20))
    assert m3.forwarded == [1]


@pytest.mark.asyncio
async def test_once_per_chat_rearms_after_reset(
    monkeypatch, dummy_message_cls, tmp_path
):
    """once_per_chat forwards again after the daily reset boundary passes."""
    from datetime import datetime

    import src.seen_chats as seen_chats_module

    class DummyClient:
        async def send_message(self, *a, **k):
            pass

    app.client = DummyClient()
    tgu.client = app.client
    app.stats = stats_module.StatsTracker(
        str(tmp_path / "stats.json"), flush_interval=0
    )

    store = seen_chats_module.SeenChatStore(str(tmp_path / "seen.json"))
    monkeypatch.setattr(app, "seen_chats", store)

    async def fake_text(message, **kwargs):
        return "HEADER"

    async def fake_get_chat_name(v, safe=False):
        return "name"

    monkeypatch.setattr(app, "get_forward_message_text", fake_text)
    monkeypatch.setattr(app, "get_chat_name", fake_get_chat_name)

    inst = app.Instance(
        name="o", words=["hi"], target_chat=1, once_per_chat=True, reset_hour=6
    )

    clock = {"now": datetime(2026, 6, 11, 7, 0, 0)}
    monkeypatch.setattr(app, "datetime", SimpleNamespace(now=lambda: clock["now"]))

    m1 = dummy_message_cls(SimpleNamespace(channel_id=1), msg_id=1, text="hi")
    await app.process_message(inst, SimpleNamespace(message=m1, chat_id=10))
    assert m1.forwarded == [1]

    # Same day, before next reset -> suppressed.
    m2 = dummy_message_cls(SimpleNamespace(channel_id=1), msg_id=2, text="hi")
    await app.process_message(inst, SimpleNamespace(message=m2, chat_id=10))
    assert m2.forwarded == []

    # Next day after reset hour -> forwards again.
    clock["now"] = datetime(2026, 6, 12, 7, 0, 0)
    m3 = dummy_message_cls(SimpleNamespace(channel_id=1), msg_id=3, text="hi")
    await app.process_message(inst, SimpleNamespace(message=m3, chat_id=10))
    assert m3.forwarded == [1]


@pytest.mark.asyncio
async def test_once_per_chat_disabled_unchanged(
    monkeypatch, dummy_message_cls, tmp_path
):
    """once_per_chat=False forwards every match (default behavior)."""

    class DummyClient:
        async def send_message(self, *a, **k):
            pass

    app.client = DummyClient()
    tgu.client = app.client
    app.stats = stats_module.StatsTracker(
        str(tmp_path / "stats.json"), flush_interval=0
    )

    async def fake_text(message, **kwargs):
        return "HEADER"

    async def fake_get_chat_name(v, safe=False):
        return "name"

    monkeypatch.setattr(app, "get_forward_message_text", fake_text)
    monkeypatch.setattr(app, "get_chat_name", fake_get_chat_name)

    inst = app.Instance(name="o", words=["hi"], target_chat=1)

    m1 = dummy_message_cls(SimpleNamespace(channel_id=1), msg_id=1, text="hi")
    await app.process_message(inst, SimpleNamespace(message=m1, chat_id=10))
    m2 = dummy_message_cls(SimpleNamespace(channel_id=1), msg_id=2, text="hi")
    await app.process_message(inst, SimpleNamespace(message=m2, chat_id=10))
    assert m1.forwarded == [1]
    assert m2.forwarded == [1]


class _FakeHandle:
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class _FakeScheduler:
    """Records scheduled callbacks so tests can fire them deterministically."""

    def __init__(self):
        self.calls = []

    def __call__(self, delay, callback):
        handle = _FakeHandle()
        self.calls.append((delay, callback, handle))
        return handle

    def fire_last(self):
        self.calls[-1][1]()


@pytest.mark.asyncio
async def test_debounce_buffers_and_flushes(monkeypatch, dummy_message_cls, tmp_path):
    """debounce_ms > 0 buffers messages and flushes the batch via _forward_messages."""
    from src.debounce import DebounceManager

    class DummyClient:
        async def send_message(self, *a, **k):
            pass

    app.client = DummyClient()
    tgu.client = app.client
    app.stats = stats_module.StatsTracker(
        str(tmp_path / "stats.json"), flush_interval=0
    )

    forward_calls = []

    async def fake_forward(inst, messages, **kwargs):
        forward_calls.append((messages, kwargs))

    monkeypatch.setattr(app, "_forward_messages", fake_forward)

    scheduler = _FakeScheduler()
    mgr = DebounceManager(clock=lambda: 0.0, scheduler=scheduler)
    monkeypatch.setattr(app, "debounce_manager", mgr)

    inst = app.Instance(name="d", words=["hi"], target_chat=1, debounce_ms=1000)

    # Non-trigger context message: buffered, counted immediately as not forwarded.
    m1 = dummy_message_cls(SimpleNamespace(channel_id=1), msg_id=1, text="hello")
    await app.process_message(inst, SimpleNamespace(message=m1, chat_id=10))
    assert forward_calls == []
    assert scheduler.calls == []
    assert app.stats.data["stats"]["total"] == 1
    assert app.stats.data["stats"]["forwarded_total"] == 0

    # Trigger: activates batch, schedules flush, defers the forwarded increment.
    m2 = dummy_message_cls(SimpleNamespace(channel_id=1), msg_id=2, text="hi")
    await app.process_message(inst, SimpleNamespace(message=m2, chat_id=10))
    assert forward_calls == []
    assert len(scheduler.calls) == 1
    assert app.stats.data["stats"]["total"] == 1  # trigger deferred

    scheduler.fire_last()
    await asyncio.sleep(0.01)

    assert len(forward_calls) == 1
    messages, kwargs = forward_calls[0]
    assert messages == [m1, m2]
    assert kwargs["trigger_message"] is m2
    assert kwargs["used_word"] == "hi"
    # Deferred trigger stats fire at flush.
    assert app.stats.data["stats"]["total"] == 2
    assert app.stats.data["stats"]["forwarded_total"] == 1


@pytest.mark.asyncio
async def test_debounce_zero_keeps_immediate_path(
    monkeypatch, dummy_message_cls, tmp_path
):
    """debounce_ms == 0 keeps the immediate single-message forward path."""

    class DummyClient:
        async def send_message(self, *a, **k):
            pass

    app.client = DummyClient()
    tgu.client = app.client
    app.stats = stats_module.StatsTracker(
        str(tmp_path / "stats.json"), flush_interval=0
    )

    scheduler = _FakeScheduler()
    from src.debounce import DebounceManager

    mgr = DebounceManager(clock=lambda: 0.0, scheduler=scheduler)
    monkeypatch.setattr(app, "debounce_manager", mgr)

    async def fake_text(message, **kwargs):
        return "HEADER"

    async def fake_get_chat_name(v, safe=False):
        return "name"

    monkeypatch.setattr(app, "get_forward_message_text", fake_text)
    monkeypatch.setattr(app, "get_chat_name", fake_get_chat_name)

    inst = app.Instance(name="d", words=["hi"], target_chat=1)

    m1 = dummy_message_cls(SimpleNamespace(channel_id=1), msg_id=1, text="hi")
    await app.process_message(inst, SimpleNamespace(message=m1, chat_id=10))

    # Forwarded immediately, debounce manager untouched.
    assert m1.forwarded == [1]
    assert scheduler.calls == []
    assert mgr._states == {}
    assert app.stats.data["stats"]["forwarded_total"] == 1


@pytest.mark.asyncio
async def test_debounce_with_once_per_chat_suppressed_trigger_buffers(
    monkeypatch, dummy_message_cls, tmp_path
):
    """A once_per_chat-suppressed trigger does not start a batch but still buffers."""
    from datetime import datetime

    import src.seen_chats as seen_chats_module
    from src.debounce import DebounceManager

    class DummyClient:
        async def send_message(self, *a, **k):
            pass

    app.client = DummyClient()
    tgu.client = app.client
    app.stats = stats_module.StatsTracker(
        str(tmp_path / "stats.json"), flush_interval=0
    )

    store = seen_chats_module.SeenChatStore(str(tmp_path / "seen.json"))
    monkeypatch.setattr(app, "seen_chats", store)
    monkeypatch.setattr(
        app, "datetime", SimpleNamespace(now=lambda: datetime(2026, 6, 11, 12, 0, 0))
    )

    forward_calls = []

    async def fake_forward(inst, messages, **kwargs):
        forward_calls.append((messages, kwargs))

    monkeypatch.setattr(app, "_forward_messages", fake_forward)

    scheduler = _FakeScheduler()
    mgr = DebounceManager(clock=lambda: 0.0, scheduler=scheduler)
    monkeypatch.setattr(app, "debounce_manager", mgr)

    inst = app.Instance(
        name="d", words=["hi"], target_chat=1, once_per_chat=True, debounce_ms=1000
    )
    key = ("d", 10)

    # First trigger: forwards (records once_per_chat), starts a batch.
    m1 = dummy_message_cls(SimpleNamespace(channel_id=1), msg_id=1, text="hi")
    await app.process_message(inst, SimpleNamespace(message=m1, chat_id=10))
    assert len(scheduler.calls) == 1
    scheduler.fire_last()
    await asyncio.sleep(0.01)
    assert len(forward_calls) == 1

    # Second trigger is suppressed by once_per_chat -> no new batch, but it buffers.
    m2 = dummy_message_cls(SimpleNamespace(channel_id=1), msg_id=2, text="hi")
    await app.process_message(inst, SimpleNamespace(message=m2, chat_id=10))
    assert len(forward_calls) == 1  # no new flush
    assert key in mgr._states
    assert [m for _, m in mgr._states[key].buffer] == [m2]
    assert mgr._states[key].active is False
    # Suppressed message counted immediately as not forwarded.
    assert app.stats.data["stats"]["total"] == 2
    assert app.stats.data["stats"]["forwarded_total"] == 1


@pytest.mark.asyncio
async def test_missing_chat_id_skips_dedup_and_debounce(
    monkeypatch, dummy_message_cls, tmp_path
):
    """An event without chat_id forwards immediately, bypassing both features."""
    from datetime import datetime

    import src.seen_chats as seen_chats_module
    from src.debounce import DebounceManager

    class DummyClient:
        async def send_message(self, *a, **k):
            pass

    app.client = DummyClient()
    tgu.client = app.client
    app.stats = stats_module.StatsTracker(
        str(tmp_path / "stats.json"), flush_interval=0
    )

    store = seen_chats_module.SeenChatStore(str(tmp_path / "seen.json"))
    monkeypatch.setattr(app, "seen_chats", store)
    monkeypatch.setattr(
        app, "datetime", SimpleNamespace(now=lambda: datetime(2026, 6, 11, 12, 0, 0))
    )

    async def fake_text(message, **kwargs):
        return "HEADER"

    async def fake_get_chat_name(v, safe=False):
        return "name"

    monkeypatch.setattr(app, "get_forward_message_text", fake_text)
    monkeypatch.setattr(app, "get_chat_name", fake_get_chat_name)

    scheduler = _FakeScheduler()
    mgr = DebounceManager(clock=lambda: 0.0, scheduler=scheduler)
    monkeypatch.setattr(app, "debounce_manager", mgr)

    # Both features enabled, but the event has no resolvable chat_id.
    inst = app.Instance(
        name="d", words=["hi"], target_chat=1, once_per_chat=True, debounce_ms=1000
    )

    m1 = dummy_message_cls(SimpleNamespace(channel_id=1), msg_id=1, text="hi")
    await app.process_message(inst, SimpleNamespace(message=m1, chat_id=None))

    # Forwarded immediately, nothing buffered, nothing recorded.
    assert m1.forwarded == [1]
    assert scheduler.calls == []
    assert mgr._states == {}
    assert store.data == {}


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
