# TypeSafe Categories Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить в инстанс гейт `categories` (TypeSafe Choice) с хэштегом категории в шапке пересылки, глобальный `ignore_bots`, скрипт прогона по выгрузке и выкатить первую версию инстанса лидов в тестовый контейнер.

**Architecture:** Новый модуль `src/categories.py` (по образцу `src/prompts.py`) делает один запрос `system_one` с Choice-вопросом и возвращает `CategoryMatch`. `src/app.py` вызывает его между `words` и `prompts` и протаскивает результат в шапку (`telegram_utils`), debounce, webhook и stats. Скрипт `src/run_categories_eval.py` переиспользует `match_categories` на выгрузке `telegram-download-chat`.

**Tech Stack:** Python 3, Telethon, `typesafe-sdk` (`AsyncTypeSafeClient`, `Choice`), pytest + pytest-asyncio, `.venv`.

**Spec:** `docs/superpowers/specs/2026-10-16-typesafe-categories-gate-design.md`. Чувствительные детали сценария (чаты, target, черновик инстанса, правила выката) — `data/typesafe-categories-sensitive/README.md` (вне git).

## Global Constraints

- Ветка `feat/typesafe-categories` от `main`; коммиты conventional (`feat:`, `test:`, `docs:`), окончание сообщения — `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Перед каждым коммитом: `.venv/bin/pre-commit run --all-files` (black + isort) и `.venv/bin/pytest -q`.
- Порядок гейтов: `words` → `categories` → `prompts`; сработавший гейт останавливает следующие.
- Имена категорий: `[a-z0-9_]+`, уникальны между `forward` и `reject`; `forward` непуст; `threshold` в `(0, 1]`, по умолчанию `0.5`; `model` по умолчанию `jev-latest`.
- Решение о пересылке: `p_forward = сумма вероятностей forward-категорий >= threshold`; хэштег — самая вероятная forward-категория.
- `{trigger}` для гейта = `category: <name> <prob с 2 знаками>`; `{category}` = `#<name>` или `""`.
- Без `categories` в инстансе шапка байт-в-байт как сейчас.
- Ошибка TypeSafe → `logger.warning`, гейт не сработал, обработка идёт к `prompts`; сервис не падает.
- `ignore_bots` (глобальный, по умолчанию `false`): `username.lower().endswith("bot")` → сообщение отбрасывается до цикла по инстансам, без `debounce_manager.cancel`.
- Ключ: `typesafe_api_key` в конфиге, иначе env `TYPESAFE_API_KEY`.
- В git не попадают тексты сообщений, имена, разметка, названия чатов/папок и заказчик сценария — только в `data/`.
- Прод не трогать без отдельного явного ок пользователя; выкат — только в локальный тестовый контейнер.

## Review Focus

1. **Сообщение без текста или только с медиа** — гейт не вызывается, ни запроса, ни ошибки (тест в Task 4).
2. **Нет ключа TypeSafe при наличии `categories` в конфиге** — гейт молча не срабатывает с одним `warning` на вызов, `prompts` продолжают работать (тест в Task 2).
3. **TypeSafe вернул вероятности не для всех категорий / лишние ключи** — отсутствующая категория считается вероятностью `0.0`, лишние игнорируются, без `KeyError` (тест в Task 2).
4. **Debounce-батч, запущенный гейтом категорий** — шапка после flush несёт хэштег, stats считают `forwarded_category` (тест в Task 4).
5. **Пользователь с username, оканчивающимся на `bot` (например `@abbot`)** — отсекается по правилу пользователя; в README явно сказано, что правило — по username, а не по флагу Telegram (тест в Task 5, документ в Task 5).

---

### Task 1: Конфиг `categories`, `typesafe_api_key`, `ignore_bots` и зависимость

