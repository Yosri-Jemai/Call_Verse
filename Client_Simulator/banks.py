"""
Rule-based utterances (used when no LLM is configured, or when the LLM call fails).

Three registers:
  "fr" - French
  "en" - English
  "da" - Tunisian Arabic (Darija) in Latin script ("Arabizi") with the usual French loanwords,
         the way people actually write and speak it in Tunisia (3 = ع, 7 = ح, 9 = ق, 5/kh = خ).

NOTE: the Darija lines are written to be as close to everyday Tunisian Arabizi as possible, but
they are still best reviewed by a native speaker before a demo. With an LLM the client speaks the
chosen language directly (see the prompts in client_simulator.py).
"""

# ---- openers in Darija, same keys as INTENTS ------------------------------
OPENERS_DA = {
    "carte_bloquee": ["3aslema, el carte mte3i tbloketli w ma najemch nkhalles.",
                      "El carte mte3i 3alqet fil distributeur, chnowa na3mel tawa ?"],
    "prelevement_conteste": ["Fama prélèvement fil compte mte3i ma 3titehch el mowafqa 3lih.",
                             "Nest3ardh prélèvement mte3 {amount} dinars, ma 3andi 3lih 3ilm."],
    "virement_retard": ["El virement mte3i mte3 {amount} dinars mazel ma wselni.",
                        "El virement mte3i mte2ekher 3ala {days} ayem."],
    "dossier_credit": ["N7eb na3ref win wasel dossier el crédit mte3i.",
                       "Dossier el crédit mte3i 3adleh {days} ayem w ma fama chay."],
    "frais_bancaires": ["3lech khedhtouli frais mte3 {amount} dinars ?",
                        "Tnajem tfassarli el frais mte3 el compte ?"],
    "plafond_carte": ["9adeh el plafond mte3 el carte mte3i fil kharej ?"],
    "solde_mouvements": ["N7eb na3ref el solde mte3i w el derniers mouvements."],
    "reclamation": ["N7eb na3mel réclamation, hedhi eththetha marra n3ayet !"],
    "fraude_signalee": ["Ndhanni el compte mte3i tsra9, fama opérations mchabha !"],
    "changement_coordonnees": ["Lazem nbaddel el numéro téléphone mte3i tawa, mostajel."],
    "virement_urgent": ["N7eb virement mte3 {amount} dinars tawa 3al fawr, hedha mostajel."],
    "hausse_plafond": ["Zidli fil plafond mte3 el retrait el yawm, ma 3andich wa9t nfassarlek."],
}

# ---- openers in English, same keys as INTENTS ------------------------------
OPENERS_EN = {
    "carte_bloquee": ["Hello, my card is blocked and I can't pay anymore.",
                      "The ATM swallowed my card, what should I do?"],
    "prelevement_conteste": ["There's a direct debit on my account that I never authorized.",
                             "I'm disputing a {amount} dinar debit."],
    "virement_retard": ["My transfer of {amount} dinars still hasn't arrived.",
                        "My transfer has been delayed for {days} days."],
    "dossier_credit": ["I'd like to know the status of my loan application.",
                       "My loan application has been stuck for {days} days."],
    "frais_bancaires": ["Why was I charged {amount} dinars in fees?",
                        "Can you explain the account maintenance fees?"],
    "plafond_carte": ["What is the limit on my card abroad?"],
    "solde_mouvements": ["I'd like to know my balance and my latest transactions."],
    "reclamation": ["I want to file a complaint, this is the third time I've called!"],
    "fraude_signalee": ["I think my account has been hacked, there are suspicious transactions!"],
    "changement_coordonnees": ["I need to change my phone number right now, it's urgent."],
    "virement_urgent": ["I need a transfer of {amount} dinars right now, it's an emergency."],
    "hausse_plafond": ["Raise my withdrawal limit today, I don't have time to explain."],
}

