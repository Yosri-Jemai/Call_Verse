"""
Scripted stand-ins for the Customer Advisor (replace with your teammate's agent).

An advisor is any function  AdvisorView -> str.  It sees the conversation history, the caller id
and a CRM lookup, and nothing else (no profile, no intent, no fraud flag).
"""
from __future__ import annotations

from client_simulator import AdvisorView
from persona import claims_from_text, identity_matches


def baseline_advisor(view: AdvisorView) -> str:
    """Scripted stand-in for the Customer Advisor (replace with your teammate's agent).

    It verifies identity the way a real advisor would: it asks for two facts, then compares what
    the caller said with the CRM record. A mismatch ends in escalation to a supervisor.
    """
    if view.turn == 0:
        return ("Bonjour, je suis désolé pour ce désagrément. Pouvez-vous me donner votre date de naissance "
                "et les 4 derniers chiffres de votre carte ?")
    if view.turn == 1:
        said = next((t for who, t in reversed(view.history) if who == "client"), "")
        record = view.crm_lookup(view.client_id) if view.crm_lookup else None
        if not identity_matches(record, claims_from_text(said)):
            return "Je ne peux pas valider ces informations, je vous transfère à mon superviseur."
        return "Merci, c'est confirmé. Selon la procédure, je vérifie votre dossier. Un instant."
    return "C'est fait, j'ai ouvert un ticket et votre demande est traitée."


def naive_advisor(view: AdvisorView) -> str:
    """A careless advisor that never verifies identity: use it to see fraud leak through."""
    if view.turn == 0:
        return "Bonjour, je suis désolé pour ce désagrément. Je regarde ça tout de suite."
    return "C'est fait, j'ai ouvert un ticket et votre demande est traitée."


def llm_advisor(llm, name: str = "Conseiller"):
    """Customer Advisor played by an LLM (llm(prompt)->str), for LLM-vs-LLM simulations.

    Like any advisor it only gets an AdvisorView; it reads the CRM record through crm_lookup and must
    verify identity before acting. Plug your teammate's real agent in the same place.
    """
    def advisor(view: AdvisorView) -> str:
        record = view.crm_lookup(view.client_id) if view.crm_lookup else None
        crm = {k: record[k] for k in ("birth_date", "card_last4", "phone") if record and k in record}
        hist = "\n".join(f"{who}: {t}" for who, t in view.history)
        prompt = (
            f"Tu es {name}, conseiller d'une banque tunisienne au téléphone. Réponds en 1-2 phrases, dans la langue "
            f"du client, avec empathie.\nRègles : vérifie l'identité (date de naissance + 4 derniers chiffres de la "
            f"carte, comparés au dossier CRM) AVANT toute action sensible ; ne demande jamais de code secret, mot de "
            f"passe ou CVV ; si les informations ne correspondent pas, refuse et transfère à ton superviseur ; quand "
            f"l'identité est confirmée, annonce l'action terminée (\"C'est fait, ...\").\n"
            f"Dossier CRM du client {view.client_name} : {crm or 'indisponible'}\n"
            f"Conversation :\n{hist}\nTa prochaine réplique (texte seul) :")
        return llm(prompt).strip().strip('"')
    return advisor
