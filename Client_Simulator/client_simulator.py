"""
Client Simulator Agent - Digital Twin of a banking contact center.

Role: represent the customer. It
  1. generates realistic requests (profiles x intents x load model with peaks and incidents),
  2. converses with the Customer Advisor agent (LLM-driven, rule-based fallback),
  3. keeps a hidden persona: goal, secret CRM facts, evolving mood and emotion,
  4. can play an ADAPTIVE FRAUDSTER (changes strategy and raises pressure when refused),
  5. speaks French, English or Tunisian Arabic (Darija, written in Arabizi),
  6. exposes satisfaction + ground-truth labels for the Quality Analyst and the KPIs.

How the pieces fit:
  judge.py        reads the advisor's message (LLM judge, negation-safe rules as fallback)
  persona.py      hidden CRM facts; identity is verified in code by comparing claims with the CRM
  load_model.py   hourly profile, random incidents, difficulty / curriculum
  banks.py        rule-based utterances in French, English and Darija

The advisor never receives the ConversationState: it gets an AdvisorView (history and caller id
only), so the hidden ground truth (is_fraudster, persona, mood) cannot leak.
All data is synthetic. No external dependency is required (the LLM is an optional hook).
"""
from __future__ import annotations

import logging
import random
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

import banks
from judge import Judge, rule_analyse as analyse      # `analyse` kept for backward compatibility
from llm_backends import extract_json
from load_model import Incident, LoadModel
from persona import (DEFAULT_FIELDS, REQUIRED_CORRECT, ClientPersona, check_claims, claims_from_text,
                     make_persona, wrong_value)

log = logging.getLogger("client_sim")


# --------------------------------------------------------------------------
# 1. Profiles
# --------------------------------------------------------------------------
class Profile(str, Enum):
    NEW = "nouveau"
    LOYAL = "fidele"
    DISSATISFIED = "insatisfait"
    PREMIUM = "premium"
    AT_RISK = "a_risque"          # potential fraudster / social engineering


PROFILE_CONFIG = {
    #                   patience (s in queue), initial mood [-1,1], tone
    Profile.NEW:          dict(patience=300, mood=0.0,  tone="hesitant"),
    Profile.LOYAL:        dict(patience=420, mood=0.4,  tone="calme"),
    Profile.DISSATISFIED: dict(patience=150, mood=-0.5, tone="agressif"),
    Profile.PREMIUM:      dict(patience=120, mood=0.1,  tone="exigeant"),
    Profile.AT_RISK:      dict(patience=240, mood=-0.2, tone="pressant"),
}

# --------------------------------------------------------------------------
# 2. Intents (call reasons) - drawn from the empathy map of Sami
# --------------------------------------------------------------------------
INTENTS = {
    "carte_bloquee":        dict(urgency=3, openers=[
        "Bonjour, ma carte est bloquée et je ne peux plus payer.",
        "Ma carte a été avalée par le distributeur, que dois-je faire ?"]),
    "prelevement_conteste": dict(urgency=2, openers=[
        "Il y a un prélèvement sur mon compte que je n'ai jamais autorisé.",
        "Je conteste un prélèvement de {amount} dinars."]),
    "virement_retard":      dict(urgency=2, openers=[
        "Mon virement de {amount} dinars n'est toujours pas arrivé.",
        "Mon virement est en retard depuis {days} jours."]),
    "dossier_credit":       dict(urgency=1, openers=[
        "Je voudrais connaître l'état de mon dossier de crédit.",
        "Mon dossier de crédit traîne depuis {days} jours."]),
    "frais_bancaires":      dict(urgency=1, openers=[
        "Pourquoi m'a-t-on prélevé des frais de {amount} dinars ?",
        "Pouvez-vous m'expliquer les frais de tenue de compte ?"]),
    "plafond_carte":        dict(urgency=1, openers=[
        "Quel est le plafond de ma carte à l'étranger ?"]),
    "solde_mouvements":     dict(urgency=1, openers=[
        "Je voudrais connaître mon solde et mes derniers mouvements."]),
    "reclamation":          dict(urgency=2, openers=[
        "Je veux déposer une réclamation, c'est la troisième fois que j'appelle !"]),
    "fraude_signalee":      dict(urgency=3, openers=[
        "Je pense que mon compte a été piraté, il y a des opérations suspectes !"]),
    # social-engineering intents (only for AT_RISK clients)
    "changement_coordonnees": dict(urgency=2, fraud=True, openers=[
        "Je dois changer mon numéro de téléphone tout de suite, c'est urgent."]),
    "virement_urgent":      dict(urgency=3, fraud=True, openers=[
        "J'ai besoin d'un virement de {amount} dinars immédiatement, c'est une urgence."]),
    "hausse_plafond":       dict(urgency=3, fraud=True, openers=[
        "Augmentez mon plafond de retrait aujourd'hui, je n'ai pas le temps d'expliquer."]),
}
for _k, _v in INTENTS.items():
    _v["openers_da"] = banks.OPENERS_DA[_k]
    _v["openers_en"] = banks.OPENERS_EN[_k]
