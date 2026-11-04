import json
import logging
from typing import Literal

from langchain_core.tools import tool
from langgraph.types import interrupt

logger = logging.getLogger("cashy.tools")


@tool
def request_manual_service_review(
    request_type: Literal[
        "card_block",
        "transaction_dispute",
        "fraud_response",
        "money_transfer",
        "limit_change",
        "profile_change",
        "restricted_request",
    ],
    summary: str,
    reason: str,
) -> str:
    """Send an unsupported high-impact bank or profile-service request for manual follow-up."""
    summary = summary.strip()
    reason = reason.strip()
    if not summary or not reason:
        return json.dumps(
            {"success": False, "error": "Provide a short request summary and reason."}
        )

    review_only = request_type == "restricted_request"
    customer_message = (
        "I can’t carry out that request. I’ve saved a summary for specialist review; "
        "no operation has been performed."
        if review_only
        else "I’ll refer your request to a specialist for review. It has not been carried out."
    )
    execution_note = (
        "Restricted request: specialist review only. Approval cannot authorize or perform the requested operation."
        if review_only
        else (
            "This prototype has no connected card, dispute, transfer, limit, or profile-management "
            "service. Approval authorizes manual follow-up only; it does not change bank data."
        )
    )
    approval = interrupt(
        {
            "action": "manual_service_review",
            "message": customer_message,
            "admin_message": f"{summary} — {reason}",
            "details": {
                "request_type": request_type,
                "summary": summary,
                "reason": reason,
                "disposition": "review_only" if review_only else "specialist_follow_up",
                "execution": execution_note,
            },
        }
    )
    if not isinstance(approval, dict) or not approval.get("approved"):
        logger.info("Administrator declined manual service request type=%s", request_type)
        return json.dumps(
            {
                "success": False,
                "status": "declined",
                "message": (
                    "The request remains recorded for specialist review. No operation was performed."
                    if review_only
                    else "The administrator declined the manual service request."
                ),
            }
        )

    if review_only:
        logger.info("Administrator acknowledged restricted request type=%s", request_type)
        return json.dumps(
            {
                "success": True,
                "status": "saved_for_specialist_review",
                "message": "The request remains saved for specialist review. No operation was performed.",
            }
        )

    logger.info("Administrator approved manual follow-up type=%s", request_type)
    return json.dumps(
        {
            "success": True,
            "status": "approved_for_manual_follow_up",
            "message": (
                "The administrator approved manual follow-up. This prototype did not perform "
                "a card, dispute, transfer, or limit operation; the administrator must complete "
                "it through the bank's authorized system."
            ),
        }
    )