**Files:**
- Modify: `src/config.py` (dataclass `CategoriesGate`, поле `Instance.categories`, парсинг в `load_instances`, `show_category` в блоке `forward_message`, ключ `category` в проверке `message_template`)
- Modify: `requirements.txt`, `pyproject.toml` (`typesafe-sdk`)
- Modify: `config-example.yml`, `README.md` (секция про гейт categories + `typesafe_api_key` + `ignore_bots` + `show_category` + `{category}`), `skills/telegram-resender/SKILL.md` и `skills/telegram-resender/instance-options.md`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass
  class CategoriesGate:
      instructions: str
      forward: dict[str, str | None]
      reject: dict[str, str | None] = field(default_factory=dict)
      threshold: float = 0.5
      model: str = "jev-latest"
  # Instance: categories: CategoriesGate | None = None
  #           forward_message_show_category: bool = True
  ```
  Глобальные ключи читаются из `config` dict как есть: `config.get("typesafe_api_key")`, `config.get("ignore_bots", False)`.

- [ ] **Step 0: Создать ветку и поставить SDK**

Run: `git switch -c feat/typesafe-categories && .venv/bin/pip install typesafe-sdk && .venv/bin/python -c "import typesafe_sdk, inspect; print(typesafe_sdk.__version__ if hasattr(typesafe_sdk,'__version__') else 'ok')"`
Expected: импорт без ошибок. Записать установленную версию в `requirements.txt` как `typesafe-sdk>=<version>` и в `pyproject.toml` `dependencies`.

- [ ] **Step 1: Написать падающие тесты** в `tests/test_config.py`

```python
def test_categories_parsed(tmp_path):
    # instance with categories: instructions, forward {lead_direct: "a", lead_weak: None}, reject {other: "b"}
    inst = load(...)[0]
    assert inst.categories.forward == {"lead_direct": "a", "lead_weak": None}
    assert inst.categories.reject == {"other": "b"}
    assert inst.categories.threshold == 0.5
    assert inst.categories.model == "jev-latest"

def test_categories_absent_is_none(): assert inst.categories is None

@pytest.mark.parametrize("bad", [
    {"instructions": "q", "forward": {}},                                  # empty forward
    {"instructions": "q", "forward": {"Lead": None}},                      # uppercase
    {"instructions": "q", "forward": {"a": None}, "reject": {"a": None}},  # duplicate
    {"instructions": "q", "forward": {"a": None}, "threshold": 0},
    {"instructions": "q", "forward": {"a": None}, "threshold": 1.5},
    {"forward": {"a": None}},                                              # no instructions
])
def test_categories_invalid_raises(bad): with pytest.raises(ValueError): ...

def test_show_category_default_true_and_validated(): ...  # default True; "yes" -> ValueError
def test_message_template_accepts_category_placeholder(): ...  # "{category}\n{trigger}" loads
```

Использовать тот же способ записи временного YAML и вызова `load_instances`, что и соседние тесты файла.

- [ ] **Step 2: Запустить — падают**

Run: `.venv/bin/pytest tests/test_config.py -q`
Expected: FAIL (`CategoriesGate`/`categories` не существуют).

- [ ] **Step 3: Реализовать** `CategoriesGate`, парсинг `_parse_categories(cfg: dict | None) -> CategoriesGate | None` в `src/config.py`, поле `forward_message_show_category`, `"category": ""` в словаре проверки `message_template`. Сообщения ошибок в стиле соседних (`categories.forward must ...`).

- [ ] **Step 4: Тесты проходят**

Run: `.venv/bin/pytest tests/test_config.py -q`
Expected: PASS.

- [ ] **Step 5: Документация.** В `config-example.yml` — закомментированный пример `categories` на нейтральном домене (как в спеке, без названий реальных чатов), `typesafe_api_key: ""`, `ignore_bots: false`. В `README.md` — подраздел «Categories gate (TypeSafe)» рядом с описанием `prompts`: ключи, порядок гейтов, правило `p_forward`, хэштег, `{category}`, `show_category`, `ignore_bots` (с оговоркой «по username»). В скилле — новые ключи в списке опций инстанса.

- [ ] **Step 6: Коммит**

```bash
git add src/config.py tests/test_config.py requirements.txt pyproject.toml config-example.yml README.md skills/telegram-resender/
git commit -m "feat(config): add categories gate, typesafe_api_key and ignore_bots keys"
```

---

### Task 2: Модуль `src/categories.py` — запрос к TypeSafe

**Files:**
- Create: `src/categories.py`
- Test: `tests/test_categories.py`

**Interfaces:**
- Consumes: `CategoriesGate` (Task 1).
- Produces:
  ```python
  config: dict = {}   # заполняется из app так же, как prompts.config

  @dataclass
  class CategoryMatch:
      category: str                    # самая вероятная forward-категория
      probability: float               # её вероятность
      p_forward: float                 # сумма вероятностей forward-категорий
      forward: bool                    # p_forward >= gate.threshold
      probabilities: dict[str, float]  # по всем категориям gate (отсутствующие = 0.0)
      model: str | None = None         # response.model
      input_tokens: int = 0

  def build_client() -> "AsyncTypeSafeClient | None"   # None, если нет ключа
  async def match_categories(gate: CategoriesGate, text: str, chat_name: str | None = None, *, client=None) -> CategoryMatch | None
  def evaluate_probabilities(gate: CategoriesGate, probabilities: dict[str, float]) -> tuple[str, float, float, bool]
  ```
  `match_categories` возвращает `None` при пустом `text`, отсутствии ключа или любой ошибке SDK (с `logger.warning`). `client` — для тестов и для eval-скрипта (переиспользование одного клиента).

- [ ] **Step 1: Падающие тесты** в `tests/test_categories.py`

```python
GATE = CategoriesGate(instructions="q", forward={"lead_direct": "a", "lead_weak": "b"},
                      reject={"competitor": "c", "other": "d"}, threshold=0.5)

