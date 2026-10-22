"""Bad-words detection. Run with: pytest -q test_profanity.py  (no external library needed)."""
import json

import profanity
from client_simulator import ClientSimulatorAgent, Profile
from judge import Judge, rule_analyse
from profanity import scan
from scorecard import score_call


def _request(profile=Profile.LOYAL):
    sim = ClientSimulatorAgent(seed=0)
    return next(r for r in sim.generate_requests("panne", 30) if r.profile == profile and not r.is_fraudster)


def _call(msgs, **kw):
    sim = ClientSimulatorAgent(seed=5, **kw)
    st = sim.start(_request())
    for m in msgs:
        sim.reply(st, m)
        if st.status != "active":
            break
    return st


# ------------------------------------------------------------------ detector
def test_detects_french_english_and_darija():
    for text in ["Vous êtes un connard", "putain de merde", "enculé", "this is bullshit", "you idiot",
                 "Shut up", "ya kelb", "Tu es une 9a7ba", "nik omek", "نيكمك", "يا كلب", "je vais vous tuer"]:
        assert scan(text)["found"], text


def test_clean_messages_and_look_alike_words_are_not_flagged():
    for text in ["Bonjour, comment puis-je vous aider ?", "Je vérifie votre contrat et votre classe de compte.",
                 "assistant", "Scunthorpe", "Voici les pros and cons", "Your card ends in 4821.", "kra",
                 "Please hold", "Mr Dickens", "analyse du bassin", "Hello, this is Nik", "تفضل سيدي",
                 "El carte mte3ek bloquée, 7ata mochkla"]:
        assert not scan(text)["found"], text
    assert scan("")["found"] is False and scan("   ")["found"] is False


def test_obfuscation_is_caught():
    for text in ["c0nnard", "sh1t", "f*ck", "m*rde", "f u c k", "f.u.c.k", "fuuuuuck", "ENCULÉ", "a55hole", "b*tch"]:
        assert scan(text)["found"], text


def test_levels():
    assert scan("c'est stupide")["level"] == 1
    assert scan("quel connard")["level"] == 2
    assert scan("fils de pute")["level"] == 3
    assert scan("quel connard, fils de pute")["level"] == 3          # the worst word wins
    assert scan("je vais vous tuer")["categories"] == ["threat"]


def test_bad_words_are_masked_in_the_output():
    r = scan("Vous êtes un connard et un idiot")
    assert r["count"] == 2
    assert "connard" not in json.dumps(r) and "idiot" not in json.dumps(r)
    assert r["masked"] == "Vous êtes un c*****d et un i***t"


def test_extra_words_file(tmp_path, monkeypatch):
    f = tmp_path / "extra.txt"
    f.write_text("# comment\ncrapule,1\nzorglub\n", encoding="utf-8")
    monkeypatch.setenv("PROFANITY_EXTRA_FILE", str(f))
    monkeypatch.setattr(profanity, "_EXTRA", [])
    monkeypatch.setattr(profanity, "_EXTRA_LOADED", False)
    assert scan("quelle crapule")["level"] == 1
    assert scan("espèce de Zorglub")["level"] == 2


# ------------------------------------------------------------------ judge
def test_judge_flags_bad_words_and_marks_the_tone_rude():
    f = rule_analyse("Merci de patienter, quel connard ce client.")
    assert f["grossier"] and f["grossier_niveau"] == 2 and f["tone"] == "rude"
    assert f["grossier_mots"] == ["c*****d"]
    ok = rule_analyse("Bonjour, je vous écoute.")
    assert not ok["grossier"] and ok["grossier_niveau"] == 0 and ok["tone"] == "polite"


def test_llm_judge_cannot_excuse_swearing():
    llm = lambda prompt: json.dumps({"resolution": False, "promise": False, "empathy": True, "identity_check": False,
                                     "identity_fields": [], "sensitive_request": False, "escalation": False,
                                     "hold": False, "uncertainty": False, "refusal": False, "sourced": False,
                                     "tone": "polite"})
    f = Judge(llm).analyse("Je comprends, mais vous êtes un idiot.")
    assert f["judge"] == "llm" and f["grossier"] and f["tone"] == "rude"


# ------------------------------------------------------------------ agent + scorecard
def test_bad_language_hurts_mood_and_is_counted():
    clean = _call(["Bonjour, je vous écoute."])
    rude = _call(["Bonjour, quel connard ce système."])
    assert rude.profanity_count == 1 and rude.profanity_points == 2
    assert rude.mood < clean.mood - 0.3