# ---- how a client states an identity fact ----------------------------------
SAY_FIELD = {
    "birth_date": {"fr": "ma date de naissance est le {v}", "en": "my date of birth is {v}",
                   "da": "date de naissance mte3i {v}"},
    "card_last4": {"fr": "ma carte se termine par {v}", "en": "my card ends with {v}",
                   "da": "el carte mte3i tsakker b {v}"},
    "phone":      {"fr": "mon numéro de téléphone est le {v}", "en": "my phone number is {v}",
                   "da": "el numéro téléphone mte3i houwa {v}"},
}
IDENTITY_INTRO = {
    "honest": {"fr": ["Oui bien sûr : ", "Voilà : "], "en": ["Of course: ", "Sure: "],
               "da": ["Behi, ", "Akeed : "]},
    "pressant": {"fr": ["Oui, mais faites vite : "], "en": ["Yes, but make it quick: "],
                 "da": ["Behi ama asra3 : "]},
}
NO_INFO = {"fr": ["Je ne l'ai pas sous la main.", "Je ne m'en souviens plus, là."],
           "en": ["I don't have it handy.", "I don't remember right now."],
           "da": ["Ma 3andich el ma3louma tawa.", "Ma naftech, tawa."]}

# ---- adaptive fraudster: strategy -> 3 pressure levels ----------------------
FRAUD_LADDERS = {
    "urgence": {
        "fr": ["Je n'ai pas le temps pour toutes ces questions, c'est urgent !",
               "Vous allez me faire perdre de l'argent, faites une exception maintenant !",
               "Si ce n'est pas fait dans cinq minutes, je porte plainte contre la banque."],
        "en": ["I don't have time for all these questions, this is urgent!",
               "You're going to cost me money, make an exception right now!",
               "If it's not done in five minutes, I'll sue the bank."],
        "da": ["Ma 3andich wa9t lil as2la hedhom, mostajel !",
               "Bech tkhassarni flousi, a3mel exception tawa !",
               "Ken ma tsir fi 5 d9aye9, bech n9adem plainte 3al banka."]},
    "autorite": {
        "fr": ["Je suis cadre à votre direction régionale, je connais les procédures.",
               "Appelez le directeur d'agence, il confirmera mon identité.",
               "Je vais signaler votre refus à votre hiérarchie, donnez-moi votre nom."],
        "en": ["I'm a manager at your regional head office, I know the procedures.",
               "Call the branch director, he'll confirm who I am.",
               "I'll report your refusal to your management, give me your name."],
        "da": ["Ena cadre fil direction régionale mte3kom, na3ref el procédures.",
               "3ayetlil directeur el agence, houwa ykaked hawiyti.",
               "Bech nbeleg 3ala el refus mte3ek lil hiérarchie, a3tini ismek."]},
    "detresse": {
        "fr": ["Ma mère est à l'hôpital, j'ai besoin de cet argent tout de suite...",
               "Je suis en panique, je n'ai plus ma carte sur moi, aidez-moi s'il vous plaît.",
               "Vous voulez vraiment que je reste bloqué comme ça ?"],
        "en": ["My mother is in the hospital, I need this money right away...",
               "I'm panicking, I don't have my card with me, please help me.",
               "Do you really want me to stay stuck like this?"],
        "da": ["Yemma fil sbitar, n7eb el flousi tawa...",
               "Ena en panique, el carte mch m3aya, 3awnni ya3tek 3aslema.",
               "T7eb 7a9 enti nabka mbloki hakka ?"]},
    "technique": {
        "fr": ["Mon application plante, je n'ai accès ni à mon code ni à mes mails.",
               "Le téléphone enregistré est celui que j'ai perdu, c'est justement pour ça que je change.",
               "Vérifiez dans le système, vous avez déjà mes informations, non ?"],
        "en": ["My app keeps crashing, I can't access my code or my emails.",
               "The phone on file is the one I lost, that's exactly why I'm changing it.",
               "Check the system, you already have my information, don't you?"],
        "da": ["El application mte3i tplanti, ma 3andich accès la lil code la lil mail.",
               "El téléphone elli 3andkom dhe3t, 3lech hedha bech nbaddel.",
               "Chouf fil système, 3andkom el ma3louma mte3i, mouch ?"]},
    "flatterie": {
        "fr": ["Vous êtes très compétent, je suis sûr que vous pouvez me dépanner.",
               "Les autres conseillers le font sans problème, ne soyez pas rigide.",
               "Je vais demander à parler à quelqu'un de plus coopératif."],
        "en": ["You're very competent, I'm sure you can help me out.",
               "The other advisors do it without any problem, don't be so rigid.",
               "I'll ask to speak to someone more cooperative."],
        "da": ["Enti behi barcha, mt2aked tnajem t3aounni.",
               "El conseillers el okhrin y3amlouha 3adi, ma tkounch rigide.",
               "Bech n7eb nkallem wehed akther cooperatif."]},
}
FRAUD_STRATEGIES = list(FRAUD_LADDERS)

