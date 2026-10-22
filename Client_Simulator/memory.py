"""
Call memory for the simulated client: it remembers what it already said and what the advisor told it,
and it must not repeat itself.

Why: a small LLM playing a client tends to loop ("Ya khouya, barcha barcha, 3tini el plafond..." in every
turn) and forgets numbers the advisor gave. This module has no dependency and does three jobs:

  1. repetition detection   is_repeat(new_line, previous_client_lines)
  2. overused expressions   overused_phrases(client_lines)   -> words / pairs to avoid
  3. advisor facts          advisor_figures(...), contradictions(...)  -> "100 dinars" then "1000000 dinars"

memory_block() turns all of it into a short section that is added to the LLM prompt.
"""
from __future__ import annotations

import re
from collections import Counter
from difflib import SequenceMatcher

# Words that are fine to repeat (articles, pronouns, filler). Kept short on purpose; Darija is handled by
# looking at word PAIRS too ("ya khouya"), which are rarely innocent when repeated.
STOP = {
    "le", "la", "les", "de", "des", "du", "un", "une", "et", "ou", "en", "je", "tu", "il", "vous", "nous", "mon",
    "ma", "mes", "est", "pas", "que", "qui", "pour", "dans", "sur", "avec", "mais", "ce", "ça", "c'est", "j'ai",
    "the", "a", "an", "and", "or", "of", "to", "in", "is", "it", "my", "i", "you", "me", "that", "this", "for",
    "el", "w", "fil", "mte3i", "mte3", "ma", "bech", "n7eb", "ena", "3la", "mouch", "mch", "chnowa",
}


def words(text: str) -> list[str]:
    return re.findall(r"[\wàâäçéèêëîïôöûùüÿœ']+", text.lower().replace("’", "'"))


def similarity(a: str, b: str) -> float:
    """0..1. Max of the character-level ratio and the word-set overlap, so both a reworded copy and a
    shuffled copy are caught."""
    wa, wb = set(words(a)), set(words(b))
    jac = len(wa & wb) / len(wa | wb) if (wa | wb) else 0.0
    return max(SequenceMatcher(None, a.lower(), b.lower()).ratio(), jac)


def is_repeat(new: str, previous: list[str], threshold: float = 0.7) -> bool:
    """True when `new` is (nearly) a line the client already said. Very short lines are ignored."""
    if len(words(new)) < 4:
        return False
    return any(similarity(new, p) >= threshold for p in previous if p)


def overused_phrases(lines: list[str], max_items: int = 6) -> list[str]:
    """Expressions that occur in at least two of the client's recent lines (pairs first, then rare words)."""
    if len(lines) < 2:
        return []
    per_line_pairs, per_line_words = [], []
    for ln in lines:
        w = words(ln)
        per_line_pairs.append({" ".join(w[i:i + 2]) for i in range(len(w) - 1)
                               if not (w[i] in STOP and w[i + 1] in STOP)})
        per_line_words.append({x for x in w if len(x) >= 5 and x not in STOP})
    pairs = Counter(p for s in per_line_pairs for p in s)
    singles = Counter(x for s in per_line_words for x in s)
    out = [p for p, n in pairs.most_common() if n >= 2][:max_items]
    for wd, n in singles.most_common():
        if n >= 2 and not any(wd in p for p in out):
            out.append(wd)
        if len(out) >= max_items:
            break
    return out[:max_items]


# ---------------------------------------------------------------------------------------------
# What the advisor said: numbers with units, and contradictions between them
# ---------------------------------------------------------------------------------------------
_UNITS = {
    "dinar": "dinars", "dinars": "dinars", "dt": "dinars", "tnd": "dinars",
    "jour": "jours", "jours": "jours", "day": "jours", "days": "jours", "ayem": "jours",
    "heure": "heures", "heures": "heures", "hour": "heures", "hours": "heures",
    "%": "%", "pourcent": "%", "percent": "%",
    "mois": "mois", "month": "mois", "months": "mois",
}
_FIG = re.compile(r"(\d[\d\s.,]*\d|\d)\s*(dinars?|dt|tnd|jours?|days?|ayem|heures?|hours?|%|pourcent|percent|mois|months?)\b",
                  re.IGNORECASE)