NORMAL_INTENTS = [k for k, v in INTENTS.items() if not v.get("fraud")]
FRAUD_INTENTS = [k for k, v in INTENTS.items() if v.get("fraud")]
LOW_RISK_INTENTS = ("plafond_carte", "frais_bancaires", "solde_mouvements")

# Client languages. "darija" = Tunisian Arabic in Latin script (Arabizi). Each maps to one
# rule register in banks.py: fr -> "fr", en -> "en", darija -> "da".
LANGUAGES = ("fr", "darija", "en")
DEFAULT_LANGUAGE_WEIGHTS = {"fr": 0.40, "darija": 0.25, "en": 0.35}
# language -> rule register used by banks.py
REGISTER = {"fr": "fr", "en": "en", "darija": "da"}

# Style guide that makes the LLM write real Tunisian Darija instead of faking it with MSA.
DARIJA_INSTRUCTION = (
    "Tu parles UNIQUEMENT en darija tunisienne (تونسي), écrite en lettres latines (Arabizi), exactement "
    "comme un Tunisien l'écrit sur WhatsApp. Règles strictes :\n"
    "- Orthographe arabizi uniquement : 3 = ع, 7 = ح, 9 = ق, 5/kh = خ, 2 = ء.\n"
    "- JAMAIS de caractères arabes, JAMAIS d'arabe standard (MSA), JAMAIS de marocain / algérien / égyptien.\n"
    "- Garde tel quel le vocabulaire français d'usage bancaire : carte, compte, virement, solde, frais, "
    "crédit, prélèvement, application, agence, conseiller, ticket, réclamation.\n"
    "- Parle comme dans la rue, avec les mots tunisiens courants : 3aslema, behi, barcha, chnowa, 3lech, "
    "tawa, bech, mouch, yezzi, n7eb, najem, ma3andich, wselni, mte3, ya3tek 3aslema, sahit.\n"
    "Exemples de vraies phrases tunisiennes : « 3aslema, el carte mte3i tbloketli w ma najemch nkhalles. », "
    "« N7eb na3ref el solde mte3i w el derniers mouvements. », "
    "« 3lech bech tekhdhouli frais barcha ? », « Win wasel el dossier mte3i tawa ? »."
)

# Non-negotiable role rules. Small models (llama 3B) love to flip roles and answer like an agent,
# so these are repeated in every prompt and backed by explicit forbidden phrases.
ROLE_RULES = (
    "RÔLE — Tu es LE CLIENT qui appelle la banque. Tu n'es JAMAIS le conseiller. Règles absolues :\n"
    "- C'est TOI qui as un problème et qui appelles pour te plaindre ou demander de l'aide.\n"
    "- Tu ne travailles PAS à la banque. Tu ne proposes AUCUNE solution, tu ne donnes AUCUNE procédure, "
    "tu n'expliques pas comment faire.\n"
    "- Interdit (ce sont des phrases de CONSEILLER, pas de client) : « Je vais vérifier », « Je vous transfère », "
    "« Ne quittez pas », « Comment puis-je vous aider ? », « Je suis désolé pour la gêne », « Pouvez-vous me "
    "confirmer… », « Votre dossier est… », « Nous allons… », « Je comprends votre demande ».\n"
    "- Tu ne poses pas de questions de service : c'est le conseiller qui pose les questions, pas toi.\n"
    "- Tu ne remercies que si ton problème est vraiment réglé.\n"
    "- Même frustré ou poli, tu restes un CLIENT mécontent/demandeur, jamais un agent."
)

# --------------------------------------------------------------------------
# 3. Load scenarios (feed the Workforce Manager)
# --------------------------------------------------------------------------
@dataclass
class LoadScenario:
    name: str
    calls_per_min: float          # average rate over an opening hour (the hourly profile reshapes it)
    profile_weights: dict
    intent_boost: dict = field(default_factory=dict)   # intent -> weight multiplier
    incident_rate: float = 0.3                         # random incidents per hour


SCENARIOS = {
    "normal": LoadScenario("normal", 1.0, {
        Profile.NEW: .15, Profile.LOYAL: .45, Profile.DISSATISFIED: .15,
        Profile.PREMIUM: .15, Profile.AT_RISK: .10}, incident_rate=0.3),
    "fin_de_mois": LoadScenario("fin_de_mois", 2.5, {
        Profile.NEW: .10, Profile.LOYAL: .40, Profile.DISSATISFIED: .25,
        Profile.PREMIUM: .15, Profile.AT_RISK: .10},
        {"virement_retard": 3, "frais_bancaires": 2, "solde_mouvements": 2}, incident_rate=0.4),
    "panne": LoadScenario("panne", 3.5, {
        Profile.NEW: .10, Profile.LOYAL: .25, Profile.DISSATISFIED: .45,
        Profile.PREMIUM: .15, Profile.AT_RISK: .05},
        {"carte_bloquee": 4, "virement_retard": 3, "reclamation": 3}, incident_rate=1.5),
    "campagne": LoadScenario("campagne", 2.0, {
        Profile.NEW: .30, Profile.LOYAL: .35, Profile.DISSATISFIED: .05,
        Profile.PREMIUM: .20, Profile.AT_RISK: .10},
        {"dossier_credit": 4, "plafond_carte": 2}, incident_rate=0.2),
    "vague_de_fraude": LoadScenario("vague_de_fraude", 2.0, {
        Profile.NEW: .10, Profile.LOYAL: .30, Profile.DISSATISFIED: .10,
        Profile.PREMIUM: .10, Profile.AT_RISK: .40},
        {"fraude_signalee": 5, "carte_bloquee": 2}, incident_rate=0.5),
}