def test_severe_insult_makes_the_client_hang_up_unless_disabled():
    msgs = ["Bonjour, je vous écoute.", "Fils de pute, répondez."]
    assert _call(msgs).status == "hung_up"
    assert _call(msgs, profanity_hangup=False).status == "active"


def test_repeated_vulgar_language_ends_the_call():
    st = _call(["Quel connard.", "Encore ce merdier de merde.", "Quel con vous faites."])
    assert st.status == "hung_up" and st.profanity_points >= 5


def test_summary_and_scorecard_report_bad_language():
    st = _call(["Bonjour, je vous écoute.", "Vous êtes un connard."], profanity_hangup=False)
    s = ClientSimulatorAgent.summary(st)
    assert s["profanity_count"] == 1 and s["profanity_points"] == 2
    sc = score_call(st)
    assert any("offensive language" in c.lower() for c in sc["critical_failures"])
    assert sc["total"] <= 40 and sc["criteria"]["compliance"]["score"] == 0
    assert any(c["step"].startswith("Professional language") and not c["done"] for c in sc["checklist"])
    turn2 = sc["per_turn"][1]
    assert "offensive language" in turn2["bad"] and turn2["bad_words"] == ["c*****d"]


def test_mild_words_are_a_warning_not_a_critical_failure():
    st = _call(["Bonjour, je vous écoute.", "Ne soyez pas stupide, je vérifie."], profanity_hangup=False)
    sc = score_call(st)
    assert not any("offensive" in c.lower() for c in sc["critical_failures"])
    assert any("Unprofessional language" in w for w in sc["warnings"])


def test_clean_call_has_no_bad_language_flags():
    st = _call(["Bonjour, je vous écoute.", "Pouvez-vous confirmer votre date de naissance ?"])
    sc = score_call(st)
    assert st.profanity_count == 0
    assert all(c["done"] for c in sc["checklist"] if c["step"].startswith("Professional language"))


# ------------------------------------------------------------------ optional ML model (fake pipeline: no download)
from toxicity_model import ToxicityModel, ToxicityModelError, load_toxicity_model


def _fake_model(score, labels=("neutral", "toxic"), threshold=0.7):
    def pipe(text):
        return [{"label": labels[1], "score": score}, {"label": labels[0], "score": 1 - score}]
    return ToxicityModel("fake", threshold, pipeline=pipe)


def test_model_score_works_with_different_label_names():
    assert _fake_model(0.9).predict("x")["toxic"]
    assert not _fake_model(0.2).predict("x")["toxic"]
    assert _fake_model(0.9, labels=("LABEL_0", "LABEL_1")).predict("x")["score"] == 0.9
    assert abs(_fake_model(0.9, labels=("not-toxic", "weird")).predict("x")["score"] - 0.9) < 1e-9


def test_model_catches_an_insult_without_a_listed_word():
    msg = "Vous ne servez à rien, madame."
    assert not rule_analyse(msg)["grossier"]
    f = Judge(toxicity_model=_fake_model(0.8)).analyse(msg)
    assert f["grossier"] and f["grossier_niveau"] == 1 and f["tone"] == "rude" and f["toxicite_modele"] == 0.8
    assert Judge(toxicity_model=_fake_model(0.97)).analyse(msg)["grossier_niveau"] == 2


def test_model_never_removes_a_word_list_detection():
    f = Judge(toxicity_model=_fake_model(0.01)).analyse("Quel connard.")
    assert f["grossier"] and f["grossier_niveau"] == 2 and f["toxicite_modele"] == 0.01


def test_model_failure_keeps_the_word_list_verdict():
    def boom(text):
        raise RuntimeError("out of memory")
    m = ToxicityModel("fake", pipeline=boom)
    f = Judge(toxicity_model=m).analyse("Quel connard.")
    assert f["grossier"] and f["toxicite_modele"] is None and "out of memory" in m.last_error
    assert not Judge(toxicity_model=m).analyse("Bonjour.")["grossier"]


def test_model_in_a_call_and_loader_errors():
    sim = ClientSimulatorAgent(seed=5, toxicity_model=_fake_model(0.9), profanity_hangup=False)
    st = sim.start(_request())
    sim.reply(st, "Vous ne servez à rien.")
    assert st.profanity_count == 1 and st.turn_log[0]["flags"]["toxicite_modele"] == 0.9
    assert load_toxicity_model("none") is None and load_toxicity_model(None) is None
    try:
        load_toxicity_model("no-such-org/no-such-model")
    except ToxicityModelError:
        pass                                    # transformers missing or model unknown: a clear error, not a crash
    else:
        raise AssertionError("expected ToxicityModelError")
