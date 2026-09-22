import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pyrogram
from pyrogram import raw


@pytest.fixture
def client():
    return pyrogram.Client("auto_no_updates", api_id=1, api_hash="a", in_memory=True)


@pytest.mark.parametrize("query", [
    raw.functions.updates.GetState(),
    raw.functions.updates.GetDifference(pts=1, date=1, qts=1),
    raw.functions.updates.GetChannelDifference(
        channel=raw.types.InputChannelEmpty(),
        filter=raw.types.ChannelMessagesFilterEmpty(),
        pts=1,
        limit=1,
    ),
])
def test_updates_queries_are_never_sent_without_updates(client, query):
    assert client._auto_needs_updates(query) is True


@pytest.mark.parametrize("query", [
    raw.functions.messages.GetHistory(
        peer=raw.types.InputPeerEmpty(), offset_id=0, offset_date=0,
        add_offset=0, limit=1, max_id=0, min_id=0, hash=0,
    ),
    raw.functions.upload.GetFile(
        location=raw.types.InputFileLocation(
            volume_id=0, local_id=0, secret=0, file_reference=b"",
        ),
        offset=0,
        limit=1,
    ),
])
def test_read_only_queries_still_skip_updates(client, query):
    assert client._auto_needs_updates(query) is False


def test_sends_still_need_updates(client):
    query = raw.functions.messages.SendMessage(
        peer=raw.types.InputPeerEmpty(), message="x", random_id=1,
    )

    assert client._auto_needs_updates(query) is True
