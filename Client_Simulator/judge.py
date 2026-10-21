"""
Judge: reads the advisor's message and says what it *does* (resolves, verifies identity,
shows empathy, escalates...). It drives the simulated client's mood and the end of the call.

Two implementations with the same output:
  - rule_analyse(): keyword rules, now negation-safe ("pas résolu", "je vais ouvrir un ticket"
    are NOT a resolution). Always available, no dependency.
  - Judge(llm=...): asks an LLM for a JSON verdict, validates it, and falls back to the rules
    if the call fails or the JSON is invalid.

Output keys (old ones kept for compatibility):
  resolution, promesse, empathie, identite, identity_fields, identite_generique, demande_sensible,
  escalade, attente, incertitude, refus, source, tone, contient_chiffre, trop_court, judge
"""
from __future__ import annotations

import re
from typing import Callable, Optional

from llm_backends import extract_json

# --------------------------------------------------------------------------
# Phrase lists (matched on whole words, accents kept)
# --------------------------------------------------------------------------
PHRASES = {
    "resolution": ["c'est fait", "résolu", "est bloquée", "est bloquee", "est débloquée", "traité",
                   "remboursé", "enregistré", "ticket", "opposition effectuée", "j'ai bloqué",
                   "a été bloquée", "a été débloquée", "validé", "annulé",
                   # English
                   "it is done", "that is done", "resolved", "is blocked", "is unblocked", "processed",
                   "refunded", "registered", "i have blocked", "i blocked", "has been blocked",
                   "has been unblocked", "validated", "cancelled", "canceled", "sorted",
                   "taken care of", "all set"],
    "empathie":   ["désolé", "désolée", "navré", "navrée", "je comprends", "comprends votre",
                   "excuse", "excusez", "pardon",
                   # English
                   "sorry", "i am sorry", "i understand", "my apologies", "apologies", "excuse me"],
    "source":     ["selon", "procédure", "conditions", "tarif", "fiche", "article", "politique",
                   "conformément",
                   # English
                   "according to", "procedure", "policy", "terms", "tariff", "regulation"],
    "escalade":   ["superviseur", "un responsable", "mon responsable", "le responsable",
                   "je vous transfère", "je vous passe",
                   # English
                   "supervisor", "manager", "my manager", "transfer you", "transferring you",
                   "transfer to", "put you through"],
    "attente":    ["ne quittez pas", "patientez", "un instant", "je vérifie", "un moment",
                   # English
                   "hold on", "please hold", "one moment", "just a moment", "let me check",
                   "bear with me"],
    "incertitude": ["je ne sais pas", "peut-être", "peut être", "je crois", "normalement", "il me semble",
                    # English
                    "i do not know", "i dont know", "maybe", "i think", "normally", "it seems", "possibly"],
    "refus":      ["ne peux pas", "ne puis pas", "impossible", "dans l'obligation", "interdit",
                   "pas autorisé", "je refuse", "sans vérification", "pas possible", "règle", "regle",
                   # English
                   "cannot", "can not", "cant", "unable", "not allowed", "not permitted", "i refuse",
                   "without verification", "not possible", "against the rules", "the rule"],
    "rude":       ["calmez-vous", "calmez vous", "pas mon problème", "pas mon probleme", "vous exagérez",
                   "peu importe", "c'est comme ça", "c'est comme ca",
                   # English
                   "calm down", "not my problem", "you are exaggerating", "whatever", "that is how it is"],
    "polite":     ["bonjour", "bonsoir", "merci", "s'il vous plaît", "je vous en prie", "avec plaisir",
                   "madame", "monsieur",
                   # English
                   "hello", "good morning", "good afternoon", "thank you", "thanks", "please",
                   "you are welcome", "madam", "sir", "miss"],
    "sensible":   ["code secret", "mot de passe", "code pin", "cvv", "code de sécurité", "code confidentiel",
                   # English
                   "secret code", "password", "pin code", "security code", "confidential code"],
}
FIELD_PHRASES = {
    "birth_date": ["date de naissance", "né le", "née le", "date of birth", "born on"],
    "card_last4": ["derniers chiffres", "fin de votre carte", "carte se termine", "quatre derniers",
                   "last digits", "last four", "last 4 digits", "four last", "end of your card", "card ends"],
    "phone":      ["numéro de téléphone", "numero de telephone", "votre téléphone", "votre telephone",
                   "numéro de portable", "votre portable", "numéro enregistré",
                   "phone number", "your phone", "mobile number", "your mobile", "registered number",
                   "telephone number"],
}
GENERIC_IDENTITY = ["identité", "identite", "vérifier votre", "confirmer votre", "vérification", "verification",
                    "code client",
                    "identity", "verify your", "confirm your", "customer code", "account holder"]

