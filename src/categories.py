"""TypeSafe (Jev) categories gate: classify a message with a single Choice question."""

import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - used for type hints only
    from typesafe_sdk import AsyncTypeSafeClient

    from .config import CategoriesGate

logger = logging.getLogger(__name__)

config: dict = {}

_client: "AsyncTypeSafeClient | None" = None
_sdk_warned = False
_client_key: tuple | None = None

# Longest parent text sent as ``reply_to`` context.
REPLY_CONTEXT_MAX_CHARS = 1000


def reply_parent_id(header) -> int | None:
    """Id of the message a reply header points to, or ``None`` if it is not a reply.

    ``header`` is a Telethon ``MessageReplyHeader`` or its JSON export (dict).
    In forum topics every message points at the topic root; only a reply inside
    the topic (``reply_to_top_id`` set) counts.
    """
    if header is None:
        return None

    def get(key):
        if isinstance(header, dict):
            return header.get(key)
        return getattr(header, key, None)

    msg_id = get("reply_to_msg_id")
    if not msg_id:
        return None
    if get("forum_topic") and not get("reply_to_top_id"):
        return None
    return int(msg_id)


@dataclass
class CategoryMatch:
    category: str
    probability: float
    p_forward: float
    forward: bool
    probabilities: dict[str, float] = field(default_factory=dict)
    model: str | None = None
    input_tokens: int = 0


def build_client() -> "AsyncTypeSafeClient | None":
    """Return a cached client, or ``None`` when no API key is configured."""
    global _client, _client_key
    api_key = config.get("typesafe_api_key") or os.environ.get("TYPESAFE_API_KEY")
    if not api_key:
        return None
    proxy = config.get("proxy_url")
    cache_key = (api_key, proxy)
    if _client is not None and _client_key == cache_key:
        return _client
    from typesafe_sdk import AsyncTypeSafeClient

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


def top_category(probabilities: dict[str, float]) -> str | None:
    """Name of the most likely category over forward AND reject, ``None`` if empty."""
    if not probabilities:
        return None
    return max(probabilities, key=lambda n: probabilities[n])


async def match_categories(
    gate: "CategoriesGate",
    text: str,
    chat_name: str | None = None,
    *,
    reply_to: str | None = None,
    owner_previous: list[str] | None = None,
    client=None,
) -> CategoryMatch | None:
    """Ask TypeSafe which category ``text`` belongs to; ``None`` on skip or failure.

    ``reply_to`` is the text of the message ``text`` answers; it goes into the
    state as context, the category is still about ``text``. ``owner_previous`` are
    the account owner's recent messages in the chat (oldest first), also context.
    """
    if not text:
        return None
    global _sdk_warned
    try:
        from typesafe_sdk import Choice
    except ImportError as exc:
        if not _sdk_warned:
            _sdk_warned = True
            logger.warning(
                "categories: typesafe_sdk is unavailable (%s), skipping", exc
            )
        return None
    try:
        if client is None:
            client = build_client()
        if client is None:
            logger.warning(
                "categories: typesafe_api_key (config) or TYPESAFE_API_KEY (env) "
                "is not set, skipping"
            )
            return None
        state: dict = {}
        if chat_name is not None:
            state["chat"] = chat_name
        if reply_to:
            state["reply_to"] = reply_to[:REPLY_CONTEXT_MAX_CHARS]
        if owner_previous:
            state["owner_previous"] = [
                t[:REPLY_CONTEXT_MAX_CHARS] for t in owner_previous
            ]
        state["message"] = text
        question = Choice(
            instructions=gate.instructions,
            criteria={**gate.forward, **gate.reject},
        )
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
