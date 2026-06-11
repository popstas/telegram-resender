## Rules on new features:
- Add tests for new features.
- Update README.md after features changes.
- If config type was changed, change config.ts generateConfig function.

## Rules before commit
- Always run `pre-commit --all-files` before commit.
- Update AGENTS.md when project structure changes.

# Repository Overview

This project forwards Telegram messages that match specific rules to a target chat. The runtime logic lives in **src/app.py** while helper modules like `config`, `prompts`, `stats` and `telegram_utils` sit alongside it. The `src/main.py` module simply invokes `app.main()`. Configuration is stored in YAML under `data/config.yml` (see `config-example.yml` for reference).

```
├── src/
│   ├── app.py            # runtime logic
│   ├── main.py           # CLI entry point
│   ├── config.py         # config helpers
│   ├── prompts.py        # OpenAI prompts
│   ├── evals.py          # evaluation helpers
│   ├── run_deepeval.py   # run eval datasets
│   ├── stats.py          # stats tracking
│   ├── trace_ids.py      # trace ID storage
│   ├── seen_chats.py     # once_per_chat dedup state (data/seen_chats.json)
│   ├── debounce.py       # debounce_ms per-chat batching
│   ├── telegram_utils.py # Telegram helpers
│   ├── generate_evals.py # build eval datasets
│   ├── bulk.py           # bulk operations CLI
│   ├── webhook.py        # target_webhook HTTP delivery
│   └── __init__.py       # marks package
├── scripts/
│   └── test_webhook_server.py  # manual webhook listener (port 8002)
├── tests/                # pytest suite
├── README.md             # setup instructions
├── pyproject.toml / requirements.txt  # dependencies
└── Dockerfile
```

## Development hints

- Create a virtual environment, install dependencies and copy the example config:
  ```bash
  pip install -r requirements.txt
  cp config-example.yml data/config.yml
  ```
- Run the bot:
  ```bash
  python -m src.main
  ```
- Tests are written with **pytest** and **pytest-asyncio**. Run them with:
  ```bash
  pytest
  ```
- Pre-commit hooks apply **black** and **isort**. Install them once:
  ```bash
  pre-commit install
  ```

You can inspect `.github/workflows/test.yml` for the CI workflow. The default branch runs the same `pytest` command and reports coverage to Coveralls.

