import json
import logging
from datetime import datetime
from langchain_core.tools import tool
from src.config import settings
from src.db.connection import get_connection

logger = logging.getLogger("cashy.tools")


@tool
def get_spending_by_category(month: int = 0, year: int = 0, customer_id: int | None = None) -> str:
    """Get spending breakdown by category for the current customer and month/year."""
    customer_id = settings.current_customer_id if customer_id is None else customer_id
    try:
        current_month = datetime.now().month if month == 0 else month
        current_year = datetime.now().year if year == 0 else year
        logger.info("[get_spending_by_category] month=%d, year=%d customer_id=%s", current_month, current_year, customer_id)

        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT c.name as category,
                           c.category_type,
                           SUM(te.amount) as total
                    FROM transaction_entries te
                    JOIN categories c ON te.category_id = c.id
                    JOIN transactions t ON te.transaction_id = t.id
                    JOIN accounts a ON te.account_id = a.id
                    WHERE EXTRACT(MONTH FROM t.transaction_date) = %s
                        AND EXTRACT(YEAR FROM t.transaction_date) = %s
                        AND te.entry_type = 'debit'
                        AND c.category_type = 'expense'
                        AND a.customer_id = %s
                    GROUP BY c.name, c.category_type
                    ORDER BY total DESC
                    """,
                    (current_month, current_year, customer_id),
                )
                columns = [desc[0] for desc in cur.description]
                rows = cur.fetchall()
                results = [dict(zip(columns, row)) for row in rows]

                logger.info("[get_spending_by_category] Returned %d categories", len(results))
                return json.dumps(
                    {
                        "success": True,
                        "month": current_month,
                        "year": current_year,
                        "spending": results,
                    },
                    default=str,
                )
    except Exception as e:
        logger.error("[get_spending_by_category] Error: %s", e)
        return json.dumps({"success": False, "error": str(e)})
