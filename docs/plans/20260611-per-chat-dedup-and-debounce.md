# Per-chat first-match dedup + debounce_ms batching

## Overview
Add two independent, per-instance forwarding features to the message pipeline
(`src/app.py` `process_message`). Both default to off so existing instances are
unaffected.

- **Feature A — first match per chat, per day (`once_per_chat`)**: when enabled,
  an instance forwards only the *first* words/prompt match per chat, then
  suppresses later matches in that chat until a daily reset hour
  (`reset_hour`, default `6`, local server time). State is persisted to
  `data/seen_chats.json` so restarts don't re-forward. Useful with
  folder-backed instances: each chat in the folder triggers once per day.
- **Feature B — `debounce_ms` batching**: when `debounce_ms > 0`, a trigger does
  not forward immediately. Instead the instance keeps a rolling per-chat buffer
  of recent messages; every new message (any sender) resets the timer, and after
  `debounce_ms` of silence the whole batch is forwarded with a single match
  header — capturing conversation context before and after the trigger. In-memory
  only (pending batches lost on restart).

## Context (from discovery)
- Files/components involved:
  - `src/app.py` — `process_message` (trigger eval + forward), `handler` in `main`.
  - `src/config.py` — `Instance` dataclass + `load_instances` parsing.
  - `src/trace_ids.py` — `TraceStore` is the persistence pattern to mirror for Feature A.
  - `src/telegram_utils.py` — `get_forward_message_text`, `get_message_url`.
  - `src/stats.py`, `src/webhook.py`, `src/trace_ids.py` — side effects fired on forward.
  - `config-example.yml`, `README.md`, `AGENTS.md` — docs to update.
  - `tests/` — pytest + pytest-asyncio suite.
- Related patterns found:
  - `TraceStore` (dirty-flag, interval flush, `atexit.register`) → reuse shape for `SeenChatStore`.
  - Forward side effects in `process_message` (header send → `forward_to` → trace_id → webhook → stats).
- Dependencies identified: telethon, pytest, pytest-asyncio. No new deps.
- Note: CLAUDE.md mentions a `config.ts generateConfig`; this is a Python project
  with no such file — `config-example.yml` is the config reference to update.

## Development Approach
- **Testing approach**: Regular (implementation + tests in the same task), matching
  the existing pytest suite style. Tests remain mandatory for every task.
- Complete each task fully before moving to the next.
- Make small, focused changes; maintain backward compatibility (both features off by default).
- **CRITICAL: every task MUST include new/updated tests** for code changed in that task
  (success + error/edge cases, as separate checklist items).
- **CRITICAL: all tests must pass before starting the next task.**
- **CRITICAL: update this plan file when scope changes during implementation.**
- Run `pytest` after each change; run `pre-commit --all-files` before commit (black + isort).

## Testing Strategy
- **Unit tests**: required every task. Use `pytest`/`pytest-asyncio`. Mock telethon
  `Message`/`client` as existing tests do.
- **E2E**: this project has no UI e2e harness. End-to-end verification is manual,
  via a real `data/config.yml` against Telegram (see Post-Completion). Deterministic
  behavior is covered by unit tests; the debounce module takes an injectable
  clock/scheduler so timing is testable without real wall-clock waits.

## Progress Tracking
- Mark completed items `[x]` immediately when done.
- Add newly discovered tasks with ➕ prefix; blockers with ⚠️ prefix.
- Keep this plan in sync with actual work.

## What Goes Where
- Implementation Steps (`[ ]`): code, tests, doc files inside this repo.
- Post-Completion (no checkboxes): manual Telegram verification, memory/runtime considerations.

## Implementation Steps

### Task 1: Add `SeenChatStore` persistence (Feature A state)
- [x] create `src/seen_chats.py` mirroring `TraceStore`: dirty-flag + interval flush
      + `atexit.register`, stored at `data/seen_chats.json`, format
      `{instance_name: {chat_id: last_forward_epoch}}`.
- [x] implement `_reset_boundary(now, reset_hour)` helper: most recent local
      occurrence of `reset_hour:00` (today if `now.hour >= reset_hour`, else yesterday).
- [x] implement `should_forward(instance, chat_id, now, reset_hour) -> bool`
      (True when no record or stored epoch < reset boundary) and
      `record(instance, chat_id, now)`.
- [x] write tests: boundary before vs after `reset_hour`; first call True then False
      same day; True again after crossing next reset boundary; multiple
      instances/chats isolated.
- [x] write tests: persistence reload from disk; corrupt/missing file → empty store.
- [x] run `pytest` — must pass before next task.

### Task 2: Add config fields `once_per_chat`, `reset_hour`, `debounce_ms`
- [x] add `once_per_chat: bool = False`, `reset_hour: int = 6`,
      `debounce_ms: int = 0` to `Instance` in `src/config.py`.
- [x] parse them in `load_instances` with validation: `0 <= reset_hour <= 23`,
      `debounce_ms >= 0` (raise `ValueError` otherwise).
- [x] document the three fields in `config-example.yml` with comments + the
      debounce example timeline.
- [x] write tests: defaults when omitted; values parsed; `ValueError` on out-of-range
      `reset_hour` and negative `debounce_ms`.
- [x] run `pytest` — must pass before next task.

### Task 3: Extract reusable forward helper + integrate `once_per_chat`
- [ ] refactor the forward side-effect block in `process_message`
      (`src/app.py`) into `async def _forward_messages(inst, messages, *, trigger_message, used_word, used_prompt, used_score, used_quote, used_reasoning, used_trace_id)`:
      send the one header (from trigger), `forward_to` each message in chronological
      order to every destination, set `trace_id` on the trigger's forwarded copy,
      fire webhook for the trigger. Honor `no_forward_message`.
