import logging
from types import SimpleNamespace

import pytest

import src.categories as categories
from src.categories import evaluate_probabilities, match_categories
from src.config import CategoriesGate

GATE = CategoriesGate(
    instructions="q",
    forward={"lead_direct": "a", "lead_weak": "b"},
    reject={"competitor": "c", "other": "d"},
    threshold=0.5,
)


class FakeClient:
    def __init__(self, probs=None, error=None):
        self.calls = []
        self.probs = probs or {
            "lead_direct": 0.6,
            "lead_weak": 0.1,
            "competitor": 0.1,
            "other": 0.2,
        }
        self.error = error

    async def system_one(self, state, questions, *, model=None, **kwargs):
        self.calls.append({"state": state, "questions": questions, "model": model})
        if self.error:
            raise self.error
        return SimpleNamespace(
            choices={"category": SimpleNamespace(probabilities=self.probs)},
            model="jev-1.13.0",
            usage=SimpleNamespace(input_tokens=120),
        )


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(categories, "config", {})
    monkeypatch.setattr(categories, "_client", None)
    monkeypatch.setattr(categories, "_client_key", None)


def test_evaluate_sums_forward_and_picks_best_forward():
    cat, prob, p_fwd, fwd = evaluate_probabilities(
        GATE,
        {"lead_direct": 0.3, "lead_weak": 0.25, "competitor": 0.05, "other": 0.4},
    )
    assert (cat, prob, round(p_fwd, 2), fwd) == ("lead_direct", 0.3, 0.55, True)


def test_evaluate_below_threshold():
    *_, fwd = evaluate_probabilities(
        GATE, {"lead_direct": 0.2, "lead_weak": 0.1, "other": 0.7}
    )
    assert fwd is False


def test_evaluate_missing_and_extra_keys():
    cat, prob, p_fwd, fwd = evaluate_probabilities(
        GATE, {"lead_weak": 0.9, "junk": 0.1}
    )
    assert (cat, prob, fwd) == ("lead_weak", 0.9, True)


@pytest.mark.asyncio
async def test_match_builds_choice_question():
    client = FakeClient()
    m = await match_categories(GATE, "текст", "Chat", client=client)
    call = client.calls[0]
    q = call["questions"]["category"]
    assert set(q.criteria) == {"lead_direct", "lead_weak", "competitor", "other"}
    assert q.instructions == "q"
    assert call["model"] == "jev-latest"
    assert call["state"] == {"chat": "Chat", "message": "текст"}
    assert m.model == "jev-1.13.0" and m.input_tokens == 120
    assert m.forward is True and m.category == "lead_direct"
    assert set(m.probabilities) == {"lead_direct", "lead_weak", "competitor", "other"}


@pytest.mark.asyncio
async def test_match_state_without_chat():
    client = FakeClient()
    await match_categories(GATE, "текст", client=client)
    assert client.calls[0]["state"] == {"message": "текст"}


@pytest.mark.asyncio
async def test_match_empty_text_no_call():
    client = FakeClient()
    assert await match_categories(GATE, "", client=client) is None
    assert not client.calls


@pytest.mark.asyncio
async def test_match_sdk_error_returns_none(caplog):
    client = FakeClient(error=RuntimeError("boom"))
    with caplog.at_level(logging.WARNING):
        assert await match_categories(GATE, "x", client=client) is None
    assert "categories" in caplog.text


@pytest.mark.asyncio
async def test_match_no_key_returns_none(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert categories.build_client() is None
    assert await match_categories(GATE, "x") is None


def test_build_client_uses_config_key_and_caches(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setitem(categories.config, "typesafe_api_key", "k1")
    c1 = categories.build_client()
    assert c1 is not None and categories.build_client() is c1
    categories.config["typesafe_api_key"] = "k2"
    assert categories.build_client() is not c1