def test_evaluate_sums_forward_and_picks_best_forward():
    cat, prob, p_fwd, fwd = evaluate_probabilities(GATE, {"lead_direct": 0.3, "lead_weak": 0.25, "competitor": 0.05, "other": 0.4})
    assert (cat, prob, round(p_fwd, 2), fwd) == ("lead_direct", 0.3, 0.55, True)

def test_evaluate_below_threshold():
    *_, fwd = evaluate_probabilities(GATE, {"lead_direct": 0.2, "lead_weak": 0.1, "other": 0.7})
    assert fwd is False

def test_evaluate_missing_and_extra_keys():
    cat, prob, p_fwd, fwd = evaluate_probabilities(GATE, {"lead_weak": 0.9, "junk": 0.1})
    assert (cat, prob, fwd) == ("lead_weak", 0.9, True)

async def test_match_builds_choice_question(fake_client):
    # fake_client.system_one records kwargs and returns obj with
    # .choices["category"].probabilities, .model="jev-1.13.0", .usage.input_tokens=120
    m = await match_categories(GATE, "текст", "Chat", client=fake_client)
    q = fake_client.calls[0]["questions"]["category"]
    assert set(q.criteria) == {"lead_direct", "lead_weak", "competitor", "other"}
    assert fake_client.calls[0]["model"] == "jev-latest"
    assert m.model == "jev-1.13.0" and m.input_tokens == 120

async def test_match_empty_text_no_call(fake_client): assert await match_categories(GATE, "", client=fake_client) is None and not fake_client.calls
async def test_match_sdk_error_returns_none(raising_client, caplog): assert ... is None; assert "categories" in caplog.text
async def test_match_no_key_returns_none(monkeypatch): # config без ключа, env TYPESAFE_API_KEY удалён
    assert await match_categories(GATE, "x") is None
