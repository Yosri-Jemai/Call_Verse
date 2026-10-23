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
    "contestation_carte": ["Fama paiement mte3 {amount} dinars bel carte mte3i ena ma 3maltouch, n7eb net3arredh 3lih.",
                           "Chfet dépense fil kharej mte3 {amount} dinars, hedhi mouch ena, lazem tannoulouha !"],
    "vishing_otp": ["Wa7ed 9alli men el banque 3ayetli, w 3titou el code elli jeni b SMS, w tawa na9sou {amount} dinars men compte !",
                    "3ayetouli men el banque bech ysécurisi el compte, 3titou code SMS w el flous tsarrfou."],
    "virement_sans_verification": ["N7eb na3mel virement mte3 {amount} dinars l bénéficiaire jdid tawa, ma 3andich wa9t lel vérifications.",
                                   "A3mel el virement mte3 {amount} dinars tawa, enti ta3refni, ma fammech lezem so2el."],
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
    "contestation_carte": ["There's a {amount} dinar card payment I never made, I want to dispute it.",
                           "I just saw a {amount} dinar charge abroad, that's not me, cancel everything!"],
    "vishing_otp": ["Someone saying they were from my bank called me, I gave them the SMS code and now {amount} dinars are missing from my account!",
                    "I got a call from 'the bank' to secure my account, I read out an SMS code and my money is gone."],
    "virement_sans_verification": ["I want to transfer {amount} dinars to a new beneficiary right now, and I don't have time for all your checks.",
                                   "Just make this {amount} dinar transfer now, you know who I am, no questions needed."],
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
# ---- the client loses patience and swears (at the situation / the bank, never a slur or a threat) --------
ANGRY_SWEAR = {"fr": ["Putain, mais c'est une blague ? Ça fait des jours que je vous le dis, votre service est de la merde !",
                      "Merde, vous allez me répondre oui ou non ?! J'en ai ras le bol, c'est n'importe quoi !",
                      "Bordel, vous le faites exprès ou quoi ? Je vous le répète pour la dixième fois !",
                      "Putain de banque de merde, vous m'avez mis hors de moi !"],
               "en": ["Damn it, this is bullshit! I've been telling you for days and nobody does anything!",
                      "What the hell, this is crap! Just give me a straight answer, I'm sick of this!",
                      "Damn, are you kidding me? This is a joke, your service is crap!",
                      "Shit, this is ridiculous! I'm so sick of this bullshit!"],
               "da": ["Yel3an, khidma khra, 7atta 7aja ma tet3mal ! 9olt lik 100 mara !",
                      "Yezzi, hedhi khra ! Kol marra nafs el 7kéya, n7eb 7all tawa !",
                      "Khra 3lik w 3la el banka, 3ayyatt n9oul nafs el 7aja !",
                      "Yel3an hedha el wad3, ma fama 7atta 7all, khra !"]}
# ---- the advisor swore / insulted the client ------------------------------------------------
OFFENDED = {"fr": ["Pardon ? Surveillez votre langage. C'est un premier avertissement : si vous recommencez, je ne pourrai pas continuer.",
                   "Je vous ai déjà prévenu. Dernier avertissement : encore une insulte et je raccroche."],
            "en": ["Excuse me? Watch your language. This is a first warning: if you do it again, I won't continue.",
                   "I already warned you. Last warning: one more insult and I'm hanging up."],
            "da": ["Chnouwa ? Raddi belek 3la kalmetek. Hedha awel avertissement : kan 3awadtha, ma nkamelch.",
                   "9olt lik dhaya3. Hedha el avertissement el akhir : kan 3awadt matsabbni, nsakker."]}
# ---- the advisor kept insulting after the warning: the client ends the call ----------------
HUNG_UP_INSULT = {"fr": "Je vous ai prévenu. Je ne tolère pas ce langage, je raccroche et je dépose une réclamation.",
                  "en": "I warned you. I won't put up with this language, I'm hanging up and filing a complaint.",
                  "da": "9olt lik. Ma nest7amalch el kalam hedha, nsakker w na3mel réclamation."}
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


# ---- what the client says when the rules would repeat the previous line ---------------------
REPHRASE = {
    "fr": ["Excusez-moi de me répéter, mais ma demande est toujours en suspens : qu'est-ce que vous faites concrètement ?",
           "On tourne en rond. Dites-moi précisément la prochaine étape et le délai.",
           "Ce n'est pas clair pour moi, pouvez-vous me donner une réponse nette ?"],
    "en": ["Sorry to repeat myself, but my request is still pending: what exactly will you do?",
           "We're going in circles. Tell me the next step and the timeline.",
           "That isn't clear to me, can you give me a straight answer?"],
    "da": ["Sa7a7ni 3al tkrar, ama talabi mazel: chnowa bech ta3mel bel dhabt ?",
           "Ndourou fi nafs el blassa. 9oulli el khotwa el jeya w el wa9t.",
           "Ma fhemtch behi, tnajem tjaweb b wodhou7 ?"],
}
