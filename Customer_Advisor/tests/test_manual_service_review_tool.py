import importlib
import json


manual_review_module = importlib.import_module("src.tools.request_manual_service_review")


def _request():
    return {
        "request_type": "transaction_dispute",
        "summary": "Dispute an unrecognized card charge.",
        "reason": "The customer says they did not make the purchase.",
    }


def test_manual_service_review_only_authorizes_follow_up(monkeypatch):
    interruptions = []

    def approve(confirmation):
        interruptions.append(confirmation)
        return {"approved": True}

    monkeypatch.setattr(manual_review_module, "interrupt", approve)
    result = json.loads(manual_review_module.request_manual_service_review.invoke(_request()))

    assert result["success"] is True
    assert result["status"] == "approved_for_manual_follow_up"
    assert "did not perform" in result["message"]
    assert interruptions == [
        {
            "action": "manual_service_review",
            "message": "I’ll refer your request to a specialist for review. It has not been carried out.",
            "admin_message": (
                "Dispute an unrecognized card charge. — "
                "The customer says they did not make the purchase."
            ),
            "details": {
                **_request(),
                "disposition": "specialist_follow_up",
                "execution": (
                    "This prototype has no connected card, dispute, transfer, limit, or profile-management "
                    "service. Approval authorizes manual follow-up only; it does not change bank data."
                ),
            },
        }
    ]


def test_manual_service_review_decline_does_not_claim_follow_up(monkeypatch):
    monkeypatch.setattr(manual_review_module, "interrupt", lambda _: {"approved": False})

    result = json.loads(manual_review_module.request_manual_service_review.invoke(_request()))

    assert result["success"] is False
    assert result["status"] == "declined"


def test_manual_service_review_requires_a_summary_and_reason(monkeypatch):
    monkeypatch.setattr(
        manual_review_module,
        "interrupt",
        lambda _: (_ for _ in ()).throw(AssertionError("Invalid requests must not pause.")),
    )

    result = json.loads(
        manual_review_module.request_manual_service_review.invoke(
            {**_request(), "summary": "  "}
        )
    )

    assert result["success"] is False
    assert "summary" in result["error"]


def test_restricted_request_is_saved_for_review_but_never_executed(monkeypatch):
    confirmation = {}
    request = {
        "request_type": "restricted_request",
        "summary": "Customer requested bypassing identity checks.",
        "reason": "The requested operation is not permitted without verification.",
    }

    def acknowledge(value):
        confirmation.update(value)
        return {"approved": True}

    monkeypatch.setattr(manual_review_module, "interrupt", acknowledge)
    result = json.loads(manual_review_module.request_manual_service_review.invoke(request))

    assert confirmation["message"] == (
        "I can’t carry out that request. I’ve saved a summary for specialist review; "
        "no operation has been performed."
    )
    assert confirmation["details"]["disposition"] == "review_only"
    assert "cannot authorize or perform" in confirmation["details"]["execution"]
    assert result == {
        "success": True,
        "status": "saved_for_specialist_review",
        "message": "The request remains saved for specialist review. No operation was performed.",
    }


def test_declined_restricted_request_remains_recorded_without_operation(monkeypatch):
    monkeypatch.setattr(manual_review_module, "interrupt", lambda _: {"approved": False})

    result = json.loads(
        manual_review_module.request_manual_service_review.invoke(
            {
                "request_type": "restricted_request",
                "summary": "Customer requested bypassing identity checks.",
                "reason": "The requested operation is not permitted without verification.",
            }
        )
    )

    assert result["success"] is False
    assert result["status"] == "declined"
    assert "remains recorded" in result["message"]
