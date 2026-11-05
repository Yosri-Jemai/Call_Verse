"""Run with: pytest -q   (the core tests need no external library; the DB test uses SQLite)."""
import json
import os
import tempfile

from advisors import baseline_advisor, naive_advisor
from client_simulator import (INTENTS, AdvisorView, ClientSimulatorAgent, Profile, advisor_view, analyse)
from db import Store
from judge import Judge, rule_analyse
from load_model import Curriculum, LoadModel
from persona import check_claims, claims_from_text, identity_matches, make_persona
import random


# ---------------------------------------------------------------- original behaviour
def test_same_seed_same_requests():
    a = ClientSimulatorAgent(seed=1).generate_requests("panne", 5)
    b = ClientSimulatorAgent(seed=1).generate_requests("panne", 5)
    assert [r.request_id for r in a] == [r.request_id for r in b]


def test_turn_log_records_identity_state_before_and_after_each_reply():
    sim = ClientSimulatorAgent(seed=7, llm=None, client_swears=False)
    req = sim.generate_requests("normal", 10)[0]
    state = sim.start(req)

    sim.reply(
        state,
        "Pour vérifier votre identité, pouvez-vous confirmer votre date de naissance et votre numéro de téléphone ?",
    )
    first_turn = state.turn_log[0]
    assert first_turn["identity_verified_before"] is False
    assert first_turn["verification_asked_before"] is False
    assert isinstance(first_turn["identity_verified_after"], bool)

    sim.reply(state, "Merci, votre carte est bloquée.")
    second_turn = state.turn_log[1]
    assert second_turn["identity_verified_before"] is state.identity_verified
    assert second_turn["verification_asked_before"] is True


def test_peak_scenarios_have_more_calls():
    n = lambda s: len(ClientSimulatorAgent(seed=2).generate_requests(s, 20))
    assert n("panne") > n("normal")


def _call(req, msgs, seed=5):
    sim = ClientSimulatorAgent(seed=seed)
    st = sim.start(req)
    for m in msgs:
        sim.reply(st, m)
        if st.status != "active":
            break
    return st


def _angry_request():
    sim = ClientSimulatorAgent(seed=0)
    return next(r for r in sim.generate_requests("panne", 30) if r.profile == Profile.DISSATISFIED
                and not r.is_fraudster)


def test_empathy_beats_cold_answers():
    req = _angry_request()
    good = _call(req, ["Je suis désolé, je comprends votre frustration.",
                       "Selon la procédure, je traite votre dossier.",
                       "C'est fait, le ticket est enregistré."])
    cold = _call(req, ["Non.", "Je ne sais pas.", "Peut-être demain."])
    assert good.satisfaction > cold.satisfaction


def test_long_wait_causes_abandon():
    sim = ClientSimulatorAgent(seed=0)
    req = sim.generate_requests("normal", 10)[0]
    st = sim.start(req, wait_seconds=req.patience + 1)
    assert st.status == "abandoned"


def test_every_intent_has_an_opener():
    assert all(v["openers"] and v["openers_da"] for v in INTENTS.values())


# ---------------------------------------------------------------- 5. judge: negation-safe
def test_negated_or_promised_resolution_is_not_resolution():
    for msg in ["Votre problème n'est pas résolu pour l'instant.", "Ce n'est pas traité.",
                "Je vais ouvrir un ticket.", "Votre carte n'est pas bloquée.", "Est-ce résolu ?"]:
        assert not rule_analyse(msg)["resolution"], msg
    assert rule_analyse("C'est fait, j'ai ouvert un ticket et c'est traité.")["resolution"]
    assert rule_analyse("Votre carte est bloquée.")["resolution"]


