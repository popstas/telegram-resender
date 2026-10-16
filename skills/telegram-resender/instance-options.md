# Instance options reference

Every key allowed under one entry of `instances:` in `data/config.yml`. Types,
defaults, and validation mirror `src/config.py` (`load_instances`) and the
matching logic in `src/app.py`. For each option: what it does, the value to
write, and the question to ask the user.

Only `name` is truly required; give an instance at least one **source** and one
**target** or it does nothing. Omit any option to accept its default.

## Identity

### `name`
- **Type:** string. **Default:** `"instance"`. **Effectively required** — must be
  unique across instances (used as the key in `data/seen_chats.json` and stats).
- **Ask:** "Name for this instance?"

## Sources — where to watch (need at least one)

### `entities`
- **Type:** list of strings (t.me links or `@username`). **Default:** `[]`.
- Specific chats/channels to monitor.
- **Ask:** "Which specific chats/channels? (t.me links or @usernames)"

### `folders`
- **Type:** list of strings (Telegram folder names). **Default:** `[]`.
- Monitors every chat currently in the named folders; re-resolved on startup and
  folder rescans. Pairs well with `once_per_chat`.
- **Ask:** "Which Telegram folders (by name)?"

### `chat_ids`
- **Type:** list of integers. **Default:** `[]` (stored as a set). Negative IDs are
  normal for groups/channels (e.g. `-1001234567890`).
- Numeric chat IDs to monitor, as an alternative to `entities`.
- **Ask:** "Any raw numeric chat IDs to watch?"

## Matching — which messages count

> `ignore_words` and `negative_words` are checked **first** and **suppress** the
> message entirely if any term appears. `words` and `prompts` are **OR with
> word-priority**: a `words` hit forwards immediately and prompts never run;
> prompts run only when no word matched. There is no keyword-AND-prompt gating.

### `words`
- **Type:** list of strings. **Default:** `[]`. Substring match, case-insensitive.
- The trigger terms. Any one match forwards the message.
- **Ask:** "Which words/phrases should trigger a forward?"

### `negative_words`
- **Type:** list of strings. **Default:** `[]`.
- If any appears, the message is **skipped** (never forwarded). Use for "matches a
  word but is the wrong kind of match."
- **Ask:** "Any words that should cancel a match?"

### `ignore_words`
- **Type:** list of strings. **Default:** `[]`.
- Same suppression effect as `negative_words` (checked one step earlier). Convention
  only: use for noise/spam terms.
- **Ask:** "Any words that mark a message as noise to ignore?"

### `prompts`
- **Type:** list of prompt objects. **Default:** `[]`. Run **only when no `word`
  matched**. Each is scored by the LLM; the message forwards if any prompt's score
  `>= threshold`.
- Prompt object keys:
  - `name` — string label (used in stats/traces).
  - `prompt` — the instruction text sent to the model. **Define the scoring scale
    here** (e.g. "score 1–10 …") so the threshold is meaningful.
  - `threshold` — integer, **default `4`**. Forwards when the returned score `>=`
    this. There is no fixed scale — it matches whatever the prompt text says.
  - Optional Langfuse keys: `langfuse_name`, `langfuse_label` (default `"latest"`),
    `langfuse_version`, `langfuse_type` (default `"text"`), and `config` (a mapping
    like `{model: gpt-4o, temperature: 0.7}`).
- **Ask:** "Describe what the AI should score for, the scale to use, and the
  cutoff." Never invent the prompt text or threshold.

### `categories`
- **Type:** mapping, or omitted. **Default:** none. TypeSafe categories gate; runs
  after `words` and before `prompts`. Needs the global `typesafe_api_key`. Messages
  without text skip it; on an API error it counts as not fired.
  - `instructions` — **required** non-empty string; the question put to the model.
  - `forward` — **required** non-empty mapping `name -> description` (description
    may be `null`). Categories that trigger forwarding.
  - `reject` — optional mapping, same shape. Contrast categories that never forward.
  - `threshold` — number in `(0, 1]`, default `0.5`. Forwards when the summed
    probability of the `forward` categories is `>=` this.
  - `model` — non-empty string, default `jev-latest`.
  - Names must match `[a-z0-9_]+` and be unique across `forward` and `reject`
    (invalid values are rejected at load). The most likely `forward` category
    becomes the `#name` hashtag in the header.
- **Ask:** "Which categories should forward and which should be rejected, with a
  short description each, and the question for the model?" Never invent them.

## Target — where matches go (need at least one)

### `target_chat`
- **Type:** integer chat ID. **Default:** `null`.
- **Ask:** "Numeric chat ID to forward matches to?"

### `target_entity`
- **Type:** string (t.me link / `@username`). **Default:** `""`.
- Alternative to `target_chat` by name.
- **Ask:** "Or a chat by @username / t.me link?"

