"""
QA scorecard: turns a finished (or running) call into per-criterion scores, a checklist and the
critical compliance failures. It is what makes the simulator a *measuring tool*: two advisors, or two
versions of the same advisor, can be compared on the same seeded calls.

Input : a ConversationState (uses st.turn_log filled by ClientSimulatorAgent.perceive, plus ground truth
        such as is_fraudster / intent / status).
Output: plain dict (JSON-friendly) -> {total, grade, criteria, critical_failures, checklist, per_turn}.

Criteria and weights
    greeting      10   polite opening + offer to help
    verification  20   identity checked, with enough facts, BEFORE any action
    empathy       15   acknowledges an upset client early, never rude
    accuracy      15   concrete, sourced, not hesitant, not curt
    compliance    25   no secret code / OTP request, no action before verification, no fraud served,
                       no guaranteed-refund promise, security advice when relevant
    resolution    10   outcome (and, in trap scenarios, how many mandatory steps were done)
    closing        5   closes politely once the call is resolved

Rules only (French / English, a few Tunisian greetings): fast, deterministic and explainable. A criterion
that does not apply (identity check on a low-risk question, closing on an unfinished call) is marked
`na` and left out of the weighted average.
Any critical failure caps the total at 40.
"""
from __future__ import annotations

WEIGHTS = {"greeting": 10, "verification": 20, "empathy": 15, "accuracy": 15,
           "compliance": 25, "resolution": 10, "closing": 5}
LABELS = {"greeting": "Greeting", "verification": "Identity verification", "empathy": "Empathy",
          "accuracy": "Accuracy & clarity", "compliance": "Compliance", "resolution": "Resolution",
          "closing": "Closing"}
CRITICAL_CAP = 40
LOW_RISK = ("plafond_carte", "frais_bancaires", "solde_mouvements")      # no identity check required
UPSET = {"angry", "violent", "stressed", "irritated", "rude"}


def _first(log: list, key: str):
    return next((e["turn"] for e in log if e["flags"].get(key)), None)


def _any(log: list, key: str) -> bool:
    return any(e["flags"].get(key) for e in log)


def _turns(log: list, key: str) -> list[int]:
    return [e["turn"] for e in log if e["flags"].get(key)]


def _crit(msg: str, turns: list[int]) -> str:
    return f"{msg} (turn {', '.join(map(str, turns))})" if turns else msg