- [ ] make `process_message`'s immediate path call `_forward_messages([message], ...)`
      so behavior is unchanged when both features are off.
- [ ] after trigger eval, when `inst.once_per_chat` is on: suppress forward if
      `seen_chats.should_forward(...)` is False; otherwise `record(...)` and forward.
      Skip suppression gracefully if `chat_id` is unavailable.
- [ ] write tests: `once_per_chat` forwards first match, suppresses second in same
      chat; different chats independent; forwards again after reset boundary;
      `once_per_chat=False` unchanged.
- [ ] write tests: `_forward_messages` sends single header + forwards all given
      messages in order; sets trace_id on trigger copy; respects `no_forward_message`.
- [ ] run `pytest` — must pass before next task.

### Task 4: Add `DebounceManager` (Feature B core)
- [ ] create `src/debounce.py` with `DebounceManager` keyed by `(instance_name, chat_id)`:
      rolling buffer of `(epoch, message)`, active-batch flag, captured header context,
      and an asyncio flush timer. Accept injectable `clock` (callable → float) and a
      scheduler so timing is testable.
- [ ] implement `add_message(key, message, now, is_trigger, header_ctx, flush_cb)`:
      append; when no active batch, trim buffer to `now - debounce_ms` (pre-trigger
      context); on trigger with no active batch, activate batch (seed = current buffer)
      and capture header; on trigger with active batch, keep first header (first-header-wins);
      always (re)schedule the flush timer to fire after `debounce_ms` of silence.
- [ ] implement flush: invoke `flush_cb` with the ordered batch + header context, then
      clear the batch for that key.
- [ ] write tests (sync bookkeeping): pre-trigger messages within window are included,
      older ones trimmed; non-trigger messages don't start a batch; second trigger keeps
      first header; rolling new message extends/reset the scheduled fire time.
- [ ] write tests (async, tiny `debounce_ms`): timer flush calls `flush_cb` once with all
      batched messages in chronological order; new message before expiry delays flush.
- [ ] run `pytest` — must pass before next task.

### Task 5: Wire `DebounceManager` into the pipeline
- [ ] in `src/app.py`, instantiate one `DebounceManager` and, when `inst.debounce_ms > 0`,
      route messages reaching the post-ignore stage of `process_message` through
      `add_message` (buffer every message; mark `is_trigger` = effective forward after
      `once_per_chat`). `flush_cb` calls `_forward_messages(batch, ...)`.
- [ ] defer trigger side effects (`forwarded=True` stats, webhook, trace) to flush for
      the debounced path; keep `forwarded=False` stats immediate for non-trigger messages;
      leave the `debounce_ms == 0` path exactly as today.
- [ ] write tests: `debounce_ms > 0` buffers and flushes via `_forward_messages` (mocked)
      with context + trigger; `debounce_ms == 0` keeps the immediate single-message path;
      `once_per_chat` + debounce together (suppressed trigger doesn't start a batch but
      message still buffers).
- [ ] run `pytest` — must pass before next task.

### Task 6: Verify acceptance criteria
- [ ] verify Overview requirements: per-chat first-match dedup with daily reset; debounce
      batching with rolling extend, pre-trigger context, single header, first-header-wins.
- [ ] verify edge cases: missing sender/chat_id; both features enabled together; features
      off = unchanged behavior.
- [ ] run full `pytest` suite — all pass.
- [ ] run `pre-commit --all-files` — black + isort clean.
- [ ] verify coverage meets project standard (~80%+) for new modules.

### Task 7: [Final] Update documentation
- [ ] update `README.md`: document `once_per_chat`, `reset_hour`, `debounce_ms`
      with the debounce example timeline.
- [ ] update `AGENTS.md` repository tree with `src/seen_chats.py` and `src/debounce.py`.

## Technical Details
- **`SeenChatStore`** (`data/seen_chats.json`): `{instance: {chat_id(str): epoch(float)}}`.
  Reset boundary = most recent local `reset_hour:00`. `should_forward` True iff no record
  or `record_epoch < boundary`.
- **`Instance` new fields**: `once_per_chat: bool=False`, `reset_hour: int=6`,
  `debounce_ms: int=0`.
- **`_forward_messages`**: single header (from trigger) → forward each message in order →
  trace_id on trigger copy → webhook for trigger. Used by both immediate (`[message]`)
  and debounce-flush (full batch) paths.
- **`DebounceManager`**: per-`(instance, chat)` rolling buffer trimmed to `debounce_ms`
  before a batch starts; once active, accumulates all chat messages and resets the
  flush timer on each; flush after `debounce_ms` silence. Injectable clock/scheduler for tests.
- **Stats**: trigger forward counts increment at delivery (immediate, or at flush for
  debounce); non-trigger messages keep `forwarded=False` increment.

## Post-Completion
*Manual / external — no checkboxes.*

**Manual verification:**
- With a real `data/config.yml`: set `once_per_chat: true` + `reset_hour` on a
  folder-backed instance; confirm only the first match per chat forwards and that it
  re-arms after the reset hour.
- Set `debounce_ms: 60000`; reproduce the TODO timeline (message, keyword trigger,
  later message) and confirm all messages forward together ~`debounce_ms` after the
  last one, with a single header.

**Runtime considerations:**
- Debounce buffers live telethon `Message` objects per active/recent chat in memory;
  bounded by chat activity within `debounce_ms`. Pending batches are lost on restart
  (acceptable, documented).
