import json
import logging
from datetime import datetime
from langchain_core.tools import tool
from langgraph.types import interrupt
from src.config import settings
from src.db.connection import get_connection

logger = logging.getLogger("cashy.tools")


@tool
def create_transaction(
    transaction_type: str,
    transaction_description: str,
    amount: float,
    account_name: str,
    category_name: str = "",
    date: str = "",
    notes: str = "",
    customer_id: int | None = None,
) -> str:
    """Create a new financial transaction for the active customer after confirmation."""
    customer_id = settings.current_customer_id if customer_id is None else customer_id
    logger.info("[create_transaction] type=%s amount=%.2f account=%s category=%s customer_id=%s",
                transaction_type, amount, account_name, category_name or "none", customer_id)

    # --- Validation ---
    if amount <= 0:
        return json.dumps({"success": False, "error": "Amount must be positive"})

    if transaction_type not in ("expense", "income", "transfer"):
        return json.dumps(
            {"success": False, "error": "transaction_type must be 'expense', 'income', or 'transfer'"}
        )

    if date and date.strip():
        try:
            transaction_date = datetime.strptime(date.strip(), "%Y-%m-%d").date()
        except ValueError:
            return json.dumps({"success": False, "error": "Invalid date format. Use YYYY-MM-DD"})
    else:
        transaction_date = datetime.now().date()

    # --- Resolve names to IDs ---
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, name FROM accounts WHERE name ILIKE %s AND is_active = true AND customer_id = %s",
                    (f"%{account_name}%", customer_id),
                )
                account = cur.fetchone()
                if not account:
                    return json.dumps(
                        {"success": False, "error": f"Account '{account_name}' not found"}
                    )
                account_id, account_full_name = account

                category_id = None
                category_full_name = "Uncategorized"
                if category_name and category_name.strip():
                    cur.execute(
                        "SELECT id, name FROM categories WHERE name ILIKE %s AND is_active = true",
                        (f"%{category_name}%",),
                    )
                    category = cur.fetchone()
                    if category:
                        category_id, category_full_name = category
    except Exception as e:
        logger.error("[create_transaction] Lookup error: %s", e)
        return json.dumps({"success": False, "error": str(e)})

    entry_type = "credit" if transaction_type == "income" else "debit"
    desc = transaction_description.strip()
    note = notes.strip() if notes and notes.strip() else None

    # --- Confirmation gate ---
    confirmation = {
        "action": "create_transaction",
        "message": "I’ll send this transaction request for specialist approval. It has not been recorded yet.",
        "admin_message": f"Create {transaction_type} of ${amount:.2f} on {account_full_name}?",
        "details": {
            "type": transaction_type,
            "amount": float(amount),
            "account": account_full_name,
            "category": category_full_name,
            "date": str(transaction_date),
            "description": desc,
        },
    }
    response = interrupt(confirmation)

    if not response.get("approved"):
        logger.info("[create_transaction] Cancelled by user")
        return json.dumps({"success": False, "message": "Transaction cancelled by user"})

    # --- Execute DB write ---
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO transactions
                    (transaction_date, description, transaction_type, total_amount, notes)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (transaction_date, desc, transaction_type, amount, note),
                )
                transaction_id = cur.fetchone()[0]

                cur.execute(
                    """
                    INSERT INTO transaction_entries
                    (transaction_id, account_id, category_id, amount, entry_type, description)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (transaction_id, account_id, category_id, amount, entry_type, desc),
                )
                entry_id = cur.fetchone()[0]

                logger.info("[create_transaction] Created txn_id=%d entry_id=%d", transaction_id, entry_id)
                return json.dumps(
                    {
                        "success": True,
                        "transaction_id": transaction_id,
                        "entry_id": entry_id,
                        "message": f"{transaction_type.title()} of ${amount:.2f} recorded on {transaction_date}",
                        "details": {
                            "type": transaction_type,
                            "amount": float(amount),
                            "account": account_full_name,
                            "category": category_full_name,
                            "date": str(transaction_date),
                            "description": desc,
                        },
                    },
                    default=str,
                )
    except Exception as e:
        logger.error("[create_transaction] Error: %s", e)
        return json.dumps({"success": False, "error": str(e)})
