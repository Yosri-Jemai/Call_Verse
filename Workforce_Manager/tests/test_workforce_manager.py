"""
Tests of the Workforce Manager. Offline: rule-based callers and scripted advisors, no LLM, no database
server, no API. Run from the repository root:

    .\\Client_Simulator\\.venv\\Scripts\\python.exe -m pytest Workforce_Manager\\tests -q
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytest.importorskip("simpy")

from Workforce_Manager import WFMConfig, WorkforceManager, run_baseline  # noqa: E402
from Workforce_Manager.approvals import (FRAUD_REPORT, PROFILE_CHANGE, SENSITIVE_TRANSACTION,  # noqa: E402
                                         assess, callback_supervisor, detect_change_request,
                                         detect_transfer_request, hold_message)
from Workforce_Manager.erlang import erlang_c, required_advisors, service_level  # noqa: E402
from Workforce_Manager.reports import compute_kpis, write_report  # noqa: E402
from Workforce_Manager.reviews import ReviewStore  # noqa: E402
from Workforce_Manager.routing import SECURITY_INTENTS, SkillsRouter, Waiting, call_category  # noqa: E402
from Workforce_Manager.team import JUNIOR, SENIOR, build_roster  # noqa: E402

from judge import rule_analyse  # noqa: E402


# ---- Erlang-C ------------------------------------------------------------------------
def test_erlang_c_textbook_value():
    assert erlang_c(3, 2.0) == pytest.approx(4 / 9, abs=1e-4)      # classic example: 3 agents, 2 Erlangs


def test_erlang_edges_and_monotonic():
    assert erlang_c(2, 2.0) == 1.0 and erlang_c(5, 0.0) == 0.0
    sls = [service_level(n, 4.0, 90, 60) for n in range(5, 12)]
    assert sls == sorted(sls) and 0 < sls[0] < 1


def test_required_advisors_meets_target():
    lam, aht = 3 / 60, 90                                          # 3 calls / min, 90 s each = 4.5 Erlangs
    n = required_advisors(lam, aht, 60, 0.8, max_occupancy=0.85)
    assert n >= math.ceil(4.5)
    assert service_level(n, lam * aht, aht, 60) >= 0.8
    assert service_level(n - 1, lam * aht, aht, 60) < 0.8 or lam * aht / (n - 1) > 0.85


# ---- detection -----------------------------------------------------------------------
@pytest.mark.parametrize("text,field", [
    ("Je dois changer mon numéro de téléphone tout de suite, c'est urgent.", "phone"),
    ("Augmentez mon plafond de retrait aujourd'hui.", "limit"),
    ("Je voudrais modifier mon adresse e-mail.", "email"),
    ("Please update my address.", "address"),
    ("I want to change my phone number.", "phone"),
])
def test_change_requests_are_detected(text, field):
    assert field in detect_change_request(text)


@pytest.mark.parametrize("text", [
    "Quel est le plafond de ma carte à l'étranger ?",
    "Mon virement de 300 dinars n'est toujours pas arrivé.",
    "Je voudrais connaître mon solde et mes derniers mouvements.",
    "Ma date de naissance est le 12/03/1985 et ma carte finit par 4821.",
])
def test_questions_are_not_change_requests(text):
    assert detect_change_request(text) == [] and not detect_transfer_request(text)


def test_transfer_to_new_beneficiary_is_detected():
    assert detect_transfer_request("Je veux faire un virement de 900 dinars vers un nouveau bénéficiaire.")


def test_assess_uses_intent_and_flags_fraud_signals():
    hist = [("client", "Je dois changer mon numéro de téléphone tout de suite, c'est urgent.")]
    a = assess("changement_coordonnees", hist, record=None)
    assert PROFILE_CHANGE in a.categories and a.blocking
    assert a.category == "fraud_suspicion"       # sensitive + no identity + contact change + pressure
    assert a.risk_score >= 3
    assert assess("virement_urgent", [("client", "Bonjour")], None).blocking
    assert SENSITIVE_TRANSACTION in assess("virement_urgent", [("client", "Bonjour")], None).categories
    fr = assess("fraude_signalee", [("client", "Mon compte a été piraté !")], None)
    assert FRAUD_REPORT in fr.categories and not fr.blocking
    assert assess("solde_mouvements", [("client", "Mon solde svp.")], None).categories == []


@pytest.mark.parametrize("lang", ["fr", "en", "darija", "mix"])
def test_hold_message_closes_the_call_without_escalation_or_refusal(lang):
    f = rule_analyse(hold_message(lang, "WFM-TEST1234"))
    assert f["resolution"] and not f["escalade"] and not f["refus"] and not f["demande_sensible"]


# ---- review store ----------------------------------------------------------------------
def test_review_store_decides_once_with_audit():
    s = ReviewStore(":memory:")
    rid = s.add(category=PROFILE_CHANGE, categories=[PROFILE_CHANGE], blocking=True, requested="change phone",
                is_fraudster=True)
    assert s.get(rid)["status"] == "pending" and s.truth(rid) is True
    with pytest.raises(ValueError):
        s.approve(rid, by="")                                     # a name is required
    s.deny(rid, by="Sami", note="call-back failed")
    assert s.get(rid)["status"] == "denied"
    with pytest.raises(ValueError):
        s.approve(rid, by="Sami")                                 # cannot change a decision
    assert [e["action"] for e in s.audit(rid)] == ["flagged", "denied"]
    st = s.stats()
    assert st["denied"] == 1 and st["fraud_denied"] == 1


def test_review_store_persists_to_file(tmp_path):
    db = tmp_path / "reviews.db"
    rid = ReviewStore(db).add(category=PROFILE_CHANGE, categories=[PROFILE_CHANGE], blocking=True)
    again = ReviewStore(db)
    assert again.get(rid)["status"] == "pending"
    again.approve(rid, by="Amel")
    assert ReviewStore(db).get(rid)["decided_by"] == "Amel"


# ---- routing ---------------------------------------------------------------------------
class _Req:
    def __init__(self, rid, intent, urgency):
        self.request_id, self.intent, self.urgency = rid, intent, urgency


def _wait(rid, intent, urgency, arrived):
    return Waiting(_Req(rid, intent, urgency), arrived, event=None, category=call_category(intent))


def test_skills_router_sends_security_to_seniors_and_simple_to_juniors():
    team = build_roster(1, 1)
    for a in team:
        a.start_shift(0)
    senior, junior = team
    q = [_wait("a", "solde_mouvements", 1, 0), _wait("b", "virement_urgent", 3, 5)]
    r = SkillsRouter(overflow_s=120)
    adv, entry, _ = r.choose([senior, junior], q, now=10)
    assert adv is senior and entry.req.request_id == "b"
    adv, entry, _ = r.choose([junior], q, now=10)
    assert adv is junior and entry.req.request_id == "a"


def test_security_call_overflows_to_junior_only_after_waiting():
    team = build_roster(0, 1)
    team[0].start_shift(0)
    q = [_wait("x", "changement_coordonnees", 2, 0)]
    r = SkillsRouter(overflow_s=120)
    assert r.choose(team, q, now=60) is None
    assert r.choose(team, q, now=125)[0] is team[0]
    assert SkillsRouter(overflow_s=None).choose(team, q, now=10_000) is None


def test_security_intents_cover_every_fraud_intent():
    from client_simulator import FRAUD_INTENTS
    assert set(FRAUD_INTENTS) <= SECURITY_INTENTS


# ---- full day -----------------------------------------------------------------------------
@pytest.fixture(scope="module")
def day():
    store = ReviewStore(":memory:")
    kw = dict(duration_min=120, seed=3)
    base = run_baseline("vague_de_fraude", seniors=1, juniors=3, **kw)
    wfm = WorkforceManager(WFMConfig(roster_seniors=3, roster_juniors=6, history_days=3), store)
    sim = wfm.simulate("vague_de_fraude", auto_review=False, **kw)
    return base, sim, store


def test_every_caller_is_accounted_for(day):
    base, sim, _ = day
    for s in (base, sim):
        assert len(s.results) == len(s.requests) > 0
        assert {r["request_id"] for r in s.results} == {q.request_id for q in s.requests}
        assert all(not a.busy for a in s.roster)                # nobody left on a call
    assert [q.request_id for q in base.requests] == [q.request_id for q in sim.requests]   # same callers


def test_no_fraud_is_executed_before_a_human_decides(day):
    _, sim, store = day
    k = compute_kpis(sim, store)
    assert k["fraud_calls"] > 0
    assert k["fraud_leaked"] == 0                              # all held or refused, nothing approved yet
    for r in sim.results:
        if r["is_fraudster"] and r["resolved"]:
            assert r["held_for_review"] and store.get(r["review_id"])["status"] == "pending"


def test_held_requests_end_with_a_validation_ticket(day):
    _, sim, _ = day
    held = [r for r in sim.results if r["held_for_review"]]
    assert held
    for r in held:
        advisor_lines = [t for who, t in sim.transcripts[r["request_id"]] if who == "conseiller"]
        assert r["review_id"] in advisor_lines[-1]


def test_callback_supervisor_blocks_fraud_and_approves_genuine(day):
    _, sim, store = day
    callback_supervisor(store, sim.run_id)
    k = compute_kpis(sim, store)
    assert k["fraud_leaked"] == 0 and k["fraud_awaiting_review"] == 0 and k["fraud_blocked_%"] == 100.0
    assert k["genuine_denied"] == 0
    assert all(rv["status"] != "pending" for rv in store.list(run_id=sim.run_id))


def test_seniors_take_most_security_calls(day):
    base, sim, store = day
    assert compute_kpis(sim, store)["security_to_senior_%"] >= compute_kpis(base)["security_to_senior_%"]


def test_report_files_are_written(day, tmp_path):
    base, sim, store = day
    out = write_report([base, sim], store, tmp_path / "rep")
    for name in ("report.md", "report.json", "calls_today.csv", "calls_wfm.csv", "staffing_wfm.csv"):
        assert (out / name).exists(), name
    text = (out / "report.md").read_text(encoding="utf-8")
    assert "## Summary" in text and "## Human reviews" in text


def test_staffing_follows_the_plan_and_roster():
    wfm = WorkforceManager(WFMConfig(roster_seniors=2, roster_juniors=3, history_days=2, reactive=False))
    sim = wfm.simulate("normal", duration_min=60, seed=1)
    for iv in sim.plan.intervals:
        assert iv.seniors <= 2 and iv.juniors <= 3 and iv.seniors >= 1
        mid = (iv.start_s + iv.end_s) / 2
        assert sim.on_duty_average(mid - 1, mid + 1, SENIOR) == pytest.approx(iv.seniors, abs=1)
        assert sim.on_duty_average(mid - 1, mid + 1, JUNIOR) <= 3


def test_kaplan_meier_handles_censored_waits():
    from Workforce_Manager.model import kaplan_meier, km_quantile
    # 4 customers: hung up at 10 s and 30 s; answered (censored) at 20 s and 40 s
    curve = kaplan_meier([10, 20, 30, 40], [1, 0, 1, 0])
    assert curve == [(0.0, 1.0), (10, 0.75), (30, 0.375)]
    assert km_quantile(curve, 0.5) == 30 and km_quantile(curve, 0.9) is None


def test_reactive_controller_uses_the_announced_wait():
    from types import SimpleNamespace
    from Workforce_Manager.staffing import ReactiveController
    c = ReactiveController(add_after_s=60, aht_s=100)
    adv = SimpleNamespace(on_duty=True, leaving=False)
    sim = SimpleNamespace(queue=[object()], roster=[adv, adv])          # 1 waiting x 100 s / 2 advisors = 50 s
    assert not c.overloaded(sim, longest=5)
    sim.queue = [object(), object()]                                    # 2 x 100 / 2 = 100 s >= 60 s goal
    assert c.overloaded(sim, longest=5)
