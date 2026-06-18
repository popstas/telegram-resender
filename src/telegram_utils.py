import asyncio
import logging
import re
from typing import List, Sequence, Set

from telethon import errors, functions, types
from telethon.utils import get_peer_id, resolve_id

logger = logging.getLogger(__name__)

client = None
entity_name_cache: dict = {}
entity_cache: dict = {}

MUTE_FOREVER = 2**31 - 1


def _format_chat_for_log(chat, *, chat_id=None, chat_title: str | None = None) -> str:
    """Return a human readable representation of a chat for logging."""

    if chat_id is None:
        chat_id = (
            getattr(chat, "id", None)
            or getattr(chat, "channel_id", None)
            or getattr(chat, "chat_id", None)
        )

    title = (
        (chat_title or "")
        or getattr(chat, "title", None)
        or getattr(chat, "username", None)
        or ""
    )
    if isinstance(title, str):
        title = title.strip()
    else:
        title = str(title).strip()

    if title and chat_id is not None:
        return f"{title} ({chat_id})"
    if title:
        return title
    if chat_id is not None:
        return str(chat_id)
    return str(chat)


def get_safe_name(name: str) -> str:
    """Return ``name`` with unsafe characters replaced by underscores."""
    safe = re.sub(r"[^\w\-_.]", "_", name.strip())
    return safe or "chat_history"


def word_in_text(words: List[str], text: str) -> bool:
    """Return True if any of the words is found in text."""
    text_lower = text.lower()
    return any(word.lower() in text_lower for word in words)


def find_word(words: List[str], text: str) -> str | None:
    """Return the first matching word found in text."""
    text_lower = text.lower()
    for word in words:
        if word.lower() in text_lower:
            return word
    return None


async def get_entity(ident):
    """Return Telegram entity using in-memory cache."""
    key = str(ident)
    if key in entity_cache:
        return entity_cache[key]
    ent = await client.get_entity(ident)
    entity_cache[key] = ent
    return ent


async def get_folder(folders, folder_name):
    target = None

    for f in folders:
        title = getattr(f, "title", "")
        if hasattr(title, "text"):
            title = title.text
        if title == folder_name:
            target = f
            break

    return target


async def list_folders():
    global client

    if not client or not client.is_connected():
        await client.connect()

    result = await client(functions.messages.GetDialogFiltersRequest())

    folders = []
    for f in result.filters:
        if isinstance(f, types.DialogFilter) or isinstance(
            f, types.DialogFilterChatlist
        ):
            folders.append(f)

    return folders


def get_message_url(message):
    """Return a t.me URL if the message has ``channel_id``."""
    chat_id = getattr(message.peer_id, "channel_id", None)
    msg_id = message.id
    url = f"https://t.me/c/{chat_id}/{msg_id}" if chat_id and msg_id else None
    return url


def _sender_full_name(message) -> str:
    """Return ``first_name last_name`` from ``message.sender``, or ``""``."""
    sender = getattr(message, "sender", None)
    if sender is None:
        return ""
    first = (getattr(sender, "first_name", None) or "").strip()
    last = (getattr(sender, "last_name", None) or "").strip()
    return " ".join(p for p in (first, last) if p)


async def get_message_source(message):
    """Return message source with chat type, name, and optional URL."""
    url = get_message_url(message)
    peer = message.peer_id

    if isinstance(peer, types.PeerChannel):
        chat_type = "channel"
    elif isinstance(peer, types.PeerChat):
        chat_type = "group"
    else:
        chat_type = "private"

    name = await get_chat_name(peer)

    if chat_type == "private":
        username = getattr(getattr(message, "sender", None), "username", None)
        if username:
            name = f"@{username}"
    else:
        chat_username = getattr(getattr(message, "chat", None), "username", None)
        if chat_username:
            name = f"@{chat_username}"

    if chat_type == "private":
        username = getattr(getattr(message, "sender", None), "username", None)
        full_name = _sender_full_name(message)
        if username:
            base_name = f"{chat_type} @{username}"
            if full_name:
                base_name = f"{base_name}, Name: {full_name}"
        elif full_name:
            base_name = f"{chat_type} Name: {full_name}"
        else:
            base_name = f"{chat_type} {name}"
    else:
        base_name = name

    if url and chat_type != "private":
        result = f"Forwarded from: [{base_name}]({url})"
    else:
        result = f"Forwarded from: {base_name}"
        if url:
            result += f" - {url}"
    return result