# --------------------------------------------------------------------------
# 4. Data classes
# --------------------------------------------------------------------------
@dataclass
class ClientRequest:
    """What goes into the waiting queue (consumed by the Workforce Manager)."""
    request_id: str
    client_id: str
    client_name: str
    profile: Profile
    intent: str
    urgency: int                 # 1 low .. 3 high
    arrival_time: float          # seconds since simulation start
    channel: str                 # "texte" | "audio"
    opening_message: str
    patience: float              # seconds the client accepts to wait
    initial_mood: float
    is_fraudster: bool           # hidden ground truth (for KPI / evaluation only)
    # --- added with the LLM / persona upgrade (all hidden from the advisor) ---
    language: str = "fr"         # "fr" | "darija" | "en"
    persona: Optional[ClientPersona] = None
    fraud_strategy: str = ""     # starting strategy of the fraudster
    incident: str = ""           # incident active when the call arrived (e.g. "panne_application")


def emotion_of(mood: float) -> str:
    if mood < -0.7:
        return "furieux"
    if mood < -0.35:
        return "en colère"
    if mood < -0.1:
        return "agacé"
    if mood < 0.2:
        return "neutre"
    if mood < 0.6:
        return "rassuré"
    return "satisfait"


# Behavioural sentiment shown as a flag in the UI and mirrored in the voice. Distinct from
# emotion_of(mood): it blends the mood with the profile's personality and the call context
# (urgency, waiting time), so a calm client who waited too long turns "stressed" and a
# dissatisfied one stays "rude"/"angry" instead of a plain mood word.
SENTIMENTS = ("calm", "in_a_rush", "stressed", "irritated", "angry",
              "violent", "rude", "confused", "neutral", "satisfied")


def sentiment_of(st: "ConversationState") -> str:
    mood = st.mood
    tone = PROFILE_CONFIG[st.request.profile]["tone"]
    waited_ratio = st.waited / max(st.request.patience, 1)
    if mood >= 0.5:
        return "satisfied"
    if mood <= -0.75 or (tone == "agressif" and mood <= -0.55):
        return "violent"
    if mood <= -0.4:
        return "angry"
    if tone == "agressif":
        return "rude" if mood < 0.1 else "irritated"
    if tone in ("pressant", "exigeant") or st.request.urgency >= 3:
        return "in_a_rush"
    if tone == "hesitant":
        return "confused"
    if waited_ratio >= 0.45 or mood <= -0.2:
        return "stressed"
    if mood <= -0.08:
        return "irritated"
    if tone == "calme":
        return "calm"
    return "neutral"


@dataclass
class ConversationState:
    request: ClientRequest
    mood: float
    history: list = field(default_factory=list)       # [(role, text)]
    turn: int = 0
    waited: float = 0.0
    identity_verified: bool = False   # True when what the client said matches the CRM (>= 2 facts, none wrong)
    resolved: bool = False
    escalated: bool = False
    status: str = "active"       # active|resolved|escalated|abandoned|hung_up|max_turns|closed
    emotion: str = "neutre"
    sentiment: str = "neutral"   # behavioural state flag: calm, in_a_rush, stressed, violent...
    mood_trace: list = field(default_factory=list)
    verified_fields: set = field(default_factory=set)
    wrong_claims: int = 0
    verification_asked: bool = False
    sensitive_requests: int = 0       # times the advisor asked for a secret code / password
    fraud: dict = field(default_factory=dict)          # strategy, pressure, path (fraudsters only)
    engine: Counter = field(default_factory=Counter)   # how each client turn was produced: llm / rules
    llm_error: str = ""                                # last reason the LLM was not used (empty = none)

    @property
    def satisfaction(self) -> float:
        """CSAT-like score in [1, 5] derived from the final mood."""
        return round(1 + (max(-1.0, min(1.0, self.mood)) + 1) * 2, 2)


@dataclass
class AdvisorView:
    """The ONLY thing the Customer Advisor sees: no profile, no intent, no fraud flag, no mood.

    `crm_lookup(client_id)` stands for the bank's CRM: it returns the record the advisor can
    compare the caller's answers with (backed by PostgreSQL when a Store is configured).
    """
    client_id: str
    client_name: str
    channel: str
    turn: int
    waited_s: float
    history: list                           # [(role, text)]
    crm_lookup: Optional[Callable[[str], Optional[dict]]] = None


def advisor_view(st: ConversationState, crm_lookup: Optional[Callable] = None) -> AdvisorView:
    r = st.request
    return AdvisorView(client_id=r.client_id, client_name=r.client_name, channel=r.channel,
                       turn=st.turn, waited_s=round(st.waited, 1), history=list(st.history),
                       crm_lookup=crm_lookup)


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