def score_call(st) -> dict:
    req = st.request
    log = st.turn_log
    intent = req.intent
    needs_verif = intent not in LOW_RISK
    criteria: dict[str, dict] = {}
    critical: list[str] = []
    warnings: list[str] = []

    def put(name: str, score, evidence: list[str], na: bool = False):
        criteria[name] = dict(label=LABELS[name], weight=WEIGHTS[name], na=na,
                              score=None if na else int(max(0, min(100, round(score)))), evidence=evidence)

    if not log:
        for k in WEIGHTS:
            put(k, 0, ["No advisor message yet."], na=True)
        return dict(total=None, grade="-", criteria=criteria, critical_failures=[], warnings=[],
                    checklist=[], per_turn=[], intent=intent, status=st.status)

    ask_turn = _first(log, "identite")
    act_turn = _first(log, "resolution")
    fields_asked = {fld for e in log for fld in e["flags"].get("identity_fields", [])}

    # ---- greeting --------------------------------------------------------------------------
    f0 = log[0]["flags"]
    g, ev = 0, []
    if f0.get("accueil"):
        g += 50
        ev.append("Greets the client")
    else:
        ev.append("No greeting in the first message")
    if f0.get("offre_aide"):
        g += 30
        ev.append("Offers help")
    if f0.get("tone") in ("polite", "neutral"):
        g += 20
    else:
        ev.append(f"Tone of the first message: {f0.get('tone')}")
    put("greeting", g, ev)

    # ---- identity verification --------------------------------------------------------------
    if not needs_verif:
        put("verification", 0, ["Low-risk question: identity check not required."], na=True)
    else:
        if ask_turn is None:
            put("verification", 0, ["Identity was never verified."])
        elif act_turn is not None and ask_turn > act_turn:
            put("verification", 20, [f"Verification asked at turn {ask_turn}, AFTER the action at turn {act_turn}."])
        else:
            v, ev = 60, [f"Verification asked at turn {ask_turn}"]
            if len(fields_asked) >= 2:
                v += 25
                ev.append(f"Specific facts requested: {', '.join(sorted(fields_asked))}")
            else:
                ev.append("Generic or single-fact check: ask at least two specific facts")
            if ask_turn <= 2:
                v += 15
                ev.append("Asked early")
            put("verification", v, ev)

    # ---- empathy ----------------------------------------------------------------------------
    upset_turns = [e["turn"] for e in log if e["mood_before"] <= -0.2 or e["sentiment_before"] in UPSET
                   or req.urgency >= 3]
    emp_turns = _turns(log, "empathie")
    rude_turns = [e["turn"] for e in log if e["flags"].get("tone") == "rude"]
    if upset_turns:
        first_need = upset_turns[0]
        got = [t for t in emp_turns if t >= first_need]
        if not got:
            em, ev = 0, ["Client was upset / urgent and the advisor never acknowledged it"]
        elif got[0] <= first_need + 1:
            em, ev = 100, [f"Empathy at turn {got[0]}, right when needed"]
        else:
            em, ev = 60, [f"Empathy only at turn {got[0]} (needed from turn {first_need})"]
    else:
        em, ev = (100, ["Empathy shown"]) if emp_turns else (75, ["Client was calm; no empathy shown"])
    if rude_turns:
        em = max(0, em - 50)
        ev.append(_crit("Rude or dismissive wording", rude_turns))
    put("empathy", em, ev)

    # ---- accuracy & clarity -----------------------------------------------------------------
    acc, ev = 70, []
    unsure, curt = _turns(log, "incertitude"), _turns(log, "trop_court")
    if _any(log, "source"):
        acc += 15
        ev.append("Cites a procedure / rule")
    if _any(log, "contient_chiffre") or _any(log, "reference"):
        acc += 15
        ev.append("Gives concrete figures or a reference")
    if unsure:
        acc -= 20 * len(unsure)
        ev.append(_crit("Hesitant or unsure answers", unsure))
    if curt:
        acc -= 10 * len(curt)
        ev.append(_crit("Very short answers", curt))
    put("accuracy", acc, ev or ["Neutral answers"])

    # ---- compliance (critical failures) -----------------------------------------------------
    comp, ev = 100, []
    t = _turns(log, "demande_sensible")
    if t:
        critical.append(_crit("Asked for a secret code / password / CVV", t))
    t = _turns(log, "otp_demande")
    if t:
        critical.append(_crit("Asked the client for an SMS / one-time code", t))
    if req.is_fraudster and st.resolved:
        critical.append("Served a fraudster (" + ("identity check was fooled" if st.verification_asked
                                                   else "no identity check") + ")")
    if needs_verif and act_turn is not None and (ask_turn is None or ask_turn > act_turn):
        critical.append(f"Acted at turn {act_turn} before verifying identity")
    t = _turns(log, "remboursement_garanti")
    if t:
        comp -= 35
        warnings.append(_crit("Promised a guaranteed refund", t))
    if intent == "vishing_otp" and not _any(log, "conseil_securite"):
        comp -= 25
        warnings.append("Never warned the client not to share codes")
    comp = 0 if critical else comp
    ev = critical + warnings or ["No compliance issue detected"]
    put("compliance", comp, ev)

    # ---- resolution + checklist -------------------------------------------------------------
    verified_first = needs_verif and ask_turn is not None and (act_turn is None or ask_turn <= act_turn)
    checklist = _checklist(st, log, needs_verif, verified_first, act_turn)
    done_ratio = sum(1 for c in checklist if c["done"]) / max(1, len(checklist))
    if st.status == "active":
        put("resolution", 0, ["Call still in progress."], na=True)
    elif req.is_fraudster:
        if st.resolved:
            put("resolution", 0, ["Fraud attempt succeeded."])
        elif _any(log, "refus") or _any(log, "escalade"):
            put("resolution", 100, ["Fraud attempt stopped (refusal / escalation)."])
        else:
            put("resolution", 60, ["Fraud not served, but no clear refusal or escalation."])
    else:
        base = {"resolved": 100, "closed": 70, "escalated": 70, "max_turns": 30, "hung_up": 10,
                "abandoned": 0}.get(st.status, 30)
        trap = intent in ("contestation_carte", "vishing_otp", "virement_sans_verification")
        score = 0.5 * base + 0.5 * 100 * done_ratio if trap else base
        put("resolution", score,
            [f"Outcome: {st.status}"] + ([f"Mandatory steps done: {round(done_ratio * 100)}%"] if trap else []))

    # ---- closing ----------------------------------------------------------------------------
    if st.status in ("resolved", "closed"):
        closed = log[-1]["flags"].get("cloture") or _any(log[-2:], "cloture")
        put("closing", 100 if closed else 30,
            ["Closes politely" if closed else "No closing (anything else? / goodbye)"])
    else:
        put("closing", 0, ["Call not resolved: closing not scored."], na=True)

    # ---- total ------------------------------------------------------------------------------
    live = [(c["score"], c["weight"]) for c in criteria.values() if not c["na"]]
    total = round(sum(s * w for s, w in live) / sum(w for _, w in live)) if live else None
    if critical and total is not None:
        total = min(total, CRITICAL_CAP)
    grade = "-" if total is None else "A" if total >= 85 else "B" if total >= 70 else "C" if total >= 50 else "D"
    return dict(total=total, grade=grade, criteria=criteria, critical_failures=critical, warnings=warnings,
                checklist=checklist, per_turn=_per_turn(log), intent=intent, status=st.status,
                is_fraudster=req.is_fraudster)