```

- [ ] **Step 2: Запустить — падают**

Run: `.venv/bin/pytest tests/test_categories.py -q`
Expected: FAIL (`ModuleNotFoundError: src.categories`).

- [ ] **Step 3: Реализовать.** `state` = `{"chat": chat_name, "message": text}` (без `chat`, если `None`). Вопрос: `Choice(instructions=gate.instructions, criteria={**gate.forward, **gate.reject})` под ключом `"category"`. Ключ: `config.get("typesafe_api_key") or os.environ.get("TYPESAFE_API_KEY")`. Прокси: если `config.get("proxy_url")` — передать в клиент через `http_client` того HTTP-пакета, на котором стоит установленный SDK (проверить `inspect.signature(AsyncTypeSafeClient)`); если прокси передать нельзя — `logger.warning` один раз при создании клиента. Клиент кэшировать на уровне модуля (`_client`), сбрасывать при смене ключа. Поля ответа сверить с установленным SDK (`response.choices[...]`, `.probabilities`, `.model`, `.usage.input_tokens`). Логировать результат на `debug`: категория, `p_forward`, распределение.

- [ ] **Step 4: Тесты проходят**

Run: `.venv/bin/pytest tests/test_categories.py -q`
Expected: PASS.

- [ ] **Step 5: Живая проверка одного запроса** (ключ в env есть):

Run: `.venv/bin/python -c "import asyncio; from src.config import CategoriesGate; from src.categories import match_categories; g=CategoriesGate(instructions='Is the author asking for help?', forward={'ask':'asks for help'}, reject={'other':'anything else'}); print(asyncio.run(match_categories(g, 'Кто может помочь с документами?')))"`
Expected: печатается `CategoryMatch(... forward=True ...)` с `model='jev-…'`.

- [ ] **Step 6: Коммит**

```bash
git add src/categories.py tests/test_categories.py
git commit -m "feat(categories): TypeSafe Choice gate module"
```

---

### Task 3: Шапка пересылки и webhook

**Files:**
- Modify: `src/telegram_utils.py:175-267` (`get_forward_reason_text`, `get_forward_message_text`)
- Modify: `src/webhook.py` (`format_json_payload`, `send_webhook`)
- Test: `tests/test_telegram_utils.py`, `tests/test_webhook.py`

**Interfaces:**
- Consumes: `CategoryMatch` (Task 2).
- Produces:
  ```python
  def get_forward_reason_text(*, prompt=None, score=None, word=None, quote=None, reasoning=None, category: CategoryMatch | None = None) -> str
  async def get_forward_message_text(message, *, ..., category: CategoryMatch | None = None, show_category: bool = True) -> str
  def format_json_payload(message, category: str | None = None) -> dict   # всегда ключ "category"
  async def send_webhook(target, message, category: str | None = None) -> None
  ```
  Приоритет причины: `word` → `category` → `prompt`.

- [ ] **Step 1: Падающие тесты**

```python
M = CategoryMatch(category="lead_direct", probability=0.823, p_forward=0.9, forward=True, probabilities={})

def test_reason_category(): assert get_forward_reason_text(category=M) == "category: lead_direct 0.82"

async def test_header_default_with_category(src="SRC"):   # source замокан как в соседних тестах
    assert await get_forward_message_text(msg, category=M) == "#lead_direct\ncategory: lead_direct 0.82\n\nSRC"

async def test_header_category_show_trigger_false(): == "#lead_direct\nSRC"
async def test_header_show_category_false(): == "category: lead_direct 0.82\n\nSRC"
async def test_header_prefix_suffix_order(): prefix="P:", suffix=":S" -> "P:#lead_direct\ncategory: lead_direct 0.82\n\nSRC:S"
async def test_template_category_placeholder(): message_template="{category} {trigger}" -> "#lead_direct category: lead_direct 0.82"
async def test_template_category_empty_without_gate(): message_template="[{category}]", word="w" -> "[]"
async def test_header_unchanged_without_category(): # существующие тесты на word/prompt должны пройти без правок

def test_json_payload_category(): assert format_json_payload(msg, category="lead_direct")["category"] == "lead_direct"
def test_json_payload_category_none(): assert format_json_payload(msg)["category"] is None
def test_text_payload_unchanged(): # format_text_payload не меняется
```

- [ ] **Step 2: Запустить — падают**

Run: `.venv/bin/pytest tests/test_telegram_utils.py tests/test_webhook.py -q`
Expected: FAIL на новых тестах.

- [ ] **Step 3: Реализовать.** Хэштег-строка `#<name>` добавляется в начало тела как `f"{hashtag}\n{body}"` (или просто `hashtag`, если тело пустое), затем оборачивается `prefix`/`suffix`. В шаблонные значения добавить `"category"`. Существующие тесты `test_webhook.py`, сравнивающие весь dict, обновить на новый ключ `"category": None`.

- [ ] **Step 4: Тесты проходят**

