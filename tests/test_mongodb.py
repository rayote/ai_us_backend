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


def test_database_connect_creates_global_submission_sort_indexes() -> None:
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
    collections["survey_responses"].create_index.assert_any_call(
        [("submitted_at", 1)],
        name="survey_responses_submitted_at",
        background=True,
    )


def test_async_collection_find_forwards_projection() -> None:
    collection = MagicMock()
    collection.find.return_value = iter([{"participant_id": "1"}])
    projection = {"_id": 0, "participant_id": 1}

    async def read_documents():
        return [
            document
            async for document in AsyncCollection(collection).find(
                {}, projection=projection
            )
        ]

    assert asyncio.run(read_documents()) == [{"participant_id": "1"}]
    collection.find.assert_called_once_with({}, projection=projection)
