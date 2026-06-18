import os
from dataclasses import dataclass, field
from typing import List, Optional, Set
from urllib.parse import urlparse

import yaml

from .prompts import Prompt
from .telegram_utils import _safe_format

# Allow overriding config path via environment variable
CONFIG_PATH = os.environ.get("CONFIG_PATH", os.path.join("data", "config.yml"))


@dataclass
class FolderTopic:
    """Configuration for automatically created folder topics."""

    name: str
    message: str | None = None
    username: str | None = None


@dataclass
class TargetWebhook:
    """Configuration for forwarding matched messages to an HTTP webhook."""

    url: str
    format: str = "text"


@dataclass
class Instance:
    """Configuration for a single monitoring instance."""

    name: str
    words: List[str]
    negative_words: List[str] = field(default_factory=list)
    ignore_words: List[str] = field(default_factory=list)
    target_chat: int | None = None
    target_entity: str | None = None
    target_webhook: TargetWebhook | None = None
    false_positive_entity: str | None = None
    true_positive_entity: str | None = None
    folders: List[str] = field(default_factory=list)
    entities: List[str] = field(default_factory=list)
    chat_ids: Set[int] = field(default_factory=set)
    folder_mute: bool = False
    no_forward_message: bool = False
    ignore_usernames_override: Optional[List[str]] = None
    once_per_chat: bool = False
    reset_hour: int = 6
    debounce_ms: int = 0
    message_template: str | None = None
    forward_message_show_trigger: bool = True
    forward_message_show_source: bool = True
    forward_message_prefix: str = ""
    forward_message_suffix: str = ""
    cancel_on_owner_reply: bool = True
    prompts: List[Prompt] = field(default_factory=list)
    folder_add_topic: List[FolderTopic] = field(default_factory=list)


def parse_proxy(proxy_url: str) -> Optional[tuple]:
    """Parse a proxy URL into a Telethon-compatible proxy tuple.

    Supported schemes: http, socks4, socks5.
    Returns (proxy_type, host, port) or (proxy_type, host, port, True, user, pass).
    """
    import python_socks

    scheme_map = {
        "http": python_socks.ProxyType.HTTP,
        "https": python_socks.ProxyType.HTTP,
        "socks4": python_socks.ProxyType.SOCKS4,
        "socks5": python_socks.ProxyType.SOCKS5,
    }

    parsed = urlparse(proxy_url)
    scheme = (parsed.scheme or "").lower()
    proxy_type = scheme_map.get(scheme)
    if proxy_type is None:
        raise ValueError(f"Unsupported proxy scheme: {scheme!r}")

    host = parsed.hostname or ""
    port = parsed.port or 1080

    if parsed.username:
        return (proxy_type, host, port, True, parsed.username, parsed.password or "")
    return (proxy_type, host, port)


def load_config() -> dict:
    """Load YAML configuration from CONFIG_PATH."""
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(f"Config file not found: {CONFIG_PATH}")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data or {}


def get_api_credentials(config: dict) -> tuple:
    """Retrieve Telegram API credentials from configuration."""
    try:
        api_id = int(config["api_id"])
        api_hash = config["api_hash"]
    except KeyError as exc:
        raise RuntimeError("api_id and api_hash must be set in config") from exc
    session = config.get("session", "data/session")
    return api_id, api_hash, session