Run: `.venv/bin/pytest tests/test_telegram_utils.py tests/test_webhook.py -q`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add src/telegram_utils.py src/webhook.py tests/test_telegram_utils.py tests/test_webhook.py
git commit -m "feat: category hashtag in forward header and webhook payload"
```

---

### Task 4: Подключение гейта в `process_message`, debounce и stats

**Files:**
- Modify: `src/app.py:113-353` (`_forward_messages`, `_debounce_message`, `process_message`, установка `categories.config` рядом с `prompts.config.update(config)` в `main` и `rescan_loop`)
- Modify: `src/stats.py:119-139` (`increment(..., used_category: bool = False)` → счётчик `forwarded_category`)
- Test: `tests/test_main_flow.py`, `tests/test_debounce.py`, `tests/test_stats.py`

**Interfaces:**
- Consumes: `match_categories`, `CategoryMatch` (Task 2); новые параметры шапки/webhook (Task 3); `Instance.categories`, `Instance.forward_message_show_category` (Task 1).
- Produces: `_forward_messages(..., used_category: CategoryMatch | None = None)`, `_debounce_message(..., used_category: CategoryMatch | None = None)`; `header_ctx["used_category"]`; `stats.increment(..., used_category=...)`.

- [ ] **Step 1: Падающие тесты** (в стиле существующих `test_main_flow.py`: `monkeypatch.setattr(app, "match_categories", fake)` и `app, "match_prompt"`)

```python
async def test_category_gate_forwards_with_hashtag():   # fake -> CategoryMatch(forward=True, category="lead_direct", probability=0.8)
    # client.send_message получил текст, начинающийся с "#lead_direct\ncategory: lead_direct 0.80"
async def test_category_gate_runs_before_prompts():     # fake forward=True -> match_prompt не вызывался
async def test_category_below_threshold_falls_to_prompts():  # forward=False -> match_prompt вызван
async def test_category_none_falls_to_prompts():        # fake -> None (ошибка API) -> match_prompt вызван
async def test_words_hit_skips_categories():            # words совпали -> match_categories не вызывался
async def test_no_text_no_category_call():              # raw_text="" -> match_categories не вызывался
async def test_instance_without_categories_never_calls(): # categories=None -> не вызывался
async def test_debounce_flush_header_has_hashtag():     # debounce_ms>0: после flush шапка с "#lead_direct", stats forwarded_category == 1
async def test_webhook_gets_category():                 # target_webhook json -> send_webhook(..., category="lead_direct")
def test_stats_forwarded_category(): # tests/test_stats.py
```

- [ ] **Step 2: Запустить — падают**

Run: `.venv/bin/pytest tests/test_main_flow.py tests/test_debounce.py tests/test_stats.py -q`
Expected: FAIL на новых тестах.

- [ ] **Step 3: Реализовать.** В `process_message`: после `find_word`, если не сработало и `inst.categories` — `match = await match_categories(inst.categories, message.raw_text, chat_name)`; при `match and match.forward` → `forward=True`, `used_category=match`; цикл `prompts` выполняется только если `forward` всё ещё `False`. `used_category` протащить в `_debounce_message`/`header_ctx`/`flush_cb`, `_forward_messages` (→ `get_forward_message_text(category=..., show_category=inst.forward_message_show_category)` и `webhook.send_webhook(..., category=used_category.category if used_category else None)`), во все вызовы `stats.increment`. Импорт `match_categories` в `app.py` как имя модуля-уровня, чтобы monkeypatch работал так же, как для `match_prompt`.

- [ ] **Step 4: Тесты проходят — весь набор**

Run: `.venv/bin/pytest -q`
Expected: PASS, ни один существующий тест не изменён по смыслу (кроме ожидаемого ключа `category` в webhook dict из Task 3).

- [ ] **Step 5: Коммит**

```bash
git add src/app.py src/stats.py tests/test_main_flow.py tests/test_debounce.py tests/test_stats.py
git commit -m "feat: run categories gate between words and prompts"
```

---

### Task 5: `ignore_bots`

**Files:**
- Modify: `src/app.py:497-502` (рядом с `ignore_user_ids` в обработчике новых сообщений)
- Test: `tests/test_main_flow.py` или `tests/test_cancel_on_owner_reply.py` (где уже собирается handler)

**Interfaces:**
- Consumes: `config.get("ignore_bots", False)`.

- [ ] **Step 1: Падающие тесты**

```python
async def test_ignore_bots_drops_bot_username():      # ignore_bots: true, sender.username="some_helper_bot" -> process_message не вызван
async def test_ignore_bots_case_insensitive():        # "SomeBot" -> не вызван
async def test_ignore_bots_off_by_default():          # ключа нет, username="x_bot" -> вызван
async def test_ignore_bots_does_not_cancel_debounce(): # debounce_ms>0, cancel_on_owner_reply true -> debounce_manager.cancel не вызван
async def test_ignore_bots_human_ending_in_bot():     # "abbot" -> отброшен (правило по username; фиксируем поведение)
```

- [ ] **Step 2: Запустить — падают**

Run: `.venv/bin/pytest tests/test_main_flow.py -q -k ignore_bots`
Expected: FAIL.

- [ ] **Step 3: Реализовать** проверку сразу после блока `ignore_user_ids`, `logger.debug("Ignoring bot @%s", username)` и `return`.

- [ ] **Step 4: Тесты проходят**

Run: `.venv/bin/pytest -q`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add src/app.py tests/
git commit -m "feat: global ignore_bots option"
```