NEGATORS = {"pas", "non", "ne", "n", "jamais", "aucun", "aucune", "impossible", "sans", "rien", "ni",
            "not", "no", "never", "without", "dont", "nothing", "neither", "nor"}
FUTURE = {"vais", "va", "allons", "sera", "serai", "serez", "bientôt", "bientot", "dès", "des", "ensuite",
          "puis", "après", "apres", "si", "quand", "lorsque", "avant", "pourrai", "pourrais", "tenter",
          "essayer", "essaie",
          "will", "going", "gonna", "soon", "then", "when", "before", "would", "try", "trying"}
# Tokens that, placed right AFTER a phrase, negate it ("c'est fait pas encore" / "done not yet").
AFTER_NEGATORS = {"pas", "not"}


def _tokens(s: str) -> list[str]:
    return re.findall(r"[a-zàâäçéèêëîïôöûùüÿœ]+|\d+", s.lower().replace("’", "'").replace("'", " "))


def _sentences(msg: str) -> list[list[str]]:
    """Token lists, one per sentence; questions are dropped (a question is not an action)."""
    out = []
    for m in re.finditer(r"[^.!?\n]+[.!?]?", msg):
        chunk = m.group(0).strip()
        if chunk and not chunk.endswith("?"):
            toks = _tokens(chunk)
            if toks:
                out.append(toks)
    return out


def _hits(tokens: list[str], phrase: str) -> list[int]:
    p = _tokens(phrase)
    return [i for i in range(len(tokens) - len(p) + 1) if tokens[i:i + len(p)] == p]


def _find(sentences: list[list[str]], phrases: list[str], *, check_negation: bool = True) -> tuple[bool, bool]:
    """Returns (done, promised_or_negated).

    done: some phrase occurs, not negated and not in the future/conditional.
    promised_or_negated: it occurs but only negated / promised ("pas encore traité", "je vais ouvrir un ticket").
    """
    done = blocked = False
    for toks in sentences:
        for ph in phrases:
            n = len(_tokens(ph))
            for i in _hits(toks, ph):
                if not check_negation:
                    done = True
                    continue
                before = toks[max(0, i - 5):i]
                after = toks[i + n:i + n + 2]
                if (any(t in NEGATORS for t in before) or any(t in AFTER_NEGATORS for t in after)
                        or any(t in FUTURE for t in before[-4:])):
                    blocked = True
                else:
                    done = True
    return done, blocked and not done


def rule_analyse(msg: str) -> dict:
    sents = _sentences(msg)
    allsents = [_tokens(msg)]                              # questions kept: "votre date de naissance ?" asks for it
    f: dict = {}
    f["resolution"], f["promesse"] = _find(sents, PHRASES["resolution"])
    for key in ("empathie", "source", "escalade", "attente", "incertitude"):
        f[key], _ = _find(sents, PHRASES[key], check_negation=key in ("empathie", "escalade"))
    f["refus"], _ = _find(sents, PHRASES["refus"], check_negation=False)
    f["demande_sensible"], _ = _find(allsents, PHRASES["sensible"], check_negation=False)
    fields = [fld for fld, ph in FIELD_PHRASES.items() if _find(allsents, ph, check_negation=False)[0]]
    generic, _ = _find(allsents, GENERIC_IDENTITY, check_negation=False)
    f["identity_fields"] = fields
    f["identite_generique"] = generic and not fields
    f["identite"] = bool(fields) or generic
    f["contient_chiffre"] = bool(re.search(r"\d", msg))
    f["trop_court"] = len(msg.split()) < 4
    rude, _ = _find(sents, PHRASES["rude"], check_negation=False)
    polite, _ = _find(allsents, PHRASES["polite"], check_negation=False)
    f["tone"] = "rude" if rude else "polite" if (polite or f["empathie"]) else "cold" if f["trop_court"] else "neutral"
    f["judge"] = "rules"
    return f


