"""
Request rate limiting (spec section 41).

Two backends behind one interface. Redis is authoritative when configured, so
limits hold across replicas; the in-memory fallback keeps a single process
protected when Redis is absent, and says so rather than pretending to be
distributed. A limiter that silently degrades to no-op under failure is worse
than none, because the dashboard still shows it as enabled.

The counter is a fixed window rather than a sliding log: it is one Redis
round-trip, and at the burst sizes that matter here the extra precision of a
sliding window buys nothing.
"""
from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(slots=True)
class LimitDecision:
    allowed: bool
    remaining: int
    limit: int
    reset_after: int      # seconds until the window rolls
    backend: str

    def headers(self) -> dict[str, str]:
        return {"x-ratelimit-limit": str(self.limit),
                "x-ratelimit-remaining": str(max(self.remaining, 0)),
                "x-ratelimit-reset": str(self.reset_after)}


class RateLimiter:
    """``limit`` requests per ``window_seconds`` per key."""

    def __init__(self, *, redis_client=None) -> None:
        self._redis = redis_client
        self._local: dict[str, tuple[int, float]] = {}

    @property
    def backend(self) -> str:
        return "redis" if self._redis is not None else "in-memory"

    async def check(self, key: str, *, limit: int, window_seconds: int) -> LimitDecision:
        if self._redis is not None:
            try:
                return await self._check_redis(key, limit, window_seconds)
            except Exception:
                # Redis being down must not take authentication down with it,
                # but the caller should know the limit is now per-process.
                pass
        return self._check_local(key, limit, window_seconds)

    async def _check_redis(self, key: str, limit: int, window: int) -> LimitDecision:
        bucket = int(time.time() // window)
        redis_key = f"rl:{key}:{bucket}"
        count = await self._redis.incr(redis_key)
        if count == 1:
            await self._redis.expire(redis_key, window)
        reset = window - int(time.time() % window)
        return LimitDecision(count <= limit, limit - int(count), limit, reset, "redis")

    def _check_local(self, key: str, limit: int, window: int) -> LimitDecision:
        now = time.time()
        bucket = int(now // window)
        stored_count, stored_bucket = self._local.get(key, (0, bucket))
        if stored_bucket != bucket:
            stored_count = 0
        stored_count += 1
        self._local[key] = (stored_count, bucket)
        if len(self._local) > 50_000:            # bound the map on a hot path
            self._prune(bucket)
        reset = window - int(now % window)
        return LimitDecision(stored_count <= limit, limit - stored_count, limit,
                             reset, "in-memory")

    def _prune(self, current_bucket: int) -> None:
        self._local = {k: v for k, v in self._local.items() if v[1] >= current_bucket}


# Per-route budgets. Authentication is far tighter than reads because it is the
# endpoint an attacker actually wants: password guessing is cheap, and a
# generous limit here undoes the cost of PBKDF2.
ROUTE_LIMITS: dict[str, tuple[int, int]] = {
    "POST /api/v1/auth/login": (10, 300),
    "POST /api/v1/auth/signup": (5, 3600),
    "POST /api/v1/auth/refresh": (60, 3600),
    "POST /api/v1/investigations": (30, 3600),
    "POST /api/v1/alerts/preview": (60, 3600),
}
DEFAULT_LIMIT = (600, 60)

_limiter = RateLimiter()


def get_limiter() -> RateLimiter:
    return _limiter


def limit_for(method: str, path: str) -> tuple[int, int]:
    return ROUTE_LIMITS.get(f"{method} {path}", DEFAULT_LIMIT)
