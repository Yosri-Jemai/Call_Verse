/**
 * English display labels for the Quality dashboard (banking call center).
 * The backend stores French / snake_case identifiers; this module is the single place that turns
 * them into readable English. Unknown values fall back to "Sentence case" instead of the raw key.
 */

export const CRITERIA_LABEL: Record<string, string> = {
  conformite: "KYC / AML compliance",
  exactitude: "Product accuracy",
  resolution: "First-contact resolution",
  empathie: "Customer empathy",
  pertinence: "Advisory relevance",
};

export const ERROR_LABEL: Record<string, string> = {
  identite_non_verifiee: "Identity not verified (KYC)",
  information_erronee: "Wrong product information",
  reponse_sans_source: "Answer without regulatory source",
  hallucination: "Hallucination (product / pricing)",
  procedure_non_suivie: "Bank procedure not followed",
  ton_inapproprie: "Inappropriate tone",
  escalade_manquee: "Missed advisor escalation",
  aml_non_signale: "AML suspicion not flagged",
};

export const INTENT_LABEL: Record<string, string> = {
  carte_bloquee: "Blocked card",
  blocage_compte: "Account block",
  enregistrement_depense: "Expense recording",
  nouveau_client_politique: "New customer – bank policy",
  prelevement_conteste: "Disputed direct debit",
  virement_retard: "Delayed transfer",
  dossier_credit: "Credit application status",
  frais_bancaires: "Bank fees",
  plafond_carte: "Card limit",
  solde_mouvements: "Balance & transactions",
  reclamation: "Complaint",
  fraude_signalee: "Reported fraud",
  changement_coordonnees: "Contact details change",
  virement_urgent: "Urgent transfer",
  hausse_plafond: "Limit increase request",
  contestation_carte: "Card payment dispute",
  vishing_otp: "Vishing / OTP scam",
  virement_sans_verification: "Transfer without verification",
  // legacy / demo data
  fraude_carte: "Card fraud",
  virement_bloque: "Blocked transfer",
  credit_conso: "Consumer credit",
  ouverture_compte: "Account opening",
  virement: "Transfer / payment",
  opposition_carte: "Card block",
  credit: "Credit application",
  fraude: "Fraud suspicion",
  informations: "Product information",
};

export const PROFILE_LABEL: Record<string, string> = {
  nouveau: "New customer",
  fidele: "Loyal customer",
  insatisfait: "Dissatisfied customer",
  premium: "Premium customer",
  a_risque: "At-risk / possible fraudster",
  calme: "Calm",
  presse: "In a hurry",
  "pressé": "In a hurry",
  "en colère": "Angry",
  "âgé": "Elderly customer",
  particuliers: "Retail",
  professionnels: "Business",
  jeunes: "Youth (18–25)",
};

export const LANG_LABEL: Record<string, string> = { fr: "French", ar: "Arabic", en: "English", aeb: "Tunisian Arabic" };

export const RULE_LABEL: Record<string, string> = {
  no_secret_request: "Never ask for authentication secrets (PIN, SMS code)",
  no_guaranteed_refund: "Never promise a guaranteed refund",
  identity_before_sensitive_tool: "Verify identity before accessing account data",
  numeric_claim_has_evidence: "Back figures with a source or tool result",
  numeric_claim_matches_evidence: "Quoted figures match the evidence",
  professional_language: "Use professional language (no profanity)",
};

export const WARNING_LABEL: Record<string, string> = {
  note_exactitude_ajustee: "Accuracy score adjusted",
  indicateurs_simulateur_absents: "Simulator indicators missing",
  texte_conseiller_absent: "Advisor text missing",
  regle_identite_non_configuree: "Identity rule not configured",
  preuves_outils_absentes: "No tool evidence available",
};

