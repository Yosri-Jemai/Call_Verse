import json
import logging
from langchain_core.tools import tool
from src.config import settings
from src.db.connection import get_connection

logger = logging.getLogger("cashy.tools")


@tool
def get_account_balance(account_name: str, customer_id: int | None = None) -> str:
    """Get the current balance for a specific account by name for the active customer."""
    customer_id = settings.current_customer_id if customer_id is None else customer_id
    logger.info("[get_account_balance] account_name=%s customer_id=%s", account_name, customer_id)
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT a.name, a.current_balance, at.name as account_type,
                           a.currency, a.institution
                    FROM accounts a
                    JOIN account_types at ON a.account_type_id = at.id
                    WHERE a.name ILIKE %s AND a.is_active = true AND a.customer_id = %s
                    """,
                    (f"%{account_name}%", customer_id),
                )
                result = cur.fetchone()

                if result:
                    logger.info("[get_account_balance] Found: %s = %s %s", result[0], result[1], result[3])
                    return json.dumps(
                        {
                            "success": True,
                            "account_name": result[0],
                            "balance": float(result[1]),
                            "account_type": result[2],
                            "currency": result[3],
                            "institution": result[4] or "N/A",
                        },
                        default=str,
                    )
                else:
                    logger.warning("[get_account_balance] Account '%s' not found", account_name)
                    return json.dumps(
                        {"success": False, "error": f"Account '{account_name}' not found"}
                    )
    except Exception as e:
        logger.error("[get_account_balance] Error: %s", e)
        return json.dumps({"success": False, "error": str(e)})
