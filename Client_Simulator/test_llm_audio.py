"""Tests for the LLM backends, the audio layer and the voice API. No network, no API key needed."""
import json

import httpx
import pytest

import audio
import llm_backends as lb
from client_simulator import ClientSimulatorAgent


# ------------------------------------------------------------------ fallback chain
def _ok(text):
    return lambda p: text


def _boom(transient=True):
    def f(p):
        raise lb.LLMError("down", transient=transient)
    return f


def test_chain_uses_first_working_provider():
    c = lb.Chain([("a", _ok('{"x":1}')), ("b", _ok("never"))], sleep=lambda s: None)
    assert c("hi") == '{"x":1}' and c.last_used == "a"


def test_chain_falls_back_and_remembers_the_failure():
    c = lb.Chain([("a", _boom()), ("b", _ok("from b"))], sleep=lambda s: None)
    assert c("hi") == "from b" and c.last_used == "b" and "a" in c.errors


def test_chain_retries_a_transient_error_once():
    calls = {"n": 0}

    def flaky(p):
        calls["n"] += 1
        if calls["n"] == 1:
            raise lb.LLMError("429")
        return "ok"
    c = lb.Chain([("a", flaky)], sleep=lambda s: None)
    assert c("hi") == "ok" and calls["n"] == 2


def test_circuit_breaker_skips_a_failed_provider_then_retries_it():
    t = {"now": 0.0}
    hits = {"a": 0}

    def a(p):
        hits["a"] += 1
        raise lb.LLMError("down")
    c = lb.Chain([("a", a), ("b", _ok("b"))], cooldown_s=30, clock=lambda: t["now"], sleep=lambda s: None)
    c("1")
    before = hits["a"]
    c("2")                                  # a is cooling down: not called again
    assert hits["a"] == before
    t["now"] = 100.0                        # cooldown over
    c("3")
    assert hits["a"] > before


def test_chain_raises_when_all_fail():
    c = lb.Chain([("a", _boom()), ("b", _boom())], sleep=lambda s: None)
    with pytest.raises(lb.LLMError, match="all LLM providers failed"):
        c("hi")


def test_chain_rejects_empty_answer():
    c = lb.Chain([("a", _ok("  ")), ("b", _ok("real"))], sleep=lambda s: None)
    assert c("hi") == "real"


# ------------------------------------------------------------------ providers (HTTP mocked)
def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_groq_sends_json_mode_and_parses(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        seen["auth"] = req.headers["authorization"]
        return httpx.Response(200, json={"choices": [{"message": {"content": ' {"reply":"salut"} '}}]})
    llm = lb.groq_llm(client=_client(handler))
    assert llm("prompt json") == '{"reply":"salut"}'
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert seen["body"]["model"] == lb.DEFAULT_MODELS["groq"] and seen["auth"] == "Bearer k"


@pytest.mark.parametrize("status,transient", [(429, True), (503, True), (401, False)])
def test_groq_error_classification(monkeypatch, status, transient):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    llm = lb.groq_llm(client=_client(lambda r: httpx.Response(status, text="err")))
    with pytest.raises(lb.LLMError) as e:
        llm("x")
    assert e.value.transient is transient


def test_groq_without_key_is_not_transient(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(lb.LLMError) as e:
        lb.groq_llm()
    assert e.value.transient is False


def test_ollama_payload(monkeypatch):
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"message": {"content": "{}"}})
    lb.ollama_llm(model="mistral", client=_client(handler))("x")
    b = seen["body"]
    assert b["format"] == "json" and b["stream"] is False and b["keep_alive"] and b["model"] == "mistral"


def test_ollama_unreachable_message_is_actionable():
    def handler(req):
        raise httpx.ConnectError("refused")
    with pytest.raises(lb.LLMError, match="ollama serve"):
        lb.ollama_llm(client=_client(handler))("x")


# ------------------------------------------------------------------ build_llm
def _avail(**on):
    return {n: {"available": on.get(n, False), "note": "n/a"} for n in lb.FACTORIES}


def test_build_none_and_unknown():
    assert lb.build_llm("none") is None and lb.build_llm("") is None
    with pytest.raises(lb.LLMError, match="unknown"):
        lb.build_llm("skynet", _available=_avail())


def test_build_auto_orders_available_providers(monkeypatch):
    made = []
    monkeypatch.setattr(lb, "FACTORIES", {n: (lambda n=n, **kw: (made.append((n, kw.get("model"))), _ok(n))[1])
                                          for n in ("groq", "gemini", "ollama", "claude")})
    chain = lb.build_llm("auto", _available=_avail(groq=True, ollama=True))
    assert [m[0] for m in made] == ["groq", "ollama"]
    assert chain.names[0].startswith("groq:") and len(chain.links) == 2