async def load_instances(config: dict) -> List[Instance]:
    """Parse instance configurations from config."""
    if "instances" not in config:
        config = {
            "instances": [
                {
                    "name": "default",
                    "folders": config.get("folders", []),
                    "chat_ids": config.get("chat_ids", []),
                    "entities": config.get("entities", []),
                    "words": config.get("words", []),
                    "negative_words": config.get("negative_words", []),
                    "ignore_words": config.get("ignore_words", []),
                    "target_chat": config.get("target_chat"),
                    "target_entity": config.get("target_entity"),
                    "false_positive_entity": config.get("false_positive_entity"),
                    "true_positive_entity": config.get("true_positive_entity"),
                    "no_forward_message": config.get("no_forward_message", False),
                }
            ]
        }

    parsed_instances: List[Instance] = []
    for inst_cfg in config.get("instances", []):
        raw_prompts = inst_cfg.get("prompts", [])
        parsed_prompts: List[Prompt] = []
        for p in raw_prompts:
            if isinstance(p, dict):
                parsed_prompts.append(
                    Prompt(
                        name=p.get("name"),
                        prompt=p.get("prompt"),
                        threshold=p.get("threshold", 4),
                        langfuse_name=p.get("langfuse_name"),
                        langfuse_label=p.get("langfuse_label", "latest"),
                        langfuse_version=p.get("langfuse_version"),
                        langfuse_type=p.get("langfuse_type", "text"),
                        config=p.get("config"),
                    )
                )
            else:
                parsed_prompts.append(Prompt(prompt=p))

        folder_topics: List[FolderTopic] = []
        for topic_cfg in inst_cfg.get("folder_add_topic", []):
            if not isinstance(topic_cfg, dict):
                continue
            name = topic_cfg.get("name")
            if not name:
                continue
            folder_topics.append(
                FolderTopic(
                    name=name,
                    message=topic_cfg.get("message"),
                    username=topic_cfg.get("username"),
                )
            )

        target_webhook_cfg = inst_cfg.get("target_webhook")
        target_webhook: TargetWebhook | None = None
        if isinstance(target_webhook_cfg, dict):
            url = target_webhook_cfg.get("url")
            if not url:
                raise ValueError(
                    "target_webhook.url is required when target_webhook is set"
                )
            fmt = target_webhook_cfg.get("format", "text")
            if fmt not in ("text", "json"):
                raise ValueError(
                    f"target_webhook.format must be 'text' or 'json', got {fmt!r}"
                )
            target_webhook = TargetWebhook(url=url, format=fmt)

        reset_hour = inst_cfg.get("reset_hour", 6)
        if not isinstance(reset_hour, int) or isinstance(reset_hour, bool):
            raise ValueError(f"reset_hour must be an integer, got {reset_hour!r}")
        if not 0 <= reset_hour <= 23:
            raise ValueError(f"reset_hour must be between 0 and 23, got {reset_hour!r}")

        debounce_ms = inst_cfg.get("debounce_ms", 0)
        if not isinstance(debounce_ms, int) or isinstance(debounce_ms, bool):
            raise ValueError(f"debounce_ms must be an integer, got {debounce_ms!r}")
        if debounce_ms < 0:
            raise ValueError(f"debounce_ms must be >= 0, got {debounce_ms!r}")

        message_template = inst_cfg.get("message_template")
        if message_template is not None:
            if not isinstance(message_template, str):
                raise ValueError(
                    f"message_template must be a string, got {message_template!r}"
                )
            # Catch malformed templates (unbalanced braces, positional or
            # attribute fields) at load time rather than silently dropping
            # forwards at runtime. Missing keys still render as "" via
            # _safe_format, so only structural errors surface here.
            try:
                _safe_format(
                    message_template,
                    {
                        "trigger": "",
                        "source": "",
                        "username": "",
                        "name": "",
                        "chat": "",
                    },
                )
            except (ValueError, IndexError, KeyError, AttributeError) as exc:
                raise ValueError(
                    f"message_template is not a valid template ({exc}): "
                    f"{message_template!r}"
                ) from exc

        fm_cfg = inst_cfg.get("forward_message", {})
        if fm_cfg is None:
            fm_cfg = {}
        if not isinstance(fm_cfg, dict):
            raise ValueError(f"forward_message must be a mapping, got {fm_cfg!r}")

        fm_show_trigger = fm_cfg.get("show_trigger", True)
        if not isinstance(fm_show_trigger, bool):
            raise ValueError(
                "forward_message.show_trigger must be a boolean, "
                f"got {fm_show_trigger!r}"
            )
        fm_show_source = fm_cfg.get("show_source", True)
        if not isinstance(fm_show_source, bool):
            raise ValueError(
                "forward_message.show_source must be a boolean, "
                f"got {fm_show_source!r}"
            )
        fm_prefix = fm_cfg.get("prefix", "")
        if not isinstance(fm_prefix, str):
            raise ValueError(
                f"forward_message.prefix must be a string, got {fm_prefix!r}"
            )
        fm_suffix = fm_cfg.get("suffix", "")
        if not isinstance(fm_suffix, str):
            raise ValueError(
                f"forward_message.suffix must be a string, got {fm_suffix!r}"
            )

        cancel_on_owner_reply = inst_cfg.get("cancel_on_owner_reply", True)
        if not isinstance(cancel_on_owner_reply, bool):
            raise ValueError(
                "cancel_on_owner_reply must be a boolean, "
                f"got {cancel_on_owner_reply!r}"
            )

        instance = Instance(
            name=inst_cfg.get("name", "instance"),
            folders=inst_cfg.get("folders", []),
            chat_ids=set(inst_cfg.get("chat_ids", [])),
            entities=inst_cfg.get("entities", []),
            words=inst_cfg.get("words", []),
            negative_words=inst_cfg.get("negative_words", []),
            ignore_words=inst_cfg.get("ignore_words", []),
            target_chat=inst_cfg.get("target_chat"),
            target_entity=inst_cfg.get("target_entity"),
            target_webhook=target_webhook,
            false_positive_entity=inst_cfg.get("false_positive_entity"),
            true_positive_entity=inst_cfg.get("true_positive_entity"),
            folder_mute=inst_cfg.get("folder_mute", False),
            no_forward_message=inst_cfg.get("no_forward_message", False),
            ignore_usernames_override=(
                inst_cfg["ignore_usernames_override"]
                if "ignore_usernames_override" in inst_cfg
                else None
            ),
            once_per_chat=inst_cfg.get("once_per_chat", False),
            reset_hour=reset_hour,
            debounce_ms=debounce_ms,
            message_template=message_template,
            forward_message_show_trigger=fm_show_trigger,
            forward_message_show_source=fm_show_source,
            forward_message_prefix=fm_prefix,
            forward_message_suffix=fm_suffix,
            cancel_on_owner_reply=cancel_on_owner_reply,
            prompts=parsed_prompts,
            folder_add_topic=folder_topics,
        )
        parsed_instances.append(instance)
    return parsed_instances
