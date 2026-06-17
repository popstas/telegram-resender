# Configurable Forwarded Message + Debounce Cancel-on-Owner-Reply

## Overview

Two per-instance features for the Telegram resender:

- **Feature A — Configurable forwarded message.** Today the preface sent before a
  forwarded message is a fixed `{trigger}\n\n{source}` layout built in
  `telegram_utils.get_forward_message_text`. Make it configurable per instance via
  (1) an optional full `message_template` string with placeholders, and (2) a
  `forward_message` block of flags (`show_trigger`, `show_source`) plus free-text
  `prefix`/`suffix` used when no template is set. The existing `no_forward_message`
  flag continues to suppress the preface entirely and takes precedence. **With no new
  config, output is byte-identical to today.**

- **Feature B — Cancel debounce batch on owner reply.** When `debounce_ms > 0` and an
  ignored user (the account owner, from `ignore_usernames` / `ignore_usernames_override`)
  posts in the chat during the debounce window, drop the accumulated batch and forward
  nothing. Gated by a new per-instance `cancel_on_owner_reply` flag defaulting to
  `true` (opt-out).

These solve: agents like Hermes need a customizable, source-aware message; and a batch
shouldn't be forwarded once the owner has already engaged the conversation.

## Context (from discovery)

- Files/components involved:
  - `src/config.py` — `@dataclass Instance` (fields at lines 35–54), instance parsing
    + validation (`debounce_ms` validated at 185–189), and `generate_config`.
  - `src/telegram_utils.py` — `get_message_source` (private/group source line, 150–172),
    `get_forward_reason_text` (175–194), `get_forward_message_text` (197–217).
  - `src/app.py` — forward path calling `get_forward_message_text`; debounce buffering
    (`_debounce_message`, `inst.debounce_ms > 0` at ~307); message handler ignore branch
    (lines 498–519) where ignored users hit `continue`.
  - `src/debounce.py` — `DebounceManager` keyed by `(instance_name, chat_id)`; has
    `add_message` / `flush`, no cancel method yet; `_BatchState` holds `handle` (timer).
- Related patterns found: existing per-instance booleans (`once_per_chat`,
  `no_forward_message`, `folder_mute`) with defaults in the dataclass and parsing in
  `generate_config`/instance builder; `debounce_ms` integer validation as a model for
  validating new fields.
- Dependencies identified: `get_forward_message_text` is async and called from the
  forward path in `app.py` (single-message and debounce-batch flush paths). Source
  fields (`{username}`, `{name}`, `{chat}`) must be derivable from the message in
  `telegram_utils`.

## Development Approach

- **Testing approach**: TDD (tests first) — write failing tests, then implement.
- Complete each task fully before moving to the next; small, focused changes.
- **CRITICAL: every task MUST include new/updated tests**; tests are required, not optional.
- **CRITICAL: all tests must pass before starting the next task.**
- Run `pytest` after each change. Maintain backward compatibility (default output unchanged).
- Run `pre-commit --all-files` before any commit (black + isort), per CLAUDE.md.

## Testing Strategy

- **Unit tests**: required for every task (pytest / pytest-asyncio). The project has no
  UI e2e harness, so coverage is unit + integration-style tests against the helpers and
  the debounce manager.
- **E2E / manual**: covered in Post-Completion (manual run against a real instance).

## Progress Tracking

- Mark completed items with `[x]` immediately when done.
- Add newly discovered tasks with ➕ prefix; blockers with ⚠️ prefix.
- Keep this plan in sync with actual work.

## What Goes Where

- **Implementation Steps** (`[ ]`): code, tests, docs achievable in this repo.
- **Post-Completion** (no checkboxes): manual run-time verification against live Telegram.

## Implementation Steps

### Task 1: Add config fields, parsing, and validation
- [x] add `Instance` fields in `src/config.py`: `message_template: str | None = None`,
      `forward_message_show_trigger: bool = True`, `forward_message_show_source: bool = True`,
      `forward_message_prefix: str = ""`, `forward_message_suffix: str = ""`,
      `cancel_on_owner_reply: bool = True`
- [x] parse them in the instance builder: read scalar `message_template`; read the nested
      `forward_message:` block (`show_trigger`, `show_source`, `prefix`, `suffix`) into the
      flat fields; read `cancel_on_owner_reply`
- [x] validate types: `message_template` must be str-or-absent; the four `forward_message`
      sub-keys must be bool/str respectively; `cancel_on_owner_reply` must be bool
      (raise `ValueError` with a clear message, mirroring `debounce_ms` validation)
- [x] update `generate_config` to emit the new keys with their defaults/comments (N/A - no
      `generate_config` function exists in this Python codebase; config is hand-written YAML)
- [x] write tests (success): defaults applied when absent; template parsed; flags + prefix
      + suffix parsed from nested block; `cancel_on_owner_reply` parsed
- [x] write tests (error/edge): wrong types for template/flags/prefix/`cancel_on_owner_reply`
      raise `ValueError`
