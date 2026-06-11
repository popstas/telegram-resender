import asyncio

import pytest

from src.debounce import DebounceManager


class FakeHandle:
    def __init__(self):
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class FakeScheduler:
    """Records scheduled callbacks; fires them only when asked."""

    def __init__(self):
        self.calls = []  # list of (delay, callback, handle)

    def __call__(self, delay, callback):
        handle = FakeHandle()
        self.calls.append((delay, callback, handle))
        return handle

    @property
    def active_calls(self):
        return [c for c in self.calls if not c[2].cancelled]

    def fire_last(self):
        delay, callback, handle = self.calls[-1]
        callback()


def _make_manager():
    scheduler = FakeScheduler()
    mgr = DebounceManager(clock=lambda: 0.0, scheduler=scheduler)
    return mgr, scheduler


KEY = ("inst", 1)


# --- sync bookkeeping tests ---------------------------------------------------


def test_pretrigger_within_window_included_older_trimmed():
    mgr, scheduler = _make_manager()
    flushed = []

    def cb(batch, ctx):
        flushed.append((batch, ctx))

    # debounce_ms=1000 → 1.0s window. now is in seconds.
    mgr.add_message(
        KEY,
        "old",
        0.0,
        debounce_ms=1000,
        is_trigger=False,
        header_ctx=None,
        flush_cb=cb,
    )
    mgr.add_message(
        KEY,
        "recent",
        0.5,
        debounce_ms=1000,
        is_trigger=False,
        header_ctx=None,
        flush_cb=cb,
    )
    # trigger at t=1.2 → cutoff 0.2, "old" (t=0) trimmed, "recent" (t=0.5) kept
    mgr.add_message(
        KEY,
        "trigger",
        1.2,
        debounce_ms=1000,
        is_trigger=True,
        header_ctx={"h": 1},
        flush_cb=cb,
    )

    scheduler.fire_last()
    assert flushed == [(["recent", "trigger"], {"h": 1})]


def test_nontrigger_messages_dont_start_batch():
    mgr, scheduler = _make_manager()
    flushed = []
    mgr.add_message(
        KEY,
        "a",
        0.0,
        debounce_ms=1000,
        is_trigger=False,
        header_ctx=None,
        flush_cb=lambda b, c: flushed.append(b),
    )
    mgr.add_message(
        KEY,
        "b",
        0.1,
        debounce_ms=1000,
        is_trigger=False,
        header_ctx=None,
        flush_cb=lambda b, c: flushed.append(b),
    )
    # No batch active → no timer scheduled.
    assert scheduler.calls == []
    # flush() on an inactive key is a no-op.
    assert mgr.flush(KEY, lambda b, c: flushed.append(b)) is None
    assert flushed == []


def test_second_trigger_keeps_first_header():
    mgr, scheduler = _make_manager()
    flushed = []
    mgr.add_message(
        KEY,
        "t1",
        0.0,
        debounce_ms=1000,
        is_trigger=True,
        header_ctx="first",
        flush_cb=lambda b, c: flushed.append((b, c)),
    )
    mgr.add_message(
        KEY,
        "t2",
        0.3,
        debounce_ms=1000,
        is_trigger=True,
        header_ctx="second",
        flush_cb=lambda b, c: flushed.append((b, c)),
    )
    scheduler.fire_last()
    assert flushed == [(["t1", "t2"], "first")]


def test_rolling_message_reschedules_fire_time():
    mgr, scheduler = _make_manager()
    mgr.add_message(
        KEY,
        "t1",
        0.0,
        debounce_ms=1000,
        is_trigger=True,
        header_ctx=None,
        flush_cb=lambda b, c: None,
    )
    first_handle = scheduler.calls[-1][2]
    # A new (non-trigger) message while active reschedules: old handle cancelled,
    # a fresh timer scheduled.
    mgr.add_message(
        KEY,
        "m2",
        0.5,
        debounce_ms=1000,
        is_trigger=False,
        header_ctx=None,
        flush_cb=lambda b, c: None,
    )
    assert first_handle.cancelled is True
    assert len(scheduler.active_calls) == 1


