import json
from types import SimpleNamespace

import pytest
import yaml

from src import categories
from src.config import CategoriesGate
from src.run_categories_eval import (
    acceptance,
    compute_metrics,
    load_export,
    load_rows,
    run_eval,
)

GATE = CategoriesGate(
    instructions="q",
    forward={"lead_direct": "a", "lead_weak": "b"},
    reject={"competitor": "c", "other": "d"},
)


def _rows(label, fwd, total, category=None):
    return [
        {
            "label": label,
            "category": category or label,
            "forward": i < fwd,
            "chat_id": 1,
            "message_id": i,
        }
        for i in range(total)
    ]


def test_load_export_skips_empty(tmp_path):
    path = tmp_path / "messages.json"
    msgs = [
        {"id": 1, "peer_id": {"_": "PeerChannel", "channel_id": 77}, "message": "a"},
        {"id": 2, "peer_id": {"_": "PeerChannel", "channel_id": 77}, "message": ""},
        {"id": 3, "peer_id": {"_": "PeerChannel", "channel_id": 77}, "message": "c"},
    ]
    path.write_text(json.dumps(msgs), encoding="utf-8")
    rows = load_export([str(path)])
    assert rows == [
        {"chat_id": 77, "message_id": 1, "text": "a"},
        {"chat_id": 77, "message_id": 3, "text": "c"},
    ]


def test_load_export_resolves_reply_parent(tmp_path):
    peer = {"_": "PeerChannel", "channel_id": 77}

    def reply(msg_id, **kw):
        return {"_": "MessageReplyHeader", "reply_to_msg_id": msg_id, **kw}

    msgs = [
        {"id": 1, "peer_id": peer, "message": "ищу PR"},
        {"id": 2, "peer_id": peer, "message": "мне тоже", "reply_to": reply(1)},
        {"id": 3, "peer_id": peer, "message": "нет родителя", "reply_to": reply(99)},
        {
            "id": 4,
            "peer_id": peer,
            "message": "в топике",
            "reply_to": reply(1, forum_topic=True, reply_to_top_id=None),
        },
    ]
    path = tmp_path / "messages.json"
    path.write_text(json.dumps(msgs), encoding="utf-8")
    rows = {r["message_id"]: r for r in load_export([str(path)])}
    assert rows[2]["reply_to"] == "ищу PR"
    assert "reply_to" not in rows[1]
    assert "reply_to" not in rows[3]
    assert "reply_to" not in rows[4]
    assert all("reply_to_msg_id" not in r for r in rows.values())


@pytest.mark.asyncio
@pytest.mark.parametrize("reply_context", [True, False])
async def test_run_eval_passes_reply_to_when_enabled(
    tmp_path, monkeypatch, reply_context
):
    monkeypatch.setattr(categories, "config", {})
    seen = []

    async def fake(gate, text, chat_name=None, *, reply_to=None, client=None):
        seen.append(reply_to)
        return None

    monkeypatch.setattr(categories, "match_categories", fake)
    peer = {"_": "PeerChannel", "channel_id": 5}
    msgs = [
        {"id": 1, "peer_id": peer, "message": "ищу PR"},
        {
            "id": 2,
            "peer_id": peer,
            "message": "мне тоже",
            "reply_to": {"reply_to_msg_id": 1},
        },
    ]
    export = tmp_path / "m.json"
    export.write_text(json.dumps(msgs), encoding="utf-8")
    gold_path = tmp_path / "gold.json"
    gold_path.write_text(
        json.dumps([{"chat_id": 5, "message_id": 2, "label": "other"}]),
        encoding="utf-8",
    )
    cat_path = tmp_path / "cat.yml"
    cat_path.write_text(
        yaml.safe_dump(
            {
                "instructions": "q",
                "forward": {"a": None},
                "reject": {"other": None},
                "reply_context": reply_context,
            }
        ),
        encoding="utf-8",
    )
    await run_eval(
        [
            "--categories",
            str(cat_path),
            "--export",
            str(export),
            "--gold",
            str(gold_path),
            "--out",
            str(tmp_path / "out"),
        ],
        client=object(),
    )
    assert seen == (["ищу PR"] if reply_context else [None])


def test_compute_metrics_counts():
    rows = _rows("lead_direct", 1, 2) + _rows("other", 1, 2)
    m = compute_metrics(rows, GATE)
    assert m["forward_recall"] == 0.5
    assert m["forward_precision"] == 0.5
    assert m["forwarded_by_label"]["other"] == (1, 2)
    assert len(m["misses"]) == 1
    assert len(m["false_forwards"]) == 1
    assert m["confusion"]["other"] == {"other": 2}