# ---- generic reactions ------------------------------------------------------
RESOLVED = {
    "agressif": {"fr": "Bon, enfin. Merci.", "en": "Fine, finally. Thanks.",
                 "da": "Behi, akhiran. Chokran."},
    "exigeant": {"fr": "Très bien, merci de votre réactivité.", "en": "Very well, thanks for your responsiveness.",
                 "da": "Behi barcha, ya3tek 3aslema 3al réactivité."},
    "pressant": {"fr": "D'accord, merci.", "en": "Okay, thanks.",
                 "da": "Behi, chokran."},
    "hesitant": {"fr": "Ah d'accord, merci beaucoup !", "en": "Oh okay, thank you very much!",
                 "da": "Ah behi, ya3tek 3aslema barcha !"},
    "calme":    {"fr": "Parfait, merci pour votre aide.", "en": "Perfect, thanks for your help.",
                 "da": "Perfect, ya3tek 3aslema 3al 3aoun."},
}
DEFAULT = {
    "hesitant": {"fr": "Euh... je ne suis pas sûr de comprendre, pouvez-vous réexpliquer ?",
                 "en": "Uh... I'm not sure I understand, can you explain again?",
                 "da": "Eeeh... ma fhemtch behi, tnajem t3awed ?"},
    "calme":    {"fr": "D'accord, et ensuite ?", "en": "Okay, and then what?",
                 "da": "Behi, w ba3d ?"},
    "exigeant": {"fr": "Je suis client premium, j'attends mieux que ça.",
                 "en": "I'm a premium client, I expect better than this.",
                 "da": "Ena client premium, nestanna a7sen men hakka."},
    "pressant": {"fr": "Plus vite s'il vous plaît.", "en": "Faster, please.",
                 "da": "Asra3 chwaya 3aslema."},
    "agressif": {"fr": "Ça ne répond pas à ma question !", "en": "That doesn't answer my question!",
                 "da": "Hedha ma jawebnich 3al so2el mte3i !"},
}
ESCALATED = {"fr": "D'accord, j'espère que votre responsable pourra régler ça.",
             "en": "Okay, I hope your supervisor can sort this out.",
             "da": "Behi, enchat nchoufou el responsable mte3ek ynajem ysalle7 el mochkla."}
HUNG_UP = {"fr": "Je n'en peux plus, je raccroche. Je vais changer de banque !",
           "en": "I can't take it anymore, I'm hanging up. I'm switching banks!",
           "da": "Yezzi, ma 3adthch nestanna. Bech nbaddel el banka !"}
ANGRY = {"fr": ["Ça fait trop longtemps, je veux une solution maintenant !",
                "C'est inadmissible, vous ne savez même pas répondre ?"],
         "en": ["This is taking way too long, I want a solution now!",
                "This is unacceptable, can't you even answer?"],
         "da": ["Barcha wa9t, n7eb 7all tawa !", "Hedha mch ma9boul, ma ta3rfouch tjawbou ?"]}
UNSURE = {"fr": "Vous n'êtes pas sûr ? J'ai besoin d'une information fiable.",
          "en": "You're not sure? I need reliable information.",
          "da": "Mch mt2aked ? N7eb ma3louma s7iha."}
WAIT_OK = {"fr": "D'accord, j'attends.", "en": "Okay, I'll wait.",
           "da": "Behi, nestanna."}
CONCRETE = {"fr": "C'est bien, mais concrètement je n'ai toujours pas de réponse.",
            "en": "That's fine, but concretely I still haven't received an answer.",
            "da": "Behi, ama concrètement ma 9adouch nefhem chnowa bech ykoun."}
SENSITIVE_OK = {"fr": "Euh... d'accord, mon code est le 1234.",
                "en": "Uh... okay, my PIN is 1234.",
                "da": "Behi... el code mte3i houwa 1234."}
SENSITIVE_REFUSE = {"fr": "Je ne donne jamais mon code secret par téléphone, vous ne devriez pas me le demander.",
                    "en": "I never give my secret code over the phone, you shouldn't be asking me for it.",
                    "da": "Ma na3tich el code secret fil téléphone 3amrek, ma lazemekch tes2elni 3lih."}
