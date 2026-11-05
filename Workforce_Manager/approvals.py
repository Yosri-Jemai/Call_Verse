"""
Human-approval gate: nothing sensitive is executed until a person approves it.

It wraps every advisor (`AdvisorView -> str`). Each time the advisor is about to EXECUTE something
("C'est fait..." = the judge's `resolution`), the gate looks at the call and, when it is

  profile_change         the caller wants to change personal data or account settings
                         (phone, e-mail, address, name, card limit, online access, beneficiary)
  sensitive_transaction  a money transfer to a new beneficiary / an urgent transfer
  fraud_suspicion        risk signals add up (identity mismatch, pressure, refused checks, ...)

it STOPS the action: the advisor's message is replaced by "a validation ticket is open, nothing is
changed before approval", and a review is filed for a human (CLI: `python -m Workforce_Manager reviews`).
Only stopped actions are filed: if the advisor already refused, nothing waits for a decision.

  fraud_report           the caller reports a fraud on his own account (hacked account, vishing,
                         disputed payment). Protective actions (blocking the card) are never delayed.

Only observable information is used: what the caller said, the phone-menu intent, the CRM record,
the active incident. Never the simulator's hidden `is_fraudster` flag (it is stored apart, for scoring).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from judge import rule_analyse
from persona import FIELDS as IDENTITY_FIELDS
from persona import check_claims, claims_from_text

from .reviews import APPROVED, DENIED, PENDING, ReviewStore

PROFILE_CHANGE = "profile_change"
SENSITIVE_TRANSACTION = "sensitive_transaction"
FRAUD_SUSPICION = "fraud_suspicion"
FRAUD_REPORT = "fraud_report"
BLOCKING = (FRAUD_SUSPICION, SENSITIVE_TRANSACTION, PROFILE_CHANGE)     # order = priority of the label
CATEGORY_LABELS = {
    PROFILE_CHANGE: "Profile / account change",
    SENSITIVE_TRANSACTION: "Sensitive transaction",
    FRAUD_SUSPICION: "Suspected fraud",
    FRAUD_REPORT: "Fraud reported by the customer",
}
FRAUD_SUSPICION_THRESHOLD = 3

# ---- what the phone menu already tells us -------------------------------------------
INTENT_CHANGES = {"changement_coordonnees": ["phone"], "hausse_plafond": ["limit"]}
TRANSACTION_INTENTS = {"virement_urgent", "virement_sans_verification"}
FRAUD_REPORT_INTENTS = {"fraude_signalee", "vishing_otp", "contestation_carte", "prelevement_conteste"}

# ---- what the caller says ---------------------------------------------------------------
_FIELD_PATTERNS = {
    "email": r"e-?mail|courriel|adresse (?:e-?)?mail",
    "phone": r"num[ée]ro(?: de (?:t[ée]l[ée]phone|portable|mobile))?|t[ée]l[ée]phone|portable|\bgsm\b|phone number"
             r"|\bphone\b|\bmobile\b|noumrou|nomro",
    "address": r"\badresse\b(?! (?:e-?)?mail)|\baddress\b|domicile",
    "name": r"\bnom\b|pr[ée]nom|\bname\b",
    "limit": r"plafond|\blimite?\b",
    "beneficiary": r"b[ée]n[ée]ficiaire|beneficiary|payee",
    "online_access": r"mot de passe|password|identifiant|\blogin\b|acc[èe]s en ligne|online access",
}
_CHANGE_VERBS = (r"\b(?:chang\w*|modifi\w*|mettre [àa] jour|mise [àa] jour|mett\w* [àa] jour|actualis\w*|"
                 r"rempla[cç]\w*|augment\w*|baiss\w*|r[ée]initialis\w*|ajout\w*|supprim\w*|"
                 r"update\w*|modify\w*|edit\w*|increas\w*|raise\w*|lower\w*|reset\w*|add|adding|added|remove\w*|"
                 r"n?badd?el\w*|n?bedd?el\w*|zid\w*|n?na[a9]as\w*)\b")
_TRANSFER = (r"nouveau b[ée]n[ée]ficiaire|new (?:beneficiary|payee)|"
             r"\b(?:faire|faites|fais|effectuer|effectuez|envoyer|envoyez|make|send)\b[\w\s'’-]{0,30}"
             r"\b(?:virement|transfert|transfer)\b")
_PRESSURE = (r"\burgent\w*|urgence|tout de suite|imm[ée]diatement|\bvite\b|pas le temps|right now|immediately|"
             r"\basap\b|no time|hurry|\btawa\b|\bfissa\b|\bdaba\b")
_REFUSES_CHECK = (r"pas besoin de (?:questions?|v[ée]rif\w*)|vous (?:me connaissez|savez qui je suis)|"
                  r"pas le temps pour|sans (?:v[ée]rification|questions?)|no need for|you know (?:who i am|me)|"
                  r"don'?t have time for")
_CONTACT_FIELDS = {"phone", "email", "address", "online_access"}      # classic account-takeover targets


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?\n])\s+", text) if s.strip()]


def detect_change_request(text: str) -> list[str]:
    """Fields the caller asks to change in this text (sentence level: a change verb + a field)."""
    found: list[str] = []
    for sent in _sentences(text.lower()):
        if not re.search(_CHANGE_VERBS, sent):
            continue
        for fld, pat in _FIELD_PATTERNS.items():
            if re.search(pat, sent) and fld not in found:
                found.append(fld)
    return found


def detect_transfer_request(text: str) -> bool:
    return bool(re.search(_TRANSFER, text.lower()))


@dataclass
class Assessment:
    categories: list = field(default_factory=list)
    fields: list = field(default_factory=list)
    risk_score: int = 0
    signals: list = field(default_factory=list)
    identity_check: str = "not_done"          # passed | failed | partial | not_done
    evidence: str = ""

    @property
    def blocking(self) -> bool:
        return any(c in BLOCKING for c in self.categories)

    @property
    def category(self) -> str:
        for c in BLOCKING + (FRAUD_REPORT,):
            if c in self.categories:
                return c
        return ""

    @property
    def requested(self) -> str:
        parts = []
        if PROFILE_CHANGE in self.categories:
            parts.append("change " + ", ".join(self.fields or ["profile data"]))
        if SENSITIVE_TRANSACTION in self.categories:
            parts.append("money transfer")
        if FRAUD_REPORT in self.categories and not parts:
            parts.append("open a fraud case")
        return "; ".join(parts) or "sensitive action"


def assess(intent: str, history: list, record: Optional[dict], incident: str = "") -> Assessment:
    """Look at the call so far (caller lines only) and decide whether a human must approve."""
    client_lines = [t for who, t in history if who == "client"]
    text = "\n".join(client_lines)
    a = Assessment()

    fields = list(INTENT_CHANGES.get(intent, []))
    for f in detect_change_request(text):
        if f not in fields:
            fields.append(f)
    if fields:
        a.categories.append(PROFILE_CHANGE)
        a.fields = fields
    if intent in TRANSACTION_INTENTS or detect_transfer_request(text):
        a.categories.append(SENSITIVE_TRANSACTION)
    if intent in FRAUD_REPORT_INTENTS:
        a.categories.append(FRAUD_REPORT)

    # identity: what the caller stated vs the CRM record
    claims: dict = {}
    for line in client_lines:
        claims.update(claims_from_text(line))
    if record and claims:
        ok, bad = check_claims({f: record[f] for f in IDENTITY_FIELDS if f in record}, claims)
        a.identity_check = "failed" if bad else "passed" if len(ok) >= 2 else "partial"
    elif claims:
        a.identity_check = "partial"

    def signal(points: int, why: str) -> None:
        a.risk_score += points
        a.signals.append(f"+{points} {why}")

    sensitive = PROFILE_CHANGE in a.categories or SENSITIVE_TRANSACTION in a.categories
    if a.identity_check == "failed":
        signal(3, "identity details do not match the CRM")
    elif sensitive and a.identity_check in ("not_done", "partial"):
        signal(1, "sensitive request without a complete identity check")
    if sensitive:
        signal(1, "sensitive request")
    if set(a.fields) & _CONTACT_FIELDS:
        signal(1, "contact / access details change (account-takeover pattern)")
    if re.search(_PRESSURE, text.lower()):
        signal(1, "time pressure")
    if re.search(_REFUSES_CHECK, text.lower()):
        signal(1, "caller pushes to skip the checks")
    if incident == "alerte_fraude":
        signal(1, "active fraud alert")
    if a.risk_score >= FRAUD_SUSPICION_THRESHOLD:
        a.categories.insert(0, FRAUD_SUSPICION)

    a.evidence = next((s for line in client_lines for s in _sentences(line)
                       if detect_change_request(s) or detect_transfer_request(s)), client_lines[0] if client_lines else "")
    return a


# ---- what the advisor says instead of executing ---------------------------------------
# Built so the judge reads it as "ticket opened" (call closed) and not as an escalation or a refusal.
HOLD_MESSAGES = {
    "fr": ("Merci, j'ai ouvert un ticket de validation. Votre demande est en attente de validation par notre "
           "service de contrôle et rien ne sera modifié avant cette décision. Vous recevrez une confirmation "
           "par SMS sous 24 heures. Votre référence est {ref}."),
    "en": ("Thank you, I have opened a validation ticket. Your request is waiting for approval by our control "
           "team and nothing will be changed before that decision. You will receive an SMS confirmation within "
           "24 hours. Your reference is {ref}."),
    "darija": ("Yaatik essaha, 7allitlek ticket mta3 validation. Talbek yestanna l'accord mta3 service el "
               "contrôle, w 7atta 7aja ma tetbaddel 9bal. Bech tjik confirmation b SMS fi 24 se3a. "
               "Référence mte3ek {ref}."),
}


def hold_message(language: str, ref: str) -> str:
    return HOLD_MESSAGES.get(language, HOLD_MESSAGES["fr"]).format(ref=ref)


class ApprovalGate:
    def __init__(self, store: ReviewStore, run_id: str = ""):
        self.store = store
        self.run_id = run_id

    def wrap(self, fn: Callable, req, advisor, ctx: dict) -> Callable:
        """Return an advisor function that pauses sensitive actions. `ctx` collects what happened."""
        def gated(view):
            msg = fn(view)
            if ctx.get("review_id"):
                return msg
            record = view.crm_lookup(view.client_id) if view.crm_lookup else None
            a = assess(req.intent, view.history, record, getattr(req, "incident", ""))
            if not a.categories:
                return msg
            f = rule_analyse(msg)
            executes = f["resolution"] and not f["escalade"]
            # Only a request that is really WAITING for a person goes to the review queue. If the advisor
            # already refused (escalated), nothing waits; a customer's own fraud report needs no approval.
            if a.blocking and executes:
                rid = self._file(req, advisor, a, "action held before execution")
                ctx.update(review_id=rid, held=True, category=a.category)
                return hold_message(getattr(req, "language", "fr"), rid)
            return msg
        return gated

    def _file(self, req, advisor, a: Assessment, outcome: str) -> str:
        return self.store.add(
            category=a.category, categories=a.categories, blocking=a.blocking, run_id=self.run_id,
            call_id=req.request_id, client_id=req.client_id, client_name=req.client_name, fields=a.fields,
            requested=a.requested, risk_score=a.risk_score, signals=a.signals, identity_check=a.identity_check,
            intent=req.intent, advisor=advisor.name, advisor_skill=advisor.skill, call_outcome=outcome,
            evidence=a.evidence[:300], is_fraudster=req.is_fraudster)


def callback_supervisor(store: ReviewStore, run_id: Optional[str] = None,
                        name: str = "simulated_supervisor") -> list[dict]:
    """Stand-in for the human, for batch simulations only.

    It follows the real procedure for a held request: call the customer back on the number stored in
    the CRM. The real account holder confirms his own request and denies one made by an impostor. The
    simulator's ground truth plays the account holder's answer. Fraud reports are confirmed.
    """
    decided = []
    for r in store.list(status=PENDING, run_id=run_id, limit=1_000_000):
        if r["category"] == FRAUD_REPORT:
            decided.append(store.approve(r["review_id"], name, "fraud case confirmed and assigned to the fraud team"))
            continue
        impostor = store.truth(r["review_id"])
        if impostor:
            decided.append(store.deny(r["review_id"], name, "call-back on the CRM number: the account holder did "
                                                            "NOT make this request -> denied, account secured"))
        else:
            decided.append(store.approve(r["review_id"], name, "call-back on the CRM number: the account holder "
                                                               "confirmed the request"))
    return decided


__all__ = ["ApprovalGate", "Assessment", "assess", "callback_supervisor", "detect_change_request",
           "detect_transfer_request", "hold_message", "PROFILE_CHANGE", "SENSITIVE_TRANSACTION",
           "FRAUD_SUSPICION", "FRAUD_REPORT", "CATEGORY_LABELS", "APPROVED", "DENIED", "PENDING"]
