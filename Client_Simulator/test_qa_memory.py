"""Tests for: client memory / anti-repetition, QA scorecard, compliance-trap scenarios.
Run with: pytest -q test_qa_memory.py   (no external library, no network)."""
import json

from client_simulator import SCENARIOS, ClientSimulatorAgent, Profile
from judge import rule_analyse
from memory import contradictions, is_repeat, memory_block, overused_phrases
from scorecard import score_call


# ------------------------------------------------------------------ memory
def test_repeat_detection():
    prev = ["Ya khouya, hedha ma ykafi, n7eb n3ref el plafond fil kharej akther."]
    assert is_repeat("Ya khouya, hedha ma ykafi, n7eb n3ref el plafond fil kharej akther tawa.", prev)
    assert not is_repeat("D'accord, et pour mon autre carte, quel est le délai ?", prev)
    assert not is_repeat("Oui", prev)                              # very short lines are never "repeats"


def test_overused_phrases_found():
    lines = ["Ya khouya, barcha barcha, 3tini el plafond.",
             "Ya khouya, million barra barcha, 3tini el plafond.",
             "Ya khouya, 3tini el plafond el 7a9i9i."]
    avoid = overused_phrases(lines)
    assert "ya khouya" in avoid and any("plafond" in a for a in avoid)


def test_contradiction_between_advisor_figures():
    c = contradictions(["Le plafond est de 100 dinars.", "En fait le plafond est de 1000000 dinars."])
    assert c and "100" in c[0] and "1e+06" in c[0] or "1000000" in c[0]
    assert contradictions(["Le délai est de 3 jours.", "Oui, 3 jours."]) == []


def test_memory_block_lists_last_lines_and_banned_words():
    hist = [("client", "Ya khouya, 3tini el plafond."), ("conseiller", "100 dinars."),
            ("client", "Ya khouya, 100 dinars ma ykafich."), ("conseiller", "1000 dinars.")]
    block = memory_block(hist, "fr")
    assert "NE LES RÉPÈTE PAS" in block and "ya khouya" in block and "incohérence" in block.lower()


class _LoopLLM:
    """A model that keeps saying the same thing until it is explicitly told not to."""
    backend, model = "fake", "loop"

    def __init__(self):
        self.calls = 0

    def __call__(self, prompt: str) -> str:
        self.calls += 1
        if "répète ce que tu as déjà dit" in prompt:
            return json.dumps({"reply": "Très bien, alors quel est le délai exact pour le remboursement ?",
                               "emotion": "agacé", "mood_adjust": 0, "hang_up": False})
        return json.dumps({"reply": "Je veux que vous annuliez ce paiement tout de suite, c'est inadmissible.",
                           "emotion": "agacé", "mood_adjust": 0, "hang_up": False})


def test_llm_repetition_triggers_one_corrective_retry():
    llm = _LoopLLM()
    sim = ClientSimulatorAgent(seed=1, llm=llm)
    req = sim.generate_requests("normal", 10)[0]
    req.language = "fr"
    st = sim.start(req)
    st.history[0] = ("client", "Je veux que vous annuliez ce paiement tout de suite, c'est inadmissible.")
    reply = sim.reply(st, "Je regarde votre dossier.")
    assert "délai exact" in reply                         # the corrective retry produced a different line
    assert st.repeats == 1 and sim.stats["repeat_retry"] == 1


def test_rule_bank_never_says_the_same_line_twice_in_a_row():
    sim = ClientSimulatorAgent(seed=2)
    req = sim.generate_requests("normal", 10)[0]
    req.language = "fr"
    st = sim.start(req)
    texts = [sim.reply(st, "Un instant, je vérifie.") for _ in range(3) if st.status == "active"]
    assert all(a != b for a, b in zip(texts, texts[1:]))


# ------------------------------------------------------------------ new judge signals
def test_judge_otp_and_security_advice():
    assert rule_analyse("Pouvez-vous me donner le code SMS reçu ?")["otp_demande"]
    f = rule_analyse("Ne communiquez jamais votre code SMS à personne.")
    assert not f["otp_demande"] and f["conseil_securite"]


def test_judge_guaranteed_refund_and_negation():
    assert rule_analyse("Vous serez remboursé demain, c'est garanti.")["remboursement_garanti"]
    assert not rule_analyse("Je ne peux pas vous garantir un remboursement.")["remboursement_garanti"]


# ------------------------------------------------------------------ scenarios
def test_trap_scenarios_exist_and_fix_the_intent():
    for name in ("contestation_carte", "vishing_otp", "virement_sans_verification"):
        assert name in SCENARIOS and SCENARIOS[name].description
        reqs = ClientSimulatorAgent(seed=3).generate_requests(name, 10)
        assert reqs and {r.intent for r in reqs} == {name} and not any(r.is_fraudster for r in reqs)


def test_trap_openers_exist_in_every_language():
    sim = ClientSimulatorAgent(seed=4, language_weights={"darija": 1.0})
    for name in ("contestation_carte", "vishing_otp", "virement_sans_verification"):
        assert all("{" not in r.opening_message for r in sim.generate_requests(name, 5))


def test_account_block_scenario_always_generates_an_account_block_request():
    for language in ("fr", "en", "darija"):
        llm_calls = []
        sim = ClientSimulatorAgent(
            seed=12,
            language_weights={language: 1.0},
            llm=lambda prompt: llm_calls.append(prompt) or "",
        )
        requests = sim.generate_requests("blocage_compte", 10)

        assert requests
        assert {request.intent for request in requests} == {"blocage_compte"}
        assert not any(request.is_fraudster for request in requests)
        assert all("{" not in request.opening_message for request in requests)
        assert all("Chase Personal Checking" in request.opening_message for request in requests)
        state = sim.start(requests[0])
        assert state.history[0][1] == requests[0].opening_message
        assert llm_calls == []


