from unittest.mock import AsyncMock

import pytest

from app.services.search_job_service import RateLimitExceeded, SearchJobStore


class FakePipeline:
    def __init__(self, request_count: int) -> None:
        self.request_count = request_count

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    def incr(self, *_):
        return self

    def expire(self, *_, **__):
        return self

    async def execute(self):
        return [self.request_count, True]


class FakeRedis:
    def __init__(self, request_count: int = 1) -> None:
        self.request_count = request_count
        self.eval = AsyncMock(return_value=["task-id", "1"])

    def pipeline(self, **_):
        return FakePipeline(self.request_count)


async def test_rate_limit_rejects_excess_request() -> None:
    store = SearchJobStore("redis://localhost", rate_limit=10)
    store.redis = FakeRedis(request_count=11)

    with pytest.raises(RateLimitExceeded):
        await store.enforce_rate_limit("client")


async def test_equivalent_queries_use_same_dedup_key() -> None:
    store = SearchJobStore("redis://localhost")
    fake_redis = FakeRedis()
    store.redis = fake_redis

    await store.reserve(" Intel   CPU ", 20, "popular", "Санкт-Петербург")
    first_dedup_key = fake_redis.eval.await_args.args[2]
    fake_redis.eval.reset_mock()
    await store.reserve("intel cpu", 20, "popular", " санкт-петербург ")
    second_dedup_key = fake_redis.eval.await_args.args[2]

    assert first_dedup_key == second_dedup_key


async def test_different_regions_use_different_dedup_keys() -> None:
    store = SearchJobStore("redis://localhost")
    fake_redis = FakeRedis()
    store.redis = fake_redis

    await store.reserve("Intel CPU", None, "popular", "Санкт-Петербург")
    first_dedup_key = fake_redis.eval.await_args.args[2]
    fake_redis.eval.reset_mock()
    await store.reserve("Intel CPU", None, "popular", "Москва")
    second_dedup_key = fake_redis.eval.await_args.args[2]

    assert first_dedup_key != second_dedup_key
