# telegram-resender

[![Coverage Status](https://coveralls.io/repos/github/popstas/telegram-resender/badge.svg?branch=main)](https://coveralls.io/github/popstas/telegram-resender?branch=main)

A Telegram client (built on [Telethon](https://github.com/LonamiWebs/Telethon),
running under your own user account — not a Bot API bot) that watches chats,
channels, and folders, then forwards matching messages to a target chat, topic,
or webhook.

**Triggers:** word lists (with negative/ignore words), OpenAI prompt matches, and
👍/👎 reactions (forwarding to true/false-positive chats).

**Actions:** forward with a link back to the source message (and a short reason
and quote for prompt matches), auto-create forum topics for folder chats,
debounce batching, once-per-chat dedup, and Langfuse tracing.

# Features

- Listen folders, chats, channels
- Multiple instances for different chats, words and targets
- Each instance has target chat
- Forwarded messages include a link to the original message
- Prompt-triggered forwards include a short reason and quote from the message
- Reactions (👍/👎) forward messages to true/false positive chats once per message
- Langfuse trace IDs for forwarded messages are recorded
- Automatically create forum topics for chats collected from folders
- Optional per-chat first-match dedup (`once_per_chat`) with a daily reset hour
- Optional debounce batching (`debounce_ms`) that groups a conversation into one forward
- Optional cancel of a pending debounce batch when the account owner replies (`cancel_on_owner_reply`)
- Configurable forwarded-message preface (`message_template` / `forward_message` flags)

## Setup

1. Install Python 3.10+ and create a virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Copy `config-example.yml` to `data/config.yml` and adjust the values:

- `api_id` – your Telegram API ID.
- `api_hash` – your Telegram API hash.
- `session` – path to your session file (default is `data/session`).
- `log_level` – logging level (default is `info`).
- `langfuse_public_key` – (optional) public key to enable Langfuse tracing.
- `langfuse_secret_key` – (optional) secret key for Langfuse.
- `langfuse_base_url` – (optional) custom Langfuse API URL.
- `proxy_url` – (optional) proxy URL for Telegram and OpenAI API calls (e.g. `socks5://127.0.0.1:1080` or `http://proxy:8080`).
- `ignore_usernames` – list of usernames to ignore when processing messages.
- `ignore_user_ids` – list of user IDs to ignore when processing messages.
- `instances` – list of monitoring instances. Each instance may contain
  `folders`, `chat_ids`, `entities`, `words`, `negative_words`, `ignore_words`, `target_chat`,
  `target_entity`, `target_webhook`, `folder_mute`, `folder_add_topic`, `false_positive_entity`, `true_positive_entity`,
  `no_forward_message`, `once_per_chat`, `reset_hour`, `debounce_ms`, `ignore_usernames_override`,
  `message_template`, `forward_message` and `cancel_on_owner_reply`.
- `ignore_usernames_override` (per instance) – if defined, this instance ignores the
  global `ignore_usernames` and uses its own list instead. An empty list (`[]`) means
  the instance ignores no usernames. Omit the key to inherit the global list.

## Per-chat dedup and debounce batching

Two optional, per-instance forwarding features control *when* matches forward.
Both default to off, so existing instances are unaffected.

### First match per chat per day (`once_per_chat`)

When `once_per_chat: true`, the instance forwards only the **first** match per
chat, then suppresses further matches in that chat until the daily reset hour.
This is handy for folder-backed instances: each chat in the folder triggers at
most once per day.

- `reset_hour` (default `6`) – local server hour (0–23) at which each chat
  re-arms. With the default, a chat that matched at 14:00 can match again after
  06:00 the next day. Ignored when `once_per_chat` is false.
- State persists across restarts in `data/seen_chats.json`
  (`{instance: {chat_id: last_forward_epoch}}`), so a restart does not
  re-forward an already-seen chat.

```yaml
instances:
  - name: Folder watcher
    folders: [Work]
    words: ["my username"]
    once_per_chat: true
    reset_hour: 6
```

### Debounce batching (`debounce_ms`)

When `debounce_ms > 0`, a trigger does not forward immediately. Instead the
instance keeps a rolling per-chat buffer of recent messages; every new message
(any sender) resets the timer, and after `debounce_ms` of silence the whole
batch is forwarded under a single match header — capturing conversation context
before and after the trigger. Buffers are in-memory only, so pending batches are
lost on restart.

If several triggers land in one batch, the header from the **first** trigger
wins. Default is `0` (forward immediately).

Example timeline with `debounce_ms: 60000` (60s):

```
10:00:00  "anyone around?"          -> buffered (pre-trigger context)
10:00:30  "my username, help!"      -> trigger, batch starts, timer set
10:00:50  "actually figured it out" -> buffered, timer reset
10:01:50  (60s of silence elapsed)  -> all three forwarded together
```

```yaml
instances:
  - name: Conversation batcher
    chat_ids: [-1001234567890]
    words: ["my username"]
    debounce_ms: 60000
```

`once_per_chat` and `debounce_ms` can be combined: a suppressed trigger does not
start a batch, but messages still buffer for any active batch. Messages without a
resolvable chat ID bypass both features and forward immediately.

### Cancel a debounce batch when you reply (`cancel_on_owner_reply`)

When `debounce_ms > 0` and one of the ignored usernames (the account owner, from
the global `ignore_usernames` or the per-instance `ignore_usernames_override`)
posts in the chat during the debounce window, the accumulated batch is dropped and
**nothing is forwarded** — the conversation is considered already handled.

This is gated by `cancel_on_owner_reply`, which defaults to `true` (opt-out). Set
it to `false` to keep delivering the batch even after you reply. It has no effect
when `debounce_ms` is `0`.

```yaml
instances:
  - name: Conversation batcher
    chat_ids: [-1001234567890]
    words: ["my username"]
    debounce_ms: 60000
    cancel_on_owner_reply: true   # default; set false to always deliver
```

## Configurable forwarded message

Before each forwarded message the bot sends a preface describing *why* the message
matched and *where* it came from. By default this is the historical layout:

```
word: my username

Forwarded from: private @username, Name: User Name
```

This preface is configurable per instance, which is useful when the target is an
agent (e.g. Hermes) that needs the source details in a specific shape. **With no
new config the output is byte-identical to the default above.**

`no_forward_message: true` still suppresses the preface entirely and takes
precedence over every option below.

### Option 1 — full template (`message_template`)

Set `message_template` to a string with placeholders. When present it overrides
the layout completely. Available placeholders (unknown placeholders and missing
values render as an empty string, never an error):

- `{trigger}` – the match reason (e.g. `word: my username` or `prompt name: 4/5`).
- `{source}` – the full `Forwarded from: …` source line.
- `{username}` – the sender `@username` (or empty).
- `{name}` – the sender full name (or empty).
- `{chat}` – the chat title/type.

```yaml
instances:
  - name: Hermes feed
    words: ["my username"]
    message_template: "{trigger}\n{source}"
```

### Option 2 — flags + wrap (`forward_message`)

When `message_template` is absent, the preface is assembled from the
`forward_message` block:

- `show_trigger` (default `true`) – include the match reason.
- `show_source` (default `true`) – include the `Forwarded from: …` line.
- `prefix` (default `""`) – free text prepended to the preface.
- `suffix` (default `""`) – free text appended to the preface.

With both `show_trigger` and `show_source` enabled the original
`{trigger}\n\n{source}` spacing is preserved.

```yaml
instances:
  - name: Source only
    words: ["my username"]
    forward_message:
      show_trigger: false
      show_source: true
      prefix: "🔔 "
      suffix: ""
```

Precedence: `no_forward_message` → suppress; else `message_template` if set; else
the `forward_message` flags with `prefix`/`suffix`.

`folder_add_topic` is a list of topics that should exist in every chat inside the
instance folders. When a topic is missing, the client will create it, send an
optional activation message inside the new thread, and invite an optional
`username` to the chat.

## Running

```bash
python -m src.main
```

The application will listen to new messages in all configured instances and
forward those containing any of the specified words to their target chats.

Statistics about processed messages are stored in `data/stats.json` and include
overall, per-instance and per-day counters. Besides the total processed
messages, the file tracks how many were forwarded in total, due to word matches
or prompt matches, and token usage in each scope: `input_tokens` (prompt),
`output_tokens` (completion), and `tokens` (total from the API when available,
otherwise the sum of input and output). With `debounce_ms`, the forwarded count
for a triggering message is recorded when the batch flushes, not when the trigger
arrives; context messages pulled into a batch are still counted as not forwarded.
Instances
that use Telegram `folders` also get a `chats` field: a sorted list of normalized
chat IDs currently resolved from those folders (updated on startup and on each
folder rescan). If you have
a file in the old format (without the `stats` section), it will be automatically
converted on startup using the new `Stats` structure. Trace IDs for forwarded
messages are saved in `data/trace_ids.json`, grouped by chat ID.

## Bulk operations

Use the `bulk` CLI to perform batch operations on all chats in a folder:

```bash
# Mute all chats in a folder
python -m src.bulk --folder MyFolder --mute

# Add a user to all chats in a folder
python -m src.bulk --folder MyFolder --add-user @username

# Combine actions
python -m src.bulk --folder MyFolder --mute --add-user @username
```

## Webhook delivery (`target_webhook`)

In addition to forwarding matched messages to a Telegram chat, an instance can
POST each match to an HTTP endpoint. This runs alongside the existing
`target_chat` / `target_entity` delivery — it does not replace it. Webhook
failures are logged and swallowed, so they never block Telegram forwarding or
raise out of the handler. Requests use a short 10-second timeout and non-2xx
responses are logged.

Configure it under any instance:

```yaml
instances:
  - name: Example (text)
    words: [hello]
    target_webhook:
      url: http://127.0.0.1:8002/webhook
      format: text   # default

  - name: Example (json)
    words: [hello]
    target_webhook:
      url: http://127.0.0.1:8002/webhook
      format: json
```

Payload formats:

- `text` (default): one line, e.g.
  `From: @user, Name: John Doe, Message: Hello, how are you?`
- `json`: an object with `from_username`, `from_name`, `message_text`,
  `chat_id`, `message_id`, `message_url`, `timestamp`.

Only `text` and `json` are accepted; any other value is rejected at config
load time.

## Manual webhook testing

A tiny HTTP listener is provided for manual end-to-end testing of an
instance's `target_webhook`. It binds to port 8002 by default, prints every
incoming request (method, headers, body) to stdout, and responds with
`200 OK` and a small JSON ack:

```bash
python scripts/test_webhook_server.py            # listens on 127.0.0.1:8002
python scripts/test_webhook_server.py --host 0.0.0.0 --port 9000
```

Point an instance's `target_webhook.url` at `http://localhost:8002/` (or any
path) and trigger a matching Telegram message to confirm delivery.

## Generate evaluation datasets

Build evaluation tasks from collected true and false positive messages:

```bash
python -m src.generate_evals --suffix run1
```

Use `--config` to provide a custom path to `config.yml` if needed.

Datasets and configuration files will be written to `data/evals/` with the
provided suffix. Each line in `messages.jsonl` also contains a `trace_id`
linking back to the corresponding Langfuse trace.

## Run evaluations

After generating datasets, run them with [DeepEval](https://github.com/confident-ai/deepeval):

```bash
python -m src.run_deepeval --instance "Inst" --prompt "Prompt" --suffix run1
```

Use `--config` to provide a custom path to `config.yml` if needed.
The command exits with status code `1` if accuracy is below 80%.

To evaluate using OpenAI's Evals API:

```bash
python -m src.run_openai_evals --instance "Inst" --prompt "Prompt" --suffix run1
```

The runner respects any `model` or `temperature` options defined in the prompt
configuration and forces JSON responses that match the `EvaluateResult` schema.

### Langfuse tracing

Set `langfuse_public_key` and `langfuse_secret_key` in the config to enable
tracing with [Langfuse](https://langfuse.com). Optionally specify
`langfuse_base_url` if using a self-hosted instance.
The client uses the Langfuse OpenAI integration, so all OpenAI calls are
automatically traced. Each request is tagged with the instance name and chat
name to make debugging easier.

#### Langfuse prompts

Prompts used for LLM evaluation can be stored in Langfuse. Set
`langfuse_name`, `langfuse_label`, `langfuse_version`, or `langfuse_type`
under a prompt entry in the config to fetch the text from Langfuse at startup.
When the local text differs from Langfuse, a new version is automatically
created and `langfuse_version` updated. The optional `config` field is forwarded
to Langfuse when creating versions. See `config-example.yml` for an example.
The compiled prompt is linked to Langfuse generations via `update_current_generation`.

## Development

It is built using [Telethon](https://github.com/LonamiWebs/Telethon).

Install pre-commit hooks:

```bash
pre-commit install
```

This will automatically run `black` and `isort` before each commit.

### Changelog and releases

The changelog is generated from [conventional commits](https://www.conventionalcommits.org/)
with [git-cliff](https://git-cliff.org/) (`cliff.toml`). The pre-commit hook keeps
[`CHANGELOG.md`](CHANGELOG.md) up to date; regenerate it manually with:

```bash
git-cliff -o CHANGELOG.md
```

`task:`, `chore: release` and merge commits are excluded from the changelog.

To cut a release, bump `version` in `pyproject.toml`, then tag and push:

```bash
git tag vX.Y.Z && git push origin vX.Y.Z
```

The [`release` workflow](.github/workflows/release.yml) generates the release notes for the
tag with git-cliff and publishes the GitHub release automatically.