def get_forward_reason_text(
    *,
    prompt=None,
    score: int | None = None,
    word: str | None = None,
    quote: str | None = None,
    reasoning: str | None = None,
) -> str:
    """Return human-readable reason for forwarding a message."""
    if word:
        return f"word: {word}"
    if prompt is not None and score is not None:
        name = getattr(prompt, "name", None) or "prompt"
        reason = f"{name}: {score}/5"
        if quote:
            reason += f" - `{quote}`"
        if reasoning:
            return f"{reason}\n\n{reasoning}"
        return reason
    return ""


class _DefaultFormatDict(dict):
    """Mapping for ``str.format_map`` that renders missing keys as ``""``."""

    def __missing__(self, key):
        return ""


def _safe_format(template: str, values: dict) -> str:
    """Substitute placeholders in ``template`` without raising on unknown keys."""
    return template.format_map(_DefaultFormatDict(values))


async def get_message_source_fields(message) -> dict:
    """Return granular source placeholders: ``username``, ``name``, ``chat``.

    ``username`` is the sender ``@username`` (or ``""``), ``name`` the sender full
    name (or ``""``), and ``chat`` the chat title/type from ``get_chat_name``.
    """
    username = getattr(getattr(message, "sender", None), "username", None) or ""
    if username:
        username = f"@{username}"
    full_name = _sender_full_name(message)
    chat = await get_chat_name(getattr(message, "peer_id", None))
    return {"username": username, "name": full_name, "chat": chat}


async def get_forward_message_text(
    message,
    *,
    prompt=None,
    score: int | None = None,
    word: str | None = None,
    quote: str | None = None,
    reasoning: str | None = None,
    message_template: str | None = None,
    show_trigger: bool = True,
    show_source: bool = True,
    prefix: str = "",
    suffix: str = "",
) -> str:
    """Return text to send before forwarding ``message``.

    With defaults (no ``message_template`` and all flags at their defaults) the
    output is byte-identical to the historical ``{reason}\\n\\n{source}`` layout.
    A ``message_template`` wins when set; otherwise the preface is assembled from
    the ``show_trigger``/``show_source`` flags wrapped by ``prefix``/``suffix``.

    Suppression via ``no_forward_message`` is the caller's responsibility (the
    forward path skips calling this helper entirely in that case).
    """
    reason = get_forward_reason_text(
        prompt=prompt,
        score=score,
        word=word,
        quote=quote,
        reasoning=reasoning,
    )
    source = await get_message_source(message)

    if message_template is not None:
        fields = await get_message_source_fields(message)
        values = {"trigger": reason, "source": source, **fields}
        return _safe_format(message_template, values)

    trigger_part = reason if show_trigger else ""
    source_part = source if show_source else ""
    if trigger_part and source_part:
        body = f"{trigger_part}\n\n{source_part}"
    else:
        body = trigger_part or source_part
    return f"{prefix}{body}{suffix}"


async def get_chat_name(chat_identifier: str, safe: bool = False) -> str:
    if not chat_identifier:
        return "chat_history"

    if (
        safe
        and isinstance(chat_identifier, (int, str))
        and chat_identifier in entity_name_cache
    ):
        return entity_name_cache[chat_identifier]

    try:
        entity = await get_entity(chat_identifier)
        if not entity:
            return None

        if hasattr(entity, "title"):
            name = entity.title
        elif hasattr(entity, "username") and entity.username:
            name = entity.username
        elif hasattr(entity, "first_name") or hasattr(entity, "last_name"):
            name = " ".join(
                filter(
                    None,
                    [
                        getattr(entity, "first_name", ""),
                        getattr(entity, "last_name", ""),
                    ],
                )
            )
        else:
            name = str(entity.id)

        safe_name = get_safe_name(name)

        if safe:
            if isinstance(chat_identifier, (int, str)):
                entity_name_cache[chat_identifier] = safe_name
            return safe_name

        return name.strip() or "chat_history"

    except Exception:
        chat = str(chat_identifier)
        if chat.startswith("@"):
            chat = chat[1:]
        elif "//" in chat:
            chat = chat.split("?")[0].rstrip("/").split("/")[-1]
            if chat.startswith("+"):
                chat = "invite_" + chat[1:]

        safe_name = get_safe_name(chat)
        if safe:
            if isinstance(chat_identifier, (int, str)):
                entity_name_cache[chat_identifier] = safe_name
            return safe_name
        return chat or "chat_history"


async def get_entity_name(peer_id, safe: bool = False) -> str:
    """Return name for the given ``peer_id``."""
    if isinstance(peer_id, int):
        pid, cls = resolve_id(peer_id)
        if cls == types.PeerChannel:
            peer = types.PeerChannel(pid)
        elif cls == types.PeerChat:
            peer = types.PeerChat(pid)
        else:
            peer = types.PeerUser(pid)
    else:
        peer = peer_id

    return await get_chat_name(peer, safe=safe)


