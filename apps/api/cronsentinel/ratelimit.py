"""Redis sliding-window rate limiter. Keyed per API key / agent / IP."""
import time

import redis
from fastapi import HTTPException, Request

from .config import settings

_r = redis.Redis.from_url(settings.redis_url, decode_responses=True)

_LUA = """
local key = KEYS[1]; local now = tonumber(ARGV[1]); local win = tonumber(ARGV[2]); local lim = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', key, 0, now - win)
local n = redis.call('ZCARD', key)
if n >= lim then return {0, n} end
redis.call('ZADD', key, now, now .. '-' .. math.random())
redis.call('EXPIRE', key, math.ceil(win / 1000))
return {1, n + 1}
"""
_script = _r.register_script(_LUA)


def check(key: str, limit_per_min: int, request: Request | None = None):
    now = int(time.time() * 1000)
    ok, n = _script(keys=[f"rl:{key}"], args=[now, 60_000, limit_per_min])
    if request is not None:
        request.state.rl_remaining = max(limit_per_min - int(n), 0)
    if not ok:
        raise HTTPException(429, "rate limit exceeded", headers={"Retry-After": "60"})
