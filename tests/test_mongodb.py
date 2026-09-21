import asyncio

from app.db.mongodb import AsyncCollection


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
        AsyncCollection(collection).find_one({"submission_id": "chat-1"}, sort=[("created_at", -1)])
    )

    assert document == {"status": "queued"}
    assert collection.filters == {"submission_id": "chat-1"}
    assert collection.options == {"sort": [("created_at", -1)]}
