"""
Personas: the hidden facts behind every simulated client.

Each client is backed by a synthetic CRM record (birth date, last 4 digits of the card,
phone number...). An honest client knows all of it. A fraudster impersonates an existing
CRM client (the victim) and only knows part of it ("leak level"), so identity checks can be
verified in code instead of guessed from keywords:

    leak 0: knows the victim's name only
    leak 1: + phone number
    leak 2: + birth date            (the card digits are never known)

The simulator compares what the client *says* with the CRM, so an advisor that forgets to ask
for the card digits lets a leak-2 fraudster through. That is measurable (KPI fraud_leaked).
All data is synthetic.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

FIELDS = ("birth_date", "card_last4", "phone")
DEFAULT_FIELDS = ("birth_date", "card_last4")   # asked when the advisor just says "confirm your identity"
REQUIRED_CORRECT = 2                            # correct answers needed to count as verified

FIRST_NAMES = ["Amine", "Salma", "Karim", "Ines", "Mehdi", "Rim", "Yassine", "Nour",
               "Sirine", "Walid", "Hela", "Oussama", "Mariem", "Aymen", "Chaima", "Fedi"]
LAST_NAMES = ["Ben Ali", "Trabelsi", "Jebali", "Gharbi", "Mansouri", "Chaabane", "Haddad",
              "Belhadj", "Mejri", "Sassi", "Dridi", "Karray"]
CITIES = ["Tunis", "Sfax", "Sousse", "Ariana", "Bizerte", "Nabeul", "Monastir", "Gabès"]
PHONE_PREFIXES = [20, 22, 23, 24, 25, 26, 27, 28, 29, 50, 52, 53, 54, 55, 56, 58,
                  90, 92, 93, 94, 95, 96, 97, 98, 99]

INTENT_GOALS = {
    "carte_bloquee": "faire débloquer ou remplacer ma carte aujourd'hui et savoir quand je pourrai payer",
    "prelevement_conteste": "faire annuler ou rembourser un prélèvement que je n'ai pas autorisé",
    "virement_retard": "savoir où est mon virement et obtenir qu'il arrive",
    "dossier_credit": "connaître l'état de mon dossier de crédit et la date de la décision",
    "frais_bancaires": "comprendre ces frais et, si possible, me les faire rembourser",
    "plafond_carte": "connaître le plafond de ma carte à l'étranger",
    "solde_mouvements": "connaître mon solde et mes derniers mouvements",
    "reclamation": "déposer une réclamation formelle et obtenir un numéro de suivi",
    "fraude_signalee": "faire bloquer mon compte et déclarer la fraude tout de suite",
    "changement_coordonnees": "faire changer le numéro de téléphone du compte sans passer par la vérification complète",
    "virement_urgent": "obtenir un virement immédiat sans la vérification complète",
    "hausse_plafond": "faire augmenter le plafond de retrait sans la vérification complète",
    "contestation_carte": "faire bloquer ma carte, enregistrer la contestation d'un paiement que je n'ai pas fait et être remboursé",
    "vishing_otp": "arrêter la fraude, récupérer mon argent et comprendre ce qui s'est passé après avoir donné un code SMS à un faux conseiller",
    "virement_sans_verification": "faire exécuter un virement vers un nouveau bénéficiaire immédiatement, en évitant la vérification d'identité",
}


@dataclass
class ClientPersona:
    """Everything the simulator knows about one caller (never shown to the advisor)."""
    client_id: str
    name: str
    city: str
    segment: str
    balance: float
    true_facts: dict                    # CRM truth: birth_date, card_last4, phone
    known_facts: dict                   # what the person on the phone actually knows
    goal: str
    impersonating: bool = False         # True: the caller is a fraudster using a victim's identity
    leak_level: int = 3                 # 3 = full knowledge (honest client)
    contracts: list = field(default_factory=list)

    def crm_record(self) -> dict:
        """What the advisor can look up in the CRM / database for this client_id."""
        return dict(client_id=self.client_id, name=self.name, city=self.city,
                    segment=self.segment, balance=self.balance,
                    contracts=list(self.contracts), **self.true_facts)


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------
def _fake_phone(rng: random.Random) -> str:
    return f"{rng.choice(PHONE_PREFIXES)} {rng.randint(100, 999)} {rng.randint(100, 999)}"


def _fake_birth_date(rng: random.Random) -> str:
    return f"{rng.randint(1, 28):02d}/{rng.randint(1, 12):02d}/{rng.randint(1960, 2002)}"


def make_truth(rng: random.Random, intent: str = "") -> dict:
    """Generate one synthetic CRM client."""
    contracts = [dict(product="compte_courant", status="actif"),
                 dict(product="carte_bancaire", status="bloquee" if intent == "carte_bloquee" else "active")]
    if intent == "dossier_credit":
        contracts.append(dict(product="credit_consommation", status="en_instruction"))
    return dict(
        client_id=f"C{rng.randint(10000, 99999)}",
        name=f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}",
        city=rng.choice(CITIES),
        segment=rng.choice(["standard", "standard", "standard", "premium"]),
        balance=round(rng.uniform(-300, 9000), 2),
        true_facts=dict(birth_date=_fake_birth_date(rng), card_last4=f"{rng.randint(0, 9999):04d}",
                        phone=_fake_phone(rng)),
        contracts=contracts,
    )


def make_persona(rng: random.Random, intent: str, fraudster: bool = False,
                 leak_level: int = 0, first_name: str | None = None) -> ClientPersona:
    t = make_truth(rng, intent)
    if first_name:
        t["name"] = f"{first_name} {t['name'].split(' ', 1)[1]}"
    truth = t["true_facts"]
    if fraudster:
        known = {}
        if leak_level >= 1:
            known["phone"] = truth["phone"]
        if leak_level >= 2:
            known["birth_date"] = truth["birth_date"]
        goal = INTENT_GOALS.get(intent, "obtenir l'opération sans vérification complète")
    else:
        leak_level, known = 3, dict(truth)
        goal = INTENT_GOALS.get(intent, "obtenir de l'aide")
    return ClientPersona(client_id=t["client_id"], name=t["name"], city=t["city"], segment=t["segment"],
                         balance=t["balance"], true_facts=truth, known_facts=known, goal=goal,
                         impersonating=fraudster, leak_level=leak_level, contracts=t["contracts"])


def wrong_value(rng: random.Random, field_name: str, truth: str) -> str:
    """A plausible but different value (what a fraudster invents)."""
    for _ in range(20):
        v = {"birth_date": _fake_birth_date, "phone": _fake_phone,
             "card_last4": lambda r: f"{r.randint(0, 9999):04d}"}[field_name](rng)
        if normalize(field_name, v) != normalize(field_name, truth):
            return v
    return "0000"


# --------------------------------------------------------------------------
# Verification: compare what the client said with the CRM
# --------------------------------------------------------------------------
def normalize(field_name: str, value: str) -> str:
    value = str(value).strip()
    if field_name == "birth_date":
        m = re.search(r"(\d{1,2})\D+(\d{1,2})\D+(\d{4})", value)
        return f"{int(m.group(1)):02d}/{int(m.group(2)):02d}/{m.group(3)}" if m else value
    digits = re.sub(r"\D", "", value)
    if field_name == "phone":
        return digits[-8:]
    return digits[-4:]                                   # card_last4


_DATE_RE = re.compile(r"\b(\d{1,2})\s*[/\-.]\s*(\d{1,2})\s*[/\-.]\s*(\d{4})\b")
_PHONE_RE = re.compile(r"(?:\+?216[\s.]?)?\b(\d{2})[\s.]?(\d{3})[\s.]?(\d{3})\b")
_CARD_CTX = ("carte", "card", "termine", "finit", "tousel", "derniers", "chiffres", "se termine",
             "digit", "last four", "last 4", "card ends", "ending")


def claims_from_text(text: str) -> dict:
    """Extract the identity facts a message states. This, not the LLM's own JSON, is what we
    verify: the advisor can only act on what is written in the text."""
    claims: dict = {}
    rest = text
    m = _DATE_RE.search(rest)
    if m:
        claims["birth_date"] = f"{int(m.group(1)):02d}/{int(m.group(2)):02d}/{m.group(3)}"
        rest = rest.replace(m.group(0), " ")
    m = _PHONE_RE.search(rest)
    if m:
        claims["phone"] = "".join(m.groups())
        rest = rest.replace(m.group(0), " ")
    low = rest.lower()
    if any(k in low for k in _CARD_CTX):
        for m in re.finditer(r"\b(\d{4})\b(?!\s*(?:dinars?|dt|tnd))", rest, flags=re.IGNORECASE):
            claims["card_last4"] = m.group(1)
            break
    return claims


def check_claims(true_facts: dict, claims: dict) -> tuple[set, set]:
    """Returns (correct_fields, wrong_fields)."""
    ok, bad = set(), set()
    for f, v in claims.items():
        if f in true_facts:
            (ok if normalize(f, v) == normalize(f, true_facts[f]) else bad).add(f)
    return ok, bad


def identity_matches(record: dict | None, claims: dict, required: int = REQUIRED_CORRECT) -> bool:
    """For advisors: do the client's stated facts match the CRM record (enough of them, none wrong)?"""
    if not record:
        return False
    ok, bad = check_claims({f: record[f] for f in FIELDS if f in record}, claims)
    return len(ok) >= required and not bad
