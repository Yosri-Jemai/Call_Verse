# Quality Analyst

Quality Analyst évalue un appel **après sa fin**. Il ne répond pas au client et
les métadonnées cachées du simulateur restent séparées de la conversation avec
Cashy. Cette première version est indépendante de `Client_Simulator/scorecard.py`.

## Capacités

- Évaluation après l'appel sur cinq critères notés de 0 à 10 : exactitude (25 %),
  conformité (25 %), résolution (20 %), empathie (15 %) et pertinence (15 %).
- Score global calculé par le code, arrondi à deux décimales ; il est plafonné à
  5/10 si une erreur critique est présente.
- Contrôles déterministes des indicateurs par tour, de la règle d'identité
  configurable et des nombres avec unité explicite.
- Le contrôle numérique compare les chiffres annoncés aux sources ou aux
  résultats d'outils attachés au même tour. Si aucune preuve n'est disponible
  dans toute la conversation, il n'invente pas d'erreur et ajoute un avertissement.
  Ce contrôle ne prouve pas l'exactitude de toutes les affirmations bancaires.
- Jugement Groq par API ; réponse JSON validée par Pydantic et réessayée jusqu'à
  trois fois si elle est invalide. Les références à des tours inexistants sont
  refusées et les erreurs identiques du code et du modèle sont fusionnées.
- Page dédiée `/quality` avec historique, détails, preuves disponibles et
  recommandations de coaching destinées à l'équipe Advisor. Elles ne sont pas
  transmises à Cashy pendant l'appel et ne le modifient pas automatiquement.
- Si Groq est indisponible, Relay propose une analyse partielle explicite par
  règles seules. Elle sauvegarde les constats et avertissements sans note
  globale, et ne doit pas être interprétée comme l'évaluation qualitative complète.

En mode autopiloté, Relay transmet les appels et résultats d'outils Cashy ainsi
que les passages de politique RAG récupérés. Les réponses manuelles ne disposent
pas de traces d'outils Cashy. Les évaluations sont persistées dans la base
PostgreSQL dédiée indiquée par `QA_DATABASE_URL` (ou dans SQLite si cette
variable est absente) dans
les tables `qa_conversations`, `qa_tours`, `qa_sources`, `qa_evaluations` et
`qa_liens` ; `quality_reports` est conservée pour la compatibilité avec les
anciens rapports.

La conversation et ses preuves sont enregistrées avant l'appel à Groq. Si Groq
échoue, la transcription reste donc stockée et pourra être réévaluée plus tard ;
aucune ligne d'évaluation n'est créée tant qu'un rapport n'a pas été produit.
Les tables sont créées automatiquement au premier accès. Le `DATABASE_URL` du
Client Simulator continue de désigner son propre stockage et n'est pas modifié
par cette configuration.

Les transcriptions persistées du simulateur sont utilisées lorsqu'elles sont
disponibles. L'intégration actuelle ne lit pas directement les fils du
checkpointer PostgreSQL de LangGraph : en leur absence, elle évalue le dossier
de l'appel transmis par Relay. Les sources et traces affichées sont donc celles
effectivement transmises à Quality Analyst, et non une reconstruction autonome
du checkpointer.

La règle d'identité est désactivée par défaut. Configure
`QA_IDENTITY_REQUIRED_TOOLS` avec les noms exacts des outils sensibles, séparés
par des virgules ; elle n'émet une erreur que si un de ces outils est appelé
sans vérification d'identité confirmée.

## Configurer Groq

Quality Analyst utilise Groq par API : aucun modèle local de plusieurs gigaoctets
n'est requis. Configure la clé dans `Client_Simulator/.env`, le fichier chargé
par l'API du simulateur :

```dotenv
GROQ_API_KEY=<ta-cle-groq>
QUALITY_ANALYST_MODEL=openai/gpt-oss-120b
QUALITY_ANALYST_FALLBACK_MODELS=openai/gpt-oss-20b,llama-3.1-8b-instant
QUALITY_ANALYST_MAX_PROMPT_TOKENS=6500
QUALITY_ANALYST_MAX_COMPLETION_TOKENS=1200
QUALITY_ANALYST_TOOL_RESULT_CHARS=300
QUALITY_ANALYST_POLICY_EXCERPT_CHARS=400
QUALITY_ANALYST_POLICY_PASSES_PER_TURN=2
QUALITY_ANALYST_COMPACT_TRACE_CHARS=150
QUALITY_ANALYST_COMPACT_POLICY_PASSES_PER_TURN=1
QA_DATABASE_URL=postgresql://postgres:<mot-de-passe>@localhost:5432/quality_analyst
```

