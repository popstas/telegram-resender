import asyncio
import logging
import time
from typing import Any, Callable, Hashable, Optional

logger = logging.getLogger(__name__)


def _default_scheduler(delay: float, callback: Callable[[], Any]) -> Any:
    """Schedule ``callback`` to run after ``delay`` seconds on the running loop.

    Returns the asyncio ``TimerHandle`` so it can be cancelled when the timer is
    rescheduled. Requires a running event loop (the production code path).
    """

    loop = asyncio.get_running_loop()
    return loop.call_later(delay, callback)


class _BatchState:
    """Per-key rolling buffer + active-batch bookkeeping."""

    def __init__(self) -> None:
        self.buffer: list[tuple[float, Any]] = []  # (epoch, message)
        self.active = False
        self.header_ctx: Any = None
        self.handle: Any = None  # scheduler handle for the pending flush


class DebounceManager:
    """Batch per-chat messages and flush after a period of silence.

    Keyed by ``(instance_name, chat_id)``. Each key keeps a rolling buffer of
    recent ``(epoch, message)`` pairs. Before a batch becomes active the buffer
    is trimmed to the ``debounce_ms`` window so only relevant pre-trigger context
    is retained. A trigger activates the batch (seeding it with the trimmed
    buffer) and captures the header context; later triggers keep the first header
    (first-header-wins). Every new message (any sender) reschedules the flush
    timer, so the batch is delivered only after ``debounce_ms`` of silence.

    ``clock`` (callable → float seconds) and ``scheduler`` (``(delay, callback)``
    → cancellable handle) are injectable so the timing logic is testable without
    real wall-clock waits.
    """

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        scheduler: Optional[Callable[[float, Callable[[], Any]], Any]] = None,
    ) -> None:
        self.clock = clock
        self.scheduler = scheduler or _default_scheduler
        self._states: dict[Hashable, _BatchState] = {}

    def add_message(
        self,
        key: Hashable,
        message: Any,
        now: Optional[float] = None,
        *,
        debounce_ms: int,
        is_trigger: bool,
        header_ctx: Any,
        flush_cb: Callable[[list, Any], Any],
    ) -> None:
        """Append ``message`` to the buffer for ``key`` and manage the batch.

        - Appends ``(now, message)`` to the rolling buffer.
        - While no batch is active, trims the buffer to the ``debounce_ms`` window.
        - A trigger with no active batch activates it (buffer becomes the seed)
          and stores ``header_ctx``; a trigger with an active batch is ignored for
          the header (first-header-wins).
        - While a batch is active, (re)schedules the flush timer to fire after
          ``debounce_ms`` of silence.
        """

        if now is None:
            now = self.clock()
        state = self._states.setdefault(key, _BatchState())
        state.buffer.append((now, message))

        window = debounce_ms / 1000.0
        if not state.active:
            cutoff = now - window
            state.buffer = [(ts, m) for ts, m in state.buffer if ts >= cutoff]

        if is_trigger and not state.active:
            state.active = True
            state.header_ctx = header_ctx

        if state.active:
            if state.handle is not None:
                state.handle.cancel()
            state.handle = self.scheduler(window, lambda: self._on_timer(key, flush_cb))

    def _on_timer(self, key: Hashable, flush_cb: Callable[[list, Any], Any]) -> None:
        """Timer callback: flush the batch, scheduling the coroutine if needed."""

        result = self.flush(key, flush_cb)
        if asyncio.iscoroutine(result):
            task = asyncio.ensure_future(result)
            task.add_done_callback(self._log_flush_exception)

    @staticmethod
    def _log_flush_exception(task: "asyncio.Future") -> None:
        """Surface exceptions from a fire-and-forget debounce flush.

        The flush coroutine runs detached from any awaiter, so without this an
        exception (e.g. a webhook send failing outside the forward try/except)
        would only show up as asyncio's GC-time "exception was never retrieved"
        warning, with no app-level log.
        """

        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("Debounce flush failed: %s", exc)

    def cancel(self, key: Hashable) -> None:
        """Drop any pending batch for ``key`` without flushing it.

        Cancels the scheduled flush timer (if any) and removes the per-key
        state, so the accumulated batch is discarded. A no-op when ``key`` has
        no state (e.g. nothing buffered, or already flushed).
        """

        state = self._states.pop(key, None)
        if state is None:
            return
        if state.handle is not None:
            state.handle.cancel()
        logger.debug("Cancelled debounce batch for %s", key)

    def flush(self, key: Hashable, flush_cb: Callable[[list, Any], Any]) -> Any:
        """Deliver the ordered batch for ``key`` via ``flush_cb`` and clear it.

        Returns whatever ``flush_cb`` returns (a coroutine for the async path) so
        callers/timers can await or schedule it. Does nothing when there is no
        active batch for ``key``.
        """

        state = self._states.get(key)
        if state is None or not state.active:
            return None
        batch = [m for _, m in state.buffer]
        header_ctx = state.header_ctx
        del self._states[key]
        logger.debug("Flushing debounce batch of %d messages for %s", len(batch), key)
        return flush_cb(batch, header_ctx)