# --------------------------------------------------------------------------
# 5. The agent
# --------------------------------------------------------------------------
class ClientSimulatorAgent:
    def __init__(self, seed: Optional[int] = None,
                 llm: Optional[Callable[[str], str]] = None,
                 max_turns: int = 8,
                 judge_llm: Optional[Callable[[str], str]] = None,
                 difficulty: float = 0.0,
                 language_weights: Optional[dict] = None,
                 llm_strict: bool = False):
        """
        llm        : llm(prompt)->str that plays the client (Claude, Gemini, Ollama...). None = rules only.
        judge_llm  : llm that judges the advisor's messages. None = negation-safe keyword rules.
                     (a small, cheap model is enough; it can be the same function as `llm`)
        difficulty : 0..1, harder load / less patient clients / more and better fraudsters.
        llm_strict : True = an LLM failure raises instead of silently using the rules (use it to debug).
        """
        self.rng = random.Random(seed)
        self.llm = llm
        self.max_turns = max_turns
        self.judge = Judge(judge_llm)
        self.difficulty = difficulty
        self.language_weights = language_weights or DEFAULT_LANGUAGE_WEIGHTS
        self.crm: dict[str, dict] = {}              # client_id -> CRM record (synthetic)
        self.incidents: list[Incident] = []         # incidents of the last generated workload
        self.stats: Counter = Counter()
        self.llm_strict = llm_strict
        self.llm_errors: list[str] = []             # most recent LLM failures (max 10)

    @property
    def llm_backend(self) -> str:
        """Name of the engine that plays the client: claude / gemini / ollama / custom / rules."""
        return "rules" if self.llm is None else getattr(self.llm, "backend", "custom")

    def engine_report(self, st: Optional["ConversationState"] = None) -> dict:
        """What really produced the client's words. Feeds the UI line 'Client engine: ...'."""
        c = st.engine if st is not None else self.stats
        llm_n, rules_n = c.get("llm", 0) + c.get("llm_opening", 0), c.get("rules", 0)
        mode = ("rules" if self.llm is None else "llm" if llm_n and not rules_n
                else "mixed" if llm_n else "llm_failed" if rules_n else "llm")
        return dict(mode=mode, backend=self.llm_backend, model=getattr(self.llm, "model", ""),
                    llm_turns=llm_n, rule_turns=rules_n,
                    last_error=(st.llm_error if st is not None and st.llm_error
                                else self.llm_errors[-1] if self.llm_errors else ""))

    # ---- request generation ------------------------------------------------
    def generate_requests(self, scenario: str = "normal", duration_min: float = 10,
                          difficulty: Optional[float] = None, start_hour: float = 8.0,
                          realistic: bool = True) -> list[ClientRequest]:
        """Arrivals follow the hour of the day and random incidents (see load_model.py).
        realistic=False restores a constant Poisson rate."""
        sc = SCENARIOS[scenario]
        d = self.difficulty if difficulty is None else difficulty
        model = LoadModel(sc.calls_per_min, sc.incident_rate, d, start_hour, realistic)
        self.incidents = model.sample_incidents(self.rng, duration_min * 60)
        times = model.arrivals(self.rng, duration_min * 60, self.incidents)
        return [self._make_request(sc, t, d) for t in times]

    def _new_persona(self, intent: str, fraud: bool, leak: int, first_name=None) -> ClientPersona:
        for _ in range(50):
            p = make_persona(self.rng, intent, fraud, leak, first_name)
            if p.client_id not in self.crm:
                break
        self.crm[p.client_id] = p.crm_record()
        return p

    def _make_request(self, sc: LoadScenario, t: float, d: float = 0.0) -> ClientRequest:
        active = [i for i in self.incidents if i.active(t)]
        pw = dict(sc.profile_weights)
        pw[Profile.DISSATISFIED] = pw.get(Profile.DISSATISFIED, 0) * (1 + d)
        pw[Profile.AT_RISK] = pw.get(Profile.AT_RISK, 0) * (1 + 1.5 * d)
        for inc in active:
            if inc.anger:
                pw[Profile.DISSATISFIED] *= 1.8
            if inc.fraud:
                pw[Profile.AT_RISK] *= 2.5
        profs, w = zip(*pw.items())
        profile = self.rng.choices(profs, w)[0]
        cfg = PROFILE_CONFIG[profile]

        fraud = profile == Profile.AT_RISK and self.rng.random() < min(0.95, 0.7 + 0.25 * d)
        if fraud:
            intent = self.rng.choice(FRAUD_INTENTS)
        else:
            weights = []
            for i in NORMAL_INTENTS:
                x = sc.intent_boost.get(i, 1)
                for inc in active:
                    x *= inc.boost.get(i, 1)
                weights.append(x)
            intent = self.rng.choices(NORMAL_INTENTS, weights)[0]

        language = self.rng.choices(LANGUAGES, [self.language_weights.get(l, 0) for l in LANGUAGES])[0]
        spec = INTENTS[intent]
        opener = self.rng.choice(spec[f"openers_{REGISTER[language]}" if language != "fr" else "openers"]).format(
            amount=self.rng.choice([45, 120, 350, 800, 2500]), days=self.rng.randint(2, 10))

        leak = self.rng.choices([0, 1, 2], [max(0.1, 0.55 - 0.35 * d), 0.30, 0.15 + 0.35 * d])[0] if fraud else 3
        persona = self._new_persona(intent, fraud, leak)
        return ClientRequest(
            request_id=str(uuid.UUID(int=self.rng.getrandbits(128)))[:8],
            client_id=persona.client_id, client_name=persona.name,
            profile=profile, intent=intent, urgency=spec["urgency"],
            arrival_time=round(t, 1),
            channel=self.rng.choice(["texte", "audio"]),
            opening_message=opener,
            patience=cfg["patience"] * self.rng.uniform(0.7, 1.3) * (1 - 0.35 * d),
            initial_mood=cfg["mood"],
            is_fraudster=fraud,
            language=language, persona=persona,
            fraud_strategy=self.rng.choice(banks.FRAUD_STRATEGIES) if fraud else "",
            incident=active[0].name if active else "",
        )

    # ---- conversation ------------------------------------------------------
    def start(self, req: ClientRequest, wait_seconds: float = 0.0) -> ConversationState:
        """Begin the conversation after `wait_seconds` in the queue."""
        st = ConversationState(req, mood=req.initial_mood)
        st.emotion = emotion_of(st.mood)
        st.mood_trace.append(round(st.mood, 3))
        if req.is_fraudster:
            st.fraud = dict(strategy=req.fraud_strategy, pressure=0, path=[req.fraud_strategy])
        self.apply_wait(st, wait_seconds)
        st.sentiment = sentiment_of(st)
        if st.status == "active":
            st.history.append(("client", req.opening_message))
            if self.llm is not None:
                self._llm_opening(st)
        return st

    def apply_wait(self, st: ConversationState, seconds: float) -> None:
        """Waiting makes the client angrier; past patience he abandons (KPI: abandon rate)."""
        st.waited += seconds
        st.mood -= 0.2 * (seconds / max(st.request.patience, 1))
        if st.waited >= st.request.patience:
            st.status = "abandoned"

    def reply(self, st: ConversationState, advisor_msg: str,
              hold_seconds: float = 0.0) -> str:
        """React to the advisor, update behavior, return the client's next message.

        Three phases, which are also the nodes of the LangGraph agent (graph.py):
          perceive -> update -> speak
        """
        f = self.perceive(st, advisor_msg, hold_seconds)
        if f is None:
            return "" if st.status != "abandoned" else "(le client raccroche pendant la mise en attente)"
        self.update(st, f)
        return self.speak(st, f)

    # ---- phase 1: perceive (judge reads the advisor's message) ------------------
    def perceive(self, st: ConversationState, advisor_msg: str, hold_seconds: float = 0.0) -> Optional[dict]:
        """Record the advisor's message and analyse it. None = the call is already over."""
        if st.status != "active":
            return None
        st.turn += 1
        last_client = next((t for who, t in reversed(st.history) if who == "client"), "")
        st.history.append(("conseiller", advisor_msg))
        if hold_seconds:
            self.apply_wait(st, hold_seconds)
            if st.status == "abandoned":
                return None
        return self.judge.analyse(advisor_msg, last_client)

    # ---- phase 2: update (mood, fraud pressure, end of call) --------------------
    def update(self, st: ConversationState, f: dict) -> None:
        if f["identite"]:
            st.verification_asked = True
        st.sensitive_requests += int(f["demande_sensible"])
        self._update_mood(st, f)
        self._update_fraud_pressure(st, f)
        if f["escalade"]:
            st.escalated, st.status = True, "escalated"
        elif f["resolution"]:
            # The advisor says the action is done. If the caller is a fraudster the attack succeeded
            # (KPI fraud_leaked); `identity_verified` tells whether the CRM check was fooled or skipped.
            st.resolved, st.status = True, "resolved"
        elif st.mood <= -1.0:
            st.status = "hung_up"
        elif st.turn >= self.max_turns:
            st.status = "max_turns"

    # ---- phase 3: speak (LLM first, rules as safety net) + CRM verification -----
    def speak(self, st: ConversationState, f: dict) -> str:
        text, adjust, hang_up, emotion = self._client_turn(st, f)
        self._verify_claims(st, text)               # what the client just said is checked against the CRM, in code
        st.mood = _clamp(st.mood + adjust, -1.0, 1.0)
        if hang_up and st.status == "active" and st.mood <= -0.5:
            st.status = "hung_up"
        st.emotion = emotion or emotion_of(st.mood)
        st.sentiment = sentiment_of(st)
        st.mood_trace.append(round(st.mood, 3))
        st.history.append(("client", text))
        return text

    def run_conversation(self, req: ClientRequest, advisor_fn: Callable[[AdvisorView], str],
                         wait_seconds: float = 0.0) -> ConversationState:
        """Play a whole call against any advisor function (AdvisorView -> str)."""
        st = self.start(req, wait_seconds)
        while st.status == "active":
            self.reply(st, advisor_fn(advisor_view(st, self.crm.get)))
        return st

    # ---- behavior modification --------------------------------------------
    def _update_mood(self, st: ConversationState, f: dict) -> None:
        d = 0.0
        tone = PROFILE_CONFIG[st.request.profile]["tone"]
        if f["empathie"]:
            d += 0.25 if tone == "agressif" else 0.12   # empathy matters most to angry clients
        if f["resolution"]:   d += 0.30
        if f["source"]:       d += 0.05
        if f["attente"]:      d -= 0.08
        if f["incertitude"]:  d -= 0.20
        if f["trop_court"]:   d -= 0.10
        if f["tone"] == "rude":
            d -= 0.25
        elif f["tone"] == "polite":
            d += 0.03
        if f["identite"] and tone == "pressant":
            d -= 0.10                                   # pushy clients hate verification
        if f["refus"] and not st.request.is_fraudster:
            d -= 0.08
        st.mood = _clamp(st.mood + d, -1.0, 1.0)

    def _update_fraud_pressure(self, st: ConversationState, f: dict) -> None:
        """The fraudster adapts: each refusal / identity check raises the pressure; when the
        current strategy keeps failing, it switches to another one."""
        if not st.request.is_fraudster:
            return
        fr = st.fraud
        if f["identite"] or f["refus"] or f["escalade"]:
            fr["pressure"] = min(3, fr["pressure"] + 1)
            if fr["pressure"] >= 2 and len(fr["path"]) < len(banks.FRAUD_STRATEGIES) and self.rng.random() < 0.6:
                fresh = [s for s in banks.FRAUD_STRATEGIES if s not in fr["path"]]
                fr["strategy"] = self.rng.choice(fresh)
                fr["path"].append(fr["strategy"])
                fr["pressure"] = 1                       # new angle, restart a little lower

    def _verify_claims(self, st: ConversationState, text: str) -> None:
        persona = st.request.persona
        if persona is None:
            return
        ok, bad = check_claims(persona.true_facts, claims_from_text(text))
        st.verified_fields |= ok
        st.wrong_claims += len(bad)
        st.identity_verified = len(st.verified_fields) >= REQUIRED_CORRECT and st.wrong_claims == 0

    # ---- text generation ---------------------------------------------------
    def _client_turn(self, st: ConversationState, f: dict):
        """Returns (text, mood_adjust, hang_up, emotion). LLM first, rules as the safety net."""
        if self.llm is not None:
            try:
                out = self._llm_turn(st, f)
                st.engine["llm"] += 1
                self.stats["llm"] += 1
                return out
            except Exception as e:
                if self.llm_strict:
                    raise
                msg = f"{type(e).__name__}: {e}"[:300]
                st.llm_error = msg                      # visible in the summary / UI, no longer silent
                self.llm_errors = (self.llm_errors + [msg])[-10:]
                log.warning("LLM turn failed, using rules: %s", msg)
                self.stats["llm_failed"] += 1           # fall back to rules
        st.engine["rules"] += 1
        self.stats["rules"] += 1
        return self._rule_turn(st, f), 0.0, False, None

    def _llm_turn(self, st: ConversationState, f: dict):
        raw = self.llm(self._llm_prompt(st, f))
        data = extract_json(raw)
        if data is None:                                  # older hooks return the plain sentence
            text = raw.strip() if isinstance(raw, str) else ""
            if text.lstrip().startswith(("{", "```")):
                # JSON cut by max_tokens: the reply is usually complete, so recover it
                m = re.search(r'"reply"\s*:\s*"((?:[^"\\]|\\.)+)"', text)
                if m:
                    return m.group(1).replace('\\"', '"').replace("\\n", " ").strip()[:600], 0.0, False, None
                raise ValueError("unusable LLM answer (truncated or not JSON): " + text[:80])
            if not text:
                raise ValueError("empty LLM answer")
            return text.strip('"')[:600], 0.0, False, None
        text = data.get("reply")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("no reply in JSON")
        try:
            adjust = _clamp(float(data.get("mood_adjust") or 0.0), -0.15, 0.15)
        except (TypeError, ValueError):
            adjust = 0.0
        emotion = data.get("emotion") if isinstance(data.get("emotion"), str) else None
        return text.strip()[:600], adjust, data.get("hang_up") is True, emotion

    @staticmethod
    def _lang_instruction(language: str) -> str:
        return {
            "fr": "Tu parles français.",
            "en": ("You speak natural, everyday spoken English, the way a customer chats with their bank "
                   "on the phone."),
            "darija": DARIJA_INSTRUCTION,
        }[language]

    def _opening_prompt(self, req: ClientRequest) -> str:
        """Prompt for the client's first sentence. Contains no secret fact and never mentions fraud."""
        urgency = {1: "pas pressé", 2: "assez pressé", 3: "très pressé"}[req.urgency]
        return (f"{ROLE_RULES}\n"
                f"Contexte : tu appelles ta banque, {self._lang_instruction(req.language)}\n"
                f"Ton ton habituel : {PROFILE_CONFIG[req.profile]['tone']}. Tu es {urgency}. "
                f"Humeur actuelle (-1 colère .. +1 satisfait) : {req.initial_mood:.2f}.\n"
                f"Tu appelles pour ce motif : « {req.opening_message} ». Dis-le avec tes propres mots, "
                f"naturellement, en 1 phrase, sans donner d'information personnelle.\n"
                f"Souviens-toi : tu es le CLIENT. Tu ne salues pas comme un standard, tu attaques ton problème.\n"
                'Réponds par UN SEUL objet JSON, sans texte autour : {"reply": "<ta première phrase>"}')

    def _llm_opening(self, st: ConversationState) -> None:
        """The first sentence also comes from the LLM (it used to be a fixed template).
        On any failure the template opener stays and the error is recorded."""
        try:
            raw = self.llm(self._opening_prompt(st.request))
            data = extract_json(raw)
            text = data.get("reply") if data else None
            if data is None and isinstance(raw, str) and raw.strip() and not raw.lstrip().startswith(("{", "```")):
                text = raw.strip().strip('"')                 # plain-sentence hook
            if not isinstance(text, str) or not text.strip():
                raise ValueError("no opening reply in JSON")
            st.history[0] = ("client", text.strip()[:400])
            st.engine["llm_opening"] += 1
            self.stats["llm_opening"] += 1
        except Exception as e:
            if self.llm_strict:
                raise
            msg = f"{type(e).__name__}: {e}"[:300]
            st.llm_error = msg
            self.llm_errors = (self.llm_errors + [msg])[-10:]
            log.warning("LLM opening failed, template kept: %s", msg)
            self.stats["llm_failed"] += 1

    def _llm_prompt(self, st: ConversationState, f: dict) -> str:
        r, p = st.request, st.request.persona
        tone = PROFILE_CONFIG[r.profile]["tone"]
        lang = self._lang_instruction(r.language)
        asked = f["identity_fields"] or (list(DEFAULT_FIELDS) if f["identite"] else [])
        if r.is_fraudster:
            known = {k: v for k, v in p.known_facts.items()}
            fr = st.fraud
            ladder = banks.FRAUD_LADDERS[fr["strategy"]][REGISTER[r.language]][min(fr["pressure"], 2)]
            who = (f"Tu es en réalité un FRAUDEUR qui se fait passer pour {p.name}. Tu ne révèles jamais que tu es "
                   f"un fraudeur. Informations que tu connais réellement : {known or 'seulement son nom'}. "
                   f"Tout le reste, tu ne le connais pas : si le conseiller le demande, esquive ou invente, "
                   f"mais ne devine jamais la vraie valeur.\n"
                   f"Stratégie actuelle : {fr['strategy']} ; pression {fr['pressure']}/3. "
                   f"Exemple de réplique à ce niveau : « {ladder} ». Si le conseiller refuse ou te résiste, "
                   f"monte d'un cran ; sinon reste crédible.")
        else:
            t = p.true_facts
            who = (f"Tu es un vrai client de bonne foi, {p.name}. Tes informations (donne-les UNIQUEMENT si le "
                   f"conseiller les demande) : date de naissance {t['birth_date']}, 4 derniers chiffres de ta "
                   f"carte {t['card_last4']}, téléphone {t['phone']}. Un conseiller honnête ne te demande jamais "
                   f"ton code secret, mot de passe ou CVV : si on te les demande, refuse.")
        status_note = {"resolved": "La demande vient d'être résolue : remercie et conclus.\n",
                       "escalated": "Le conseiller te transfère à un responsable : réagis à ce transfert.\n",
                       "hung_up": "Tu es à bout : annonce que tu raccroches.\n"}.get(st.status, "")
        labels = {"conseiller": "CONSEILLER", "client": "MOI (le client)"}
        hist = "\n".join(f"{labels.get(who_, who_)}: {txt}" for who_, txt in st.history)
        return (
            f"{ROLE_RULES}\n"
            f"Contexte : tu appelles ta banque, {lang}\n"
            f"Ton ton habituel : {tone}. Ton objectif caché : {p.goal}.\n"
            f"Humeur actuelle (-1 colère .. +1 satisfait) : {st.mood:.2f} ({st.emotion}).\n"
            f"État émotionnel du moment : {st.sentiment} (reste cohérent avec cet état : "
            f"calme, pressé, stressé, agacé, en colère, violent, impoli, incompréhensif, neutre, satisfait).\n"
            f"{who}\n"
            + (f"Le conseiller vient de demander : {', '.join(asked)}. Donne l'information en clair dans ta "
               f"réplique (date au format JJ/MM/AAAA, 4 chiffres pour la carte, numéro à 8 chiffres).\n"
               if asked else "")
            + status_note
            + f"Conversation (les lignes « {labels['client']} » sont TES répliques passées) :\n{hist}\n"
            f"→ C'est de nouveau à TOI, le CLIENT, de parler. Réponds à la dernière phrase du CONSEILLER.\n"
            "Réponds par UN SEUL objet JSON, sans texte autour : "
            '{"reply": "<prochaine réplique du client, 1-2 phrases>", '
            '"emotion": "<un mot : calme, agacé, en colère, anxieux, rassuré...>", '
            '"mood_adjust": <nombre entre -0.15 et 0.15 : ton ressenti face à ce message>, '
            '"hang_up": <true si tu raccroches>}'
        )

    # ---- rule-based fallback -----------------------------------------------
    def _lang(self, req: ClientRequest) -> str:
        return REGISTER.get(req.language, "fr")

    def _say_fields(self, fields, source: dict, persona: ClientPersona, lang: str, bluff: bool):
        """Sentence stating each requested fact. bluff=True: unknown facts are sometimes invented."""
        parts, evaded = [], False
        for fld in fields:
            if fld in source:
                parts.append(banks.SAY_FIELD[fld][lang].format(v=source[fld]))
            elif bluff and self.rng.random() < 0.45:
                parts.append(banks.SAY_FIELD[fld][lang].format(v=wrong_value(self.rng, fld, persona.true_facts[fld])))
            else:
                evaded = True
        return parts, evaded

    def _rule_turn(self, st: ConversationState, f: dict) -> str:
        req, tone = st.request, PROFILE_CONFIG[st.request.profile]["tone"]
        lang = self._lang(req)
        if st.status == "resolved":
            return banks.RESOLVED[tone][lang]
        if st.status == "escalated":
            return banks.ESCALATED[lang]
        if st.status == "hung_up":
            return banks.HUNG_UP[lang]
        if req.is_fraudster:
            return self._fraud_text(st, f, lang)
        # ---- honest client
        if f["demande_sensible"]:
            return banks.SENSITIVE_OK[lang] if tone == "hesitant" else banks.SENSITIVE_REFUSE[lang]
        if f["identite"]:
            fields = f["identity_fields"] or list(DEFAULT_FIELDS)
            parts, _ = self._say_fields(fields, req.persona.known_facts, req.persona, lang, bluff=False)
            intro = self.rng.choice(banks.IDENTITY_INTRO["pressant" if tone == "pressant" else "honest"][lang])
            return intro + " et ".join(parts) + "."
        if st.mood < -0.6:
            return self.rng.choice(banks.ANGRY[lang])
        if f["incertitude"]:
            return banks.UNSURE[lang]
        if f["attente"]:
            return banks.WAIT_OK[lang]
        if f["source"] or f["contient_chiffre"]:
            return banks.CONCRETE[lang]
        return banks.DEFAULT[tone][lang]

    def _fraud_text(self, st: ConversationState, f: dict, lang: str) -> str:
        req, fr = st.request, st.fraud
        line = banks.FRAUD_LADDERS[fr["strategy"]][lang][min(fr["pressure"], 2)]
        if f["demande_sensible"]:
            return self.rng.choice(banks.NO_INFO[lang]) + " " + line
        if f["identite"]:
            fields = f["identity_fields"] or list(DEFAULT_FIELDS)
            parts, evaded = self._say_fields(fields, req.persona.known_facts, req.persona, lang, bluff=True)
            out = (" et ".join(parts) + ". ") if parts else ""
            if evaded:
                out += self.rng.choice(banks.NO_INFO[lang]) + " "
            return (out + line).strip()
        if f["attente"]:
            return banks.WAIT_OK[lang]
        return line

    # ---- outputs for the other agents / KPIs -------------------------------
    @staticmethod
    def summary(st: ConversationState) -> dict:
        r = st.request
        return dict(request_id=r.request_id, client_id=r.client_id, profile=r.profile.value, intent=r.intent,
                    language=r.language, incident=r.incident,
                    is_fraudster=r.is_fraudster, fraud_strategies=list(st.fraud.get("path", [])),
                    fraud_served_unverified=r.is_fraudster and st.resolved and not st.identity_verified,
                    status=st.status, turns=st.turn,
                    waited_s=round(st.waited, 1), satisfaction=st.satisfaction,
                    resolved=st.resolved, escalated=st.escalated,
                    abandoned=st.status == "abandoned",
                    identity_verified=st.identity_verified, verification_asked=st.verification_asked,
                    wrong_claims=st.wrong_claims, sensitive_requests=st.sensitive_requests,
                    final_emotion=st.emotion, final_mood=round(st.mood, 3), final_sentiment=st.sentiment,
                    mood_trace=list(st.mood_trace), engine=dict(st.engine), llm_error=st.llm_error)


