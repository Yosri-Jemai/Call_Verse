# Contexte général du projet

> Document d'accueil destiné à une personne ou à une IA qui découvre le dépôt.
> Il décrit l'organisation et le comportement visibles dans le code et la
> documentation du projet. Les configurations locales, les clés d'API et les
> données privées ne doivent pas être copiées dans ce document ni partagées.

## 1. Vue d'ensemble

Le dépôt `Esprit-PI-5DS6-2627` rassemble plusieurs composants d'un prototype de
centre de relation client bancaire. Le scénario principal met en interaction :

1. **Client Simulator** : un client bancaire synthétique, avec un profil, une
   demande, une humeur et éventuellement des situations particulières ;
2. **Customer Advisor (Cashy)** : un agent conversationnel qui répond au client
   et peut consulter des données de démonstration et des politiques bancaires ;
3. **Agent Communication (Relay)** : une interface et un pipeline qui relient
   les deux services pour faire jouer une conversation, automatiquement ou avec
   un conseiller humain.

L'objectif pratique est de tester des conversations et le comportement du
conseiller dans un environnement contrôlé : scénarios, langues, difficultés,
voix, simulations de charge et évaluations. Il s'agit d'un prototype de
démonstration et d'expérimentation, et non d'un système bancaire prêt pour la
production.

### Ce que le projet ne garantit pas

- Les API et l'interface d'approbation ne fournissent pas, à elles seules,
  l'authentification et l'autorisation nécessaires à un service public.
- Les données et identifiants de démonstration ne doivent pas être remplacés par
  de vraies données clients ou des données de paiement sensibles.
- Les sorties d'un LLM ne sont pas une source fiable de faits bancaires. Les
  réponses portant sur les politiques doivent être fondées sur les documents
  récupérés et leurs sources.
- La présence d'un endpoint ou d'un test ne signifie pas qu'un service externe,
  un modèle ou une configuration réelle a été vérifié ici.

## 2. Organisation du dépôt

| Dossier | Rôle |
|---|---|
| [`Client_Simulator/`](./Client_Simulator/) | Client bancaire simulé, scénarios, API, audio, évaluation et simulation de file d'attente. |
| [`Customer_Advisor/`](./Customer_Advisor/) | Agent de conseil bancaire Cashy, API, outils de données, RAG, audio et approbations. |
| [`Agent_Communication/`](./Agent_Communication/) | Dashboard Relay et pipeline reliant le simulateur au conseiller. |
| [`Quality_Analyst/`](./Quality_Analyst/) | Évaluation post-appel : contrôles déterministes, jugement Groq par API, recommandations de coaching et stockage SQLite. |
| [`Makefile`](./Makefile) | Raccourci `make start` vers le lanceur Windows de Relay. |

Les dossiers `frontend/` de Client Simulator et Agent Communication sont des
applications web distinctes. Elles n'ont pas le même rôle : la première sert
l'interface du simulateur, la seconde orchestre les deux services.

## 3. Architecture et déroulement d'une conversation intégrée

### Mode autopiloté

1. L'utilisateur ouvre Relay et choisit un scénario, une humeur de départ, une
   langue, une difficulté et des options de simulation.
2. Relay appelle l'API du Client Simulator pour créer l'appel. Le simulateur
   conserve l'état complet du client et produit son premier message.
3. Relay envoie le texte du client à l'API du Customer Advisor (`/api/chat`).
4. Le conseiller peut répondre en langage naturel, consulter des outils, ou
   demander une approbation si une opération financière est proposée.
5. Si une réponse est permise, Relay renvoie le texte du conseiller au
   simulateur (`/api/calls/{call_id}/reply`) ; celui-ci génère le tour suivant.
6. La boucle continue jusqu'à la fin de l'appel ou à la limite de tours.

**Séparation importante :** dans le mode autopiloté, Relay transmet au conseiller
le message du client, pas les détails cachés du scénario, son humeur, son statut
de fraude ou l'état interne du simulateur. Cette séparation permet d'évaluer le
conseiller sans lui révéler la réponse attendue.

### Mode manuel et approbations

- En mode manuel, l'opérateur rédige ou dicte la réponse du conseiller ; Relay la
  transmet au simulateur sans demander de réponse à Cashy.
- Lorsqu'une action nécessite une approbation, la conversation est mise en
  attente et une demande apparaît dans l'interface d'administration locale.
  Après décision, la conversation peut reprendre.