def test_judge_reads_identity_fields_and_secret_requests():
    f = rule_analyse("Pouvez-vous me donner votre date de naissance et les 4 derniers chiffres de votre carte ?")
    assert set(f["identity_fields"]) == {"birth_date", "card_last4"}
    assert rule_analyse("Pouvez-vous confirmer votre identité ?")["identite_generique"]
    assert rule_analyse("Donnez-moi votre code secret.")["demande_sensible"]
    assert rule_analyse("Calmez-vous, ce n'est pas mon problème.")["tone"] == "rude"
    assert not rule_analyse("Je ne comprends pas votre demande.")["empathie"]


def test_llm_judge_overrides_rules_and_falls_back():
    verdict = {"resolution": False, "promise": True, "empathy": False, "identity_check": False,
               "identity_fields": [], "sensitive_request": False, "escalation": False, "hold": False,
               "uncertainty": False, "refusal": False, "sourced": False, "tone": "cold"}
    j = Judge(lambda p: "voici :\n```json\n" + json.dumps(verdict) + "\n```")
    f = j.analyse("C'est fait, c'est traité.")             # rules say resolved, the LLM judge says no
    assert f["judge"] == "llm" and not f["resolution"] and f["tone"] == "cold"
    for broken in (lambda p: "pas du json", lambda p: '{"resolution": true}', lambda p: 1 / 0):
        j = Judge(broken)
        f = j.analyse("C'est fait, c'est traité.")
        assert f["judge"] == "rules" and f["resolution"] and j.stats["llm_failed"] == 1


# ---------------------------------------------------------------- 1. persona, hidden facts, verification
def test_claims_are_extracted_and_checked_against_crm():
    p = make_persona(random.Random(1), "carte_bloquee")
    t = p.true_facts
    said = f"Ma date de naissance est le {t['birth_date']} et ma carte se termine par {t['card_last4']}."
    ok, bad = check_claims(t, claims_from_text(said))
    assert ok == {"birth_date", "card_last4"} and not bad
    assert identity_matches(p.crm_record(), claims_from_text(said))
    assert not identity_matches(p.crm_record(), claims_from_text("ma carte se termine par 0000 et date 01/01/1900"))
    assert claims_from_text("Je veux un virement de 2500 dinars") == {}      # an amount is not a card number


def _fraud_requests(sim, n=12, **kw):
    reqs = [r for r in sim.generate_requests("vague_de_fraude", 90, **kw) if r.is_fraudster]
    assert len(reqs) >= n
    return reqs[:n]


def test_honest_client_passes_baseline_verification_and_is_resolved():
    sim = ClientSimulatorAgent(seed=3)
    honest = [r for r in sim.generate_requests("normal", 60) if not r.is_fraudster][:15]
    for r in honest:
        st = sim.run_conversation(r, baseline_advisor)
        assert st.identity_verified and st.status == "resolved", (r.language, st.history)


def test_careless_advisor_leaks_fraud_but_verifying_advisor_blocks_it():
    sim = ClientSimulatorAgent(seed=4)
    reqs = _fraud_requests(sim)
    naive = [sim.run_conversation(r, naive_advisor) for r in reqs]
    careful = [sim.run_conversation(r, baseline_advisor) for r in reqs]
    assert all(st.resolved and not st.identity_verified for st in naive)
    assert not any(st.resolved for st in careful)
    assert all(st.status == "escalated" for st in careful)


def test_fraudster_with_leaked_data_passes_a_weak_check_but_not_the_card_digits():
    """leak level 2 = the fraudster knows phone + birth date. Asking only those two is not enough."""
    sim = ClientSimulatorAgent(seed=6)
    leakers = [r for r in _fraud_requests(sim, 1, difficulty=1.0) + [
        r for r in sim.generate_requests("vague_de_fraude", 90, difficulty=1.0)
        if r.is_fraudster and r.persona.leak_level == 2]][:6]

    def weak(view: AdvisorView) -> str:
        if view.turn == 0:
            return "Bonjour. Pouvez-vous me donner votre date de naissance et votre numéro de téléphone ?"
        return "C'est fait, j'ai ouvert un ticket et votre demande est traitée."

    leakers = [r for r in leakers if r.persona.leak_level == 2]
    assert leakers
    assert all(sim.run_conversation(r, weak).identity_verified for r in leakers)       # fooled
    assert not any(sim.run_conversation(r, baseline_advisor).identity_verified for r in leakers)


