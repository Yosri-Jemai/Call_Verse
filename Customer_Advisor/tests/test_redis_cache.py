import json
from datetime import date

from redis.exceptions import ConnectionError as RedisConnectionError

from src.agent import policy_rag
from src.cache import redis_cache
from src.cache.redis_cache import RedisJsonCache
from src.config import settings
from src import api
import src.tools.bank_policy_search as bank_policy_search_tool


class FakeRedis:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value):
        self.values[key] = value


class UnavailableRedis:
    def get(self, key):
        raise RedisConnectionError("Redis is unavailable")

    def set(self, key, value):
        raise RedisConnectionError("Redis is unavailable")


def test_redis_json_cache_round_trips_json_without_expiration():
    client = FakeRedis()
    cache = RedisJsonCache(client)
    value = {"found": True, "documents": [{"title": "Frais"}]}

    cache.set("policy-key", value)

    assert cache.get("policy-key") == value
    assert json.loads(client.values["policy-key"]) == value


def test_redis_cache_fails_open_when_redis_is_unavailable(caplog):
    cache = RedisJsonCache(UnavailableRedis())

    assert cache.get("policy-key") is None
    cache.set("policy-key", {"found": False})

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

        def get(self, key):
            return self.values.get(key)

        def set(self, key, value):
            self.values[key] = value

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
    assert next(iter(cache.values.values())) == result


def test_blank_policy_search_does_not_access_redis_or_rag(monkeypatch):
    def fail_if_called():
        raise AssertionError("Blank queries should not access the cache or RAG index.")

    monkeypatch.setattr(bank_policy_search_tool, "get_policy_search_cache", fail_if_called)
    monkeypatch.setattr(bank_policy_search_tool, "search_policy_collection", fail_if_called)

    assert bank_policy_search_tool._search_policy("  ") == {
        "found": False,
        "message": policy_rag.NO_RELEVANT_DOCUMENTS,
    }


def test_redis_cache_test_endpoint_performs_and_verifies_a_cached_policy_lookup(monkeypatch):
    class MemoryCache:
        def __init__(self):
            self.values = {}

        def get(self, key):
            return self.values.get(key)

        def set(self, key, value):
            self.values[key] = value

    cache = MemoryCache()
    result = {"found": True, "documents": [{"title": "Paiement sans contact"}]}
    calls = []

    def search(query, category):
        calls.append((query, category))
        key = bank_policy_search_tool.build_policy_cache_key(query, category)
        cached = cache.get(key)
        if cached is not None:
            return cached
        cache.set(key, result)
        return result

    monkeypatch.setattr(redis_cache, "get_policy_search_cache", lambda: cache)
    monkeypatch.setattr(bank_policy_search_tool, "_search_policy", search)

    response = api.verify_redis_policy_cache()

    assert response["cached"] is True
    assert response["cache_hit_verified"] is True
    assert response["already_cached"] is False
    assert response["policy_found"] is True
    assert len(calls) == 2
    assert calls[0] == calls[1]