async def to_event_chat_id(peer) -> int | None:
    """Convert various peer representations to ``event.chat_id`` format."""
    if peer is None:
        return None

    if isinstance(peer, int):
        if peer <= 0:
            return peer
        try:
            ent = await client.get_input_entity(peer)
            return get_peer_id(ent)
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("Failed to resolve peer %s: %s", peer, exc)
            return -peer

    try:
        return get_peer_id(peer)
    except Exception:
        if hasattr(peer, "channel_id"):
            return get_peer_id(types.PeerChannel(peer.channel_id))
        if hasattr(peer, "chat_id"):
            return get_peer_id(types.PeerChat(peer.chat_id))
        if hasattr(peer, "user_id"):
            return peer.user_id
    return None


async def warm_entity_cache() -> None:
    """Cache dialog entities so bare user/chat IDs resolve to input entities.

    Folder ``include_peers`` and config ``chat_ids`` arrive as bare IDs with no
    ``access_hash``; Telethon can only resolve those if it has seen them before.
    One ``get_dialogs()`` pass at startup caches their ``access_hash``,
    preventing "Could not find the input entity for PeerUser" errors and the
    ``-user_id`` fallback in :func:`to_event_chat_id` that breaks private-chat
    matching. Resilient by design: a failure here must never crash startup.
    """

    try:
        await client.get_dialogs()
        logger.info("Warmed entity cache from dialogs")
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("Failed to warm entity cache from dialogs: %s", exc)


async def normalize_chat_ids(ids: Set[int]) -> Set[int]:
    """Normalize a set of chat IDs to ``event.chat_id`` format."""
    result = set()
    for cid in ids:
        result.add(await to_event_chat_id(cid))
    return {i for i in result if i is not None}


async def get_folders_chat_ids(config_folders):
    """Return chat IDs for all peers included in the given folders."""
    chat_ids = set()
    if not config_folders:
        return chat_ids

    folders = await list_folders()
    for folder_name in config_folders:
        folder = await get_folder(folders, folder_name)
        if not folder:
            continue

        for dialog in folder.include_peers:
            chat_id = await to_event_chat_id(dialog)
            if chat_id is not None:
                chat_ids.add(chat_id)

    return chat_ids


async def _get_forum_topic_by_name(channel, title: str):
    chat_display = _format_chat_for_log(channel)
    try:
        result = await client(
            functions.messages.GetForumTopicsRequest(
                peer=channel,
                offset_date=0,
                offset_id=0,
                offset_topic=0,
                limit=100,
                q=title,
            )
        )
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Failed to fetch topics for %s: %s", chat_display, exc)
        return None

    for topic in getattr(result, "topics", []) or []:
        if getattr(topic, "title", "") == title:
            return topic
    return None


async def _create_forum_topic(channel, title: str):
    chat_display = _format_chat_for_log(channel)
    try:
        await client(
            functions.messages.CreateForumTopicRequest(peer=channel, title=title)
        )
    except Exception as exc:  # pylint: disable=broad-except
        logger.error(
            "Failed to create topic '%s' for %s: %s",
            title,
            chat_display,
            exc,
        )
        return None
    return await _get_forum_topic_by_name(channel, title)


async def _add_user_to_channel(channel, username: str) -> str:
    """Add user to channel. Returns "added", "already", or "" on failure."""
    if not username:
        return ""

    chat_display = _format_chat_for_log(channel)
    try:
        user = await client.get_input_entity(username)
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Failed to resolve username '%s': %s", username, exc)
        return ""

    try:
        result = await client(
            functions.channels.InviteToChannelRequest(channel=channel, users=[user])
        )
        missing = getattr(result, "missing_invitees", None) or []
        if missing:
            logger.warning(
                "Could not add '%s' to %s: user's privacy settings prevent direct add",
                username,
                chat_display,
            )
            return "privacy"
        return "added"
    except (errors.UserAlreadyParticipantError, errors.UserAlreadyInvitedError):
        logger.debug(
            "Username '%s' is already a participant of %s",
            username,
            chat_display,
        )
        return "already"
    except Exception as exc:  # pylint: disable=broad-except
        logger.error(
            "Failed to add username '%s' to %s: %s",
            username,
            chat_display,
            exc,
        )
    return ""


