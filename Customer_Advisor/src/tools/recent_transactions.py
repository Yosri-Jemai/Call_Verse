import json
import logging
from langchain_core.tools import tool
from src.config import settings
from src.db.connection import get_connection

logger = logging.getLogger("cashy.tools")


@tool
def get_recent_transactions(limit: int = 10, customer_id: int | None = None) -> str:
    """Get the most recent transactions for the current customer."""
    customer_id = settings.current_customer_id if customer_id is None else customer_id
    logger.info("[get_recent_transactions] limit=%d customer_id=%s", limit, customer_id)
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT t.id, t.transaction_date, t.description, t.transaction_type,
                           t.total_amount,
                           debit_a.name  AS from_account,
                           credit_a.name AS to_account,
                           c.name        AS category
                    FROM transactions t
                    LEFT JOIN transaction_entries debit_te
                        ON debit_te.transaction_id = t.id AND debit_te.entry_type = 'debit'
                    LEFT JOIN accounts debit_a
                        ON debit_te.account_id = debit_a.id
                    LEFT JOIN transaction_entries credit_te
                        ON credit_te.transaction_id = t.id AND credit_te.entry_type = 'credit'
                    LEFT JOIN accounts credit_a
                        ON credit_te.account_id = credit_a.id
                    LEFT JOIN categories c
                        ON debit_te.category_id = c.id
                    WHERE (debit_a.customer_id = %s OR credit_a.customer_id = %s)
                    ORDER BY t.transaction_date DESC, t.id DESC
                    LIMIT %s
                    """,
                    (customer_id, customer_id, limit),
                )
                columns = [desc[0] for desc in cur.description]
                rows = cur.fetchall()
                results = [dict(zip(columns, row)) for row in rows]

                logger.info("[get_recent_transactions] Returned %d transactions", len(results))
                return json.dumps(
                    {"success": True, "count": len(results), "transactions": results},
                    default=str,
                )
    except Exception as e:
        logger.error("[get_recent_transactions] Error: %s", e)
        return json.dumps({"success": False, "error": str(e)})
