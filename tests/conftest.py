import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# Ensure project root is on sys.path for module imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def dummy_message_cls():
    """Factory for creating simple dummy message objects."""

    class DummyMessage:
        def __init__(self, peer_id, msg_id: int = 123, text: str | None = None):
            self.peer_id = peer_id
            self.id = msg_id
            self.raw_text = text
            self.forwarded: list[int] = []

        async def forward_to(self, target):
            self.forwarded.append(target)

    return DummyMessage


@pytest.fixture
def dummy_tg_client():
    """Minimal stand-in for :class:`TelegramClient`."""

    class DummyTG:
        def __init__(self):
            self.on_handler = None
            self.sent = []
            self.started = False
            self.dialogs_warmed = False

        async def start(self):
            self.started = True

        async def get_dialogs(self, *args, **kwargs):
            self.dialogs_warmed = True
            return []

        def on(self, event):  # noqa: D401 - same interface as telethon
            def deco(func):
                self.on_handler = func
                return func

            return deco

        async def send_message(self, *args, **kwargs):
            self.sent.append((args, kwargs))

        async def run_until_disconnected(self):
            return None

    return DummyTG()


@pytest.fixture
def dummy_client_for_list():
    """Client used for ``list_folders`` tests."""

    class DummyClientForList:
        def __init__(self, filters):
            self.connected = False
            self.filters = filters
            self.calls: list[str] = []

        def is_connected(self):
            return self.connected

        async def connect(self):
            self.connected = True
            self.calls.append("connect")

        async def __call__(self, req):
            self.calls.append("request")
            return SimpleNamespace(filters=self.filters)

    return DummyClientForList


@pytest.fixture
def create_filter():
    from telethon import types

    def _create_filter():
        return types.DialogFilter(
            id=1, title=None, pinned_peers=[], include_peers=[], exclude_peers=[]
        )

    return _create_filter


@pytest.fixture
def dummy_folder_cls():
    class DummyFolder:
        def __init__(self, title):
            self.title = title
            self.include_peers = []

    return DummyFolder


@pytest.fixture
def dummy_peer_cls():
    class DummyPeer:
        def __init__(self, cid):
            self.channel_id = cid

    return DummyPeer


@pytest.fixture
def dummy_folder_peers_cls(dummy_folder_cls, dummy_peer_cls):
    class DummyFolderPeers(dummy_folder_cls):
        def __init__(self, title, peers):
            super().__init__(title)
            self.include_peers = [dummy_peer_cls(cid) for cid in peers]

    return DummyFolderPeers