async def add_topic_from_folders(
    folder_names: List[str], topics: Sequence["FolderTopic"]
):
    from .config import FolderTopic  # Local import to avoid circular dependency

    if not folder_names or not topics:
        return []

    added: List[tuple[int | None, int | None, str]] = []
    folders = await list_folders()
    for fname in folder_names:
        folder = await get_folder(folders, fname)
        if not folder:
            continue
        for peer in getattr(folder, "include_peers", []) or []:
            try:
                channel = await client.get_entity(peer)
            except Exception as exc:  # pylint: disable=broad-except
                logger.error("Failed to get entity for peer %s: %s", peer, exc)
                continue
            if not isinstance(channel, types.Channel) or not (
                getattr(channel, "megagroup", False)
                and getattr(channel, "forum", False)
            ):
                continue
            chat_id = getattr(channel, "id", None)
            chat_title = getattr(channel, "title", "") or ""
            for topic in topics:
                if not isinstance(topic, FolderTopic):
                    continue
                user_added = await _add_user_to_channel(channel, topic.username)
                if not user_added:
                    continue
                existing = await _get_forum_topic_by_name(channel, topic.name)
                topic_created = False
                target_topic = existing
                if not existing:
                    created = await _create_forum_topic(channel, topic.name)
                    if not created:
                        continue
                    topic_created = True
                    target_topic = created
                if not topic_created:
                    continue
                topic_id = getattr(target_topic, "id", None)
                top_msg_id = getattr(target_topic, "top_message", None)
                thread_id = top_msg_id if top_msg_id is not None else topic_id
                if topic.message and thread_id is not None:
                    try:
                        await asyncio.sleep(2)
                        await client.send_message(
                            channel,
                            topic.message,
                            reply_to=thread_id,
                        )
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.error(
                            "Failed to send message to topic '%s' in %s: %s",
                            topic.name,
                            _format_chat_for_log(
                                channel, chat_id=chat_id, chat_title=chat_title
                            ),
                            exc,
                        )
                added.append((chat_id, thread_id, chat_title))
                logger.info(
                    "Added topic to chat %s thread %s (%s)",
                    chat_id,
                    thread_id,
                    chat_title,
                )
    return added


async def resolve_entities(entities: List[str]) -> Set[int]:
    """Resolve Telegram links or usernames to chat IDs."""
    resolved = set()
    for ent in entities:
        try:
            entity = await get_entity(ent)
            resolved.add(get_peer_id(entity))
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("Failed to resolve entity %s: %s", ent, exc)
    return resolved


async def mute_notify_peer(notify_peer) -> None:
    try:
        settings = await client(functions.account.GetNotifySettingsRequest(notify_peer))
        mute_until = getattr(settings, "mute_until", None)
        ts = (
            int(mute_until.timestamp())
            if hasattr(mute_until, "timestamp")
            else (mute_until or 0)
        )
        if ts != MUTE_FOREVER:
            await client(
                functions.account.UpdateNotifySettingsRequest(
                    peer=notify_peer,
                    settings=types.InputPeerNotifySettings(mute_until=MUTE_FOREVER),
                )
            )
            logger.info("Muted peer %s", notify_peer)
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Failed to mute peer %s: %s", notify_peer, exc)


async def mute_peer_and_topics(peer) -> None:
    logger.debug("Muting peer %s - %s", peer, await get_entity_name(peer.channel_id))
    try:
        ip = await client.get_input_entity(peer)
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Failed to resolve peer %s for mute: %s", peer, exc)
        return

    await mute_notify_peer(types.InputNotifyPeer(ip))


async def mute_chats_from_folders(folder_names: List[str]) -> None:
    if not folder_names:
        return
    folders = await list_folders()
    for fname in folder_names:
        folder = await get_folder(folders, fname)
        if not folder:
            continue
        for p in getattr(folder, "include_peers", []):
            await mute_peer_and_topics(p)


async def _is_participant(channel, username: str) -> bool:
    """Check if user is already a participant of the channel."""
    try:
        user = await client.get_input_entity(username)
        await client(
            functions.channels.GetParticipantRequest(channel=channel, participant=user)
        )
        return True
    except errors.UserNotParticipantError:
        return False
    except Exception:  # pylint: disable=broad-except
        return False


async def add_user_to_folder_chats(folder_name: str, username: str) -> None:
    """Add a user to all chats in a folder."""
    if not folder_name or not username:
        return
    folders = await list_folders()
    folder = await get_folder(folders, folder_name)
    if not folder:
        logger.error("Folder '%s' not found", folder_name)
        return
    for peer in getattr(folder, "include_peers", []) or []:
        try:
            channel = await client.get_entity(peer)
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("Failed to get entity for peer %s: %s", peer, exc)
            continue
        chat_display = _format_chat_for_log(channel)
        if await _is_participant(channel, username):
            logger.info("'%s' is already a participant of %s", username, chat_display)
            continue
        result = await _add_user_to_channel(channel, username)
        if result == "added":
            logger.info("Added '%s' to %s", username, chat_display)
        elif result != "privacy":
            logger.error("Failed to add '%s' to %s", username, chat_display)
