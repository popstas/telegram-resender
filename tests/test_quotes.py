"""Blockquote entities are marked with "> " for the categories gate and target_file."""

import json
from types import SimpleNamespace

import pytest
from telethon import types

from src import app, config as config_module, run_categories_eval, target_file
from src import telegram_utils as tgu
from src.config import TargetFile
from src.telegram_utils import mark_quotes, quoted_text


def u16(s: str) -> int:
    """Length of ``s`` in UTF-16 code units, the way Telegram counts offsets."""
    return len(s.encode("utf-16-le")) // 2


def quote(before: str, quoted: str, collapsed: bool | None = None):
    """Entity quoting ``quoted`` that follows ``before`` in the text."""
    return types.MessageEntityBlockquote(
        offset=u16(before), length=u16(quoted), collapsed=collapsed
    )


TAIL = "\nTask: Website / publish\n\nLink: https://example.com/task/1"


def test_latin_quote_after_reply():
    head = "Ann Smith:\nok\n"
    q = "You closed it without publishing.\nPlease cancel such tasks.\n"
    text = head + q + TAIL
    assert mark_quotes(text, [quote(head, q)]) == (
        "Ann Smith:\nok\n"
        "> You closed it without publishing.\n"
        "> Please cancel such tasks.\n" + TAIL
    )


def test_cyrillic_quote_keeps_blank_line_before_task():
    head = "Иванова Анна:\nЗдесь у нас правило 1500, работаем по предоплате\n"
    q = "Почему мы опубликовали до оплаты?\n"
    text = head + q + "\nЗадача: Публикация\n\nСсылка: https://example.com/c/2"
    assert mark_quotes(text, [quote(head, q)]) == (
        head + "> Почему мы опубликовали до оплаты?\n"
        "\nЗадача: Публикация\n\nСсылка: https://example.com/c/2"
    )


def test_emoji_before_and_inside_quote_uses_utf16_offsets():
    head = "Пётр 🙂🚀:\nготово 👍\n"
    q = "Сделай 🔥 кнопку\nи статус 🎉\n"
    text = head + q + "\nЗадача: тест 😀"
    ent = quote(head, q)
    assert ent.offset != len(head)  # emoji outside the BMP take two units
    assert mark_quotes(text, [ent]) == (
        head + "> Сделай 🔥 кнопку\n> и статус 🎉\n\nЗадача: тест 😀"
    )


def test_expandable_quote_and_blank_line_inside():
    head = "Анна:\nсогласна\n"
    q = "Первый абзац.\n\nВторой абзац.\n"
    text = head + q + TAIL
    assert mark_quotes(text, [quote(head, q, collapsed=True)]) == (
        head + "> Первый абзац.\n>\n> Второй абзац.\n" + TAIL
    )


def test_several_quotes_and_other_entities_untouched():
    a = "Анна:\nответ один\n"
    q1 = "цитата один\n"
    b = "ответ два\n"
    q2 = "цитата два\n"
    text = a + q1 + b + q2 + TAIL
    url = types.MessageEntityUrl(offset=u16(text) - 25, length=25)
    bold = types.MessageEntityBold(offset=0, length=4)
    ents = [quote(a + q1 + b, q2), url, quote(a, q1), bold]
    assert mark_quotes(text, ents) == (
        a + "> цитата один\n" + b + "> цитата два\n" + TAIL
    )


def test_quote_mid_line_starts_on_new_line():
    head = "see:"
    q = "quoted"
    text = head + q + "tail"
    assert mark_quotes(text, [quote(head, q)]) == "see:\n> quoted\ntail"


def test_quote_at_start_and_end_of_text():
    q = "only quote"
    assert mark_quotes(q, [quote("", q)]) == "> only quote"


def test_overlapping_and_out_of_range_quotes_are_merged_and_clamped():
    text = "abc\nquoted line\n"
    ents = [
        types.MessageEntityBlockquote(offset=4, length=5),
        types.MessageEntityBlockquote(offset=6, length=100),
    ]
    assert mark_quotes(text, ents) == "abc\n> quoted line\n"


def test_dict_entities_from_json_export():
    head = "Анна:\nок\n"
    q = "замечание\n"
    ents = [{"_": "MessageEntityBlockquote", "offset": u16(head), "length": u16(q)}]
    assert mark_quotes(head + q + TAIL, ents) == head + "> замечание\n" + TAIL


@pytest.mark.parametrize(
    "entities",
    [None, [], [types.MessageEntityBold(offset=0, length=3)], "not a list"],
)
def test_text_without_quotes_is_unchanged(entities):
    text = "Анна:\nобычный текст 🙂\n" + TAIL
    assert mark_quotes(text, entities) is text


def test_empty_text():
    assert mark_quotes("", [quote("", "x")]) == ""
    assert mark_quotes(None, [quote("", "x")]) is None


def test_quoted_text_prefers_message_and_falls_back_to_raw_text():
    head, q = "Анна:\nок\n", "замечание\n"
    msg = SimpleNamespace(message=head + q, raw_text=head + q, entities=[quote(head, q)])
    assert quoted_text(msg) == head + "> замечание\n"
    assert quoted_text(SimpleNamespace(raw_text="plain")) == "plain"
    assert quoted_text(SimpleNamespace(message=None, raw_text=None)) is None


