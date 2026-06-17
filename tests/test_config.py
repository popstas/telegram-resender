import pytest

import src.config as config


def test_load_config_success(tmp_path, monkeypatch):
    cfg_file = tmp_path / "cfg.yml"
    cfg_file.write_text("foo: 1")
    monkeypatch.setattr(config, "CONFIG_PATH", str(cfg_file))
    assert config.load_config() == {"foo": 1}


def test_load_config_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", str(tmp_path / "nonexistent.yml"))
    with pytest.raises(FileNotFoundError):
        config.load_config()


def test_get_api_credentials_success():
    cfg = {"api_id": "123", "api_hash": "hash", "session": "sess"}
    assert config.get_api_credentials(cfg) == (123, "hash", "sess")


def test_get_api_credentials_missing():
    with pytest.raises(RuntimeError):
        config.get_api_credentials({})


def test_config_path_env_override(tmp_path, monkeypatch):
    cfg = tmp_path / "env.yml"
    cfg.write_text("bar: 2")
    monkeypatch.setenv("CONFIG_PATH", str(cfg))
    import importlib

    cfg_module = importlib.reload(config)
    assert cfg_module.CONFIG_PATH == str(cfg)
    assert cfg_module.load_config() == {"bar": 2}


def test_parse_proxy_socks5():
    result = config.parse_proxy("socks5://127.0.0.1:1080")
    import python_socks

    assert result == (python_socks.ProxyType.SOCKS5, "127.0.0.1", 1080)


def test_parse_proxy_http():
    result = config.parse_proxy("http://proxy.example.com:8080")
    import python_socks

    assert result == (python_socks.ProxyType.HTTP, "proxy.example.com", 8080)


def test_parse_proxy_with_auth():
    result = config.parse_proxy("socks5://user:pass@127.0.0.1:1080")
    import python_socks

    assert result == (
        python_socks.ProxyType.SOCKS5,
        "127.0.0.1",
        1080,
        True,
        "user",
        "pass",
    )


def test_parse_proxy_unsupported_scheme():
    with pytest.raises(ValueError, match="Unsupported proxy scheme"):
        config.parse_proxy("ftp://127.0.0.1:21")


@pytest.mark.asyncio
async def test_load_instances_target_webhook_text_default():
    cfg = {
        "instances": [
            {
                "name": "inst",
                "words": [],
                "target_webhook": {"url": "http://localhost:8002/hook"},
            }
        ]
    }
    instances = await config.load_instances(cfg)
    assert instances[0].target_webhook is not None
    assert instances[0].target_webhook.url == "http://localhost:8002/hook"
    assert instances[0].target_webhook.format == "text"


@pytest.mark.asyncio
async def test_load_instances_target_webhook_json():
    cfg = {
        "instances": [
            {
                "name": "inst",
                "words": [],
                "target_webhook": {
                    "url": "http://localhost:8002/hook",
                    "format": "json",
                },
            }
        ]
    }
    instances = await config.load_instances(cfg)
    assert instances[0].target_webhook.format == "json"


@pytest.mark.asyncio
async def test_load_instances_target_webhook_invalid_format():
    cfg = {
        "instances": [
            {
                "name": "inst",
                "words": [],
                "target_webhook": {"url": "http://x", "format": "xml"},
            }
        ]
    }
    with pytest.raises(ValueError, match="target_webhook.format"):
        await config.load_instances(cfg)


@pytest.mark.asyncio
async def test_load_instances_target_webhook_missing_url():
    cfg = {
        "instances": [
            {
                "name": "inst",
                "words": [],
                "target_webhook": {"format": "text"},
            }
        ]
    }
    with pytest.raises(ValueError, match="target_webhook.url"):
        await config.load_instances(cfg)


@pytest.mark.asyncio
async def test_load_instances_target_webhook_absent_defaults_to_none():
    cfg = {"instances": [{"name": "inst", "words": []}]}
    instances = await config.load_instances(cfg)
    assert instances[0].target_webhook is None


@pytest.mark.asyncio
async def test_load_instances_dedup_debounce_defaults():
    cfg = {"instances": [{"name": "inst", "words": []}]}
    instances = await config.load_instances(cfg)
    assert instances[0].once_per_chat is False
    assert instances[0].reset_hour == 6
    assert instances[0].debounce_ms == 0


@pytest.mark.asyncio
async def test_load_instances_dedup_debounce_values():
    cfg = {
        "instances": [
            {
                "name": "inst",
                "words": [],
                "once_per_chat": True,
                "reset_hour": 3,
                "debounce_ms": 60000,
            }
        ]
    }
    instances = await config.load_instances(cfg)
    assert instances[0].once_per_chat is True
    assert instances[0].reset_hour == 3
    assert instances[0].debounce_ms == 60000


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [-1, 24, 100])
async def test_load_instances_reset_hour_out_of_range(bad):
    cfg = {"instances": [{"name": "inst", "words": [], "reset_hour": bad}]}
    with pytest.raises(ValueError, match="reset_hour"):
        await config.load_instances(cfg)


@pytest.mark.asyncio
async def test_load_instances_reset_hour_not_int():
    cfg = {"instances": [{"name": "inst", "words": [], "reset_hour": "6"}]}
    with pytest.raises(ValueError, match="reset_hour"):
        await config.load_instances(cfg)


@pytest.mark.asyncio
async def test_load_instances_debounce_negative():
    cfg = {"instances": [{"name": "inst", "words": [], "debounce_ms": -1}]}
    with pytest.raises(ValueError, match="debounce_ms"):
        await config.load_instances(cfg)


