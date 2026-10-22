import json
import logging
from langchain_core.tools import tool
from src.db.connection import get_connection

logger = logging.getLogger("cashy.tools")


@tool
def finance_db_query(query: str) -> str:
    """Execute a SQL SELECT query on the financial database. Only SELECT queries are allowed.
    Use this for custom queries not covered by the other tools."""
    logger.info("[finance_db_query] SQL: %s", query[:120])
    if not query.strip().upper().startswith("SELECT"):
        logger.warning("[finance_db_query] Rejected non-SELECT query")
        return json.dumps({"error": "Only SELECT queries allowed"})

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query)
                columns = [desc[0] for desc in cur.description]
                rows = cur.fetchall()
                results = [dict(zip(columns, row)) for row in rows]
                logger.info("[finance_db_query] Returned %d rows", len(results))
                return json.dumps(
                    {"success": True, "count": len(results), "data": results},
                    default=str,
                )
    except Exception as e:
        logger.error("[finance_db_query] Error: %s", e)
        return json.dumps({"error": str(e)})
