import asyncio
import logging
from datetime import datetime
from typing import List

from telethon import TelegramClient, events, types
from telethon.errors import TypeNotFoundError

from . import langfuse_utils, prompts, telegram_utils, webhook
from .config import (
    Instance,
    get_api_credentials,
    load_config,
    load_instances,
    parse_proxy,
)
from .prompts import Prompt, match_prompt
from .seen_chats import seen_chats
from .stats import stats as global_stats
from .telegram_utils import (
    add_topic_from_folders,
    find_word,
    get_chat_name,
    get_folders_chat_ids,
    get_forward_message_text,
    get_message_url,
    mute_chats_from_folders,
    normalize_chat_ids,
    resolve_entities,
    word_in_text,
)
from .trace_ids import trace_ids

logger = logging.getLogger(__name__)

client: TelegramClient | None = None
config: dict = {}
instances: List[Instance] = []

langfuse = None

# Use shared stats tracker
stats = global_stats

NEGATIVE_REACTIONS = {"👎"}  # thumbs down
POSITIVE_REACTIONS = {"👍"}  # thumbs up

# Track messages already forwarded for reactions
forwarded_positive: set[tuple[int, int]] = set()
forwarded_negative: set[tuple[int, int]] = set()


def setup_logging(level: str = "info") -> None:
    """Configure logging for the application."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(level=numeric_level, format="%(levelname)s - %(message)s")
    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("langfuse").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("httpcore.http11").setLevel(logging.WARNING)
    logging.getLogger("httpcore.connection").setLevel(logging.WARNING)


async def update_instance_chat_ids(instance: Instance, first_run: bool = False) -> None:
    """Refresh chat IDs for a single instance."""
    folder_ids_raw = await get_folders_chat_ids(instance.folders)
    if instance.folders:
        folder_only = await normalize_chat_ids(set(folder_ids_raw))
        stats.set_folder_chats(instance.name, sorted(folder_only))
    else:
        stats.clear_folder_chats(instance.name)

    new_ids = set(folder_ids_raw)
    new_ids.update(instance.chat_ids)
    new_ids.update(await resolve_entities(instance.entities))
    instance.chat_ids = await normalize_chat_ids(new_ids)
    if instance.folder_mute:
        await mute_chats_from_folders(instance.folders)
        exit()
    if instance.folder_add_topic:
        await add_topic_from_folders(instance.folders, instance.folder_add_topic)
        exit()
    log_level = logging.INFO if first_run else logging.DEBUG
    logger.log(
        log_level,
        "Instance '%s': listening to %d chats from %d folders and %d entities",
        instance.name,
        len(instance.chat_ids),
        len(instance.folders),
        len(instance.entities),
    )


async def rescan_loop(instance: Instance, interval: int = 3600) -> None:
    """Periodically rescan folders for chat IDs."""
    global config
    while True:
        await asyncio.sleep(interval)
        config = load_config()
        prompts.config.update(config)
        await update_instance_chat_ids(instance, False)


async def _forward_messages(
    inst: Instance,
    messages: list,
    *,
    trigger_message,
    used_word: str | None,
    used_prompt: Prompt | None,
    used_score: int,
    used_quote: str | None,
    used_reasoning: str | None,
    used_trace_id: str | None,
) -> None:
    """Forward a batch of messages to every destination with a single header.

    The header is built from ``trigger_message`` and sent once per destination
    (unless ``inst.no_forward_message``); each message in ``messages`` is then
    forwarded in chronological order. The trigger's forwarded copy receives the
    ``trace_id`` and the webhook fires once for the trigger.
    """
    source_name = await get_chat_name(
        getattr(trigger_message, "chat_id", None), safe=True
    )
    try:
        if not inst.no_forward_message:
            text = await get_forward_message_text(
                trigger_message,
                prompt=used_prompt,
                score=used_score,
                word=used_word,
                quote=used_quote,
                reasoning=used_reasoning,
            )
        destinations = []
        dest_names = []
        if inst.target_chat is not None:
            destinations.append(inst.target_chat)
            dest_names.append(await get_chat_name(inst.target_chat, safe=True))
        if inst.target_entity:
            destinations.append(inst.target_entity)
            dest_names.append(await get_chat_name(inst.target_entity, safe=True))
        for dest, dname in zip(destinations, dest_names):
            if not inst.no_forward_message:
                await client.send_message(dest, text)
            for msg in messages:
                forwarded = await msg.forward_to(dest)
                if msg is trigger_message and forwarded and used_trace_id:
                    trace_ids.set(forwarded.chat_id, forwarded.id, used_trace_id)
                f_url = get_message_url(forwarded) if forwarded else None
                logger.info(
                    "Forwarded message %s from %s to %s for %s (target url: %s)",
                    msg.id,
                    source_name,
                    dname,
                    inst.name,
                    f_url,
                )
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Failed to forward message: %s", exc)
    if inst.target_webhook is not None:
        await webhook.send_webhook(inst.target_webhook, trigger_message)


async def process_message(inst: Instance, event: events.NewMessage.Event) -> None:
    """Handle a new message for a specific instance."""
    message = event.message
    if message.raw_text and word_in_text(inst.ignore_words, message.raw_text):
        logger.debug(
            "Ignoring message %s for %s due to ignore_words",
            message.id,
            inst.name,
        )
        return
    if message.raw_text and word_in_text(inst.negative_words, message.raw_text):
        logger.debug(
            "Ignoring message %s for %s due to negative_words",
            message.id,
            inst.name,
        )
        return
    chat_name = await get_chat_name(event.chat_id, safe=True)
    forward = False
    used_word: str | None = None
    used_prompt: Prompt | None = None
    used_score = 0
    used_quote: str | None = None
    used_reasoning: str | None = None
    used_trace_id: str | None = None

    if message.raw_text:
        w = find_word(inst.words, message.raw_text)
        if w:
            forward = True
            used_word = w
        else:
            for p in inst.prompts:
                res = await match_prompt(p, message.raw_text, inst.name, chat_name)
                sc = res.score
                trace_id = res.trace_id
                if sc > used_score:
                    used_score = sc
                    used_prompt = p
                    used_quote = res.quote
                    used_reasoning = res.reasoning
                    used_trace_id = trace_id
                if sc >= (p.threshold or 4):
                    forward = True
                    break
    if forward and inst.once_per_chat:
        chat_id = getattr(event, "chat_id", None)
        if chat_id is not None:
            now = datetime.now()
            if seen_chats.should_forward(inst.name, chat_id, now, inst.reset_hour):
                seen_chats.record(inst.name, chat_id, now)
            else:
                logger.debug(
                    "Suppressing message %s for %s: already forwarded from chat %s today",
                    message.id,
                    inst.name,
                    chat_id,
                )
                forward = False
    if forward:
        await _forward_messages(
            inst,
            [message],
            trigger_message=message,
            used_word=used_word,
            used_prompt=used_prompt,
            used_score=used_score,
            used_quote=used_quote,
            used_reasoning=used_reasoning,
            used_trace_id=used_trace_id,
        )
    else:
        logger.debug(
            "Message %s from %s not forwarded for %s",
            message.id,
            chat_name,
            inst.name,
        )
    stats.increment(
        inst.name,
        forwarded=forward,
        used_word=used_word is not None,
        used_prompt=used_prompt is not None,
    )


async def handle_reaction(update: "types.UpdateMessageReactions") -> None:
    """Forward reacted messages to true/false positive entities."""

    if not update or not hasattr(update, "reactions"):
        return

    emojis: list[str] = []
    for rc in getattr(update.reactions, "results", []):
        reaction = getattr(rc, "reaction", None)
        if isinstance(reaction, types.ReactionEmoji):
            emojis.append(reaction.emoticon)

    positive = any(e in POSITIVE_REACTIONS for e in emojis)
    negative = any(e in NEGATIVE_REACTIONS for e in emojis)
    if not (positive or negative):
        return

    peer_id = await telegram_utils.to_event_chat_id(update.peer)
    key = (peer_id, update.msg_id)

    if positive and key in forwarded_positive:
        logger.debug("Skip message %s already forwarded as positive", key)
        return
    if negative and key in forwarded_negative:
        logger.debug("Skip message %s already forwarded as negative", key)
        return

    for inst in instances:
        if not inst.target_entity:
            continue
        entity = await telegram_utils.get_entity(inst.target_entity)
        target_id = await telegram_utils.to_event_chat_id(entity)
        if peer_id != target_id:
            continue

        dest = None
        if positive:
            dest = inst.true_positive_entity
        elif negative:
            dest = inst.false_positive_entity
        if not dest:
            continue

        message = await client.get_messages(update.peer, ids=update.msg_id)
        if not message:
            return
        trace_id = trace_ids.get(peer_id, message.id)
        forwarded = await message.forward_to(dest)
        if forwarded and trace_id:
            trace_ids.set(forwarded.chat_id, forwarded.id, trace_id)
        if positive:
            forwarded_positive.add(key)
        elif negative:
            forwarded_negative.add(key)
        f_url = get_message_url(forwarded) if forwarded else None
        logger.info(
            "Forwarded message %s from %s to %s for %s (target url: %s)",
            message.id,
            inst.target_entity,
            dest,
            inst.name,
            f_url,
        )
        break


async def run_until_disconnected_resilient(
    tg_client: TelegramClient, backoff_seconds: float = 2.0
) -> None:
    """Run the Telethon update loop, restarting on unknown TL constructors.

    Telegram introduces new TL types continuously. If the installed Telethon
    version doesn't yet know one, the update loop raises ``TypeNotFoundError``
    via ``_updates_error`` and disconnects. Reconnect and keep listening so the
    bot stays up. All other exceptions propagate.
    """
    while True:
        try:
            await tg_client.run_until_disconnected()
            return
        except TypeNotFoundError as exc:
            logger.warning(
                "Telethon could not parse a TL object (%s); reconnecting. "
                "Upgrade telethon if this keeps happening.",
                exc,
            )
            # Telethon disconnects on _updates_error; clear and reconnect so
            # the next iteration can resume listening.
            try:
                tg_client._updates_error = None
            except AttributeError:
                pass
            await asyncio.sleep(backoff_seconds)
            connect = getattr(tg_client, "connect", None)
            is_connected = getattr(tg_client, "is_connected", None)
            if callable(connect) and (not callable(is_connected) or not is_connected()):
                await connect()


async def main() -> None:
    global client, instances, config
    config = load_config()
    prompts.config.update(config)
    global langfuse
    langfuse = langfuse_utils.init_langfuse(config)
    prompts.langfuse = langfuse

    setup_logging(config.get("log_level", "info"))

    api_id, api_hash, session_name = get_api_credentials(config)

    proxy = parse_proxy(config["proxy_url"]) if config.get("proxy_url") else None
    client = TelegramClient(session_name, api_id, api_hash, proxy=proxy)
    telegram_utils.client = client
    await client.start()

    prompts.stats = stats

    instances = await load_instances(config)
    await prompts.load_langfuse_prompts(instances)
    for inst in instances:
        await update_instance_chat_ids(inst, True)
        asyncio.create_task(rescan_loop(inst))

    @client.on(events.Raw(types.UpdateMessageReactions))
    async def reaction_event_handler(update) -> None:
        await handle_reaction(update)

    @client.on(events.NewMessage)
    async def handler(event: events.NewMessage.Event) -> None:
        sender = getattr(event.message, "sender", None)
        if sender is None:
            try:
                sender = await event.get_sender()
            except Exception:  # pylint: disable=broad-except
                logger.debug(
                    "Failed to resolve sender for chat %s msg %s",
                    getattr(event, "chat_id", None),
                    getattr(event.message, "id", None),
                    exc_info=True,
                )
                sender = None
        username = getattr(sender, "username", None)
        user_id = getattr(sender, "id", None)
        if user_id and user_id in config.get("ignore_user_ids", []):
            logger.debug("Ignoring message from id %s", user_id)
            return

        global_ignore = config.get("ignore_usernames", [])
        for inst in instances:
            if event.chat_id not in inst.chat_ids:
                continue
            effective_ignore = (
                inst.ignore_usernames_override
                if inst.ignore_usernames_override is not None
                else global_ignore
            )
            if username and username.lower() in [u.lower() for u in effective_ignore]:
                logger.debug(
                    "Ignoring message from @%s for instance %s", username, inst.name
                )
                continue
            if not username and effective_ignore:
                logger.debug(
                    "No username for sender id=%s in chat %s; ignore list not applied for instance %s",
                    user_id,
                    event.chat_id,
                    inst.name,
                )
            await process_message(inst, event)

    await run_until_disconnected_resilient(client)
