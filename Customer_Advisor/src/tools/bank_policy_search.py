import json

from langchain_core.tools import tool

from src.agent.policy_rag import (
    get_embeddings,
    get_policy_collection,
    search_policy_collection,
)


@tool
def search_bank_policies(query: str, category: str | None = None) -> str:
    """Search official bank-policy passages for fees, limits, eligibility, or procedures."""
    result = search_policy_collection(
        query,
        get_policy_collection(),
        get_embeddings(),
        category=category,
    )
    return json.dumps(result, ensure_ascii=False)