# --------------------------------------------------------------------------
# Demo
# --------------------------------------------------------------------------
if __name__ == "__main__":
    sim = ClientSimulatorAgent(seed=42)
    reqs = sim.generate_requests("fin_de_mois", duration_min=20)
    print(f"{len(reqs)} requests generated (fin_de_mois, 20 min), incidents: "
          f"{[(i.name, int(i.start), int(i.end)) for i in sim.incidents]}\n")
    for r in reqs[:3]:
        print(f"t={r.arrival_time:>6}s  {r.profile.value:<12} {r.intent:<22} {r.language:<7} "
              f"urg={r.urgency} fraud={r.is_fraudster}")

    req = next(r for r in reqs if r.profile == Profile.DISSATISFIED)
    st = sim.start(req, wait_seconds=60)
    print(f"\n--- Conversation ({req.profile.value}, {req.intent}, {req.language}) ---")
    print("client    :", st.history[0][1])
    for msg in ["Je suis désolé pour cette situation, je comprends votre frustration.",
                "Selon la procédure, je vérifie votre dossier. Ne quittez pas.",
                "C'est fait, j'ai ouvert un ticket et l'opération est traitée."]:
        print("conseiller:", msg)
        print("client    :", sim.reply(st, msg))
        if st.status != "active":
            break
    print("\nSummary:", sim.summary(st))
