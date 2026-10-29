import json
from datetime import date

from redis.exceptions import ConnectionError as RedisConnectionError

from src.agent import policy_rag
from src.cache.redis_cache import RedisJsonCache
from src.config import settings
import src.tools.bank_policy_search as bank_policy_search_tool


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.expirations = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, ex):
        self.values[key] = value
        self.expirations[key] = ex


class UnavailableRedis:
    def get(self, key):
        raise RedisConnectionError("Redis is unavailable")

    def set(self, key, value, ex):
        raise RedisConnectionError("Redis is unavailable")


def test_redis_json_cache_round_trips_json_and_sets_expiration():
    client = FakeRedis()
    cache = RedisJsonCache(client)
    value = {"found": True, "documents": [{"title": "Frais"}]}

    cache.set("policy-key", value, ttl_seconds=120)

    assert cache.get("policy-key") == value
    assert json.loads(client.values["policy-key"]) == value
    assert client.expirations["policy-key"] == 120


def test_redis_cache_fails_open_when_redis_is_unavailable(caplog):
    cache = RedisJsonCache(UnavailableRedis())

    assert cache.get("policy-key") is None
    cache.set("policy-key", {"found": False}, ttl_seconds=30)

    assert "Redis cache read failed" in caplog.text
    assert "Redis cache write failed" in caplog.text


def test_policy_cache_key_scopes_query_category_and_validity_date():
    original = bank_policy_search_tool.build_policy_cache_key(
        "  frais   de carte ", "fees", today=date(2026, 10, 27)
    )

    assert original == bank_policy_search_tool.build_policy_cache_key(
        "frais de carte", "fees", today=date(2026, 10, 27)
    )
    assert original != bank_policy_search_tool.build_policy_cache_key(
        "frais de carte", "limits", today=date(2026, 10, 27)
    )
    assert original != bank_policy_search_tool.build_policy_cache_key(
        "frais de carte", "fees", today=date(2026, 10, 28)
    )
    assert "frais" not in original


def test_policy_search_uses_cached_result_before_loading_rag(monkeypatch):
    class MemoryCache:
        def __init__(self):
            self.values = {}
            self.ttls = {}

        def get(self, key):
            return self.values.get(key)

        def set(self, key, value, ttl_seconds):
            self.values[key] = value
            self.ttls[key] = ttl_seconds

    cache = MemoryCache()
    result = {"found": True, "documents": [{"title": "Tarif"}]}
    retrieval_calls = []
    monkeypatch.setattr(bank_policy_search_tool, "get_policy_search_cache", lambda: cache)
    monkeypatch.setattr(
        bank_policy_search_tool,
        "get_policy_collection",
        lambda: object(),
    )
    monkeypatch.setattr(bank_policy_search_tool, "get_embeddings", lambda: object())

    def retrieve(*args, **kwargs):
        retrieval_calls.append((args, kwargs))
        return result

    monkeypatch.setattr(bank_policy_search_tool, "search_policy_collection", retrieve)

    assert bank_policy_search_tool._search_policy("frais de carte", "fees") == result
    assert bank_policy_search_tool._search_policy("frais de carte", "fees") == result

    assert len(retrieval_calls) == 1
    assert next(iter(cache.ttls.values())) == settings.redis_policy_cache_ttl_seconds


def test_blank_policy_search_does_not_access_redis_or_rag(monkeypatch):
    def fail_if_called():
        raise AssertionError("Blank queries should not access the cache or RAG index.")

    monkeypatch.setattr(bank_policy_search_tool, "get_policy_search_cache", fail_if_called)
    monkeypatch.setattr(bank_policy_search_tool, "search_policy_collection", fail_if_called)

    assert bank_policy_search_tool._search_policy("  ") == {
        "found": False,
        "message": policy_rag.NO_RELEVANT_DOCUMENTS,
    }
