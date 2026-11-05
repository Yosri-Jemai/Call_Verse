import json
import logging

from langchain_core.tools import tool
from langgraph.types import interrupt

from src.config import settings
from src.db.connection import get_connection

logger = logging.getLogger("cashy.tools")


@tool
def block_account(account_name: str, reason: str) -> str:
    """Request admin approval to block the active customer's bank account."""
    account_name = account_name.strip()
    reason = reason.strip()
    if not account_name:
        return json.dumps({"success": False, "error": "Provide the exact account name to block."})
    if not reason:
        return json.dumps({"success": False, "error": "Provide the customer's reason for blocking the account."})

    customer_id = settings.current_customer_id
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, name, is_active
                    FROM accounts
                    WHERE LOWER(name) = LOWER(%s) AND customer_id = %s
                    """,
                    (account_name, customer_id),
                )
                account = cur.fetchone()
    except Exception as exc:
        logger.exception("[block_account] Lookup failed for customer_id=%s", customer_id)
        return json.dumps({"success": False, "error": f"Account lookup failed: {type(exc).__name__}."})

    if account is None:
        return json.dumps(
            {"success": False, "error": "That exact account was not found for the active customer."}
        )
    account_id, account_name, is_active = account
    if not is_active:
        return json.dumps({"success": False, "error": "That account is already inactive."})

    approval = interrupt(
        {
            "action": "block_account",
            "message": "I’ll refer your account-block request for specialist approval. The account is not blocked yet.",
            "admin_message": f"Block {account_name}: {reason}",
            "details": {
                "account": account_name,
                "reason": reason,
                "effect": "Marks the account inactive in PostgreSQL.",
            },
        }
    )
    if not isinstance(approval, dict) or not approval.get("approved"):
        return json.dumps({"success": False, "message": "The administrator declined the account block."})

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE accounts
                    SET is_active = FALSE
                    WHERE id = %s AND customer_id = %s AND is_active = TRUE
                    RETURNING id, name
                    """,
                    (account_id, customer_id),
                )
                updated_account = cur.fetchone()
    except Exception as exc:
        logger.exception("[block_account] Update failed for customer_id=%s", customer_id)
        return json.dumps({"success": False, "error": f"Account block failed: {type(exc).__name__}."})

    if updated_account is None:
        return json.dumps(
            {"success": False, "error": "The account changed before approval could be applied."}
        )

    logger.info(
        "[block_account] customer_id=%s account_id=%s blocked after admin approval",
        customer_id,
        account_id,
    )
    return json.dumps(
        {
            "success": True,
            "account_id": updated_account[0],
            "account_name": updated_account[1],
            "is_active": False,
            "message": f"Account {updated_account[1]} is now blocked.",
        }
    )
