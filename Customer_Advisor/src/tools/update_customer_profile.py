import json
import logging
import re

from langchain_core.tools import tool

from src.config import settings
from src.db.connection import get_connection

logger = logging.getLogger("cashy.tools")
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@tool
def update_customer_profile(name: str = "", email: str = "") -> str:
    """Update the active customer's name and/or email address when explicitly requested."""
    changes: dict[str, str] = {}
    if name:
        normalized_name = name.strip()
        if not normalized_name or len(normalized_name) > 200:
            return json.dumps({"success": False, "error": "Name must be between 1 and 200 characters."})
        if any(ord(character) < 32 for character in normalized_name):
            return json.dumps({"success": False, "error": "Name contains invalid control characters."})
        changes["name"] = normalized_name

    if email:
        normalized_email = email.strip()
        if len(normalized_email) > 255 or not EMAIL_PATTERN.fullmatch(normalized_email):
            return json.dumps({"success": False, "error": "Provide a valid email address."})
        changes["email"] = normalized_email

    if not changes:
        return json.dumps({"success": False, "error": "Provide a new name, email address, or both."})

    customer_id = settings.current_customer_id
    assignments = ", ".join(f"{field} = %s" for field in changes)
    values = list(changes.values())
    values.append(customer_id)
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE customers
                    SET {assignments}, updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s
                    RETURNING id, name, email
                    """,
                    values,
                )
                row = cur.fetchone()
                if row is None:
                    return json.dumps({"success": False, "error": "The active customer profile was not found."})
    except Exception as exc:
        logger.exception("[update_customer_profile] Failed for customer_id=%s", customer_id)
        return json.dumps({"success": False, "error": f"Profile update failed: {type(exc).__name__}."})

    logger.info(
        "[update_customer_profile] customer_id=%s fields=%s",
        customer_id,
        sorted(changes),
    )
    return json.dumps(
        {
            "success": True,
            "customer_id": row[0],
            "name": row[1],
            "email": row[2],
            "updated_fields": sorted(changes),
        }
    )
