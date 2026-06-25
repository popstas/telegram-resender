from types import SimpleNamespace

import pytest

import src.telegram_utils as tgu
from src.bulk import parse_args


def test_parse_args_mute():
    args = parse_args(["--folder", "TestFolder", "--mute"])
    assert args.folder == "TestFolder"
    assert args.mute is True
    assert args.add_user is None


def test_parse_args_add_user():
    args = parse_args(["--folder", "TestFolder", "--add-user", "someuser"])
    assert args.folder == "TestFolder"
    assert args.mute is False
    assert args.add_user == "someuser"


def test_parse_args_remove_user():
    args = parse_args(["--folder", "TestFolder", "--remove-user", "someuser"])
    assert args.folder == "TestFolder"
    assert args.mute is False
    assert args.add_user is None
    assert args.remove_user == "someuser"


def test_parse_args_combined():
    args = parse_args(
        ["--folder", "F", "--mute", "--add-user", "u", "--remove-user", "r"]
    )
    assert args.folder == "F"
    assert args.mute is True
    assert args.add_user == "u"
    assert args.remove_user == "r"


def test_parse_args_folder_required():
    with pytest.raises(SystemExit):
        parse_args(["--mute"])


@pytest.mark.asyncio
async def test_add_user_to_folder_chats(monkeypatch, caplog):
    invited = []

    class DummyClient:
        async def get_entity(self, peer):
            return SimpleNamespace(id=peer.channel_id, title=f"Chat{peer.channel_id}")

        async def get_input_entity(self, username):
            return username

        async def __call__(self, request):
            from telethon import functions

            if isinstance(request, functions.channels.InviteToChannelRequest):
                invited.append((request.channel, request.users))
                return SimpleNamespace()
            raise AssertionError("Unexpected request")

    dummy_client = DummyClient()
    monkeypatch.setattr(tgu, "client", dummy_client)

    folder = SimpleNamespace(
        title="TestFolder",
        include_peers=[
            SimpleNamespace(channel_id=1),
            SimpleNamespace(channel_id=2),
        ],
    )

    async def fake_list_folders():
        return [folder]

    monkeypatch.setattr(tgu, "list_folders", fake_list_folders)

    with caplog.at_level("INFO"):
        await tgu.add_user_to_folder_chats("TestFolder", "@testuser")

    assert len(invited) == 2
    assert sum("Added" in r.message for r in caplog.records) == 2


@pytest.mark.asyncio
async def test_add_user_to_folder_chats_already_participant(monkeypatch, caplog):
    from telethon import errors, functions

    class DummyClient:
        async def get_entity(self, peer):
            return SimpleNamespace(id=peer.channel_id, title=f"Chat{peer.channel_id}")

        async def get_input_entity(self, username):
            return username

        async def __call__(self, request):
            if isinstance(request, functions.channels.GetParticipantRequest):
                if request.channel.id == 2:
                    return SimpleNamespace()  # user is participant
                raise errors.UserNotParticipantError(request=None)
            if isinstance(request, functions.channels.InviteToChannelRequest):
                return SimpleNamespace()
            raise AssertionError("Unexpected request")

    monkeypatch.setattr(tgu, "client", DummyClient())

    folder = SimpleNamespace(
        title="F",
        include_peers=[
            SimpleNamespace(channel_id=1),
            SimpleNamespace(channel_id=2),
        ],
    )

    async def fake_list_folders():
        return [folder]

    monkeypatch.setattr(tgu, "list_folders", fake_list_folders)

    with caplog.at_level("INFO"):
        await tgu.add_user_to_folder_chats("F", "@user")

    messages = [r.message for r in caplog.records]
    assert sum("Added" in m for m in messages) == 1
    assert sum("already a participant" in m for m in messages) == 1


@pytest.mark.asyncio
async def test_add_user_to_folder_chats_folder_not_found(monkeypatch, caplog):
    async def fake_list_folders():
        return []

    monkeypatch.setattr(tgu, "client", SimpleNamespace())
    monkeypatch.setattr(tgu, "list_folders", fake_list_folders)

    with caplog.at_level("ERROR"):
        await tgu.add_user_to_folder_chats("Missing", "@user")

    assert any("not found" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_add_user_to_folder_chats_empty_args():
    await tgu.add_user_to_folder_chats("", "@user")
    await tgu.add_user_to_folder_chats("folder", "")


@pytest.mark.asyncio
async def test_remove_user_from_folder_chats(monkeypatch, caplog):
    from telethon import functions

    kicked = []

    class DummyClient:
        async def get_entity(self, peer):
            return SimpleNamespace(id=peer.channel_id, title=f"Chat{peer.channel_id}")

        async def get_input_entity(self, username):
            return username

        async def kick_participant(self, channel, user):
            kicked.append((channel, user))
            return SimpleNamespace()

        async def __call__(self, request):
            if isinstance(request, functions.channels.GetParticipantRequest):
                return SimpleNamespace()  # user is a participant
            raise AssertionError("Unexpected request")

    monkeypatch.setattr(tgu, "client", DummyClient())

    folder = SimpleNamespace(
        title="TestFolder",
        include_peers=[
            SimpleNamespace(channel_id=1),
            SimpleNamespace(channel_id=2),
        ],
    )

    async def fake_list_folders():
        return [folder]

    monkeypatch.setattr(tgu, "list_folders", fake_list_folders)

    with caplog.at_level("INFO"):
        await tgu.remove_user_from_folder_chats("TestFolder", "@testuser")

    assert len(kicked) == 2
    assert sum("Removed" in r.message for r in caplog.records) == 2


@pytest.mark.asyncio
async def test_remove_user_from_folder_chats_not_participant(monkeypatch, caplog):
    from telethon import errors, functions

    kicked = []

    class DummyClient:
        async def get_entity(self, peer):
            return SimpleNamespace(id=peer.channel_id, title=f"Chat{peer.channel_id}")

        async def get_input_entity(self, username):
            return username

        async def kick_participant(self, channel, user):
            kicked.append((channel, user))
            return SimpleNamespace()

        async def __call__(self, request):
            if isinstance(request, functions.channels.GetParticipantRequest):
                if request.channel.id == 2:
                    return SimpleNamespace()  # user is participant
                raise errors.UserNotParticipantError(request=None)
            raise AssertionError("Unexpected request")

    monkeypatch.setattr(tgu, "client", DummyClient())

    folder = SimpleNamespace(
        title="F",
        include_peers=[
            SimpleNamespace(channel_id=1),
            SimpleNamespace(channel_id=2),
        ],
    )

    async def fake_list_folders():
        return [folder]

    monkeypatch.setattr(tgu, "list_folders", fake_list_folders)

    with caplog.at_level("INFO"):
        await tgu.remove_user_from_folder_chats("F", "@user")

    messages = [r.message for r in caplog.records]
    assert len(kicked) == 1
    assert sum("Removed" in m for m in messages) == 1
    assert sum("not a participant" in m for m in messages) == 1


@pytest.mark.asyncio
async def test_remove_user_from_folder_chats_folder_not_found(monkeypatch, caplog):
    async def fake_list_folders():
        return []

    monkeypatch.setattr(tgu, "client", SimpleNamespace())
    monkeypatch.setattr(tgu, "list_folders", fake_list_folders)

    with caplog.at_level("ERROR"):
        await tgu.remove_user_from_folder_chats("Missing", "@user")

    assert any("not found" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_remove_user_from_folder_chats_empty_args():
    await tgu.remove_user_from_folder_chats("", "@user")
    await tgu.remove_user_from_folder_chats("folder", "")