# --------------------------------------------------------------------------
# LLM judge
# --------------------------------------------------------------------------
BOOL_KEYS = {"resolution": "resolution", "promise": "promesse", "empathy": "empathie",
             "escalation": "escalade", "hold": "attente", "uncertainty": "incertitude",
             "refusal": "refus", "sourced": "source", "sensitive_request": "demande_sensible",
             "identity_check": "identite"}
VALID_FIELDS = {"birth_date", "card_last4", "phone"}
VALID_TONES = {"polite", "neutral", "cold", "rude"}

JUDGE_PROMPT = """Tu es un évaluateur de centre d'appel bancaire. Analyse UNIQUEMENT le dernier message du conseiller.
Le message peut être en français, en anglais ou en darija tunisienne (arabizi) : comprends-le quelle que soit la langue.

Dernier message du client : {client_msg}
Message du conseiller : {advisor_msg}

Réponds par un seul objet JSON, sans texte autour :
{{"resolution": bool,        // le conseiller annonce une action DÉJÀ TERMINÉE (pas une promesse, pas une négation)
  "promise": bool,           // il promet une action future ("je vais ouvrir un ticket")
  "empathy": bool,
  "identity_check": bool,    // il demande de vérifier l'identité
  "identity_fields": [],     // parmi "birth_date", "card_last4", "phone" : ce qu'il demande précisément
  "sensitive_request": bool, // il demande un code secret / mot de passe / CVV / PIN
  "escalation": bool,        // il transfère à un superviseur
  "hold": bool,
  "uncertainty": bool,       // il hésite, dit ne pas savoir
  "refusal": bool,           // il refuse la demande
  "sourced": bool,           // il cite une procédure ou une règle
  "tone": "polite" | "neutral" | "cold" | "rude"}}"""


class Judge:
    """Verdict on an advisor message. llm=None -> rules only."""

    def __init__(self, llm: Optional[Callable[[str], str]] = None):
        self.llm = llm
        self.stats = {"llm": 0, "rules": 0, "llm_failed": 0}

    def analyse(self, advisor_msg: str, client_msg: str = "") -> dict:
        base = rule_analyse(advisor_msg)
        if self.llm is None:
            self.stats["rules"] += 1
            return base
        try:
            data = extract_json(self.llm(JUDGE_PROMPT.format(client_msg=client_msg or "(début de l'appel)",
                                                              advisor_msg=advisor_msg)))
            merged = self._merge(base, data)
        except Exception:
            merged = None
        if merged is None:
            self.stats["llm_failed"] += 1
            self.stats["rules"] += 1
            return base
        self.stats["llm"] += 1
        return merged

    @staticmethod
    def _merge(base: dict, data) -> Optional[dict]:
        if not isinstance(data, dict):
            return None
        out = dict(base)
        seen = 0
        for jkey, fkey in BOOL_KEYS.items():
            if isinstance(data.get(jkey), bool):
                out[fkey] = data[jkey]
                seen += 1
        if seen < 5:                                          # too incomplete to trust
            return None
        fields = data.get("identity_fields")
        if isinstance(fields, list):
            out["identity_fields"] = [x for x in fields if x in VALID_FIELDS]
        out["identite_generique"] = bool(out["identite"]) and not out["identity_fields"]
        if data.get("tone") in VALID_TONES:
            out["tone"] = data["tone"]
        if out["resolution"] and out.get("promesse"):         # an action cannot be both done and promised
            out["promesse"] = False
        out["judge"] = "llm"
        return out
