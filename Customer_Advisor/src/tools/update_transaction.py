import json
import logging
from datetime import datetime
from langchain_core.tools import tool
from langgraph.types import interrupt
from src.config import settings
from src.db.connection import get_connection

logger = logging.getLogger("cashy.tools")


@tool
def update_transaction(
    transaction_id: int,
    description: str = "",
    amount: float = 0.0,
    date: str = "",
    category_name: str = "",
    notes: str = "",
    customer_id: int | None = None,
) -> str:
    """Update an existing transaction for the active customer after confirmation."""
    customer_id = settings.current_customer_id if customer_id is None else customer_id
    logger.info("[update_transaction] id=%d customer_id=%s", transaction_id, customer_id)

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Fetch current transaction details for confirmation display
                cur.execute(
                    """
                    SELECT t.id, t.transaction_date, t.description, t.transaction_type,
                           t.total_amount, t.notes,
                           a.name as account_name, c.name as category_name
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

                current = {
                    "id": row[0],
                    "date": str(row[1]),
                    "description": row[2],
                    "type": row[3],
                    "amount": float(row[4]),
                    "notes": row[5] or "",
                    "account": row[6],
                    "category": row[7] or "Uncategorized",
                }

                # Build changes summary
                changes = {}
                if description and description.strip():
                    changes["description"] = description.strip()
                if amount > 0:
                    changes["amount"] = amount
                if date and date.strip():
                    try:
                        datetime.strptime(date.strip(), "%Y-%m-%d")
                        changes["date"] = date.strip()
                    except ValueError:
                        return json.dumps({"success": False, "error": "Invalid date format. Use YYYY-MM-DD"})
                if notes and notes.strip():
                    changes["notes"] = notes.strip()

                # Resolve category if provided
                new_category_id = None
                if category_name and category_name.strip():
                    cur.execute(
                        "SELECT id, name FROM categories WHERE name ILIKE %s AND is_active = true",
                        (f"%{category_name}%",),
                    )
                    cat = cur.fetchone()
                    if cat:
                        new_category_id = cat[0]
                        changes["category"] = cat[1]
                    else:
                        return json.dumps({"success": False, "error": f"Category '{category_name}' not found"})

                if not changes:
                    return json.dumps({"success": False, "error": "No fields to update"})

    except Exception as e:
        logger.error("[update_transaction] Lookup error: %s", e)
        return json.dumps({"success": False, "error": str(e)})

    # --- Confirmation gate ---
    confirmation = {
        "action": "update_transaction",
        "message": "I’ll send this transaction change for specialist approval. It has not been applied yet.",
        "admin_message": f"Update transaction #{transaction_id}?",
        "current": current,
        "changes": changes,
    }
    response = interrupt(confirmation)

    if not response.get("approved"):
        logger.info("[update_transaction] Cancelled by user")
        return json.dumps({"success": False, "message": "Update cancelled by user"})

    # --- Execute the update ---
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                # Update transactions table
                tx_updates = []
                tx_params = []
                if "description" in changes:
                    tx_updates.append("description = %s")
                    tx_params.append(changes["description"])
                if "amount" in changes:
                    tx_updates.append("total_amount = %s")
                    tx_params.append(changes["amount"])
                if "date" in changes:
                    tx_updates.append("transaction_date = %s")
                    tx_params.append(changes["date"])
                if "notes" in changes:
                    tx_updates.append("notes = %s")
                    tx_params.append(changes["notes"])

                if tx_updates:
                    tx_params.append(transaction_id)
                    cur.execute(
                        f"UPDATE transactions SET {', '.join(tx_updates)} WHERE id = %s",
                        tx_params,
                    )

                # Update transaction_entries if amount or category changed
                if "amount" in changes:
                    cur.execute(
                        "UPDATE transaction_entries SET amount = %s WHERE transaction_id = %s",
                        (changes["amount"], transaction_id),
                    )
                if new_category_id is not None:
                    cur.execute(
                        "UPDATE transaction_entries SET category_id = %s WHERE transaction_id = %s",
                        (new_category_id, transaction_id),
                    )

                logger.info("[update_transaction] Updated txn_id=%d fields=%s", transaction_id, list(changes.keys()))
                return json.dumps(
                    {
                        "success": True,
                        "transaction_id": transaction_id,
                        "message": f"Transaction #{transaction_id} updated",
                        "changes": changes,
                    },
                    default=str,
                )
    except Exception as e:
        logger.error("[update_transaction] Error: %s", e)
        return json.dumps({"success": False, "error": str(e)})