def test_advisor_never_sees_ground_truth():
    sim = ClientSimulatorAgent(seed=7)
    req = _fraud_requests(sim, 1)[0]
    view = advisor_view(sim.start(req), sim.crm.get)
    assert not hasattr(view, "request") and not hasattr(view, "mood")
    assert set(vars(view)) == {"client_id", "client_name", "channel", "turn", "waited_s", "history", "crm_lookup"}
    assert view.crm_lookup(view.client_id)["client_id"] == req.client_id


# ---------------------------------------------------------------- 2. adaptive fraudster
def test_fraudster_raises_pressure_and_switches_strategy_when_refused():
    sim = ClientSimulatorAgent(seed=8)
    switched = 0
    for r in _fraud_requests(sim):
        st = sim.start(r)
        for msg in ["Je ne peux pas faire ça sans vérification.", "Désolé, c'est impossible sans vérification.",
                    "Je dois d'abord vérifier votre identité, c'est la règle.", "Impossible, c'est interdit."]:
            sim.reply(st, msg)
            if st.status != "active":
                break
        assert st.fraud["pressure"] >= 1
        switched += len(st.fraud["path"]) > 1
    assert switched >= 3                                  # most of them change angle at least once


def test_fraud_rule_lines_escalate_with_pressure():
    sim = ClientSimulatorAgent(seed=9)
    r = _fraud_requests(sim, 1)[0]
    st = sim.start(r)
    lines = [sim.reply(st, m) for m in ["Je ne peux pas.", "Impossible.", "Je refuse, c'est interdit."] if st.status == "active"]
    assert len(set(lines)) > 1                            # the pitch is not repeated verbatim


# ---------------------------------------------------------------- 3. Darija / code-switching
def test_languages_are_generated_and_darija_is_latin_script():
    sim = ClientSimulatorAgent(seed=10)
    reqs = sim.generate_requests("fin_de_mois", 120, realistic=False)
    assert {r.language for r in reqs} == {"fr", "darija", "en"}
    da = [r for r in reqs if r.language == "darija"]
    assert all(r.opening_message.isascii() or "é" in r.opening_message for r in da)
    st = sim.start(da[0])
    assert sim.reply(st, "Je vérifie votre dossier, un instant.")            # answers in the same register


def test_llm_receives_language_instruction():
    seen = []
    sim = ClientSimulatorAgent(seed=11, llm=lambda p: seen.append(p) or '{"reply": "ok"}',
                               language_weights={"darija": 1.0})
    st = sim.start(sim.generate_requests("normal", 30)[0])
    sim.reply(st, "Bonjour, je vous écoute.")
    assert "darija" in seen[0].lower() and "arabizi" in seen[0].lower()


def test_english_is_a_first_class_language():
    sim = ClientSimulatorAgent(seed=12, language_weights={"en": 1.0})
    reqs = sim.generate_requests("normal", 60, realistic=False)
    assert reqs and all(r.language == "en" for r in reqs)
    assert all(v["openers_en"] for v in INTENTS.values())


# ---------------------------------------------------------------- 1. LLM engine with safe fallback
def test_llm_engine_is_used_and_falls_back_to_rules():
    req = _angry_request()
    good = '```json\n{"reply": "Ça fait deux heures que j\'attends !", "emotion": "en colère", "mood_adjust": 0.9}\n```'
    sim = ClientSimulatorAgent(seed=5, llm=lambda p: good)
    st = sim.start(req)
    before = st.mood
    text = sim.reply(st, "Bonjour, je vous écoute.")
    assert text.startswith("Ça fait deux heures") and st.engine["llm"] == 1 and st.emotion == "en colère"
    assert st.mood - before < 0.2                         # mood_adjust 0.9 is clamped to +0.15

    for broken in (lambda p: 1 / 0, lambda p: "{cassé", lambda p: '{"reply": ""}', lambda p: ""):
        sim = ClientSimulatorAgent(seed=5, llm=broken)
        st = sim.start(req)
        assert sim.reply(st, "Bonjour, je vous écoute.")
        assert st.engine["rules"] == 1 and sim.stats["llm_failed"] >= 1

    sim = ClientSimulatorAgent(seed=5, llm=lambda p: "Je veux une solution !")   # older hook: plain text
    st = sim.start(req)
    assert sim.reply(st, "Bonjour.") == "Je veux une solution !" and st.engine["llm"] == 1


