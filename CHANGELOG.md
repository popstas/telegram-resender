# Changelog

All notable changes to this project are documented here.


## Unreleased

### Features

- Verify acceptance criteria for configurable message and debounce cancel
- Cancel debounce batch on owner reply in handler
- Add DebounceManager.cancel to drop pending batch
- Thread instance config into the forward path
- Render configurable forwarded-message preface in telegram_utils
- Add config fields, parsing, and validation for forward message and cancel-on-owner-reply

### Documentation

- Add plan for configurable forward message + debounce cancel-on-owner-reply
- Update pyproject description to match new project scope
- Clarify project is a Telethon client and describe triggers/actions

### Build

- Add git-cliff changelog generation and release workflow

### Miscellaneous

- Rename project to telegram-resender

## v0.2.0 - 2026-06-17

### Features

- Document once_per_chat, reset_hour, debounce_ms
- Verify per-chat dedup and debounce acceptance criteria
- Wire DebounceManager into the message pipeline
- Add DebounceManager for debounce_ms batching
- Extract _forward_messages helper and integrate once_per_chat
- Add once_per_chat, reset_hour, debounce_ms config fields
- Add SeenChatStore persistence for per-chat dedup
- config: Add ignore_usernames_override per instance
- telegram: Include sender name in forward header
- Add target_webhook delivery for matched messages (#53)
- stats: Track input and output token usage
- stats: Add chats list for folder-backed instances
- proxy: Use proxy_url for Telegram API
- bulk: Add bulk CLI for folder operations
- Add --config to generate_evals (#49)
- Add no_forward_message option (#39)
- More verbose stats.json with forwarded count and tokens by day (#37)
- Add negative_words support (#36)
- Add ignore_user_ids (#35)
- Integrate Langfuse prompts (#33)
- Add Langfuse tracing support (#32)
- Add false positive reaction forwarding (#30)
- Format forwarded message with link, bold cite (#29)
- Add ignore_words support (#27)
- Add fragment citation in forward reason (#23)
- Add forward reason, extend prompt config (#22)
- Add proxy support and token stats (#21)
- Add llm message evaluation with openai (#19)
- Add message processing stats.json (#18)
- Add folder mute feature (#17)
- Better "Forwarded from" message (#16)
- Reload config hourly (#13)
- Add log_level option, display chats titles (#5)
- Add multi-instance monitoring and entities support (url instead of chat id) (#4)
- Add Dockerfile and periodic folder rescans (#3)
- Mvp: Add Telegram mention resender (#1)

### Bug Fixes

- telegram: Warm entity cache at startup to resolve folder PeerUsers
- review: Log exceptions from fire-and-forget debounce flush
- review: Use get_running_loop, test chat_id fallback, doc edge cases
- app: Await get_sender for ignore_usernames
- telegram: Survive unknown TL constructors
- bulk: Detect privacy and membership
- Add muted log
- Track daily token usage in stats (#50)
- Track forwarded stats at all levels (#48)
- Update prompt evaluation schema (#40)
- Avoid duplicate reaction forwards (#38)
- Collect all chat types from folder (#14)

### Documentation

- Add plan for per-chat dedup and debounce_ms batching