@pytest.mark.asyncio
async def test_load_instances_debounce_not_int():
    cfg = {"instances": [{"name": "inst", "words": [], "debounce_ms": "100"}]}
    with pytest.raises(ValueError, match="debounce_ms"):
        await config.load_instances(cfg)


@pytest.mark.asyncio
async def test_load_instances_forward_message_defaults():
    cfg = {"instances": [{"name": "inst", "words": []}]}
    instances = await config.load_instances(cfg)
    inst = instances[0]
    assert inst.message_template is None
    assert inst.forward_message_show_trigger is True
    assert inst.forward_message_show_source is True
    assert inst.forward_message_prefix == ""
    assert inst.forward_message_suffix == ""
    assert inst.cancel_on_owner_reply is True


@pytest.mark.asyncio
async def test_load_instances_message_template_parsed():
    cfg = {
        "instances": [
            {
                "name": "inst",
                "words": [],
                "message_template": "{trigger}\n{source}",
            }
        ]
    }
    instances = await config.load_instances(cfg)
    assert instances[0].message_template == "{trigger}\n{source}"


@pytest.mark.asyncio
async def test_load_instances_forward_message_block_parsed():
    cfg = {
        "instances": [
            {
                "name": "inst",
                "words": [],
                "forward_message": {
                    "show_trigger": False,
                    "show_source": False,
                    "prefix": "PRE",
                    "suffix": "SUF",
                },
            }
        ]
    }
    instances = await config.load_instances(cfg)
    inst = instances[0]
    assert inst.forward_message_show_trigger is False
    assert inst.forward_message_show_source is False
    assert inst.forward_message_prefix == "PRE"
    assert inst.forward_message_suffix == "SUF"


@pytest.mark.asyncio
async def test_load_instances_cancel_on_owner_reply_parsed():
    cfg = {"instances": [{"name": "inst", "words": [], "cancel_on_owner_reply": False}]}
    instances = await config.load_instances(cfg)
    assert instances[0].cancel_on_owner_reply is False


@pytest.mark.asyncio
async def test_load_instances_message_template_not_str():
    cfg = {"instances": [{"name": "inst", "words": [], "message_template": 5}]}
    with pytest.raises(ValueError, match="message_template"):
        await config.load_instances(cfg)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["{trigger", "{0}", "{trigger.foo}"])
async def test_load_instances_message_template_malformed(bad):
    cfg = {"instances": [{"name": "inst", "words": [], "message_template": bad}]}
    with pytest.raises(ValueError, match="not a valid template"):
        await config.load_instances(cfg)


@pytest.mark.asyncio
async def test_load_instances_message_template_unknown_placeholder_ok():
    # Unknown placeholders are allowed (render as "" at runtime), so they must
    # not trip the load-time validation.
    cfg = {
        "instances": [
            {"name": "inst", "words": [], "message_template": "{unknown} {trigger}"}
        ]
    }
    instances = await config.load_instances(cfg)
    assert instances[0].message_template == "{unknown} {trigger}"


@pytest.mark.asyncio
async def test_load_instances_forward_message_show_trigger_not_bool():
    cfg = {
        "instances": [
            {
                "name": "inst",
                "words": [],
                "forward_message": {"show_trigger": "yes"},
            }
        ]
    }
    with pytest.raises(ValueError, match="forward_message.show_trigger"):
        await config.load_instances(cfg)


@pytest.mark.asyncio
async def test_load_instances_forward_message_show_source_not_bool():
    cfg = {
        "instances": [
            {
                "name": "inst",
                "words": [],
                "forward_message": {"show_source": 1},
            }
        ]
    }
    with pytest.raises(ValueError, match="forward_message.show_source"):
        await config.load_instances(cfg)


@pytest.mark.asyncio
async def test_load_instances_forward_message_prefix_not_str():
    cfg = {
        "instances": [{"name": "inst", "words": [], "forward_message": {"prefix": 1}}]
    }
    with pytest.raises(ValueError, match="forward_message.prefix"):
        await config.load_instances(cfg)


@pytest.mark.asyncio
async def test_load_instances_forward_message_suffix_not_str():
    cfg = {
        "instances": [
            {"name": "inst", "words": [], "forward_message": {"suffix": True}}
        ]
    }
    with pytest.raises(ValueError, match="forward_message.suffix"):
        await config.load_instances(cfg)


@pytest.mark.asyncio
async def test_load_instances_forward_message_not_mapping():
    cfg = {"instances": [{"name": "inst", "words": [], "forward_message": "nope"}]}
    with pytest.raises(ValueError, match="forward_message"):
        await config.load_instances(cfg)


@pytest.mark.asyncio
async def test_load_instances_cancel_on_owner_reply_not_bool():
    cfg = {
        "instances": [{"name": "inst", "words": [], "cancel_on_owner_reply": "true"}]
    }
    with pytest.raises(ValueError, match="cancel_on_owner_reply"):
        await config.load_instances(cfg)


@pytest.mark.asyncio
async def test_load_instances_folder_add_topic():
    cfg = {
        "instances": [
            {
                "name": "inst",
                "words": [],
                "folder_add_topic": [
                    {"name": "Topic", "message": "hello", "username": "user"}
                ],
            }
        ]
    }

    instances = await config.load_instances(cfg)
    assert instances[0].folder_add_topic
    topic = instances[0].folder_add_topic[0]
    assert topic.name == "Topic"
    assert topic.message == "hello"
    assert topic.username == "user"