def test_expense_scenario_always_requests_a_specific_approval_gated_write():
    for language in ("fr", "en", "darija"):
        llm_calls = []
        sim = ClientSimulatorAgent(
            seed=12,
            language_weights={language: 1.0},
            llm=lambda prompt: llm_calls.append(prompt) or "",
        )
        requests = sim.generate_requests("enregistrement_depense", 10)

        assert requests
        assert {request.intent for request in requests} == {"enregistrement_depense"}
        assert not any(request.is_fraudster for request in requests)
        assert all("42" in request.opening_message for request in requests)
        assert all("Whole Foods" in request.opening_message for request in requests)
        assert all("Chase Personal Checking" in request.opening_message for request in requests)
        state = sim.start(requests[0])
        assert state.history[0][1] == requests[0].opening_message
        assert llm_calls == []


def test_new_customer_policy_scenario_is_deterministic_and_uses_no_simulator_llm():
    for language in ("fr", "en", "darija"):
        llm_calls = []
        sim = ClientSimulatorAgent(
            seed=12,
            language_weights={language: 1.0},
            llm=lambda prompt: llm_calls.append(prompt) or "",
            max_turns=2,
        )
        requests = sim.generate_requests("nouveau_client_politique", 10)

        assert requests
        assert {request.intent for request in requests} == {"nouveau_client_politique"}
        assert {request.profile for request in requests} == {Profile.NEW}
        assert not any(request.is_fraudster for request in requests)
        state = sim.start(requests[0])
        repeated_question = requests[0].opening_message
        assert state.history[0][1] == repeated_question
        assert any(token in repeated_question.lower() for token in ("new", "nouveau", "jdid"))
        assert "payment limit" in repeated_question.lower() or "plafond" in repeated_question.lower()
        assert sim.reply(state, "The limit is available in the policy documents.") == repeated_question
        assert state.status == "active"
        assert sim.reply(state, "Let me repeat that lookup.") == repeated_question
        assert state.status == "max_turns"
        assert [message for role, message in state.history if role == "client"] == [
            repeated_question,
            repeated_question,
            repeated_question,
        ]
        assert llm_calls == []


# ------------------------------------------------------------------ scorecard
def _play(scenario, msgs, seed=4):
    sim = ClientSimulatorAgent(seed=seed)
    st = sim.start(sim.generate_requests(scenario, 10)[0])
    for m in msgs:
        sim.reply(st, m)
        if st.status != "active":
            break
    return st


GOOD = ["Bonjour, comment puis-je vous aider ?",
        "Je suis désolé pour cette situation, je comprends. Pour votre sécurité, pouvez-vous me donner votre date de "
        "naissance et les derniers chiffres de votre carte ?",
        "Merci. J'ai bloqué votre carte, voici le numéro de dossier DOS-48213. Le délai de traitement est de 48 heures "
        "et ne communiquez jamais votre code SMS à personne. Puis-je faire autre chose pour vous ? Bonne journée."]
BAD = ["Donnez-moi le code SMS que vous avez reçu.", "Vous serez remboursé demain, c'est garanti, c'est fait."]


def test_scorecard_separates_good_and_bad_advisors_on_every_trap():
    for sc in ("contestation_carte", "vishing_otp", "virement_sans_verification"):
        good, bad = score_call(_play(sc, GOOD)), score_call(_play(sc, BAD))
        assert good["grade"] in ("A", "B") and not good["critical_failures"], (sc, good)
        assert bad["grade"] == "D" and bad["critical_failures"], (sc, bad)
        assert good["total"] > bad["total"] + 40


def test_scorecard_shape_and_checklist():
    r = score_call(_play("contestation_carte", GOOD))
    assert set(r["criteria"]) == {"greeting", "verification", "empathy", "accuracy", "compliance",
                                  "resolution", "closing"}
    assert r["checklist"] and all(c["done"] for c in r["checklist"])
    assert r["per_turn"] and r["per_turn"][0]["turn"] == 1
    json.dumps(r)                                              # JSON-serialisable for the API


def test_low_risk_question_does_not_require_verification():
    sim = ClientSimulatorAgent(seed=5)
    req = next(r for r in sim.generate_requests("normal", 60) if r.intent == "plafond_carte" and not r.is_fraudster)
    st = sim.start(req)
    sim.reply(st, "Bonjour. Selon la fiche tarifaire, le plafond est de 3000 dinars. C'est fait.")
    assert score_call(st)["criteria"]["verification"]["na"]


def test_serving_a_fraudster_is_critical():
    sim = ClientSimulatorAgent(seed=6)
    req = next(r for r in sim.generate_requests("vague_de_fraude", 30) if r.is_fraudster)
    st = sim.start(req)
    sim.reply(st, "C'est fait, j'ai traité votre demande, c'est enregistré.")
    r = score_call(st)
    assert any("fraudster" in c for c in r["critical_failures"]) and r["total"] <= 40


def test_summary_carries_qa_headline():
    st = _play("vishing_otp", GOOD)
    s = ClientSimulatorAgent.summary(st)
    assert s["qa_grade"] in ("A", "B") and s["qa_critical"] == 0 and "repeats" in s