Le modèle par défaut reprend celui configuré pour Customer Advisor dans ce projet.
Tu peux le remplacer par un modèle Groq auquel ton compte a accès. Après un HTTP
429 ou un modèle absent (404), Quality Analyst essaie les modèles listés dans
`QUALITY_ANALYST_FALLBACK_MODELS`. Un HTTP 413 ne déclenche pas de changement de
modèle : le juge réessaie une fois avec un prompt compact, puis signale clairement
si la conversation dépasse toujours la limite TPM.

Le prompt envoyé à Groq est limité à environ 6500 tokens pour réserver la place
des 1200 tokens de réponse dans la limite TPM habituelle de 8000. Les résultats
d'outils et extraits RAG ne sont tronqués que dans la copie destinée au juge ; la
transcription, les preuves des contrôles déterministes et les données sauvegardées
restent complètes. Les valeurs sont configurables dans `Client_Simulator/.env`
avec les variables ci-dessus.

La variable `QUALITY_ANALYST_GROQ_API_KEY` peut être utilisée à la place de
`GROQ_API_KEY` si tu souhaites séparer la clé d'analyse de celle utilisée pour
l'audio et le simulateur. La clé reste côté backend et n'est jamais envoyée au
navigateur. `QA_DATABASE_URL` doit pointer vers la base PostgreSQL QA dédiée ;
ne remplace pas le `DATABASE_URL` du Client Simulator. Ne mets jamais un vrai
mot de passe ou une vraie clé dans `.env.example` ou dans Git.

Une clé absente ou invalide produit une erreur explicite. Il n'y a pas de
bascule silencieuse vers un modèle local.

## Analyser un dossier JSON

Le JSON doit avoir cette forme :

```json
{
  "call_id": "demo-001",
  "transcript": [
    {"turn": 1, "role": "client", "text": "Ma carte est bloquée."},
    {"turn": 2, "role": "conseiller", "text": "Je comprends votre inquiétude."}
  ],
  "context": {
    "scenario": "normal",
    "intent": "carte_bloquee",
    "status": "resolved",
    "resolved": true,
    "is_fraudster": false,
    "advisor_turns": [
      {"turn": 1, "flags": {"empathie": true}}
    ]
  }
}
```

Enregistrer le dossier dans `appel.json`, puis lancer depuis la racine du dépôt :

```powershell
.\Client_Simulator\.venv\Scripts\python.exe `
  .\Quality_Analyst\analyst.py `
  --input .\appel.json `
  --output .\rapport.json
```

Le rapport est enregistré dans la base définie par `QA_DATABASE_URL`, ou dans
`Quality_Analyst/data/quality_reports.sqlite3` si la variable n'est pas définie.
Pour tester les règles sans appeler Groq, utiliser `--no-llm` ; le rapport
n'affiche alors pas de score global complet, pour ne pas faire passer l'analyse
partielle pour une évaluation totale.

## Depuis Relay (parcours recommandé)

Lance le projet avec `.\Agent_Communication\launch_all.ps1`, puis utilise le
dashboard comme d'habitude, en mode autopiloté ou manuel. Après la fin de chaque
appel, Relay déclenche l'analyse. La page `/quality` affiche l'historique, le
score, les critères, les constats, les recommandations de coaching et les sources
récupérées par Cashy.

Les APIs, le dashboard et la clé Groq doivent être disponibles. Les
recommandations sont destinées à l'équipe : elles ne modifient pas Cashy
automatiquement. Les responsables peuvent s'en servir pour mettre à jour ses
consignes et améliorer les prochains appels. Le rapport est enregistré dans
la base définie par `QA_DATABASE_URL`. Le schéma QA est créé automatiquement au
premier appel ; aucune table n'est ajoutée à `cashy_banking`.

## Depuis le simulateur terminal (facultatif)

Depuis `Client_Simulator`, `Agent_Communication/run_simulation.py` peut lancer
l'analyse à la fin de l'appel :

```powershell
.\Client_Simulator\.venv\Scripts\python.exe `
  .\Agent_Communication\run_simulation.py `
  --scenario contestation_carte `
  --language fr `
  --quality-analysis
```

L'option `--quality-analysis` est facultative, pour garder les simulations
existantes inchangées. Il faut que `GROQ_API_KEY` soit disponible dans
l'environnement du processus. Le rapport peut être écrit avec
`--quality-report` et la base changée avec `--quality-db` (chemin SQLite ou URL
PostgreSQL).

## Tests

```powershell
.\Client_Simulator\.venv\Scripts\python.exe -m pytest .\Quality_Analyst\tests -q
```
