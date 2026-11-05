"""
Tests of the Workforce Manager API used by the Relay dashboard (offline, temporary database and folders).

    .\\Client_Simulator\\.venv\\Scripts\\python.exe -m pytest Workforce_Manager\\tests -q
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    import Workforce_Manager.api as api
    from Workforce_Manager.reviews import ReviewStore
    tmp = tmp_path_factory.mktemp("wfm_api")
    from Workforce_Manager.dataset import build_dataset
    from Workforce_Manager.model import learn
    # a small call log: 3 mornings (2 training + 1 test) of two day types
    days = [(dt, 30_000 + 10 * k + d, "test" if d == 2 else "train", 8, 11)
            for k, dt in enumerate(("fin_de_mois", "vague_de_fraude")) for d in range(3)]
    build_dataset(workers=3, out_dir=tmp, days=days)
    model = learn(tmp / "call_log.csv")
    old = api.STORE, api.RUNS_DIR, api.LIVE_DIR, api.learned_from_conversations, api.cached_model
    api.STORE, api.RUNS_DIR, api.LIVE_DIR = ReviewStore(tmp / "reviews.db"), tmp / "runs", tmp / "live"
    api.learned_from_conversations = lambda: dict(LEARNED, rows=list(LEARNED["rows"]), intents=dict(LEARNED["intents"]))
    api.cached_model = lambda: model
    yield TestClient(api.app)
    api.STORE, api.RUNS_DIR, api.LIVE_DIR, api.learned_from_conversations, api.cached_model = old


# what the project database would return after 3 finished live conversations
LEARNED = {"source": "test", "db_error": "", "conversations": 3, "seconds_per_exchange": 30, "avg_exchanges": 4.0,
           "handle_s": 120.0, "handle_risky_s": 150.0, "handle_simple_s": 90.0, "risky_measured": True,
           "simple_measured": True, "risky_calls": 1, "risky_share": 0.333, "thieves": 1, "thieves_stopped": 1,
           "outcomes": {"resolved": 2, "escalated": 1}, "intents": {"virement_urgent": 1, "solde_mouvements": 2},
           "avg_satisfaction": 3.5,
           "rows": [{"call_id": "c1", "created_at": "2026-11-04", "intent": "virement_urgent", "category": "security",
                     "turns": 5, "status": "escalated", "satisfaction": 3.0, "is_fraudster": True}]}


@pytest.fixture(scope="module")
def run(client):
    r = client.post("/api/runs", json={"scenario": "vague_de_fraude", "duration_min": 120, "seed": 7})
    assert r.status_code == 200, r.text
    return r.json()


def test_health_and_plan(client):
    assert client.get("/api/health").json()["service"] == "workforce_manager"
    p = client.post("/api/plan", json={"scenario": "fin_de_mois", "duration_min": 60}).json()
    assert len(p["intervals"]) == 4 and all(i["seniors"] >= 1 for i in p["intervals"])
    assert "measured from 3 live conversation" in p["sources"]["call_length"] and "150 s" in p["sources"]["call_length"]


def test_learned_lists_the_conversations(client):
    d = client.get("/api/learned").json()
    assert d["conversations"] == 3 and d["rows"][0]["minutes"] == 2.5 and d["rows"][0]["thief"] is True


def test_plan_needs_conversations(client, monkeypatch):
    import Workforce_Manager.api as api
    monkeypatch.setattr(api, "learned_from_conversations", lambda: dict(LEARNED, conversations=0, handle_s=None))
    assert client.post("/api/plan", json={"scenario": "normal", "duration_min": 60}).status_code == 409
    assert client.post("/api/plan", json={"scenario": "nope"}).status_code == 400


def test_run_compares_today_and_wfm_without_leaking_ground_truth(client, run):
    assert set(run["runs"]) == {"today", "wfm"}
    assert run["runs"]["wfm"]["kpis"]["fraud_leaked"] == 0
    assert run["runs"]["today"]["kpis"]["fraud_leaked"] > 0
    assert run["pending_reviews"] > 0
    assert "is_fraudster" not in str(run)               # the page never receives the hidden flag
    assert client.get("/api/runs").json()[0]["id"] == run["id"]
    assert client.get(f"/api/runs/{run['id']}/report.md").status_code == 200


def test_only_stopped_actions_wait_for_a_person(client, run):
    every = client.get("/api/reviews?status=all&actionable=false").json()
    assert every and all(r["call_outcome"].startswith("action held") for r in every)
    assert len(every) == run["runs"]["wfm"]["kpis"]["actions_held"]


def test_review_decision_is_signed_final_and_reveals_truth_after(client, run):
    rv = client.get("/api/reviews").json()[0]
    assert rv["status"] == "pending" and rv["run_key"] == run["id"]
    d = client.get(f"/api/reviews/{rv['review_id']}").json()
    assert d["truth"] is None and len(d["transcript"]) >= 2
    assert client.post(f"/api/reviews/{rv['review_id']}/decision", json={"approved": True, "by": ""}).status_code == 422
    d = client.post(f"/api/reviews/{rv['review_id']}/decision", json={"approved": False, "by": "Tester"}).json()
    assert d["status"] == "denied" and d["truth"] is not None
    assert d["truth"]["decision_correct"] == d["truth"]["is_fraudster"]
    assert client.post(f"/api/reviews/{rv['review_id']}/decision",
                       json={"approved": True, "by": "Tester"}).status_code == 409


def test_auto_review_clears_the_run(client, run):
    after = client.post(f"/api/runs/{run['id']}/auto-review").json()
    assert after["pending_reviews"] == 0
    assert after["runs"]["wfm"]["kpis"]["fraud_awaiting_review"] == 0


LIVE = {"call_id": "live001", "client": {"name": "Fedi", "client_id": "C1", "intent": "changement_coordonnees",
                                         "language": "fr"},
        "history": [{"role": "client", "text": "Je dois changer mon numéro de téléphone tout de suite, c'est urgent."}]}


def test_live_call_pauses_until_a_person_approves(client):
    p = client.post("/api/live/check", json=LIVE | {"advisor_message": "Pouvez-vous me donner votre date de naissance ?"}).json()
    assert p["action"] == "pause" and p["review"]["status"] == "pending"
    rid = p["review"]["review_id"]
    assert client.post("/api/live/check", json=LIVE | {"advisor_message": "Un instant."}).json()["action"] == "wait"
    assert client.get("/api/health").json()["pending_reviews"] >= 1
    assert client.get("/api/reviews?source=live").json()[0]["review_id"] == rid
    d = client.get(f"/api/reviews/{rid}").json()
    assert d["source"] == "live" and d["transcript"] and d["truth"] is None
    client.post(f"/api/reviews/{rid}/decision", json={"approved": True, "by": "Chaima"})
    assert client.post("/api/live/check", json=LIVE | {"advisor_message": "C'est fait."}).json()["action"] == "approved"


def test_live_call_refused_by_a_person_gets_a_refusal(client):
    call = LIVE | {"call_id": "live003"}
    rid = client.post("/api/live/check", json=call | {"advisor_message": "Je vérifie."}).json()["review"]["review_id"]
    client.post(f"/api/reviews/{rid}/decision", json={"approved": False, "by": "Chaima"})
    d = client.post("/api/live/check", json=call | {"advisor_message": "C'est fait."}).json()
    assert d["action"] == "denied" and "refusé" in d["replace_with"]
    safe = client.post("/api/live/check", json=call | {
        "advisor_message": "I understand your frustration. I cannot carry out the transfer without identity verification. I can help verify your account or connect you with a supervisor."
    }).json()
    assert safe["action"] == "continue"
    unsafe = client.post("/api/live/check", json=call | {
        "advisor_message": "C'est fait, le virement est effectué."
    }).json()
    assert unsafe["action"] == "denied"


def test_live_call_can_be_flagged_by_hand_and_plain_calls_pass(client):
    ok = client.post("/api/live/check", json={"call_id": "plain1", "client": {"intent": "solde_mouvements"},
                                              "history": [{"role": "client", "text": "Mon solde svp."}],
                                              "advisor_message": "C'est fait, voici votre solde."}).json()
    assert ok["action"] == "none"
    f = client.post("/api/live/flag", json={**LIVE, "client": {**LIVE["client"], "intent": "solde_mouvements"},
                                            "history": [{"role": "client", "text": "Bonjour."}],
                                            "call_id": "live002", "reason": "voice sounds scripted"}).json()
    assert f["action"] == "flag" and f["review"]["category"] == "fraud_suspicion"


def test_control_room_replays_a_consistent_day(client):
    import Workforce_Manager.api as api
    before = len(api.STORE.list(limit=100_000))
    d = client.post("/api/control-room", json={"scenario": "vague_de_fraude", "duration_min": 60, "seed": 3}).json()
    kinds = [e["kind"] for e in d["events"]]
    assert kinds.count("arrive") == d["callers"] > 0
    assert kinds.count("assign") + kinds.count("abandon") >= kinds.count("arrive") - 1
    assert d["events"] == sorted(d["events"], key=lambda e: e["t"])
    # every assigned call ends, every advisor who takes a call is on duty
    on = set()
    for e in d["events"]:
        if e["kind"] == "on":
            on.add(e["advisor"])
        elif e["kind"] == "off":
            on.discard(e["advisor"])
        elif e["kind"] == "assign":
            assert e["advisor"] in on
    assert d["summary"]["thieves_succeeded"] == 0 and d["gate"]["held"] == d["summary"]["held"]
    assert d["learned"]["train_days"] == 2 and d["learned"]["test_day"]
    assert "supervisor" not in kinds                              # no simulated decision, no hidden label used
    first_end = next(e for e in d["events"] if e["kind"] == "end")
    c = client.get(f"/api/control-room/{d['id']}/calls/{first_end['call']}").json()
    assert c["transcript"] and c["advisor"] == first_end["advisor"]
    assert len(api.STORE.list(limit=100_000)) == before      # the replay never fills your approval queue


def test_too_small_team_is_flagged_and_callbacks_limit_the_damage(client):
    d = client.post("/api/control-room", json={"scenario": "fin_de_mois", "duration_min": 120, "seed": 3,
                                               "seniors": 1, "juniors": 1}).json()
    c, s = d["team_check"], d["summary"]
    assert not c["ok"] and c["need_seniors"] > 1 and c["calls_over_capacity"] > 0
    assert s["alerts"] > 0 and s["callbacks_accepted"] > 0 and s["called_back"] > 0
    # every caller ends up somewhere: served, hung up, or on tomorrow's call-back list
    assert s["answered"] + s["hung_up"] + s["callback_tomorrow"] == d["callers"]
    called_back = {e["call"] for e in d["events"] if e["kind"] == "callback"}
    offered = {e["call"] for e in d["events"] if e["kind"] == "offer" and e["accepted"]}
    assert called_back <= offered
    # risky and urgent callers are never pushed to a call-back
    risky = {e["call"] for e in d["events"] if e["kind"] == "arrive" and (e["category"] == "security" or e["urgency"] >= 3)}
    assert not (offered & risky)
    big = client.post("/api/control-room", json={"scenario": "fin_de_mois", "duration_min": 120, "seed": 3,
                                                 "seniors": c["need_seniors"], "juniors": c["need_juniors"]}).json()
    assert big["team_check"]["ok"] and big["summary"]["hung_up"] < s["hung_up"]


def test_model_endpoint_reports_what_was_learned(client):
    m = client.get("/api/model").json()
    assert m["train_days"] == {"fin_de_mois": 2, "vague_de_fraude": 2}
    assert m["call_length"]["measured_on"]["all"] > 50
    assert set(m["forecast_test_error"]) == {"fin_de_mois", "vague_de_fraude"}
    assert m["patience"]["curve"][0] == [0, 1.0]


def test_clear_history_hides_decided_but_keeps_pending_and_audit(client):
    import Workforce_Manager.api as api
    call = LIVE | {"call_id": "live010"}
    done = client.post("/api/live/check", json=call | {"advisor_message": "Je vérifie."}).json()["review"]["review_id"]
    client.post(f"/api/reviews/{done}/decision", json={"approved": True, "by": "Chaima"})
    waiting = client.post("/api/live/check", json=LIVE | {"call_id": "live011", "advisor_message": "Un instant."}
                          ).json()["review"]["review_id"]
    assert client.post("/api/reviews/clear-history", json={"by": "Chaima"}).json()["cleared"] >= 1
    shown = {r["review_id"] for r in client.get("/api/reviews?source=live&status=all").json()}
    assert done not in shown and waiting in shown                     # pending requests are never cleared
    assert any(a["action"] == "hidden" for a in api.STORE.audit(done))   # the signed decision is still on record
    assert api.STORE.get(done)["status"] == "approved"
