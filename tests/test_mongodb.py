import asyncio
from unittest.mock import MagicMock, patch

from app.db.mongodb import AsyncCollection, MongoDatabase


class RecordingCollection:
    def __init__(self) -> None:
        self.filters = None
        self.options = None

    def find_one(self, filters, **kwargs):
        self.filters = filters
        self.options = kwargs
        return {"status": "queued"}


def test_async_collection_find_one_forwards_query_options() -> None:
    collection = RecordingCollection()

    document = asyncio.run(
        AsyncCollection(collection).find_one(
            {"submission_id": "chat-1"}, sort=[("created_at", -1)]
        )
    )

    assert document == {"status": "queued"}
    assert collection.filters == {"submission_id": "chat-1"}
    assert collection.options == {"sort": [("created_at", -1)]}


def test_database_connect_creates_global_chat_submission_sort_index() -> None:
    collections = {}
    raw_database = MagicMock()
    raw_database.__getitem__.side_effect = lambda name: collections.setdefault(
        name, MagicMock()
    )
    client = MagicMock()
    client.__getitem__.return_value = raw_database
    with patch("app.db.mongodb.MongoClient", return_value=client):
        database = MongoDatabase("mongodb://unused.test", "ai_us_test")
        asyncio.run(database.connect())

    collections["chat_submissions"].create_index.assert_any_call(
        [("submitted_at", 1)],
        name="chat_submissions_submitted_at",
        background=True,
    )
