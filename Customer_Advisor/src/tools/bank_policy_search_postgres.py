import json

from langchain_core.tools import tool

from src.db.connection import get_connection

MAX_POLICY_RESULTS = 5

CREATE_POLICY_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS bank_policy_documents (
        id BIGSERIAL PRIMARY KEY,
        category TEXT NOT NULL,
        title TEXT NOT NULL,
        content TEXT NOT NULL,
        source TEXT NOT NULL,
        source_url TEXT,
        valid_from DATE,
        valid_until DATE,
        is_active BOOLEAN NOT NULL DEFAULT TRUE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        search_vector TSVECTOR GENERATED ALWAYS AS (
            to_tsvector('french', coalesce(title, '') || ' ' ||
                        coalesce(category, '') || ' ' || coalesce(content, ''))
        ) STORED,
        UNIQUE (source, title)
    )
"""

CREATE_POLICY_INDEX_SQL = """
    CREATE INDEX IF NOT EXISTS idx_bank_policy_documents_search
    ON bank_policy_documents USING GIN (search_vector)
"""


def ensure_policy_table() -> None:
    """Create the policy-document store and search index if they do not exist."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(CREATE_POLICY_TABLE_SQL)
            cur.execute(CREATE_POLICY_INDEX_SQL)


@tool
def search_bank_policies(query: str) -> str:
    """Search the PostgreSQL full-text index for fees, limits, eligibility, or procedures."""
    if not query.strip():
        return json.dumps({"found": False, "message": "The policy search query is empty."})

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT category, title, content, source, source_url, valid_from, valid_until
                FROM bank_policy_documents
                WHERE is_active = TRUE
                  AND (valid_from IS NULL OR valid_from <= CURRENT_DATE)
                  AND (valid_until IS NULL OR valid_until >= CURRENT_DATE)
                  AND search_vector @@ websearch_to_tsquery('french', %s)
                ORDER BY ts_rank(search_vector, websearch_to_tsquery('french', %s)) DESC,
                         updated_at DESC
                LIMIT %s
                """,
                (query, query, MAX_POLICY_RESULTS),
            )
            rows = cur.fetchall()

    if not rows:
        return json.dumps(
            {
                "found": False,
                "message": "No active bank-policy document matched this question.",
            }
        )

    documents = [
        {
            "category": row[0],
            "title": row[1],
            "content": row[2],
            "source": row[3],
            "source_url": row[4],
            "valid_from": row[5],
            "valid_until": row[6],
        }
        for row in rows
    ]
    return json.dumps({"found": True, "documents": documents}, ensure_ascii=False, default=str)