### `target_webhook`
- **Type:** mapping. **Default:** none. Runs **alongside** the Telegram target, not
  instead of it. Failures are logged and swallowed (10s timeout).
  - `url` — **required** when the block is present.
  - `format` — `"text"` (default) or `"json"`. Any other value is rejected at load.
    `text` = one line; `json` = object with `from_username`, `from_name`,
    `message_text`, `chat_id`, `message_id`, `message_url`, `timestamp`.
- **Ask:** "POST matches to an HTTP endpoint too? URL and format (text/json)?"

## Forwarding behavior — when to forward

### `once_per_chat`
- **Type:** bool. **Default:** `false`. Forward only the **first** match per chat
  per day, then suppress until the reset hour. State in `data/seen_chats.json`
  survives restarts.
- **Ask:** "Forward only the first match per chat each day?"

### `reset_hour`
- **Type:** integer **0–23** (out of range is rejected). **Default:** `6`. Local
  hour each chat re-arms. Ignored when `once_per_chat` is false.
- **Ask:** "What hour (0–23) should the daily limit reset?"

### `debounce_ms`
- **Type:** integer `>= 0` (negative is rejected). **Default:** `0` (forward
  immediately). When `> 0`, buffers per-chat messages and forwards the whole batch
  after that many ms of silence; every new message resets the timer. Buffers are
  in-memory (lost on restart).
- **Ask:** "Batch a burst of messages before forwarding? Silence window in ms?"

### `cancel_on_owner_reply`
- **Type:** bool. **Default:** `true`. Only matters when `debounce_ms > 0`. If an
  ignored username (account owner) posts during the window, the batch is dropped
  and nothing forwards. Set `false` to always deliver.
- **Ask:** "If you reply during a batch window, cancel that batch?"

## Forwarded-message preface

Precedence: `no_forward_message` → `message_template` → `forward_message` flags.

### `no_forward_message`
- **Type:** bool. **Default:** `false`. Suppresses the preface entirely; wins over
  the two below.
- **Ask:** "Send forwarded messages with no preface at all?"

### `message_template`
- **Type:** string or null. **Default:** `null`. Overrides the whole preface layout.
  Placeholders (missing values render as `""`, never error): `{trigger}`,
  `{source}`, `{username}`, `{name}`, `{chat}`, `{category}` (`#<name>` when the
  categories gate fired, else empty). Malformed templates (unbalanced
  braces, positional/attribute fields) are rejected at load.
- **Ask:** "Custom preface template? Placeholders: {trigger} {source} {username}
  {name} {chat} {category}."

### `forward_message`
- **Type:** mapping (used only when `message_template` is absent). Keys, each
  validated for type:
  - `show_trigger` — bool, default `true` (include the match reason).
  - `show_category` — bool, default `true` (hashtag line when the categories gate
    fired; independent of `show_trigger`).
  - `show_source` — bool, default `true` (include the `Forwarded from: …` line).
  - `prefix` — string, default `""` (prepended).
  - `suffix` — string, default `""` (appended).
- **Ask:** "Toggle the match reason / source line, or add prefix/suffix text?"

## Folder automation

### `folder_mute`
- **Type:** bool. **Default:** `false`. Mute the instance's folder chats.
- **Ask:** "Mute the chats in these folders?"

### `folder_add_topic`
- **Type:** list of topic objects. **Default:** `[]`. Ensures each topic exists in
  every folder chat; creates it if missing, sends an optional message in the new
  thread, invites an optional user. Entries without `name` are skipped.
  - `name` — **required** topic title.
  - `message` — optional text sent into the created thread.
  - `username` — optional user to invite to the chat.
- **Ask:** "Auto-create any topics in the folder chats? (name, optional activation
  message, optional user to invite)"

## Misc

### `ignore_bots` (global, not per instance)
- **Type:** bool in the top-level config. **Default:** `false`. Drops messages
  before any instance runs when the sender username ends in "bot"
  (case-insensitive). Matches by username, **not** Telegram's bot flag, so
  `@abbot` is also dropped. Does not reset debounce batches.
- **Ask:** "Ignore messages from bots globally?"

### `typesafe_api_key` (global, not per instance)
- **Type:** string in the top-level config. Needed by `categories`; fallback is env
  `TYPESAFE_API_KEY`.

### `ignore_usernames_override`
- **Type:** list of strings, or omitted. **Default:** omitted (inherits the global
  `ignore_usernames`). If set — even to `[]` — this instance uses this list instead;
  `[]` means ignore nobody.
- **Ask:** "Override the global ignored-usernames list for this instance?"

### `false_positive_entity` / `true_positive_entity`
- **Type:** string. **Default:** `""`. Chats where you drop messages to label them
  as false/true positives, feeding eval-dataset generation.
- **Ask:** "Chats to collect false/true-positive examples for evals?"
