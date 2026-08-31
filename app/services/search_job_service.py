import hashlib
import json
from dataclasses import dataclass
from uuid import uuid4

from redis.asyncio import Redis


class RateLimitExceeded(Exception):
    pass


@dataclass(frozen=True)
class SearchReservation:
    task_id: str
    is_new: bool


class SearchJobStore:
    _RESERVE_SCRIPT = """
    local existing = redis.call('GET', KEYS[1])
    if existing then
        return {existing, '0'}
    end
    redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
    redis.call('SET', KEYS[2], ARGV[3], 'EX', ARGV[2])
    return {ARGV[1], '1'}
    """
    _RELEASE_SCRIPT = """
    if redis.call('GET', KEYS[1]) == ARGV[1] then
        redis.call('DEL', KEYS[1])
    end
    redis.call('DEL', KEYS[2])
    return 1
    """

    def __init__(
        self,
        redis_url: str,
        ttl_seconds: int = 3600,
        rate_limit: int = 10,
        rate_window_seconds: int = 60,
    ) -> None:
        self.redis = Redis.from_url(redis_url, decode_responses=True)
        self.ttl_seconds = ttl_seconds
        self.rate_limit = rate_limit
        self.rate_window_seconds = rate_window_seconds

    async def enforce_rate_limit(self, client_key: str) -> None:
        key = f"search-rate:{client_key}"
        async with self.redis.pipeline(transaction=True) as pipeline:
            pipeline.incr(key)
            pipeline.expire(key, self.rate_window_seconds, nx=True)
            request_count, _ = await pipeline.execute()
        if request_count > self.rate_limit:
            raise RateLimitExceeded

    async def reserve(
        self,
        name: str,
        limit: int | None,
        sort: str,
        region: str,
        owner: str = "",
    ) -> SearchReservation:
        normalized_name = " ".join(name.split()).casefold()
        payload = json.dumps(
            {
                "name": normalized_name,
                "limit": limit,
                "sort": sort,
                "region": self._normalize_region(region),
                "owner": owner,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        search_key = hashlib.sha256(payload.encode()).hexdigest()
        dedup_key = f"search-dedup:{search_key}"
        task_id = str(uuid4())
        job_key = self._job_key(task_id)
        result = await self.redis.eval(
            self._RESERVE_SCRIPT,
            2,
            dedup_key,
            job_key,
            task_id,
            self.ttl_seconds,
            owner,
        )
        return SearchReservation(task_id=result[0], is_new=result[1] == "1")

    async def release(
        self,
        name: str,
        limit: int | None,
        sort: str,
        region: str,
        task_id: str,
        owner: str = "",
    ) -> None:
        normalized_name = " ".join(name.split()).casefold()
        payload = json.dumps(
            {
                "name": normalized_name,
                "limit": limit,
                "sort": sort,
                "region": self._normalize_region(region),
                "owner": owner,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        search_key = hashlib.sha256(payload.encode()).hexdigest()
        await self.redis.eval(
            self._RELEASE_SCRIPT,
            2,
            f"search-dedup:{search_key}",
            self._job_key(task_id),
            task_id,
        )

    async def exists(self, task_id: str, owner: str = "") -> bool:
        value = await self.redis.get(self._job_key(task_id))
        return value == owner

    @staticmethod
    def _job_key(task_id: str) -> str:
        return f"search-job:{task_id}"

    @staticmethod
    def _normalize_region(region: str) -> str:
        return " ".join(region.split()).casefold().replace("ё", "е")