def test_flush_clears_batch_for_key():
    mgr, scheduler = _make_manager()
    flushed = []
    mgr.add_message(
        KEY,
        "t1",
        0.0,
        debounce_ms=1000,
        is_trigger=True,
        header_ctx=None,
        flush_cb=lambda b, c: flushed.append(b),
    )
    scheduler.fire_last()
    # State cleared; firing again does nothing and a new message starts fresh.
    scheduler.fire_last()
    assert flushed == [["t1"]]


def test_keys_isolated():
    mgr, scheduler = _make_manager()
    flushed = {}

    def cb_for(name):
        return lambda b, c: flushed.__setitem__(name, b)

    mgr.add_message(
        ("inst", 1),
        "a1",
        0.0,
        debounce_ms=1000,
        is_trigger=True,
        header_ctx=None,
        flush_cb=cb_for("k1"),
    )
    mgr.add_message(
        ("inst", 2),
        "b1",
        0.0,
        debounce_ms=1000,
        is_trigger=True,
        header_ctx=None,
        flush_cb=cb_for("k2"),
    )
    # Fire both scheduled timers.
    scheduler.calls[0][1]()
    scheduler.calls[1][1]()
    assert flushed == {"k1": ["a1"], "k2": ["b1"]}


def test_clock_used_when_now_omitted():
    scheduler = FakeScheduler()
    ticks = iter([10.0, 10.4, 11.5])
    mgr = DebounceManager(clock=lambda: next(ticks), scheduler=scheduler)
    flushed = []
    mgr.add_message(
        KEY,
        "old",
        debounce_ms=1000,
        is_trigger=False,
        header_ctx=None,
        flush_cb=lambda b, c: flushed.append(b),
    )
    mgr.add_message(
        KEY,
        "recent",
        debounce_ms=1000,
        is_trigger=False,
        header_ctx=None,
        flush_cb=lambda b, c: flushed.append(b),
    )
    # trigger at clock=11.5, cutoff=10.5 → "old"(10.0) trimmed, "recent"(10.4) trimmed too
    mgr.add_message(
        KEY,
        "trigger",
        debounce_ms=1000,
        is_trigger=True,
        header_ctx=None,
        flush_cb=lambda b, c: flushed.append(b),
    )
    scheduler.fire_last()
    assert flushed == [["trigger"]]


# --- async tests (tiny real debounce) ----------------------------------------


@pytest.mark.asyncio
async def test_async_timer_flushes_once_in_order():
    mgr = DebounceManager()  # real monotonic clock + asyncio scheduler
    flushed = []

    async def cb(batch, ctx):
        flushed.append((list(batch), ctx))

    mgr.add_message(
        KEY, "ctx", debounce_ms=20, is_trigger=False, header_ctx=None, flush_cb=cb
    )
    mgr.add_message(
        KEY,
        "trigger",
        debounce_ms=20,
        is_trigger=True,
        header_ctx={"h": 1},
        flush_cb=cb,
    )
    await asyncio.sleep(0.05)
    assert flushed == [(["ctx", "trigger"], {"h": 1})]


@pytest.mark.asyncio
async def test_async_flush_exception_is_logged(caplog):
    mgr = DebounceManager()

    async def cb(batch, ctx):
        raise RuntimeError("boom")

    mgr.add_message(
        KEY, "trigger", debounce_ms=20, is_trigger=True, header_ctx=None, flush_cb=cb
    )
    with caplog.at_level("ERROR"):
        await asyncio.sleep(0.05)
    assert any("Debounce flush failed" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_async_new_message_delays_flush():
    mgr = DebounceManager()
    flushed = []

    async def cb(batch, ctx):
        flushed.append(list(batch))

    mgr.add_message(
        KEY, "trigger", debounce_ms=40, is_trigger=True, header_ctx=None, flush_cb=cb
    )
    await asyncio.sleep(0.02)  # before expiry
    assert flushed == []  # not flushed yet
    mgr.add_message(
        KEY, "later", debounce_ms=40, is_trigger=False, header_ctx=None, flush_cb=cb
    )
    await asyncio.sleep(0.02)  # would have fired if not rescheduled
    assert flushed == []
    await asyncio.sleep(0.04)  # now silence has elapsed
    assert flushed == [["trigger", "later"]]