def _acc_metrics(**by_label):
    return {"forwarded_by_label": by_label}


def test_acceptance_thresholds():
    base = {
        "lead_direct": (2, 2),
        "reco_letters": (1, 1),
        "lead_weak": (7, 10),
        "other": (1, 100),
        "competitor": (0, 5),
    }
    ok = acceptance(_acc_metrics(**base))
    assert ok["lead_weak_recall"] is True
    assert all(ok.values())
    assert (
        acceptance(_acc_metrics(**{**base, "lead_weak": (6, 10)}))["lead_weak_recall"]
        is False
    )
    assert (
        acceptance(_acc_metrics(**{**base, "competitor": (4, 9)}))[
            "competitor_forwarded"
        ]
        is False
    )
    assert (
        acceptance(_acc_metrics(**{**base, "competitor": (3, 9)}))[
            "competitor_forwarded"
        ]
        is True
    )
    assert (
        acceptance(_acc_metrics(**{**base, "other": (10, 100)}))["other_share"] is True
    )
    assert (
        acceptance(_acc_metrics(**{**base, "other": (11, 100)}))["other_share"] is False
    )
    assert (
        acceptance(_acc_metrics(**{**base, "lead_direct": (1, 2)}))["lead_direct_all"]
        is False
    )


class FakeClient:
    def __init__(self):
        self.calls = 0

    async def system_one(self, state, questions, *, model=None, **kwargs):
        self.calls += 1
        if "boom" in state["message"]:
            raise RuntimeError("api down")
        if "offer" in state["message"]:
            probs = {"lead_direct": 0.9, "lead_weak": 0, "competitor": 0, "other": 0.1}
        else:
            probs = {"lead_direct": 0, "lead_weak": 0, "competitor": 0, "other": 1}
        return SimpleNamespace(
            choices={"category": SimpleNamespace(probabilities=probs)},
            model="jev-test",
            usage=SimpleNamespace(input_tokens=5),
        )