def test_identity_claims_in_llm_text_are_verified_in_code():
    sim = ClientSimulatorAgent(seed=12)
    req = next(r for r in sim.generate_requests("normal", 60) if not r.is_fraudster)
    t = req.persona.true_facts
    sim.llm = lambda p: json.dumps({"reply": f"Date de naissance {t['birth_date']}, carte se termine par {t['card_last4']}."})
    st = sim.start(req)
    sim.reply(st, "Quelle est votre date de naissance et les 4 derniers chiffres de votre carte ?")
    assert st.identity_verified
    sim.llm = lambda p: '{"reply": "Ma carte se termine par 1111 et je suis né le 01/01/1970."}'
    st = sim.start(req)
    sim.reply(st, "Quelle est votre date de naissance et les 4 derniers chiffres de votre carte ?")
    assert not st.identity_verified and st.wrong_claims >= 1


def test_fraudster_prompt_never_contains_the_secret_facts_it_does_not_know():
    sim = ClientSimulatorAgent(seed=13)
    seen = []
    sim.llm = lambda p: seen.append(p) or '{"reply": "Je suis pressé."}'
    req = _fraud_requests(sim, 1)[0]
    st = sim.start(req)
    sim.reply(st, "Quelle est votre date de naissance ?")
    truth, known = req.persona.true_facts, req.persona.known_facts
    for fld, value in truth.items():
        assert (value in seen[-1]) == (fld in known), fld
    honest = next(r for r in sim.generate_requests("normal", 30) if not r.is_fraudster)
    seen.clear()
    sim.reply(sim.start(honest), "Bonjour.")
    assert honest.persona.true_facts["card_last4"] in seen[-1]


# ---------------------------------------------------------------- 4. realistic load
def test_hourly_profile_and_incidents_shape_the_load():
    def count(start_hour, seed):
        return len(ClientSimulatorAgent(seed=seed).generate_requests("normal", 60, start_hour=start_hour))
    peak = sum(count(10, s) for s in range(8))
    lunch = sum(count(13, s) for s in range(8))
    assert peak > 1.6 * lunch

    m = LoadModel(2.0, incident_rate_per_hour=0.0)
    assert m.rate(0, []) == m.base * m.hourly(0)
    rng = random.Random(1)
    incs = LoadModel(2.0, incident_rate_per_hour=6.0).sample_incidents(rng, 7200)
    assert incs and all(i.end > i.start and i.mult > 1 for i in incs)
    assert LoadModel(2.0, 6.0, realistic=False).sample_incidents(rng, 7200) == []


def test_incident_boosts_the_matching_intents():
    sim = ClientSimulatorAgent(seed=14)
    reqs = sim.generate_requests("panne", 120, difficulty=0.5)
    during = [r for r in reqs if r.incident == "panne_application"]
    assert during, "scenario panne should produce an application outage"
    share = lambda rs: sum(r.intent in ("carte_bloquee", "virement_retard", "reclamation") for r in rs) / len(rs)
    others = [r for r in reqs if not r.incident]
    assert not others or share(during) > share(others) + 0.1


