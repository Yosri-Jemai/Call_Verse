import json
import logging
from langchain_core.tools import tool
from src.config import settings
from src.db.connection import get_connection

logger = logging.getLogger("cashy.tools")


@tool
def get_all_accounts(include_inactive: bool = False, customer_id: int | None = None) -> str:
    """Get a list of all accounts for the current customer with their balances, types, and institutions."""
    customer_id = settings.current_customer_id if customer_id is None else customer_id
    logger.info("[get_all_accounts] include_inactive=%s customer_id=%s", include_inactive, customer_id)
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                query = """
                    SELECT a.name, a.current_balance, at.name as account_type,
                           a.currency, a.institution, a.is_active
                    FROM accounts a
                    JOIN account_types at ON a.account_type_id = at.id
                    WHERE a.customer_id = %s
                """
                params = [customer_id]
                if not include_inactive:
                    query += " AND a.is_active = true"
                query += " ORDER BY at.name, a.name"

                cur.execute(query, params)
                columns = [desc[0] for desc in cur.description]
                rows = cur.fetchall()
                results = [dict(zip(columns, row)) for row in rows]

                logger.info("[get_all_accounts] Returned %d accounts", len(results))
                return json.dumps(
                    {"success": True, "count": len(results), "accounts": results},
                    default=str,
                )
    except Exception as e:
        logger.error("[get_all_accounts] Error: %s", e)
        return json.dumps({"success": False, "error": str(e)})