# --- target_file ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_target_file_writes_marked_quote(tmp_path, monkeypatch):
    async def fake_source(message):
        return "Forwarded from: private @bot"

    async def fake_full_sender(message):
        return None

    monkeypatch.setattr(tgu, "get_message_source", fake_source)
    monkeypatch.setattr(tgu, "_get_full_sender", fake_full_sender)
    head, q = "Анна:\nок\n", "Переведи в отменённые 🙏\n"
    text = head + q + "\nЗадача: тест"
    msg = SimpleNamespace(
        id=5,
        message=text,
        raw_text=text,
        entities=[quote(head, q, collapsed=True)],
        media=None,
        date=None,
        chat_id=1,
        peer_id=SimpleNamespace(user_id=1),
        sender=SimpleNamespace(username="bot"),
        chat=None,
    )
    path = await target_file.write_batch(TargetFile(dir=str(tmp_path)), [msg], msg)
    with open(path, encoding="utf-8") as f:
        content = f.read()
    assert content == (
        "Forwarded from: private @bot\n\n"
        "Анна:\nок\n> Переведи в отменённые 🙏\n\nЗадача: тест\n"
    )


# --- categories gate -------------------------------------------------------------


class _Msg:
    def __init__(self, text, entities=None, reply_to=None, parent=None):
        self.peer_id = SimpleNamespace(channel_id=1)
        self.id = 3
        self.message = text
        self.raw_text = text
        self.entities = entities
        self.reply_to = reply_to
        self._parent = parent
        self.forwarded = []

    async def forward_to(self, target):
        self.forwarded.append(target)

    async def get_reply_message(self):
        return self._parent


def _setup(monkeypatch, tmp_path, *, words=None, reply_context=False):
    from src import prompts
    from src import stats as stats_module

    class DummyClient:
        async def send_message(self, *a, **k):
            return None

    app.client = DummyClient()
    tgu.client = app.client
    app.stats = stats_module.StatsTracker(
        str(tmp_path / "stats.json"), flush_interval=0
    )

    async def fake_chat_name(v, safe=False):
        return "chat"

    async def fake_source(msg):
        return "src"

    monkeypatch.setattr(app, "get_chat_name", fake_chat_name)
    monkeypatch.setattr(tgu, "get_chat_name", fake_chat_name)
    monkeypatch.setattr(tgu, "get_message_source", fake_source)

    cat_calls, prompt_calls = [], []

    async def fake_categories(gate, text, chat_name=None, **kw):
        cat_calls.append((text, kw.get("reply_to")))
        return None

    async def fake_prompt(prompt, text, inst_name, chat_name):
        prompt_calls.append(text)
        return prompts.MatchPromptResult(score=0, reasoning="", quote="", trace_id=None)

    monkeypatch.setattr(app, "match_categories", fake_categories)
    monkeypatch.setattr(app, "match_prompt", fake_prompt)
    inst = app.Instance(
        name="c",
        words=words or [],
        prompts=[prompts.Prompt(name="p", prompt="p", threshold=4)],
        target_chat=1,
        categories=config_module.CategoriesGate(
            instructions="x", forward={"request": None}, reply_context=reply_context
        ),
    )
    return inst, cat_calls, prompt_calls


@pytest.mark.asyncio
async def test_gate_gets_marked_text_words_and_prompts_get_plain(
    monkeypatch, tmp_path
):
    inst, cat_calls, prompt_calls = _setup(monkeypatch, tmp_path, words=["нет-такого"])
    head, q = "Анна:\nок\n", "Смени статус\n"
    text = head + q + TAIL
    msg = _Msg(text, [quote(head, q)])
    await app.process_message(inst, SimpleNamespace(message=msg, chat_id=1))
    assert cat_calls == [(head + "> Смени статус\n" + TAIL, None)]
    assert prompt_calls == [text]


@pytest.mark.asyncio
async def test_gate_text_without_quotes_is_unchanged(monkeypatch, tmp_path):
    inst, cat_calls, _ = _setup(monkeypatch, tmp_path)
    text = "Анна:\nпросто текст\n" + TAIL
    msg = _Msg(text, [types.MessageEntityUrl(offset=u16(text) - 25, length=25)])
    await app.process_message(inst, SimpleNamespace(message=msg, chat_id=1))
    assert cat_calls == [(text, None)]


@pytest.mark.asyncio
async def test_reply_context_parent_quotes_are_marked(monkeypatch, tmp_path):
    inst, cat_calls, _ = _setup(monkeypatch, tmp_path, reply_context=True)
    head, q = "Анна:\nок\n", "старый вопрос\n"
    parent = _Msg(head + q, [quote(head, q)])
    msg = _Msg("мне тоже", reply_to=SimpleNamespace(reply_to_msg_id=2), parent=parent)
    await app.process_message(inst, SimpleNamespace(message=msg, chat_id=1))
    assert cat_calls == [("мне тоже", head + "> старый вопрос\n")]


# --- eval export -------------------------------------------------------------------


def test_load_export_marks_quotes(tmp_path):
    head, q = "Анна:\nок\n", "замечание 🙂\n"
    data = [
        {
            "id": 1,
            "peer_id": {"user_id": 10},
            "message": head + q + TAIL,
            "entities": [
                {"_": "MessageEntityBlockquote", "offset": u16(head), "length": u16(q)}
            ],
        },
        {"id": 2, "peer_id": {"user_id": 10}, "message": "plain"},
    ]
    path = tmp_path / "messages.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    rows = run_categories_eval.load_export([str(path)])
    assert rows[0]["text"] == head + "> замечание 🙂\n" + TAIL
    assert rows[1]["text"] == "plain"