def test_difficulty_makes_it_harder():
    n = lambda d: sum(len(ClientSimulatorAgent(seed=s).generate_requests("normal", 60, difficulty=d)) for s in range(6))
    assert n(1.0) > 1.4 * n(0.0)
    easy = ClientSimulatorAgent(seed=3).generate_requests("normal", 120, difficulty=0.0)
    hard = ClientSimulatorAgent(seed=3).generate_requests("normal", 120, difficulty=1.0)
    mean = lambda rs: sum(r.patience for r in rs) / len(rs)
    assert mean(hard) < mean(easy)


def test_curriculum_linear_and_adaptive():
    c = Curriculum(start=0.1, end=0.9, episodes=5)
    assert [c.level_at(i) for i in range(5)] == [0.1, 0.3, 0.5, 0.7, 0.9]
    a = Curriculum(start=0.1, end=1.0, target=0.8, step=0.2)
    assert a.update(0.95) == 0.3 and a.update(0.95) == 0.5
    assert a.update(0.2) < 0.5 and a.update(0.2) >= 0.1


# ---------------------------------------------------------------- database
def test_store_roundtrip_sqlite():
    path = os.path.join(tempfile.mkdtemp(), "t.db")
    store = Store("sqlite:///" + path)
    sim = ClientSimulatorAgent(seed=15)
    reqs = sim.generate_requests("normal", 30)[:4]
    store.upsert_clients(sim.crm.values())
    store.upsert_clients(sim.crm.values())                                    # idempotent
    rec = store.get_client(reqs[0].client_id)
    assert rec["card_last4"] == sim.crm[reqs[0].client_id]["card_last4"] and rec["contracts"]
    states = [sim.run_conversation(r, baseline_advisor) for r in reqs]
    run_id = store.create_run("simulation", "normal", "fifo", 15, 3, 0.0, {})
    store.save_incidents(run_id, sim.incidents)
    store.save_calls(run_id, [sim.summary(s) for s in states], {s.request.request_id: s.history for s in states})
    store.save_kpis(run_id, {"abandon_rate": 0.0, "calls": 4})
    assert len(store.run_calls(run_id)) == 4 and store.run_kpis(run_id)["calls"] == 4
    assert store.transcript(run_id, reqs[0].request_id)[0]["role"] == "client"
    assert store.list_runs(5)[0]["run_id"] == run_id
    store.close()


# ---------------------------------------------------------------- LLM fixes + LangGraph agent
def test_truncated_json_reply_is_recovered_not_dropped():
    cut = '{"reply": "Ça fait deux heures que j\'attends !", "emotion": "en colè'   # cut by max_tokens
    sim = ClientSimulatorAgent(seed=5, llm=lambda p: cut)
    st = sim.start(_angry_request())
    assert sim.reply(st, "Bonjour, je regarde.").startswith("Ça fait deux heures") and st.engine["llm"] == 1


def test_llm_failure_is_recorded_not_silent_and_strict_mode_raises():
    def broken(p):
        raise ConnectionError("no network")
    sim = ClientSimulatorAgent(seed=5, llm=broken)
    st = sim.start(_angry_request())
    sim.reply(st, "Bonjour.")
    rep = sim.engine_report(st)
    assert rep["mode"] == "llm_failed" and "no network" in rep["last_error"] and st.llm_error
    assert sim.summary(st)["llm_error"] == st.llm_error
    strict = ClientSimulatorAgent(seed=5, llm=broken, llm_strict=True)
    try:
        strict.reply(strict.start(_angry_request()), "Bonjour.")
        raise AssertionError("strict mode must raise")
    except ConnectionError:
        pass


