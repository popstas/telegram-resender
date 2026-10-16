"""TypeSafe (Jev) categories gate: classify a message with a single Choice question."""

import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from typesafe_sdk import AsyncTypeSafeClient, Choice

if TYPE_CHECKING:  # pragma: no cover - used for type hints only
    from .config import CategoriesGate

logger = logging.getLogger(__name__)

config: dict = {}

_client: AsyncTypeSafeClient | None = None
_client_key: tuple | None = None


@dataclass
class CategoryMatch:
    category: str
    probability: float
    p_forward: float
    forward: bool
    probabilities: dict[str, float] = field(default_factory=dict)
    model: str | None = None
    input_tokens: int = 0


def build_client() -> AsyncTypeSafeClient | None:
    """Return a cached client, or ``None`` when no API key is configured."""
    global _client, _client_key
    api_key = config.get("typesafe_api_key") or os.environ.get("TYPESAFE_API_KEY")
    if not api_key:
        return None
    proxy = config.get("proxy_url")
    cache_key = (api_key, proxy)
    if _client is not None and _client_key == cache_key:
        return _client
    kwargs: dict = {"api_key": api_key}
    if proxy:
        import httpx2

        kwargs["http_client"] = httpx2.AsyncClient(proxy=proxy)
    _client = AsyncTypeSafeClient(**kwargs)
    _client_key = cache_key
    return _client


def evaluate_probabilities(
    gate: "CategoriesGate", probabilities: dict[str, float]
) -> tuple[str, float, float, bool]:
    """Return (best forward category, its probability, p_forward, forward?)."""
    fwd = {name: float(probabilities.get(name, 0.0)) for name in gate.forward}
    p_forward = sum(fwd.values())
    category = max(fwd, key=lambda n: fwd[n])
    return category, fwd[category], p_forward, p_forward >= gate.threshold


async def match_categories(
    gate: "CategoriesGate",
    text: str,
    chat_name: str | None = None,
    *,
    client=None,
) -> CategoryMatch | None:
    """Ask TypeSafe which category ``text`` belongs to; ``None`` on skip or failure."""
    if not text:
        return None
    if client is None:
        client = build_client()
    if client is None:
        logger.warning("categories: TYPESAFE_API_KEY is not set, skipping")
        return None
    state: dict = {"message": text}
    if chat_name is not None:
        state = {"chat": chat_name, **state}
    question = Choice(
        instructions=gate.instructions,
        criteria={**gate.forward, **gate.reject},
    )
    try:
        response = await client.system_one(
            state, {"category": question}, model=gate.model
        )
        raw = response.choices["category"].probabilities
        probabilities = {
            name: float(raw.get(name, 0.0)) for name in {**gate.forward, **gate.reject}
        }
        category, probability, p_forward, forward = evaluate_probabilities(
            gate, probabilities
        )
        match = CategoryMatch(
            category=category,
            probability=probability,
            p_forward=p_forward,
            forward=forward,
            probabilities=probabilities,
            model=response.model,
            input_tokens=response.usage.input_tokens or 0,
        )
    except Exception as exc:
        logger.warning("categories: TypeSafe request failed: %s", exc)
        return None
    logger.debug(
        "categories: %s p_forward=%.3f forward=%s %s",
        match.category,
        match.p_forward,
        match.forward,
        match.probabilities,
    )
    return match