- [x] run `pytest` - must pass before next task

### Task 2: Render configurable preface in telegram_utils
- [x] extract granular source fields in `src/telegram_utils.py` so `{username}`, `{name}`,
      `{chat}` can be filled (reuse the logic already in `get_message_source`)
- [x] add a safe formatter that substitutes `{trigger}`, `{source}`, `{username}`, `{name}`,
      `{chat}` and renders missing/None values as `""` without raising on unknown keys
- [x] extend `get_forward_message_text` to accept the instance's template/flags
      (template wins; else assemble from `show_trigger`/`show_source` + `prefix`/`suffix`,
      preserving the current `{trigger}\n\n{source}` spacing when both present)
- [x] ensure `no_forward_message` precedence is respected (preface suppressed regardless)
      (caller in `app.py` skips calling the helper when `no_forward_message`; verified at
      the app level in Task 3/5)
- [x] write tests (success): default config reproduces today's exact output;
      template path renders each placeholder; flags drop trigger/source; prefix/suffix wrap
- [x] write tests (edge): empty template, unknown placeholder, None source fields,
      `no_forward_message=True` short-circuits (short-circuit covered at app level in Task 3)
- [x] run `pytest` - must pass before next task

### Task 3: Thread instance config into the forward path
- [x] update the call site(s) of `get_forward_message_text` in `src/app.py` (single-message
      and debounce-batch flush paths) to pass the instance's template/flags (both paths share
      `_forward_messages`, so the single call site covers both)
- [x] confirm no behavior change for instances without the new config (default-unchanged test
      asserts the helper receives the historical defaults)
- [x] write/extend tests covering the app-level forward path with a custom template and
      with flags (success + the default-unchanged case)
- [x] run `pytest` - must pass before next task

### Task 4: Add DebounceManager.cancel
- [ ] add `cancel(key)` to `src/debounce.py`: cancel the pending timer `handle` (if any)
      and drop the `_BatchState` for the key; no-op when the key is absent
- [ ] write tests (success): cancel after an active batch removes state and prevents flush
- [ ] write tests (edge): cancel with no active batch / unknown key is a safe no-op;
      cancel cancels the scheduled handle (assert via injected scheduler)
- [ ] run `pytest` - must pass before next task

### Task 5: Wire cancel-on-owner-reply into the handler
- [ ] in `src/app.py` ignore branch (≈ lines 507–511), before `continue`: when the sender
      is in the effective ignore list AND `inst.cancel_on_owner_reply` AND `inst.debounce_ms > 0`,
      call `debounce_manager.cancel((inst.name, event.chat_id))`
- [ ] keep per-instance semantics (only the matching instance's batch is cancelled)
- [ ] write tests (success): ignored user during window drops the batch (nothing forwarded)
- [ ] write tests (edge): `cancel_on_owner_reply=False` does not cancel; non-ignored user
      does not cancel; `debounce_ms=0` path unaffected
- [ ] run `pytest` - must pass before next task

### Task 6: Verify acceptance criteria
- [ ] verify all Overview requirements implemented (template, flags, prefix/suffix,
      no_forward_message precedence, default-unchanged output, cancel opt-out)
- [ ] run full `pytest` suite
- [ ] run `pre-commit --all-files` - all issues fixed
- [ ] verify coverage meets project standard (80%+)

### Task 7: Update documentation
- [ ] update `README.md` with the new instance options + examples
- [ ] update `config-example.yml` with `message_template`, `forward_message` block, and
      `cancel_on_owner_reply`
- [ ] update `AGENTS.md` only if module structure/responsibilities changed
- [ ] check off the two items in `docs/TODO.md`

## Technical Details

- **Placeholders**: `{trigger}` = `get_forward_reason_text(...)`; `{source}` =
  `get_message_source(...)`; `{username}` = sender `@username`; `{name}` = sender full
  name; `{chat}` = chat title/type. Substitution must not raise on missing keys (use a
  defaulting mapping, e.g. a `format_map` with a `defaultdict`-style fallback to `""`).
- **Config shape**:
  ```yaml
  instances:
    - name: example
      # Option 1 — full template (overrides layout):
      message_template: "{trigger}\n{source}"
      # Option 2 — flags + wrap (used when message_template is absent):
      forward_message:
        show_trigger: true
        show_source: true
        prefix: ""
        suffix: ""
      cancel_on_owner_reply: true   # default true
  ```
- **Precedence**: `no_forward_message` → suppress; else `message_template` if set; else
  flags + prefix/suffix.
- **Debounce key**: `(inst.name, chat_id)`, identical to `add_message` keying.

## Post-Completion

**Manual verification** (against a live instance):
- Configure an instance with a custom `message_template` and confirm the forwarded preface
  matches; toggle `show_trigger`/`show_source` and prefix/suffix.
- With `debounce_ms > 0`, trigger a batch, then reply yourself (owner username) within the
  window and confirm nothing is forwarded; repeat with `cancel_on_owner_reply: false` and
  confirm the batch is still delivered.
