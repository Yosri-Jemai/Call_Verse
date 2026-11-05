import json
import logging
from langchain_core.tools import tool
from langgraph.types import interrupt
from src.config import settings
from src.db.connection import get_connection

logger = logging.getLogger("cashy.tools")


@tool
def delete_transaction(transaction_id: int, customer_id: int | None = None) -> str:
    """Delete a transaction for the active customer after confirmation."""
    customer_id = settings.current_customer_id if customer_id is None else customer_id
    logger.info("[delete_transaction] id=%d customer_id=%s", transaction_id, customer_id)

    # Fetch transaction details for confirmation display
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT t.id, t.transaction_date, t.description, t.transaction_type,
                           t.total_amount, a.name as account_name, c.name as category_name
                    FROM transactions t
                    JOIN transaction_entries te ON te.transaction_id = t.id
                    JOIN accounts a ON te.account_id = a.id
                    LEFT JOIN categories c ON te.category_id = c.id
                    WHERE t.id = %s AND a.customer_id = %s
                    LIMIT 1
                    """,
                    (transaction_id, customer_id),
                )
                row = cur.fetchone()
                if not row:
                    return json.dumps({"success": False, "error": f"Transaction {transaction_id} not found"})

                details = {
                    "id": row[0],
                    "date": str(row[1]),
                    "description": row[2],
                    "type": row[3],
                    "amount": float(row[4]),
                    "account": row[5],
                    "category": row[6] or "Uncategorized",
                }

                # Count entries that will be deleted
                cur.execute(
                    "SELECT COUNT(*) FROM transaction_entries WHERE transaction_id = %s",
                    (transaction_id,),
                )
                entry_count = cur.fetchone()[0]

    except Exception as e:
        logger.error("[delete_transaction] Lookup error: %s", e)
        return json.dumps({"success": False, "error": str(e)})

    # --- Confirmation gate ---
    confirmation = {
        "action": "delete_transaction",
        "message": "I’ll send this deletion request for specialist approval. Nothing has been deleted.",
        "admin_message": f"Delete transaction #{transaction_id} and {entry_count} entries?",
        "details": details,
        "entries_to_delete": entry_count,
    }
    response = interrupt(confirmation)

    if not response.get("approved"):
        logger.info("[delete_transaction] Cancelled by user")
        return json.dumps({"success": False, "message": "Deletion cancelled by user"})

    # --- Execute the delete (CASCADE handles transaction_entries) ---
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM transactions WHERE id = %s", (transaction_id,))
                if cur.rowcount == 0:
                    return json.dumps({"success": False, "error": f"Transaction {transaction_id} not found"})

                logger.info("[delete_transaction] Deleted txn_id=%d (%d entries)", transaction_id, entry_count)
                return json.dumps(
                    {
                        "success": True,
                        "transaction_id": transaction_id,
                        "message": f"Transaction #{transaction_id} deleted along with {entry_count} entries",
                        "entries_deleted": entry_count,
                    },
                    default=str,
                )
    except Exception as e:
        logger.error("[delete_transaction] Error: %s", e)
        return json.dumps({"success": False, "error": str(e)})
