import asyncio
from types import SimpleNamespace

import pytest

import src.app as app
import src.stats as stats_module
import src.telegram_utils as tgu
from src.debounce import DebounceManager


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


async def _setup_handler(
    monkeypatch, dummy_tg_client, tmp_path, *, config, instances, mgr
):
    """Bring up ``app.main`` with stubs and return the registered handler.

    Injects ``mgr`` as the module-level debounce manager so both the handler's
    cancel path and ``process_message`` share the same (test-controllable)
    instance.
    """

    monkeypatch.setattr(app, "load_config", lambda: config)
    monkeypatch.setattr(app, "get_api_credentials", lambda cfg: (1, "h", "s"))

    dummy_client = dummy_tg_client
    monkeypatch.setattr(app, "TelegramClient", lambda s, a, b, proxy=None: dummy_client)

    monkeypatch.setattr(
        app,
        "stats",
        stats_module.StatsTracker(str(tmp_path / "stats.json"), flush_interval=0),
    )
    monkeypatch.setattr(app, "debounce_manager", mgr)

    async def fake_rescan(inst):
        return None

    monkeypatch.setattr(app, "rescan_loop", fake_rescan)

    chat_ids = {inst.name: set(inst.chat_ids) or {1} for inst in instances}

    async def fake_update(inst, fr):
        inst.chat_ids = chat_ids[inst.name]

    monkeypatch.setattr(app, "update_instance_chat_ids", fake_update)

    async def fake_load_instances(cfg):
        return instances

    monkeypatch.setattr(app, "load_instances", fake_load_instances)

    async def fake_get_message_source(m):
        return "URL"

    monkeypatch.setattr(tgu, "get_message_source", fake_get_message_source)

    async def fake_get_chat_name(v, safe=False):
        return "name"

    monkeypatch.setattr(tgu, "get_chat_name", fake_get_chat_name)
    monkeypatch.setattr(app, "get_chat_name", fake_get_chat_name)

    await app.main()
    return dummy_client.on_handler


@pytest.mark.asyncio
async def test_cancel_on_owner_reply_drops_batch(
    monkeypatch, dummy_tg_client, dummy_message_cls, tmp_path
):
    """An owner (ignored) reply during the debounce window drops the batch."""
    forward_calls = []

    async def fake_forward(inst, messages, **kwargs):
        forward_calls.append((messages, kwargs))

    monkeypatch.setattr(app, "_forward_messages", fake_forward)

    scheduler = _FakeScheduler()
    mgr = DebounceManager(clock=lambda: 0.0, scheduler=scheduler)

    inst = app.Instance(name="i", words=["hi"], target_chat=99, debounce_ms=1000)
    handler = await _setup_handler(
        monkeypatch,
        dummy_tg_client,
        tmp_path,
        config={"log_level": "info", "ignore_usernames": ["owner"]},
        instances=[inst],
        mgr=mgr,
    )

    # Trigger from a normal user activates the batch and schedules a flush.
    trigger = dummy_message_cls(SimpleNamespace(channel_id=1), msg_id=1, text="hi")
    trigger.sender = SimpleNamespace(username="user", id=11)
    await handler(SimpleNamespace(message=trigger, chat_id=1))
    assert len(scheduler.calls) == 1
    assert ("i", 1) in mgr._states
    handle = scheduler.calls[-1][2]

    # Owner replies in the same chat during the window: batch is cancelled.
    owner_msg = dummy_message_cls(SimpleNamespace(channel_id=1), msg_id=2, text="ok")
    owner_msg.sender = SimpleNamespace(username="owner", id=22)
    await handler(SimpleNamespace(message=owner_msg, chat_id=1))

    assert ("i", 1) not in mgr._states
    assert handle.cancelled is True

    # Firing the (now cancelled) timer forwards nothing.
    scheduler.fire_last()
    await asyncio.sleep(0.01)
    assert forward_calls == []