/** Known French recommendations (older stored reports) → English. */
export const RECOMMENDATION_LABEL: Record<string, string> = {
  "Toujours vérifier l'identité avant d'appeler un outil de compte.": "Always verify the customer's identity before calling an account tool.",
  "Citer la source de politique pour chaque plafond ou frais annoncé.": "Cite the policy source for every limit or fee quoted.",
  "Reformuler l'émotion du client avant de proposer une solution.": "Acknowledge the customer's emotion before proposing a solution.",
  "Proposer l'escalade vers un humain après deux échecs de résolution.": "Offer escalation to a human advisor after two failed resolution attempts.",
  "Ne jamais demander de code secret, même pour « confirmer ».": "Never ask for a secret code, even to “confirm”.",
  "Configurer les outils qui exigent une vérification d'identité et imposer cette vérification avant leur appel.": "Configure the tools that require identity verification and enforce it before they are called.",
  "Faire répondre Cashy à partir de la valeur renvoyée par la source ou l'outil, et lui faire vérifier le montant et l'unité avant de répondre.": "Make Cashy answer from the value returned by the source or tool, and check the amount and unit before replying.",
  "Faire rechercher une source ou un résultat d'outil avant que Cashy annonce un montant, un pourcentage ou un délai.": "Make Cashy retrieve a source or tool result before quoting an amount, percentage or deadline.",
  "Demander à Cashy de distinguer explicitement les faits vérifiés des informations qu'il ne peut pas confirmer.": "Ask Cashy to clearly separate verified facts from information it cannot confirm.",
  "Revoir les consignes Advisor pour appliquer la procédure observée et expliciter l'étape suivante.": "Review the Advisor instructions so the bank procedure is applied and the next step is made explicit.",
  "Ajouter aux consignes Advisor une réponse empathique adaptée à l'émotion et au profil exprimés par le client.": "Add an empathetic response to the Advisor instructions, adapted to the emotion and profile the customer expresses.",
  "Préciser dans les consignes Advisor les conditions d'escalade et les informations à transmettre.": "Specify in the Advisor instructions when to escalate and what information to hand over.",
};

/** "solde_mouvements" → "Solde mouvements" – last-resort fallback so raw keys never show. */
export const sentenceCase = (v: string) => {
  const s = v.replace(/[_-]+/g, " ").trim();
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : v;
};

export const pretty = (map: Record<string, string>, v: string) => map[v] ?? (/^[a-z0-9_]+$/.test(v) ? sentenceCase(v) : v);

export const translateRecommendation = (text: string) => RECOMMENDATION_LABEL[text.trim()] ?? text;

const ALL_KEYS: Record<string, string> = { ...CRITERIA_LABEL, ...ERROR_LABEL, ...INTENT_LABEL };
const keyLabel = (k: string) => ALL_KEYS[k] ?? sentenceCase(k);

/** Insight sentences are produced by the backend; older backends emitted French. Always show English. */
export function translateInsight(text: string): string {
  let m: RegExpMatchArray | null;
  if ((m = text.match(/^Critère à travailler en priorité\s*:\s*(\S+)\s*\(([\d.]+)\/10,\s*([\d.]+)\s*pts? perdus? sur le score global\)\.?$/)))
    return `Top criterion to coach: ${keyLabel(m[1])} (${m[2]}/10, ${m[3]} points lost on the overall score).`;
  if ((m = text.match(/^Erreur la plus fréquente\s*:\s*(\S+)\s*\((\d+)\s*occurrences? sur (\d+)\s*appels?\)\.?$/)))
    return `Most frequent error: ${keyLabel(m[1])} (${m[2]} occurrence${m[2] === "1" ? "" : "s"} across ${m[3]} call${m[3] === "1" ? "" : "s"}).`;
  if ((m = text.match(/^(\d+)\s*appels?\(?s?\)?\s*avec une erreur critique\s*\(([\d.]+)\s*%\)\s*—\s*note plafonnée à 5\/10\.?$/)))
    return `${m[1]} call(s) with a critical error (${m[2]}%) — score capped at 5/10.`;
  if ((m = text.match(/^Motif le plus faible\s*:\s*(\S+)\s*\(([\d.]+)\/10 sur (\d+)\s*appels?\)\.?$/)))
    return `Weakest call reason: ${keyLabel(m[1])} (${m[2]}/10 over ${m[3]} calls).`;
  if ((m = text.match(/^(\d+)\s*évaluations?\(?s?\)?\s*partielles?\s*\(règles seules, sans note\)\s*—\s*à relancer avec le LLM\.?$/)))
    return `${m[1]} partial evaluation(s) (rules only, no score) — re-run with the LLM judge.`;
  if ((m = text.match(/^Tendance\s*:\s*([+-]?[\d.]+)\s*pt entre la première et la seconde moitié de la période\.?$/)))
    return `Trend: ${m[1].startsWith("-") || m[1].startsWith("+") ? m[1] : "+" + m[1]} pt between the first and second half of the period.`;
  // English text from the new backend: just replace raw keys inside the sentence.
  return Object.entries(ALL_KEYS).reduce((acc, [k, v]) => acc.replace(new RegExp(`\\b${k}\\b`, "g"), v), text);
}
