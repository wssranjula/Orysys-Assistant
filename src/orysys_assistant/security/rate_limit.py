"""Atomic Redis token bucket plus a concurrency-safe in-memory test adapter."""

import asyncio
import math
from dataclasses import dataclass
from time import monotonic
from typing import Protocol, cast

from redis.asyncio import Redis

from orysys_assistant.config import Settings
from orysys_assistant.domain.models import Role


@dataclass(frozen=True, slots=True)
class BucketPolicy:
    capacity: int
    refill_per_minute: float

    @property
    def refill_per_second(self) -> float:
        return self.refill_per_minute / 60


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    remaining: int
    retry_after_seconds: int


class TokenBucketRateLimiter(Protocol):
    async def consume(self, user_id: str, role: Role) -> RateLimitResult: ...

    async def ping(self) -> bool: ...

    async def close(self) -> None: ...


def policies_from_settings(settings: Settings) -> dict[Role, BucketPolicy]:
    return {
        Role.VIEWER: BucketPolicy(
            settings.rate_limit_viewer_capacity,
            settings.rate_limit_viewer_refill_per_minute,
        ),
        Role.ANALYST: BucketPolicy(
            settings.rate_limit_analyst_capacity,
            settings.rate_limit_analyst_refill_per_minute,
        ),
        Role.ADMINISTRATOR: BucketPolicy(
            settings.rate_limit_administrator_capacity,
            settings.rate_limit_administrator_refill_per_minute,
        ),
    }


class InMemoryTokenBucket:
    """Process-local adapter for tests and explicit development fallback only."""

    def __init__(self, policies: dict[Role, BucketPolicy]) -> None:
        self._policies = policies
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = asyncio.Lock()

    async def consume(self, user_id: str, role: Role) -> RateLimitResult:
        policy = self._policies[role]
        async with self._lock:
            now = monotonic()
            tokens, updated_at = self._buckets.get(user_id, (float(policy.capacity), now))
            tokens = min(
                float(policy.capacity),
                tokens + (now - updated_at) * policy.refill_per_second,
            )
            if tokens >= 1:
                tokens -= 1
                self._buckets[user_id] = (tokens, now)
                return RateLimitResult(True, math.floor(tokens), 0)

            retry_after = math.ceil((1 - tokens) / policy.refill_per_second)
            self._buckets[user_id] = (tokens, now)
            return RateLimitResult(False, 0, max(1, retry_after))

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        return None


REDIS_TOKEN_BUCKET_SCRIPT = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])
local current_time = redis.call('TIME')
local now = tonumber(current_time[1]) + tonumber(current_time[2]) / 1000000
local bucket = redis.call('HMGET', key, 'tokens', 'updated_at')
local tokens = tonumber(bucket[1]) or capacity
local updated_at = tonumber(bucket[2]) or now
tokens = math.min(capacity, tokens + math.max(0, now - updated_at) * refill_rate)
local allowed = 0
local retry_after = 0
if tokens >= 1 then
    tokens = tokens - 1
    allowed = 1
else
    retry_after = (1 - tokens) / refill_rate
end
redis.call('HSET', key, 'tokens', tostring(tokens), 'updated_at', tostring(now))
redis.call('EXPIRE', key, ttl)
return {tostring(allowed), tostring(tokens), tostring(retry_after)}
"""


class RedisTokenBucket:
    """Shared, atomic token bucket suitable for concurrent API instances."""

    def __init__(self, redis_url: str, policies: dict[Role, BucketPolicy]) -> None:
        self._redis = Redis.from_url(redis_url, decode_responses=True)
        self._policies = policies

    async def consume(self, user_id: str, role: Role) -> RateLimitResult:
        policy = self._policies[role]
        ttl = max(60, math.ceil((policy.capacity / policy.refill_per_second) * 2))
        raw = await self._redis.eval(
            REDIS_TOKEN_BUCKET_SCRIPT,
            1,
            f"orysys:rate-limit:{user_id}",
            policy.capacity,
            policy.refill_per_second,
            ttl,
        )
        values = cast(list[str], raw)
        allowed = values[0] == "1"
        remaining = max(0, math.floor(float(values[1])))
        retry_after = max(0, math.ceil(float(values[2])))
        return RateLimitResult(allowed, remaining, retry_after)

    async def ping(self) -> bool:
        return bool(await self._redis.ping())

    async def close(self) -> None:
        await self._redis.aclose()


def build_rate_limiter(settings: Settings) -> TokenBucketRateLimiter:
    policies = policies_from_settings(settings)
    if settings.rate_limit_backend.lower() == "memory":
        return InMemoryTokenBucket(policies)
    return RedisTokenBucket(settings.redis_url, policies)