def test_build_explicit_chain_with_model(monkeypatch):
    monkeypatch.setattr(lb, "FACTORIES", {n: (lambda n=n, **kw: _ok(f"{n}/{kw.get('model')}"))
                                          for n in ("groq", "gemini", "ollama", "claude")})
    chain = lb.build_llm("groq:llama-3.1-8b-instant,ollama:mistral", _available=_avail(groq=True))
    assert chain.names == ["groq:llama-3.1-8b-instant", "ollama:mistral"]
    assert chain("x") == "groq/llama-3.1-8b-instant"


def test_build_nothing_available_explains_why():
    with pytest.raises(lb.LLMError, match="GROQ|n/a"):
        lb.build_llm("groq", _available=_avail())


# ------------------------------------------------------------------ the simulator with a chain
def test_simulator_plays_with_a_fallback_chain_and_survives_total_failure():
    good = lb.Chain([("bad", _boom()), ("ok", _ok(json.dumps(
        {"reply": "Bonjour, ma carte est bloquée.", "emotion": "agacé", "mood_adjust": 0.0, "hang_up": False})))],
        sleep=lambda s: None)
    sim = ClientSimulatorAgent(seed=3, llm=good)
    st = sim.start(sim.generate_requests("normal", 5)[0])
    sim.reply(st, "Bonjour, je vous écoute.")
    assert st.engine["llm"] == 1 and good.last_used == "ok"

    dead = lb.Chain([("bad", _boom())], sleep=lambda s: None)
    sim2 = ClientSimulatorAgent(seed=3, llm=dead)
    st2 = sim2.start(sim2.generate_requests("normal", 5)[0])
    assert sim2.reply(st2, "Bonjour.") and st2.engine["rules"] == 1 and sim2.stats["llm_failed"] == 1


# ------------------------------------------------------------------ audio
def test_voice_follows_gender_and_language():
    assert audio.pick_voice("Salma Trabelsi", "fr") == ("fr-FR-DeniseNeural", "fr")
    assert audio.pick_voice("Karim Jebali", "mix") == ("fr-FR-HenriNeural", "fr")
    assert audio.pick_voice("Karim Jebali", "darija") == ("ar-TN-HediNeural", "ar")
    assert audio.pick_voice("Rim Sassi", "darija") == ("ar-TN-ReemNeural", "ar")


def test_prosody_follows_emotion():
    assert audio.prosody("en colère")[0] == "+12%"
    assert audio.prosody("anxieux")[1] == "+6Hz"
    assert audio.prosody(None, 0.5) == ("+0%", "+0Hz")


@pytest.mark.parametrize("t", ["", " ", ".", "Sous-titrage ST' 501", "Merci.", "Thanks for watching!"])
def test_hallucinated_transcripts_are_dropped(t):
    assert audio.looks_like_hallucination(t)


def test_real_sentence_is_kept():
    assert not audio.looks_like_hallucination("Bonjour, pouvez-vous me donner votre date de naissance ?")


def test_stt_falls_back_to_the_next_provider(monkeypatch):
    monkeypatch.setattr(audio, "_stt_groq", lambda p, l: (_ for _ in ()).throw(audio.AudioError("no key")))
    monkeypatch.setattr(audio, "_stt_faster_whisper", lambda p, l, s: "Bonjour madame")
    assert audio.speech_to_text("x.webm") == "Bonjour madame"


def test_stt_silence_returns_empty(monkeypatch):
    monkeypatch.setattr(audio, "_stt_groq", lambda p, l: "Sous-titres réalisés par la communauté d'Amara.org")
    assert audio.speech_to_text("x.webm", provider="groq") == ""


def test_stt_all_fail_gives_a_fix(monkeypatch):
    for fn in ("_stt_groq",):
        monkeypatch.setattr(audio, fn, lambda *a: (_ for _ in ()).throw(audio.AudioError("no key")))
    for fn in ("_stt_faster_whisper", "_stt_whisper"):
        monkeypatch.setattr(audio, fn, lambda *a: (_ for _ in ()).throw(ImportError()))
    with pytest.raises(audio.AudioError, match="GROQ_API_KEY"):
        audio.speech_to_text("x.webm")


def test_arabic_script_helper():
    llm = _ok('```json\n{"ar": "ما نجمش نكلم"}\n```')
    assert audio.to_arabic_script("ma najamch nkalem", llm) == "ما نجمش نكلم"
    assert audio.to_arabic_script("texte", None) == "texte"
    assert audio.to_arabic_script("texte", _boom()) == "texte"