def test_env_file_loading_and_auto_llm_explains_itself():
    import llm_backends as lb
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, ".env")
        open(p, "w").write('# c\nFOO_TEST_KEY_X="abc"\nexport BAR_TEST_KEY_X=def # note\n')
        os.environ.pop("FOO_TEST_KEY_X", None); os.environ.pop("BAR_TEST_KEY_X", None)
        assert set(lb.load_env(p)) == {"FOO_TEST_KEY_X", "BAR_TEST_KEY_X"}
        assert os.environ["FOO_TEST_KEY_X"] == "abc" and os.environ["BAR_TEST_KEY_X"] == "def"
    saved = {k: os.environ.pop(k) for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY") if k in os.environ}
    try:
        try:
            lb.claude_llm()
            raise AssertionError("must fail without key")
        except lb.LLMError as e:
            assert "ANTHROPIC_API_KEY" in str(e)
    finally:
        os.environ.update(saved)


def test_extract_json_ignores_template_comments():
    from llm_backends import extract_json
    assert extract_json('{"a": true, // note\n "b": "x // y"}') == {"a": True, "b": "x // y"}


def test_graph_matches_direct_conversation_and_traces_nodes():
    from graph import run_call
    req = ClientSimulatorAgent(seed=3).generate_requests("normal", 5)[0]
    direct_sim = ClientSimulatorAgent(seed=3); direct_sim.generate_requests("normal", 5)
    direct = direct_sim.run_conversation(req, baseline_advisor)
    g_sim = ClientSimulatorAgent(seed=3); g_sim.generate_requests("normal", 5)
    out = run_call(g_sim, req, baseline_advisor)
    assert out["conv"].history == direct.history and out["summary"]["status"] == direct.status
    assert out["trace"][0] == "init" and out["trace"][-1] == "finish"
    assert out["trace"][1:5] == ["advisor", "perceive", "update", "speak"]


def test_graph_uses_the_llm_and_reports_engine():
    from graph import run_call
    sim = ClientSimulatorAgent(seed=3, llm=lambda p: '{"reply": "Merci de votre aide.", "emotion": "calme", "mood_adjust": 0.05}')
    req = sim.generate_requests("normal", 5)[0]
    out = run_call(sim, req, baseline_advisor)
    rep = sim.engine_report(out["conv"])
    assert rep["mode"] == "llm" and rep["llm_turns"] == out["conv"].turn + 1 >= 2 and rep["rule_turns"] == 0   # +1 = opening
    assert all(t == "Merci de votre aide." for who, t in out["conv"].history[1:] if who == "client")


def test_graph_abandoned_in_queue_goes_straight_to_finish():
    from graph import run_call
    sim = ClientSimulatorAgent(seed=0)
    req = sim.generate_requests("normal", 10)[0]
    out = run_call(sim, req, baseline_advisor, wait_seconds=req.patience + 1)
    assert out["trace"] == ["init", "finish"] and out["summary"]["status"] == "abandoned"


def test_llm_advisor_only_sees_the_advisor_view():
    from advisors import llm_advisor
    seen = []
    adv = llm_advisor(lambda p: seen.append(p) or "Bonjour, quelle est votre date de naissance ?")
    sim = ClientSimulatorAgent(seed=3); req = sim.generate_requests("normal", 5)[0]
    st = sim.start(req)
    adv(advisor_view(st, sim.crm.get))
    assert req.profile.value not in seen[0] and "fraud" not in seen[0].lower().replace("fraude", "")


def test_opening_message_comes_from_the_llm_and_leaks_nothing():
    seen = []
    sim = ClientSimulatorAgent(seed=3, llm=lambda p: seen.append(p) or '{"reply": "Allô, ma carte est bloquée !"}')
    req = next(r for r in sim.generate_requests("vague_de_fraude", 30) if r.is_fraudster)
    st = sim.start(req)
    assert st.history[0] == ("client", "Allô, ma carte est bloquée !") and st.engine["llm_opening"] == 1
    assert sim.engine_report(st)["llm_turns"] == 1
    p = req.persona
    assert all(v not in seen[0] for v in p.true_facts.values()) and "fraud" not in seen[0].lower().replace("fraude", "")


def test_opening_falls_back_to_template_and_reports_error():
    sim = ClientSimulatorAgent(seed=3, llm=lambda p: "{cassé")
    req = sim.generate_requests("normal", 5)[0]
    st = sim.start(req)
    assert st.history[0] == ("client", req.opening_message) and st.llm_error