---

### Task 6: Скрипт прогона `src/run_categories_eval.py`

**Files:**
- Create: `src/run_categories_eval.py`
- Test: `tests/test_run_categories_eval.py`
- Modify: `README.md` (раздел про evals — команда запуска), `AGENTS.md`/`CLAUDE.md` (структура: новые `categories.py`, `run_categories_eval.py`)

**Interfaces:**
- Consumes: `match_categories(..., client=...)`, `build_client()` (Task 2), `load_config`, `load_instances`, `CategoriesGate` (Task 1).
- Produces (CLI): `python -m src.run_categories_eval (--instance NAME | --categories FILE.yml) --export PATH [--export PATH ...] --gold FILE.json [--out DIR=data/evals] [--concurrency 4] [--label NAME]`.
- Формат выгрузки (`telegram-download-chat` JSON): список сообщений с полями `id: int`, `peer_id: dict` (`channel_id`), `message: str`, `user_display_name: str`; сообщения с пустым `message` пропускаются. Username в выгрузке нет.
- Формат gold: `[{"chat_id": int, "message_id": int, "label": str, "is_bot": bool}]`; `label` — одна из категорий гейта; сообщения с `is_bot: true` пропускаются (имитация `ignore_bots`); сообщения без gold-записи пропускаются и считаются в отчёте как `unlabeled`.
- Функции для тестов:
  ```python
  def load_export(paths: list[str]) -> list[dict]          # {"chat_id","message_id","text"}
  def compute_metrics(rows: list[dict], gate: CategoriesGate) -> dict
      # rows: {"label": str, "category": str | None, "forward": bool}
      # -> {"confusion": {label: {predicted: n}}, "forwarded_by_label": {label: (n_fwd, n_total)},
      #     "forward_precision": float, "forward_recall": float, "misses": [...], "false_forwards": [...]}
  def acceptance(metrics: dict) -> dict[str, bool]
      # all lead_direct & reco_letters forwarded; lead_weak recall >= 0.7;
      # competitor forwarded <= 3; other forwarded share <= 0.10
  ```

- [ ] **Step 1: Падающие тесты** на синтетических данных (выдуманные тексты, без реальных):

```python
def test_load_export_skips_empty(tmp_path): # 3 сообщения, одно с message="" -> 2 строки с chat_id из peer_id.channel_id
def test_compute_metrics_counts(): # 2 lead_direct (1 forwarded), 2 other (1 forwarded) -> forward_recall==0.5, forwarded_by_label["other"]==(1,2)
def test_acceptance_thresholds(): # lead_weak 7/10 -> True, 6/10 -> False; competitor 4 forwarded -> False; other 10% -> True, 11% -> False
async def test_run_uses_fake_client_and_writes_report(tmp_path): # fake client, --out tmp_path -> json-отчёт с "baseline" метками и метриками
```

- [ ] **Step 2: Запустить — падают**