def test_tts_picks_voice_and_falls_back(monkeypatch):
    seen = {}
    monkeypatch.setattr(audio, "_tts_edge", lambda t, o, v, r, p: (_ for _ in ()).throw(ImportError()))
    monkeypatch.setattr(audio, "_tts_gtts", lambda t, o, lang: seen.setdefault("lang", lang) and o)
    assert audio.text_to_speech("ahla", "o.mp3", client_name="Karim X", language="darija") == "o.mp3"
    assert seen["lang"] == "ar"
    monkeypatch.setattr(audio, "_tts_edge", lambda t, o, v, r, p: seen.update(voice=v, rate=r) or o)
    audio.text_to_speech("ahla", "o.mp3", client_name="Salma X", language="fr", emotion="agacé")
    assert seen["voice"] == "fr-FR-DeniseNeural" and seen["rate"] == "+12%"


# ------------------------------------------------------------------ API
@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    import api
    monkeypatch.setenv("QA_DATABASE_URL", "")
    api._LLM_CACHE.clear()
    api.CALLS.clear()
    api.CALL_SCENARIOS.clear()
    api.QUALITY_REPORTS.clear()
    return TestClient(api.app)


def test_backends_endpoint(client):
    j = client.get("/api/backends").json()
    assert set(j["llm"]) == {"groq", "gemini", "ollama", "claude"} and "darija" in j["languages"]
    assert set(j["stt"]) == {"groq", "faster_whisper", "whisper"} and set(j["tts"]) == {"edge", "gtts"}


def test_quality_analysis_requires_a_completed_call(client):
    c = client.post("/api/calls", json={"scenario": "normal"}).json()

    response = client.post(f"/api/calls/{c['call_id']}/quality-analysis", json={})

    assert response.status_code == 409


def test_quality_analysis_uses_hidden_call_state_and_persists_report(client, monkeypatch, tmp_path):
    import api
    expected = {"call_id": "placeholder", "overall_score_0_to_100": 75}
    monkeypatch.setattr(api, "analyze_case", lambda case, **kwargs: {
        **expected,
        "call_id": case["call_id"],
        "scenario": case["context"]["scenario"],
        "advisor_trace": case["context"]["advisor_trace"],
    })
    saved = []

    def fake_save(report, path, **kwargs):
        saved.append((report, path))
        return 17

    monkeypatch.setattr(api, "save_report", fake_save)
    c = client.post("/api/calls", json={"scenario": "contestation_carte"}).json()
    api.QUALITY_ANALYST_DIR = tmp_path
    ended = client.post(f"/api/calls/{c['call_id']}/end")
    assert ended.status_code == 200

    response = client.post(
        f"/api/calls/{c['call_id']}/quality-analysis",
        json={"advisor_trace": [{"turn": 1, "tools_called": ["search_bank_policies"]}]},
    )

    assert response.status_code == 200
    assert response.json()["call_id"] == c["call_id"]
    assert response.json()["scenario"] == "contestation_carte"
    assert response.json()["advisor_trace"][0]["turn"] == 1
    assert response.json()["database_id"] == 17
    assert len(saved) == 1


def test_quality_analysis_saves_conversation_before_llm_failure(client, monkeypatch):
    import api
    saved = []
    monkeypatch.setattr(api, "save_conversation", lambda case, database, **kwargs: saved.append(case))
    monkeypatch.setattr(
        api,
        "analyze_case",
        lambda case, **kwargs: (_ for _ in ()).throw(api.QualityAnalystError("Groq denied access.")),
    )
    call = client.post("/api/calls", json={"scenario": "normal"}).json()
    client.post(f"/api/calls/{call['call_id']}/end")

    response = client.post(f"/api/calls/{call['call_id']}/quality-analysis", json={})

    assert response.status_code == 502
    assert len(saved) == 1
    assert saved[0]["call_id"] == call["call_id"]


def test_quality_analysis_can_run_rules_only_without_calling_groq(client, monkeypatch, tmp_path):
    import api
    captured = {}
    monkeypatch.setattr(api, "QUALITY_ANALYST_DIR", tmp_path)
    monkeypatch.setattr(api, "save_conversation", lambda *args, **kwargs: None)
    monkeypatch.setattr(api, "save_report", lambda *args, **kwargs: 23)

    def fake_analyze(case, **kwargs):
        captured.update(kwargs)
        return {
            "call_id": case["call_id"],
            "database_id": None,
            "score_global": None,
            "overall_score_0_to_100": None,
            "llm_judgment": {"status": "skipped", "provider": "groq"},
            "rule_evaluation": {"checks": [], "errors": [], "warnings": []},
            "recommendations": [],
        }

    monkeypatch.setattr(api, "analyze_case", fake_analyze)
    call = client.post("/api/calls", json={"scenario": "normal"}).json()
    client.post(f"/api/calls/{call['call_id']}/end")

    response = client.post(
        f"/api/calls/{call['call_id']}/quality-analysis",
        json={"rules_only": True},
    )

    assert response.status_code == 200
    assert captured["use_llm"] is False
    assert response.json()["llm_judgment"]["status"] == "skipped"
    assert response.json()["score_global"] is None