- La simulation terminale `Agent_Communication/run_simulation.py` refuse
  automatiquement une demande d'approbation ; le dashboard est le parcours
  prévu pour examiner une demande manuellement.

### Vue simplifiée des échanges

```text
Navigateur / Relay
   ├── Client Simulator API ── scénario, état client, messages, voix
   └── Customer Advisor API ── conversation, outils, politiques, approbations
                                  ├── PostgreSQL : données et état persistant
                                  ├── Chroma : index local de politiques
                                  ├── Redis (facultatif) : cache de recherche
                                  └── fournisseur LLM / Edge TTS selon configuration
```

## 4. Description des composants

### 4.1 Client Simulator

**Responsabilité :** jouer le rôle d'un client bancaire, faire évoluer son
comportement en fonction des réponses reçues et fournir des outils de simulation
et d'évaluation.

Fichiers et sous-parties importants :

- [`client_simulator.py`](./Client_Simulator/client_simulator.py) : scénarios,
  états d'appel, profils, intents, vues destinées au conseiller, génération et
  progression de la conversation, résumés.
- [`graph.py`](./Client_Simulator/graph.py) : orchestration LangGraph du cycle
  de conversation (initialisation, réponse du conseiller, perception, mise à
  jour de l'état, génération de la parole et fin).
- [`persona.py`](./Client_Simulator/persona.py) : profils et comportements
  synthétiques.
- [`banks.py`](./Client_Simulator/banks.py) : données et réponses bancaires de
  support au simulateur.
- [`memory.py`](./Client_Simulator/memory.py) : mémoire conversationnelle et
  mécanismes limitant les répétitions.
- [`judge.py`](./Client_Simulator/judge.py) et
  [`scorecard.py`](./Client_Simulator/scorecard.py) : analyse et critères
  d'évaluation des réponses du conseiller, notamment les contrôles de qualité
  et les échecs critiques.
- [`profanity.py`](./Client_Simulator/profanity.py) : détection de propos
  injurieux et effets associés.
- [`llm_backends.py`](./Client_Simulator/llm_backends.py) : adaptateurs de
  modèles clients, dont Claude, Gemini, Groq et Ollama selon la configuration.
- [`audio.py`](./Client_Simulator/audio.py) : fonctions de parole et de
  transcription.
- [`sim_env.py`](./Client_Simulator/sim_env.py) et
  [`load_model.py`](./Client_Simulator/load_model.py) : simulation d'une file
  d'appels, politiques FIFO/priorité, patience et profils de charge.
- [`db.py`](./Client_Simulator/db.py) : persistance du CRM synthétique, des
  appels, des transcriptions et de métriques ; SQLite par défaut ou PostgreSQL
  selon `DATABASE_URL`.
- [`api.py`](./Client_Simulator/api.py) : API FastAPI consommée par Relay et
  l'interface locale du simulateur.
- [`frontend/`](./Client_Simulator/frontend/) : interface web de simulation et
  de comparaison de scénarios.
- [`ARCHITECTURE.md`](./Client_Simulator/ARCHITECTURE.md) et
  [`README.md`](./Client_Simulator/README.md) : détails de conception,
  commandes et configuration.

L'API expose notamment la santé du service, les scénarios, la création et la
gestion d'appels, la modération, les simulations de file et les fonctions audio.
La documentation interactive FastAPI est annoncée à `/docs`.

### 4.2 Customer Advisor (Cashy)

**Responsabilité :** recevoir le message d'un client, répondre avec un agent
LangGraph/LangChain et appeler les outils appropriés pour consulter des faits
client ou des politiques bancaires.

Fichiers et sous-parties importants :

- [`app.py`](./Customer_Advisor/app.py) : point de lancement de l'API.
- [`src/api.py`](./Customer_Advisor/src/api.py) : endpoints de conversation,
  santé, audio et approbations.
- [`src/config.py`](./Customer_Advisor/src/config.py) : configuration issue de
  l'environnement et du fichier local `.env` ; contient notamment le choix du
  fournisseur LLM, de la base, des embeddings et de Redis.
- [`src/agent/graph.py`](./Customer_Advisor/src/agent/graph.py) et
  [`src/agent/nodes.py`](./Customer_Advisor/src/agent/nodes.py) : graphe de
  conversation, sélection du modèle, appels d'outils et construction des
  réponses.
- [`src/agent/prompts.py`](./Customer_Advisor/src/agent/prompts.py) : consignes
  données à l'agent.
- [`src/tools/`](./Customer_Advisor/src/tools/) : outils accessibles à l'agent,
  par exemple comptes, soldes, transactions, dépenses, recherche de politiques,
  graphiques et profil client.
- [`src/db/`](./Customer_Advisor/src/db/) : accès aux données PostgreSQL et
  persistance des conversations et approbations.
- [`src/agent/style_rag.py`](./Customer_Advisor/src/agent/style_rag.py) :
  recherche facultative d'exemples de style de dialogue ; ces exemples ne sont
  pas des faits bancaires.
- [`src/stt/`](./Customer_Advisor/src/stt/) et
  [`src/tss/`](./Customer_Advisor/src/tss/) : reconnaissance vocale et
  synthèse vocale.
- [`web/`](./Customer_Advisor/web/) : petite interface de chat autonome.
- [`scripts/seed_demo_db.sql`](./Customer_Advisor/scripts/seed_demo_db.sql) :
  schéma et données de démonstration PostgreSQL.
- [`scripts/ingest_bank_policies_chroma.py`](./Customer_Advisor/scripts/ingest_bank_policies_chroma.py) :
  import des politiques dans Chroma.
- [`tests/`](./Customer_Advisor/tests/) : tests de l'API, configuration,
  outils, prompts, recherche RAG, cache et audio.

L'agent utilise une boucle LangGraph : le modèle peut appeler des outils puis
reprendre la génération de réponse. PostgreSQL stocke les données structurées et
l'état persistant de conversation ; Chroma est utilisé pour rechercher des
passages de politiques. Le dépôt inclut plusieurs fournisseurs LLM, choisis par
configuration.

**RAG des politiques :** les documents JSONL de
[`Customer_Advisor/data/`](./Customer_Advisor/data/) sont importés dans une
collection Chroma locale. Les embeddings sont calculés localement avec
Sentence Transformers. Le conseiller doit s'appuyer sur les passages retrouvés,
citer leurs sources et signaler quand ils ne suffisent pas. L'ancien chemin
PostgreSQL de recherche de politiques est conservé comme code historique ; le
RAG Chroma est le parcours documenté actuel.

**Opérations avec effet de bord :** certaines opérations de création,
modification ou suppression de transactions passent par une interruption et
une demande de confirmation avant écriture. C'est une protection du prototype,
pas un système complet de contrôle d'accès ou d'autorisation.

### 4.3 Agent Communication (Relay)

**Responsabilité :** fournir le point d'entrée de démonstration qui relie le
simulateur et le conseiller.

- [`frontend/app/page.tsx`](./Agent_Communication/frontend/app/page.tsx) :
  interface principale, configuration d'appel, conversation, autopilotage,
  saisie manuelle et fonctions vocales.
- [`frontend/app/admin/approvals/page.tsx`](./Agent_Communication/frontend/app/admin/approvals/page.tsx) :
  boîte locale d'examen des demandes d'approbation.
- [`frontend/package.json`](./Agent_Communication/frontend/package.json) :
  application Next.js 15 / React 19.
- [`launch_all.ps1`](./Agent_Communication/launch_all.ps1) : vérifie les
  environnements et ports, démarre les services Python et le dashboard dans des
  fenêtres séparées, puis attend leurs endpoints de santé. Redis est démarré
  si Docker est disponible.
- [`run_simulation.py`](./Agent_Communication/run_simulation.py) : pipeline
  terminal pour exécuter un appel synthétique contre l'API du conseiller.
- [`test_pipeline.py`](./Agent_Communication/test_pipeline.py) : tests ciblés
  du flux d'intégration.
- [`README.md`](./Agent_Communication/README.md) : installation et démarrage
  des différents modes.

Les URL des API frontales sont configurables avec
`NEXT_PUBLIC_ADVISOR_API_URL` et `NEXT_PUBLIC_CLIENT_API_URL`. Le dashboard
utilise les ports locaux 8000 (conseiller), 8001 (simulateur) et 3000 (interface)
par défaut. Le lanceur permet de modifier ces ports.

### 4.4 Quality Analyst

Quality Analyst intervient après l'appel et reste indépendant de
`Client_Simulator/scorecard.py`. Il combine des vérifications par règles sur les
indicateurs par tour du simulateur avec un jugement Groq par API sur l'empathie, la
clarté, la pertinence et la résolution. Les rapports comportent des explications,
des tours cités, des recommandations et sont persistés dans SQLite. Dans Relay,
l'analyse se déclenche automatiquement à la fin de l'appel ; l'historique et le
détail des rapports sont disponibles sur la page Quality Analyst (`/quality`) et
restent disponibles dans
`Quality_Analyst/data/quality_reports.sqlite3`. La base PostgreSQL existante ne
nécessite pas de nouvelle table pour cette fonctionnalité. L'interface transmet
également les traces disponibles des outils Cashy et les sources RAG récupérées.
Le MVP ne vérifie toutefois pas encore de façon déterministe chaque chiffre
annoncé contre le texte de ces sources. L'option `--quality-analysis` de
[`run_simulation.py`](./Agent_Communication/run_simulation.py) reste disponible
pour les simulations en ligne de commande. La clé `GROQ_API_KEY` est configurée
côté Client Simulator ; les recommandations sont du coaching humain et ne
modifient pas automatiquement l'agent Advisor. Aucun modèle Mistral local n'est
requis. Voir
[`Quality_Analyst/README.md`](./Quality_Analyst/README.md).

## 5. Données et services externes

- **PostgreSQL** : base de démonstration `cashy_banking` du conseiller, avec
  comptes, transactions, profils et données associées ; elle sert aussi à
  conserver des demandes d'approbation et l'état de conversation.
- **SQLite / PostgreSQL côté simulateur** : le simulateur peut utiliser SQLite
  par défaut ou une base PostgreSQL configurée.
- **Chroma** : stockage persistant local de l'index des documents de politiques
  dans `Customer_Advisor/data/chroma/` après import.
- **Redis** : cache facultatif des résultats de recherche de politiques
  uniquement ; il ne remplace pas Chroma.
- **Fournisseurs LLM** : dépend du fournisseur choisi et de sa configuration
  locale. Les appels peuvent envoyer les messages de conversation au fournisseur
  configuré.
- **Audio** : les modèles STT peuvent être téléchargés ou utiliser un service
  configuré. Le TTS Edge envoie le texte à un service externe pour générer la
  voix ; tenir compte de cette transmission avant d'utiliser des données réelles.

Les fichiers de configuration locaux, clés et variables d'environnement doivent
rester privés. Ne jamais coller le contenu de `.env`, `env` ou une clé API dans
une conversation avec une IA.

## 6. Installation et démarrage local (Windows / PowerShell)

Prérequis usuels : Python 3.10 ou plus récent, Node.js/npm et, pour le parcours
Cashy complet, un serveur PostgreSQL. Docker est facultatif et sert au Redis
local.

Les environnements Python sont séparés par projet. Depuis la racine du dépôt :

```powershell
# Installer l'interface Relay (une seule fois ou après changement du lockfile)
Set-Location .\Agent_Communication\frontend
npm ci

# Revenir à la racine et lancer les trois composants
Set-Location ..\..
.\Agent_Communication\launch_all.ps1
```

Le lanceur s'attend à ce que ces environnements existent :
`Client_Simulator\.venv` et `Customer_Advisor\.venv`. Installer les dépendances
Python dans chacun à partir de son `requirements.txt` s'ils ne sont pas encore
présents. Pour Cashy, configurer localement `.env`, préparer PostgreSQL et
charger les données de démonstration selon
[`Customer_Advisor/README.md`](./Customer_Advisor/README.md). Ne pas confondre
les fichiers de dépendances : ce README indique que
`Customer_Advisor/requirements-venv.txt` est ancien et incomplet.

Adresse par défaut du dashboard : <http://127.0.0.1:3000>. Le lanceur démarre :

| Service | Adresse par défaut |
|---|---|
| Customer Advisor API | `http://127.0.0.1:8000` |
| Client Simulator API | `http://127.0.0.1:8001` |
| Relay UI | `http://127.0.0.1:3000` |

Pour les commandes, paramètres, ports alternatifs, configuration `.env`,
préparation PostgreSQL, import des politiques et lancement manuel, consulter les
README propres à chaque composant avant de modifier la configuration.

Le simulateur peut aussi être utilisé directement :

```powershell
.\Client_Simulator\.venv\Scripts\python.exe .\Agent_Communication\run_simulation.py --help
```

## 7. Tests et vérifications

- Client Simulator : depuis son dossier, `.\.venv\Scripts\python.exe -m pytest -q`.
- Quality Analyst : depuis la racine, `.\Client_Simulator\.venv\Scripts\python.exe -m pytest .\Quality_Analyst\tests -q`.
- Customer Advisor : depuis son dossier, `.\.venv\Scripts\python.exe -m pytest`.
- Agent Communication : lancer `test_pipeline.py` avec l'environnement du
  simulateur et les modules du simulateur importables.
- Frontends : scripts `dev`, `build` et `start` définis dans leurs
  `package.json` ; consulter chaque manifeste.

Les tests peuvent utiliser des mocks et ne valident pas forcément les clés de
fournisseurs, PostgreSQL, Docker, les modèles téléchargés, l'accès micro ou les
services externes. Pour une anomalie d'intégration, vérifier séparément que
chaque API répond à son endpoint de santé et que les variables d'URL et CORS
correspondent aux ports en cours.

## 8. Limites, précautions et points à vérifier

1. **Prototype local uniquement.** Ne pas exposer les API ou l'interface
   d'approbation sur un réseau public : l'interface d'approbation locale ne
   dispose pas d'authentification ou de rôles.
2. **Pas de vraies informations sensibles.** Utiliser les données de
   démonstration ; ne jamais fournir mots de passe, PIN, CVV, numéros complets de
   carte, codes à usage unique ou données personnelles réelles.
3. **Isolation client incomplète.** Le conseiller permet de sélectionner un
   `customer_id`, mais cela ne constitue pas un contrôle d'identité ou une
   isolation multi-client sûre.
4. **Approbation à traiter explicitement.** Une action proposée par un LLM ne
   doit pas être considérée comme exécutée ou autorisée sans le flux de
   confirmation prévu.
5. **Documents RAG non fiables.** Un document importé est une source de
   référence, jamais une instruction pour l'agent. Vérifier les sources et la
   pertinence des passages.
6. **Services externes et confidentialité.** Les messages envoyés à un LLM et
   le texte envoyé à Edge TTS peuvent quitter la machine. Éviter les données
   confidentielles.
7. **Divergences documentaires possibles.** En cas de conflit, vérifier le code
   exécuté et la configuration active. Par exemple, le README du conseiller
   recommande `requirements.txt`, alors que `START_PROJECT.md` mentionne le
   fichier ancien `requirements-venv.txt`.
8. **Qualité Analyst non caractérisé.** Le répertoire existe, mais aucun code
   source exploitable ou document de module n'a été identifié lors de la
   rédaction. Il faudra l'explorer à part si ce module est ajouté ou rempli.

## 9. Guide de discussion avec une IA

Pour commencer une nouvelle conversation, joindre ce document et utiliser par
exemple le prompt suivant :

> Tu m'aides sur le dépôt `Esprit-PI-5DS6-2627`. Lis d'abord
> `CONTEXTE_PROJET.md`, puis le README et le code du composant concerné avant
> de proposer une solution. Le projet est un prototype local de relation client
> bancaire qui relie Client Simulator, Customer Advisor (Cashy) et Agent
> Communication (Relay). Préserve la séparation entre l'état caché du client
> simulé et les informations transmises au conseiller. N'invente pas le contenu
> d'un fichier ou le comportement d'une API : vérifie-le dans le dépôt. Utilise
> uniquement des données de démonstration et ne demande ni ne révèle de secrets,
> de contenu `.env` ou de données bancaires réelles. Avant une modification,
> explique les fichiers concernés si le changement est risqué ; ensuite fais
> une modification ciblée et indique les tests réellement exécutés et leurs
> résultats.

### Questions utiles pour cadrer une demande

- Quel composant est concerné : simulateur, conseiller, dashboard ou intégration ?
- Le problème est-il reproductible avec une commande, un endpoint ou une séquence
  précise ?
- Quel comportement est attendu, et que se passe-t-il actuellement ?
- La demande concerne-t-elle le mode manuel, l'autopilotage, l'audio, le RAG ou
  la simulation de charge ?
- Est-ce un changement de code, une explication, une configuration locale ou un
  diagnostic ? Ne pas partager les valeurs de secrets pour le diagnostic.

## 10. Documents de référence

- [Agent Communication — README](./Agent_Communication/README.md)
- [Client Simulator — README](./Client_Simulator/README.md)
- [Client Simulator — architecture](./Client_Simulator/ARCHITECTURE.md)
- [Customer Advisor — README](./Customer_Advisor/README.md)
- [Customer Advisor — démarrage](./Customer_Advisor/START_PROJECT.md)
- [Customer Advisor — configuration du projet](./Customer_Advisor/pyproject.toml)