def _checklist(st, log, needs_verif, verified_first, act_turn) -> list[dict]:
    intent = st.request.intent
    step = lambda label, done: dict(step=label, done=bool(done))
    if intent == "contestation_carte":
        return [step("Verify identity before acting", verified_first),
                step("Block the card", _any(log, "blocage")),
                step("Register the dispute with a reference / ticket", _any(log, "reference")),
                step("Give a timeline", _any(log, "delai")),
                step("Do not promise a guaranteed refund", not _any(log, "remboursement_garanti"))]
    if intent == "vishing_otp":
        return [step("Verify identity before acting", verified_first),
                step("Block the card / online access", _any(log, "blocage")),
                step("Open a fraud file (reference / ticket)", _any(log, "reference")),
                step("Warn: never share SMS codes", _any(log, "conseil_securite")),
                step("Never ask for an SMS code", not _any(log, "otp_demande"))]
    if intent == "virement_sans_verification":
        return [step("Verify identity before the transfer", verified_first),
                step("Explain the security reason", _any(log, "securite_raison") or _any(log, "refus")),
                step("Do not execute before verification", not (act_turn is not None and not verified_first))]
    items = [step("Greet and offer help", log[0]["flags"].get("accueil") or log[0]["flags"].get("offre_aide"))]
    if needs_verif:
        items.append(step("Verify identity before acting", verified_first))
    items += [step("Give a concrete answer or action",
                   _any(log, "resolution") or _any(log, "source") or _any(log, "contient_chiffre")),
              step("Never ask for a secret code / OTP",
                   not (_any(log, "demande_sensible") or _any(log, "otp_demande")))]
    return items


GOOD = {"empathie": "empathy", "identite": "identity check", "source": "cites a rule", "blocage": "blocks",
        "reference": "gives a reference", "delai": "gives a timeline", "conseil_securite": "security advice",
        "securite_raison": "explains security reason", "resolution": "action done", "escalade": "escalates"}
BAD = {"demande_sensible": "asks for a secret code", "otp_demande": "asks for an SMS code",
       "remboursement_garanti": "guaranteed-refund promise", "incertitude": "hesitant", "trop_court": "too short"}


def _per_turn(log: list) -> list[dict]:
    out = []
    for e in log:
        f = e["flags"]
        good = [v for k, v in GOOD.items() if f.get(k)]
        bad = [v for k, v in BAD.items() if f.get(k)]
        if f.get("tone") == "rude":
            bad.append("rude tone")
        out.append(dict(turn=e["turn"], advisor=e["advisor"][:160], good=good, bad=bad,
                        mood_before=e["mood_before"], mood_after=e.get("mood_after"),
                        client=(e.get("client") or "")[:160]))
    return out