def test_quality_report_history_lists_and_returns_saved_report(client, monkeypatch, tmp_path):
    import api

    monkeypatch.setattr(api, "QUALITY_ANALYST_DIR", tmp_path)
    report = {
        "call_id": "history-call",
        "scenario": "normal",
        "overall_score_0_to_100": 88,
        "llm_judgment": {"status": "completed", "summary": "Conseil clair."},
    }
    report_id = api.save_report(
        report,
        tmp_path / "data" / "quality_reports.sqlite3",
    )

    history = client.get("/api/quality/reports")
    detail = client.get(f"/api/quality/reports/{report_id}")
    missing = client.get("/api/quality/reports/999")

    assert history.status_code == 200
    assert history.json()[0]["database_id"] == report_id
    assert history.json()[0]["summary"] == "Conseil clair."
    assert detail.status_code == 200
    assert detail.json()["call_id"] == "history-call"
    assert detail.json()["stored_at"]
    assert missing.status_code == 404


def test_unusable_llm_is_a_clear_400(client):
    r = client.post("/api/calls", json={"llm": "groq"})
    assert r.status_code == 400 and "GROQ" in r.json()["detail"]


def test_call_with_llm_chain_reports_engine(client):
    import api
    api._LLM_CACHE["fake"] = lb.Chain([("fake:m", _ok(json.dumps(
        {"reply": "Oui, allô ?", "emotion": "calme", "mood_adjust": 0, "hang_up": False})))])
    c = client.post("/api/calls", json={"llm": "fake", "language": "darija", "scenario": "normal"}).json()
    assert c["client"]["language"] == "darija"
    r = client.post(f"/api/calls/{c['call_id']}/reply", json={"message": "Bonjour, je vous écoute."}).json()
    assert r["engine"].get("llm") == 1 and r["llm_used"] == "fake:m"


def test_voice_turn_round_trip(client, monkeypatch):
    import api
    c = client.post("/api/calls", json={"scenario": "normal"}).json()
    monkeypatch.setattr(audio, "speech_to_text", lambda p, language=None, **k: "Bonjour, je vous écoute.")
    monkeypatch.setattr(audio, "text_to_speech", lambda t, o, *a, **k: open(o, "wb").write(b"ID3fake") and o)
    r = client.post(f"/api/calls/{c['call_id']}/voice", files={"file": ("v.webm", b"abc", "audio/webm")}).json()
    assert r["transcript"] == "Bonjour, je vous écoute." and r["silence"] is False
    import base64
    assert base64.b64decode(r["audio_b64"]) == b"ID3fake"
    assert r["history"][-2]["role"] == "conseiller" and r["history"][-1]["role"] == "client"


def test_voice_silence_leaves_the_call_untouched(client, monkeypatch):
    c = client.post("/api/calls", json={"scenario": "normal"}).json()
    monkeypatch.setattr(audio, "speech_to_text", lambda p, language=None, **k: "")
    r = client.post(f"/api/calls/{c['call_id']}/voice", files={"file": ("v.webm", b"abc", "audio/webm")}).json()
    assert r["silence"] is True and r["turn"] == c["turn"] and len(r["history"]) == len(c["history"])


def test_voice_tts_failure_keeps_the_text_reply(client, monkeypatch):
    c = client.post("/api/calls", json={"scenario": "normal"}).json()
    monkeypatch.setattr(audio, "speech_to_text", lambda p, language=None, **k: "Bonjour.")
    monkeypatch.setattr(audio, "text_to_speech",
                        lambda *a, **k: (_ for _ in ()).throw(audio.AudioError("offline")))
    r = client.post(f"/api/calls/{c['call_id']}/voice", files={"file": ("v.webm", b"abc", "audio/webm")}).json()
    assert r["audio_b64"] is None and "offline" in r["tts_error"] and r["history"][-1]["role"] == "client"


def test_stt_failure_is_a_clean_error(client, monkeypatch):
    c = client.post("/api/calls", json={"scenario": "normal"}).json()
    monkeypatch.setattr(audio, "speech_to_text",
                        lambda *a, **k: (_ for _ in ()).throw(audio.AudioError("no key | not installed")))
    r = client.post(f"/api/calls/{c['call_id']}/voice", files={"file": ("v.webm", b"abc", "audio/webm")})
    assert r.status_code == 501