@pytest.mark.asyncio
async def test_cancel_on_owner_reply_disabled_keeps_batch(
    monkeypatch, dummy_tg_client, dummy_message_cls, tmp_path
):
    """With cancel_on_owner_reply=False, an owner reply leaves the batch intact."""

    async def fake_forward(inst, messages, **kwargs):
        return None

    monkeypatch.setattr(app, "_forward_messages", fake_forward)

    scheduler = _FakeScheduler()
    mgr = DebounceManager(clock=lambda: 0.0, scheduler=scheduler)

    inst = app.Instance(
        name="i",
        words=["hi"],
        target_chat=99,
        debounce_ms=1000,
        cancel_on_owner_reply=False,
    )
    handler = await _setup_handler(
        monkeypatch,
        dummy_tg_client,
        tmp_path,
        config={"log_level": "info", "ignore_usernames": ["owner"]},
        instances=[inst],
        mgr=mgr,
    )

    trigger = dummy_message_cls(SimpleNamespace(channel_id=1), msg_id=1, text="hi")
    trigger.sender = SimpleNamespace(username="user", id=11)
    await handler(SimpleNamespace(message=trigger, chat_id=1))
    assert ("i", 1) in mgr._states

    owner_msg = dummy_message_cls(SimpleNamespace(channel_id=1), msg_id=2, text="ok")
    owner_msg.sender = SimpleNamespace(username="owner", id=22)
    await handler(SimpleNamespace(message=owner_msg, chat_id=1))

    # Batch survives the owner reply.
    assert ("i", 1) in mgr._states


@pytest.mark.asyncio
async def test_non_ignored_user_does_not_cancel(
    monkeypatch, dummy_tg_client, dummy_message_cls, tmp_path
):
    """A non-ignored sender does not cancel the batch (it gets buffered)."""

    async def fake_forward(inst, messages, **kwargs):
        return None

    monkeypatch.setattr(app, "_forward_messages", fake_forward)

    scheduler = _FakeScheduler()
    mgr = DebounceManager(clock=lambda: 0.0, scheduler=scheduler)

    inst = app.Instance(name="i", words=["hi"], target_chat=99, debounce_ms=1000)
    handler = await _setup_handler(
        monkeypatch,
        dummy_tg_client,
        tmp_path,
        config={"log_level": "info", "ignore_usernames": ["owner"]},
        instances=[inst],
        mgr=mgr,
    )

    trigger = dummy_message_cls(SimpleNamespace(channel_id=1), msg_id=1, text="hi")
    trigger.sender = SimpleNamespace(username="user", id=11)
    await handler(SimpleNamespace(message=trigger, chat_id=1))
    assert ("i", 1) in mgr._states

    other = dummy_message_cls(SimpleNamespace(channel_id=1), msg_id=2, text="more")
    other.sender = SimpleNamespace(username="someone", id=33)
    await handler(SimpleNamespace(message=other, chat_id=1))

    assert ("i", 1) in mgr._states


@pytest.mark.asyncio
async def test_owner_reply_no_cancel_when_debounce_zero(
    monkeypatch, dummy_tg_client, dummy_message_cls, tmp_path
):
    """With debounce_ms=0 there is no batch; cancel is never invoked."""

    async def fake_forward(inst, messages, **kwargs):
        return None

    monkeypatch.setattr(app, "_forward_messages", fake_forward)

    scheduler = _FakeScheduler()
    mgr = DebounceManager(clock=lambda: 0.0, scheduler=scheduler)
    cancel_calls = []
    monkeypatch.setattr(mgr, "cancel", lambda key: cancel_calls.append(key))

    inst = app.Instance(name="i", words=["hi"], target_chat=99, debounce_ms=0)
    handler = await _setup_handler(
        monkeypatch,
        dummy_tg_client,
        tmp_path,
        config={"log_level": "info", "ignore_usernames": ["owner"]},
        instances=[inst],
        mgr=mgr,
    )

    owner_msg = dummy_message_cls(SimpleNamespace(channel_id=1), msg_id=1, text="ok")
    owner_msg.sender = SimpleNamespace(username="owner", id=22)
    await handler(SimpleNamespace(message=owner_msg, chat_id=1))

    assert cancel_calls == []
