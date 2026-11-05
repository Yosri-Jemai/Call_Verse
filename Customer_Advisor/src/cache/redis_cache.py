import json
import logging
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

from src.config import settings

logger = logging.getLogger("cashy.cache")


class RedisJsonCache:
    """Small JSON cache adapter; Redis outages never block knowledge retrieval."""

    def __init__(self, client: Any | None = None):
        self._client = client

    def _get_client(self) -> Redis:
        if self._client is None:
            self._client = Redis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=1,
                socket_timeout=1,
            )
        return self._client

    def get(self, key: str) -> dict[str, Any] | None:
        if not settings.redis_cache_enabled:
            return None

        try:
            value = self._get_client().get(key)
        except RedisError as exc:
            logger.warning("Redis cache read failed; querying knowledge base: %s", exc)
            return None

        if value is None:
            return None
        try:
            result = json.loads(value)
        except json.JSONDecodeError:
            logger.warning("Ignoring invalid JSON in Redis cache for key %s", key)
            return None
        if not isinstance(result, dict):
            logger.warning("Ignoring non-object Redis cache value for key %s", key)
            return None
        return result

    def set(self, key: str, value: dict[str, Any]) -> None:
        if not settings.redis_cache_enabled:
            return

        try:
            self._get_client().set(
                key,
                json.dumps(value, ensure_ascii=False, separators=(",", ":")),
            )
        except RedisError as exc:
            logger.warning("Redis cache write failed; result was not cached: %s", exc)


_policy_search_cache = RedisJsonCache()


def get_policy_search_cache() -> RedisJsonCache:
    return _policy_search_cache