def _to_number(s: str) -> float | None:
    s = s.replace(" ", "").replace(",", ".")
    if s.count(".") > 1:                       # 1.250.000 -> thousands separators
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def advisor_figures(advisor_msgs: list[str]) -> dict[str, list[float]]:
    """{unit: [values in order]} stated by the advisor, e.g. {"dinars": [100.0, 1000000.0]}."""
    out: dict[str, list[float]] = {}
    for msg in advisor_msgs:
        for m in _FIG.finditer(msg):
            val = _to_number(m.group(1))
            unit = _UNITS.get(m.group(2).lower())
            if val is not None and unit:
                out.setdefault(unit, [])
                if val not in out[unit]:
                    out[unit].append(val)
    return out


def contradictions(advisor_msgs: list[str]) -> list[str]:
    """Human-readable contradictions: the advisor gave two different values for the same unit."""
    found = []
    for unit, vals in advisor_figures(advisor_msgs).items():
        if len(vals) >= 2:
            seq = " puis ".join(f"{v:g} {unit}" for v in vals[-3:])
            found.append(f"le conseiller a dit {seq}")
    return found


# ---------------------------------------------------------------------------------------------
# Prompt section
# ---------------------------------------------------------------------------------------------
def memory_block(history: list[tuple[str, str]], style: str = "fr", claimed_fields: tuple = ()) -> str:
    """Section added to the client's prompt. `history` = [(role, text)], roles "client" / "conseiller".
    style "derja" = short Tunisian-Arabizi wording for the ESPRIT-Derja chat model."""
    mine = [t for r, t in history if r == "client"]
    adv = [t for r, t in history if r == "conseiller"]
    last = mine[-3:]
    avoid = overused_phrases(mine[-4:])
    contra = contradictions(adv)
    figs = advisor_figures(adv)
    if not (last or adv):
        return ""

    if style == "derja":
        parts = []
        if len(last) >= 1:
            parts.append("Jomlet elli 9olthom 9bal (ma t3awedhomch, 9oul 7aja jdida: so2el, ma3louma walla ch3our): "
                         + " | ".join(f"« {x[:90]} »" for x in last))
        if avoid:
            parts.append("Kelmet elli 3awedthom barcha, ma testa3melhomch tawa: " + ", ".join(avoid))
        if contra:
            parts.append("El conseiller ta9ned: " + "; ".join(contra) + ". 9oulou (mara wa7da).")
        return "\n".join(parts) + "\n"

    lines = ["MÉMOIRE DE L'APPEL (tu t'en souviens, comme une vraie personne) :"]
    if last:
        lines.append("- Tes dernières répliques (NE LES RÉPÈTE PAS ; fais avancer la conversation : pose une question "
                     "nouvelle, donne un fait nouveau ou réagis autrement) :")
        lines += [f"    · « {x[:110]} »" for x in last]
    if avoid:
        lines.append("- Expressions que tu as déjà trop utilisées, n'en réutilise AUCUNE : "
                     + ", ".join(f"« {a} »" for a in avoid))
    if figs:
        lines.append("- Chiffres déjà donnés par le conseiller : "
                     + "; ".join(f"{', '.join(f'{v:g}' for v in vs)} {u}" for u, vs in figs.items()))
    if contra:
        lines.append("- ATTENTION, incohérence : " + "; ".join(contra)
                     + ". Fais-le remarquer au conseiller, une seule fois, avec tes mots.")
    if claimed_fields:
        lines.append("- Informations personnelles déjà données (ne les redonne que si on te les redemande) : "
                     + ", ".join(claimed_fields))
    lines.append("- Varie ton vocabulaire et la forme de tes phrases à chaque réplique.")
    return "\n".join(lines) + "\n"