Run: `.venv/bin/pytest tests/test_run_categories_eval.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Реализовать.** Параллельность через `asyncio.Semaphore(concurrency)`, один общий клиент. Вывод в stdout: таблица forwarded/total по меткам, precision/recall, результат `acceptance` построчно, список промахов (`chat_id:message_id label→predicted p_forward` — без текста в stdout, текст только в файле отчёта). Файл `<out>/categories-eval-<label or timestamp>.json`: конфиг гейта, `response.model`, метрики, поштучные строки (с текстом).

- [ ] **Step 4: Тесты проходят**

Run: `.venv/bin/pytest -q`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add src/run_categories_eval.py tests/test_run_categories_eval.py README.md AGENTS.md CLAUDE.md
git commit -m "feat: categories eval runner over telegram-download-chat exports"
```

---

### Task 7: Эталонная разметка, базовый прогон и подбор (без коммитов в git)

Все артефакты — в `data/` (вне git). Источники и черновик инстанса — `data/typesafe-categories-sensitive/README.md`.

- [ ] **Step 1: Разметка.** Субагентом разметить все текстовые сообщения выгрузки в `data/evals/leads-gold.json` по формату Task 6, по критериям и признакам из заметки, указанной в sensitive README. `is_bot: true` — для сообщений ботов чата и постов канала. Сверить итоговые количества по меткам со сводкой в sensitive README (допуск ±30% по каждой метке, все перечисленные там прямые лиды — `lead_direct`).

- [ ] **Step 2: Базовый прогон** с черновиком категорий из sensitive README (сохранить его в `data/typesafe-categories-sensitive/categories-v1.yml`):

Run: `.venv/bin/python -m src.run_categories_eval --categories data/typesafe-categories-sensitive/categories-v1.yml --export "<пути из sensitive README>" --gold data/evals/leads-gold.json --label baseline`
Expected: отчёт `data/evals/categories-eval-baseline.json`; записать метрики и `model` в sensitive README (раздел «Прогоны»).

- [ ] **Step 3: Подбор** (не более 5 итераций): правка `instructions`/описаний (допустимо на английском)/`threshold` → `categories-vN.yml` → прогон с `--label vN`. Остановиться, когда `acceptance` весь `True`. Если после 5 итераций не достигнут — остановиться и показать пользователю отчёт, выкат не делать.

- [ ] **Step 4: Зафиксировать** финальную версию, версию модели (`jev-1.x.y` вместо алиаса, если подбор шёл на ней) и метрики baseline vs final в sensitive README.

---

### Task 8: Выкат в тестовый контейнер и отчёт

- [ ] **Step 1: target_chat.** Проверить реальный id группы из sensitive README через Telethon (`get_entity`) тем же session-файлом, что использует бот, и формат по аналогии с существующими инстансами в `data/config.yml`.

- [ ] **Step 2: Конфиг.** В `data/config.yml` добавить `ignore_bots: true` и инстанс из финальной версии категорий (`words: []`, `folders` из sensitive README). Целевую группу задать через `target_entity`, а не `target_chat`: `handle_reaction` сопоставляет реакции 👍/👎 только с инстансами, у которых есть `target_entity`. `typesafe_api_key` — если в контейнере нет env `TYPESAFE_API_KEY`.

- [ ] **Step 3: Валидация конфига**

Run: `.venv/bin/python -c "from src.config import load_config, load_instances; print([i.name for i in load_instances(load_config())])"`
Expected: список инстансов включает новый, без исключений.

- [ ] **Step 4: Перезапуск тестового контейнера** (rebuild из-за новой зависимости):

Run: `cd ~/projects/docker/telegram-resender && docker compose up -d --build && docker compose logs --tail 100`
Expected: старт без трейсбеков, в логах инстанс загружен и найдены chat_ids папки. Прод не трогать.

- [ ] **Step 5: Живая проверка.** Дождаться (или найти в логах на `debug`) хотя бы одного вызова гейта; если в target пришла пересылка — шапка начинается с хэштега. Что проверить не удалось — записать.

- [ ] **Step 6: Отчёт** в сессию `telegram-lead-filter-setup` (SendMessage): коммиты ветки, список категорий, baseline vs final метрики, статус контейнера. Без текстов сообщений.

- [ ] **Step 7: Финализация** по `do finalize`: отметить пункт в `docs/TODO.md` (в коммите с последним кодом или `task:`), PR с чек-листом ручной проверки, ждать ревью пользователя.
