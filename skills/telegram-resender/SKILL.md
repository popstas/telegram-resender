---
name: telegram-resender
description: Use when adding, editing, or removing a monitoring instance in the telegram-resender data/config.yml, or when the user asks what an instance config option (words, folders, target_chat, debounce_ms, once_per_chat, prompts/threshold, categories, ignore_bots, message_template, forward_message, target_webhook) means or does.
---

# telegram-resender config

Manage the `instances:` list in this project's `data/config.yml` through a
step-by-step interview, then validate the result with the project's own loader.

An **instance** is one set of rules: watch some Telegram sources, match messages,
forward matches to a target. Every option is documented in
[instance-options.md](instance-options.md) — that file is the source of truth for
types, defaults, validation, and the question to ask. Read it before asking about
or writing any option you are unsure of.

## The one rule: ask, don't guess

Values like the instance `name`, the trigger `words`, the `target_chat`, and
especially prompt text and `threshold` are the user's to decide. **Never invent
them.** Use `AskUserQuestion` for every field whose value you do not already have
from the user. A plausible guess that parses is still wrong.

## Flow

1. **Locate & read** `data/config.yml` (respect the `CONFIG_PATH` env var, same as
   `src/config.py`). If it is missing, tell the user and stop. List the existing
   instance `name`s.
2. **Branch** on intent: add / edit / remove / explain-an-option.
3. Do the branch below.
4. **Validate** (always, after any write — see Validate).

### Add an instance (wizard)

Ask **essentials first**, one `AskUserQuestion` round per group. Consult
instance-options.md for each option's meaning and allowed values.

- **Round A — identity + sources:** `name` (must be unique); at least one source —
  `entities` (t.me links / @usernames), `folders` (folder names), or `chat_ids`.
- **Round B — matching:** `words` (trigger terms), `negative_words`, `ignore_words`.
  Explain the semantics below.
- **Round C — target:** at least one of `target_chat`, `target_entity`,
  `target_webhook`.
- **Then ask once:** "Configure advanced options?" If yes, run grouped rounds only
  for the areas the user wants:
  - forwarding *when*: `once_per_chat` (+`reset_hour`), `debounce_ms`
    (+`cancel_on_owner_reply`)
  - forwarded-message preface: `no_forward_message` / `message_template` /
    `forward_message` (`show_trigger`, `show_category`, `show_source`, `prefix`, `suffix`)
  - AI scoring: `prompts` (`name`, `prompt`, `threshold`, optional Langfuse keys)
  - AI categorization: `categories` (`instructions`, `forward`, `reject`,
    `threshold`, `model`); needs global `typesafe_api_key`
  - folder automation: `folder_mute`, `folder_add_topic`
  - misc: `ignore_usernames_override`, `false_positive_entity`,
    `true_positive_entity`

### Edit an instance

List instances, let the user pick one and the field(s) to change, confirm the new
value against instance-options.md validation, then apply a surgical Edit.

### Remove an instance

Let the user pick, show the block, confirm, delete only that block.

### Explain an option

Answer from instance-options.md. Do not read source unless the reference lacks it.

## Matching semantics — state these, they surprise people

`ignore_words` and `negative_words` both **suppress** the message entirely if any
term appears (they are checked before matching). Same effect; use `ignore_words`
for noise, `negative_words` for "not this kind of match."

`words` and `prompts` are **OR with word-priority, never AND**: if any `word`
matches, the message forwards immediately and **the prompts never run**. Prompts
run **only when no word matched**. So you cannot express "keyword AND high AI
score" in config — if a user asks for that gating, tell them it is a code change,
not a config option.

`threshold` (default 4) forwards when the model's returned score is `>=` it. The
scale is whatever your prompt text tells the model to use — define the scale in
the prompt so the threshold has meaning.

## Write-back rules

- Edit `data/config.yml` **in place with the Edit tool** — do not round-trip
  through a YAML dumper (it would strip comments and reorder keys).
- Match the surrounding indentation and the block style in `config-example.yml`.
- Omit options left at their default rather than writing every key.

## Validate

After every write, run the project's own loader from the repo root (no bot
restart — tell the user to restart the bot themselves):

```
.venv/bin/python -c "import asyncio; from src.config import load_config, load_instances; asyncio.run(load_instances(load_config()))"
```

Silent exit = valid. A traceback = invalid: read the `ValueError`, fix the config
(or revert your edit), and re-run until it passes.

## Common mistakes

| Mistake | Reality |
|---------|---------|
| Guessing a name, word, prompt text, or threshold | Ask the user. These are theirs to decide. |
| Configuring "keyword AND AI score" | Code treats them as OR/fallback. Not possible in config. |
| Assuming a fixed 1–10 prompt scale | The scale lives in the prompt text; set it there, then pick a matching threshold. |
| Writing every key at its default | Omit defaults; keep the block readable. |
| Round-tripping the YAML through a dumper | Strips comments/order. Use surgical Edits. |
| Skipping validation | Always run the loader; a typo silently breaks the next bot start. |
