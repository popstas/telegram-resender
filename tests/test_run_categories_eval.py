import json
from types import SimpleNamespace

import pytest
import yaml

from src import categories
from src.config import CategoriesGate
from src.run_categories_eval import acceptance, compute_metrics, load_export, run_eval

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
    base = {"lead_direct": (2, 2), "reco_letters": (1, 1)}
    ok = acceptance(_acc_metrics(**base, lead_weak=(7, 10)))
    assert ok["lead_weak_recall"] is True
    assert all(ok.values())
    assert acceptance(_acc_metrics(**base, lead_weak=(6, 10)))["lead_weak_recall"] is False
    assert acceptance(_acc_metrics(**base, competitor=(4, 9)))["competitor_forwarded"] is False
    assert acceptance(_acc_metrics(**base, competitor=(3, 9)))["competitor_forwarded"] is True
    assert acceptance(_acc_metrics(**base, other=(10, 100)))["other_share"] is True
    assert acceptance(_acc_metrics(**base, other=(11, 100)))["other_share"] is False
    assert (
        acceptance(_acc_metrics(lead_direct=(1, 2), reco_letters=(1, 1)))[
            "lead_direct_all"
        ]
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
            "--categories", str(cat_path),
            "--export", str(export),
            "--gold", str(gold_path),
            "--out", str(out),
            "--label", "baseline",
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
