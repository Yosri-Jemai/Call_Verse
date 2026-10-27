import hashlib
import json
import logging
from datetime import date

from langchain_core.tools import tool

from src.agent.policy_rag import (
    NO_RELEVANT_DOCUMENTS,
    TOP_K,
    get_embeddings,
    get_policy_collection,
    search_policy_collection,
)
from src.cache.redis_cache import get_policy_search_cache
from src.config import settings

logger = logging.getLogger("cashy.agent")
POLICY_CACHE_KEY_VERSION = 1


def build_policy_cache_key(
    query: str,
    category: str | None = None,
    *,
    today: date | None = None,
) -> str:
    """Build a versioned key without storing the user's query in Redis."""
    cache_scope = json.dumps(
        {
            "version": POLICY_CACHE_KEY_VERSION,
            "query": " ".join(query.split()),
            "category": category,
            "date": (today or date.today()).isoformat(),
            "top_k": TOP_K,
            "similarity_threshold": settings.similarity_threshold,
            "embedding_model": settings.embedding_model,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(cache_scope.encode("utf-8")).hexdigest()
    return f"cashy:policy-search:v{POLICY_CACHE_KEY_VERSION}:{digest}"


def _search_policy(query: str, category: str | None = None) -> dict:
    if not query.strip():
        return {"found": False, "message": NO_RELEVANT_DOCUMENTS}

    cache = get_policy_search_cache()
    cache_key = build_policy_cache_key(query, category)
    cached_result = cache.get(cache_key)
    if cached_result is not None:
        logger.info("Bank-policy retrieval cache hit")
        return cached_result

    result = search_policy_collection(
        query,
        get_policy_collection(),
        get_embeddings(),
        category=category,
    )
    cache.set(cache_key, result, settings.redis_policy_cache_ttl_seconds)
    return result


@tool
def search_bank_policies(query: str, category: str | None = None) -> str:
    """Search official bank-policy passages for fees, limits, eligibility, or procedures."""
    result = _search_policy(query, category)
    return json.dumps(result, ensure_ascii=False)