@pytest.mark.asyncio
async def test_run_uses_fake_client_and_writes_report(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(categories, "config", {})
    peer = {"_": "PeerChannel", "channel_id": 5}
    msgs = [
        {"id": 1, "peer_id": peer, "message": "an offer here"},
        {"id": 2, "peer_id": peer, "message": "plain chatter"},
        {"id": 3, "peer_id": peer, "message": "robot offer"},
        {"id": 4, "peer_id": peer, "message": "unlabeled text"},
        {"id": 5, "peer_id": peer, "message": "boom"},
        {"id": 6, "peer_id": peer, "message": ""},
    ]
    export = tmp_path / "m.json"
    export.write_text(json.dumps(msgs), encoding="utf-8")
    gold = [
        {"chat_id": 5, "message_id": 1, "label": "lead_direct", "is_bot": False},
        {"chat_id": 5, "message_id": 2, "label": "other", "is_bot": False, "note": "x"},
        {"chat_id": 5, "message_id": 3, "label": "other", "is_bot": True},
        {"chat_id": 5, "message_id": 5, "label": "lead_direct", "is_bot": False},
    ]
    gold_path = tmp_path / "gold.json"
    gold_path.write_text(json.dumps(gold), encoding="utf-8")
    cat_path = tmp_path / "cat.yml"
    cat_path.write_text(
        yaml.safe_dump(
            {
                "instructions": "q",
                "forward": {"lead_direct": "a", "lead_weak": "b"},
                "reject": {"competitor": "c", "other": "d"},
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "out"
    client = FakeClient()
    report_path = await run_eval(
        [
            "--categories",
            str(cat_path),
            "--export",
            str(export),
            "--gold",
            str(gold_path),
            "--out",
            str(out),
            "--label",
            "baseline",
        ],
        client=client,
    )
    assert report_path == out / "categories-eval-baseline.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["label"] == "baseline"
    assert report["response_model"] == "jev-test"
    assert report["gate"]["forward"] == {"lead_direct": "a", "lead_weak": "b"}
    assert report["counts"] == {
        "evaluated": 2,
        "errors": 1,
        "unlabeled": 1,
        "skipped_bots": 1,
    }
    assert report["metrics"]["forwarded_by_label"]["lead_direct"] == [1, 1]
    assert report["metrics"]["forward_recall"] == 1.0
    assert client.calls == 3  # bot and unlabeled are not sent
    assert any(r.get("text") == "an offer here" for r in report["rows"])
    printed = capsys.readouterr().out
    assert "an offer here" not in printed
    assert "errors" in printed


def test_acceptance_empty_label_fails_and_errors_fail():
    base = {
        "lead_direct": (2, 2),
        "reco_letters": (1, 1),
        "lead_weak": (7, 10),
        "other": (1, 100),
    }
    assert all(acceptance({"forwarded_by_label": base}).values())
    for name in base:
        rest = {k: v for k, v in base.items() if k != name}
        assert not all(acceptance({"forwarded_by_label": rest}).values())
        assert acceptance({"forwarded_by_label": {**base, name: (0, 0)}}) != {}
    assert acceptance({"forwarded_by_label": base, "errors": 2})["no_errors"] is False
    # competitor absent is fine
    assert acceptance({"forwarded_by_label": base})["competitor_forwarded"] is True


def _setup(tmp_path, msgs, gold):
    peer = {"_": "PeerChannel", "channel_id": 5}
    export = tmp_path / "m.json"
    export.write_text(
        json.dumps([{"id": i, "peer_id": peer, "message": m} for i, m in msgs]),
        encoding="utf-8",
    )
    gold_path = tmp_path / "gold.json"
    gold_path.write_text(
        json.dumps(
            [
                {"chat_id": 5, "message_id": i, "label": lb, "is_bot": False}
                for i, lb in gold
            ]
        ),
        encoding="utf-8",
    )
    cat = tmp_path / "cat.yml"
    cat.write_text(
        yaml.safe_dump(
            {
                "instructions": "q",
                "forward": {"lead_direct": "a"},
                "reject": {"other": "d"},
            }
        ),
        encoding="utf-8",
    )
    return [
        "--categories",
        str(cat),
        "--export",
        str(export),
        "--gold",
        str(gold_path),
        "--out",
        str(tmp_path / "out"),
        "--label",
        "t",
    ]


@pytest.mark.asyncio
async def test_all_errors_fail_no_errors_and_ids_printed(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(categories, "config", {})
    argv = _setup(
        tmp_path, [(1, "boom"), (2, "boom")], [(1, "lead_direct"), (2, "other")]
    )
    path = await run_eval(argv, client=FakeClient())
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["acceptance"]["no_errors"] is False
    assert report["acceptance"]["lead_direct_all"] is False
    assert report["metrics"]["error_ids"] == [
        {"chat_id": 5, "message_id": 1},
        {"chat_id": 5, "message_id": 2},
    ]
    assert "5:1" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_unknown_gold_label_fails_fast(tmp_path, monkeypatch):
    monkeypatch.setattr(categories, "config", {})
    argv = _setup(
        tmp_path, [(1, "x"), (2, "y")], [(1, "typo_label"), (2, "typo_label")]
    )
    client = FakeClient()
    with pytest.raises(SystemExit) as exc:
        await run_eval(argv, client=client)
    assert "typo_label" in str(exc.value) and "2 rows" in str(exc.value)
    assert client.calls == 0


class RecordingClient(FakeClient):
    def __init__(self):
        super().__init__()
        self.states = []

    async def system_one(self, state, questions, *, model=None, **kwargs):
        self.states.append(state)
        return await super().system_one(state, questions, model=model, **kwargs)


def _rows_argv(tmp_path, rows):
    rows_path = tmp_path / "rows.jsonl"
    rows_path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n\n",
        encoding="utf-8",
    )
    cat = tmp_path / "cat.yml"
    cat.write_text(
        yaml.safe_dump(
            {
                "instructions": "q",
                "forward": {"lead_direct": "a"},
                "reject": {"other": "d"},
            }
        ),
        encoding="utf-8",
    )
    return [
        "--categories",
        str(cat),
        "--rows",
        str(rows_path),
        "--out",
        str(tmp_path / "out"),
        "--label",
        "r",
    ]


@pytest.mark.asyncio
async def test_rows_input_passes_owner_previous(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(categories, "config", {})
    argv = _rows_argv(
        tmp_path,
        [
            {
                "id": "a1",
                "text": "an offer here",
                "owner_previous": ["earlier owner note"],
                "label": "forward",
            },
            {
                "id": "b2",
                "text": "plain chatter",
                "owner_previous": [],
                "label": "reject",
            },
        ],
    )
    client = RecordingClient()
    path = await run_eval(argv, client=client)
    by_text = {s["message"]: s for s in client.states}
    assert by_text["an offer here"]["owner_previous"] == ["earlier owner note"]
    assert "owner_previous" not in by_text["plain chatter"]
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["counts"]["evaluated"] == 2
    assert report["metrics"]["forwarded_by_label"] == {
        "forward": [1, 1],
        "reject": [0, 1],
    }
    assert report["metrics"]["forward_recall"] == 1.0
    assert report["metrics"]["forward_precision"] == 1.0
    assert report["acceptance"] == {
        "no_errors": True,
        "forward_recall": True,
        "reject_cut_60": True,
    }
    assert {r["id"] for r in report["rows"]} == {"a1", "b2"}
    assert "an offer here" not in capsys.readouterr().out


@pytest.mark.asyncio
async def test_rows_acceptance_fails_and_reports_ids(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(categories, "config", {})
    argv = _rows_argv(
        tmp_path,
        [
            {"id": "m1", "text": "plain one", "label": "forward"},
            {"id": "m2", "text": "plain two", "label": "lead_direct"},
            {"id": "f1", "text": "offer one", "label": "reject"},
            {"id": "f2", "text": "offer two", "label": "other"},
            {"id": "e1", "text": "boom", "label": "forward"},
        ],
    )
    path = await run_eval(argv, client=FakeClient())
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["acceptance"] == {
        "no_errors": False,
        "forward_recall": False,  # two misses
        "reject_cut_60": False,  # 0 of 2 rejects cut
    }
    assert [m["id"] for m in report["metrics"]["misses"]] == ["m1", "m2"]
    assert [m["id"] for m in report["metrics"]["false_forwards"]] == ["f1", "f2"]
    assert report["metrics"]["error_ids"] == [{"id": "e1"}]
    out = capsys.readouterr().out
    assert "m1" in out and "f2" in out and "e1" in out


@pytest.mark.asyncio
async def test_rows_unknown_label_fails_fast(tmp_path, monkeypatch):
    monkeypatch.setattr(categories, "config", {})
    argv = _rows_argv(tmp_path, [{"id": "x", "text": "t", "label": "typo"}])
    client = FakeClient()
    with pytest.raises(SystemExit) as exc:
        await run_eval(argv, client=client)
    assert "typo" in str(exc.value)
    assert client.calls == 0


@pytest.mark.asyncio
async def test_rows_and_export_are_exclusive(tmp_path, monkeypatch):
    monkeypatch.setattr(categories, "config", {})
    argv = _rows_argv(tmp_path, [{"id": "x", "text": "t", "label": "reject"}])
    with pytest.raises(SystemExit):
        await run_eval(
            [*argv, "--export", "e.json", "--gold", "g.json"], client=object()
        )
    with pytest.raises(SystemExit):
        await run_eval([*argv, "--gold", "g.json"], client=object())


@pytest.mark.asyncio
async def test_export_mode_requires_export_and_gold(tmp_path, monkeypatch):
    monkeypatch.setattr(categories, "config", {})
    cat = tmp_path / "cat.yml"
    cat.write_text(
        yaml.safe_dump(
            {"instructions": "q", "forward": {"a": None}, "reject": {"b": None}}
        ),
        encoding="utf-8",
    )
    for extra in ([], ["--export", "e.json"], ["--gold", "g.json"]):
        with pytest.raises(SystemExit):
            await run_eval(["--categories", str(cat), *extra], client=object())


@pytest.mark.asyncio
async def test_rows_chat_is_passed_as_chat_name(tmp_path, monkeypatch):
    monkeypatch.setattr(categories, "config", {})
    argv = _rows_argv(
        tmp_path,
        [
            {"id": "c1", "text": "with chat", "chat": "Acme DM", "label": "forward"},
            {"id": "c2", "text": "no chat", "label": "reject"},
        ],
    )
    client = RecordingClient()
    await run_eval(argv, client=client)
    by_text = {s["message"]: s for s in client.states}
    assert by_text["with chat"]["chat"] == "Acme DM"
    assert "chat" not in by_text["no chat"]


@pytest.mark.parametrize(
    "line, fragment",
    [
        ('{"id": "a", "text": "t", "label": "forward"', "rows.jsonl:2"),
        ('{"id": "a", "text": 5, "label": "forward"}', "rows.jsonl:2: text"),
        ('{"id": "a", "text": "t", "label": 1}', "rows.jsonl:2: label"),
        (
            '{"id": "a", "text": "t", "label": "forward", "owner_previous": "x"}',
            "rows.jsonl:2: owner_previous",
        ),
        (
            '{"id": "a", "text": "t", "label": "forward", "owner_previous": [1]}',
            "rows.jsonl:2: owner_previous",
        ),
        (
            '{"id": "a", "text": "t", "label": "forward", "chat": 7}',
            "rows.jsonl:2: chat",
        ),
        ("[1, 2]", "rows.jsonl:2"),
    ],
)
def test_load_rows_rejects_bad_rows(tmp_path, line, fragment):
    path = tmp_path / "rows.jsonl"
    path.write_text(
        '{"id": "ok", "text": "t", "label": "reject"}\n' + line + "\n",
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as exc:
        load_rows(str(path))
    assert str(path).replace("rows.jsonl", "") in str(exc.value)
    assert fragment in str(exc.value)
