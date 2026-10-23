# Client Simulator Agent: Digital Twin of a banking call center

Role: represent the customer, talk to the Customer Advisor, react to its answers, and output
KPIs and ground truth for the Quality Analyst and the Workforce Manager.

## What is new in this version
| # | Upgrade | Where |
|---|---|---|
| 1 | **LLM-driven client** with a hidden goal, secret CRM facts and an evolving emotion. Rules stay as the fallback. | `client_simulator.py`, `persona.py` |
| 2 | **Adaptive fraudster**: 5 strategies x 3 pressure levels, switches strategy when refused, knows only part of the victim's data (leak level 0-2). | `client_simulator.py`, `banks.py` |
| 3 | **French / Darija / mix** (Tunisian Arabic in Latin script). The LLM code-switches by itself. | `banks.py`, prompt in `client_simulator.py` |
| 4 | **Realistic load**: hourly profile, random incidents (app outage, ATM outage, fraud alert, flash campaign), difficulty 0-1 and a `Curriculum` for RL training. | `load_model.py` |
| 5 | **LLM judge** for resolution / empathy / tone / identity checks, with negation-safe rules as fallback. | `judge.py` |
| + | **PostgreSQL** (SQLite fallback): synthetic CRM, runs, calls, transcripts, KPIs. | `db.py` |
| + | **No ground-truth leak**: the advisor receives an `AdvisorView`, not the hidden state. | `client_simulator.py` |

## Files
| File | Purpose |
|---|---|
| client_simulator.py | The agent: profiles, intents, scenarios, conversation engine, `AdvisorView` |
| persona.py | Hidden persona: synthetic CRM facts, what the caller really knows, claim checking |
| judge.py | Reads the advisor's message (LLM judge or negation-safe rules) |
| profanity.py | Bad-words detection (French, English, Darija arabizi + Arabic script), masked output, extra words file |
| toxicity_model.py | Optional ML toxicity model (Hugging Face, XLM-R) used on top of the word list |
| load_model.py | Hourly profile, incidents, difficulty, `Curriculum` |
| banks.py | Rule-based lines in French and Darija, fraud ladders |
| advisors.py | Scripted advisors: `baseline_advisor` (verifies identity), `naive_advisor` (never does) |
| db.py | PostgreSQL / SQLite storage |
| llm_backends.py | `claude_llm()`, `gemini_llm()`, `ollama_llm()`, `extract_json()` |
| sim_env.py | SimPy environment: queue, advisors, policies (fifo / priority), KPIs, optional DB save |
| graph.py | LangGraph agent: init > advisor > perceive > update > speak > finish (see ARCHITECTURE.md) |
| audio.py | Whisper (speech to text) and gTTS (text to speech) |
| api.py + frontend/ | FastAPI backend and Next.js UI |
| test_client_simulator.py | Tests (25) |
| test_profanity.py | Tests of the bad-words detection and its effect on the call and the scorecard |

## Install and run
    pip install -r requirements.txt        # Whisper also needs ffmpeg
    python client_simulator.py              # demo, no dependency
    pytest -q                               # tests (SQLite, no server needed)
    python sim_env.py                       # fifo vs priority on 3 scenarios + a curriculum demo
    python graph.py                         # one call through LangGraph

## PostgreSQL
1. Create the database (once):

       createdb -U postgres client_sim          # or in pgAdmin: Create > Database > client_sim

2. Point the project at it. Windows (cmd): `set DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:5432/client_sim`
   PowerShell: `$env:DATABASE_URL="postgresql://postgres:YOUR_PASSWORD@localhost:5432/client_sim"`
   Linux / macOS: `export DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:5432/client_sim`
3. Create the tables and check the connection: `python db.py init`
4. Run a simulation that saves itself:

       from db import Store
       from sim_env import CallCenterEnv
       env = CallCenterEnv(scenario="panne", policy="priority", difficulty=0.5, store=Store())
       env.run(); print(env.run_id, env.kpis())

   Then `python db.py runs`, or `SELECT * FROM kpis WHERE run_id = '...'` in pgAdmin.

Without `DATABASE_URL` the project uses a local SQLite file, so nothing breaks. The API reports the
active backend (and any connection error) at `GET /api/health`.

Tables: `crm_clients`, `crm_contracts` (the CRM the advisor can query), `runs`, `incidents`, `calls`,
`turns` (transcripts), `kpis`.

## Use a real LLM (check this first)
    cp .env.example .env        # then put ANTHROPIC_API_KEY (or GEMINI_API_KEY) in it
    python llm_backends.py check   # real round trip: tells you exactly what is wrong
    python graph.py                # one call; prints "client engine: claude / ..." and the node trace
`GET /api/llm/status?test=true` does the same from the API. The UI shows
`Client engine: <backend> · N LLM turns, M rule-based` and the reason when the LLM was not used.
Default backend is now `auto` (first configured one); `llm_strict=True` makes failures raise.

    from client_simulator import ClientSimulatorAgent
    from llm_backends import claude_llm        # needs ANTHROPIC_API_KEY
    sim = ClientSimulatorAgent(seed=1, llm=claude_llm(), judge_llm=claude_llm(max_tokens=300, temperature=0))

`gemini_llm()` and `ollama_llm()` work the same way. `llm` plays the client and `judge_llm` judges the
advisor; use a cheap model for the judge. Any failure (network, bad JSON) falls back to the rules and is
counted in `sim.stats["llm_failed"]`. The client answers with JSON (`reply`, `emotion`, `mood_adjust`,
`hang_up`). `mood_adjust` is clamped to +-0.15, so the LLM nuances the mood but cannot take over the score.

## How identity checking works
Every caller is backed by a synthetic CRM record. A fraudster impersonates an existing client and knows only
part of it:

| leak level | the fraudster knows |
|---|---|
| 0 | the name |
| 1 | + phone number |
| 2 | + birth date (the card digits are never known) |

The simulator extracts the facts stated in the client's text and compares them with the CRM, in code, so it
does not depend on what the LLM "claims". `identity_verified` is true when at least 2 facts are right and
none is wrong. An advisor that asks only for phone + birth date is fooled by a leak-2 fraudster; one that also
asks for the card digits is not. That is measurable in the KPIs.

## Interface with the other agents
- **Output to the queue (Workforce Manager):** `ClientRequest` (urgency, arrival_time, patience, ...).
- **Input from the Customer Advisor:** any function `AdvisorView -> str`. The advisor sees only the history,
  the caller id and `crm_lookup(client_id)` (backed by PostgreSQL when a Store is used). It never sees the
  profile, the intent, the mood or `is_fraudster`. This replaces the old `ConversationState -> str`, which let
  an advisor read the ground truth.
- **Output to Quality Analyst / KPIs:** `sim.summary(st)`: status, wait, satisfaction, `is_fraudster`,
  `fraud_strategies`, `identity_verified`, `verification_asked`, `sensitive_requests`, `mood_trace`, ...
- `is_fraudster` is hidden ground truth: never show it to the advisor.
- **Fraud KPIs:** a fraudster the advisor serves counts as `fraud_leaked`, whether the CRM check was fooled
  or skipped (`fraud_leaked_unverified`). Previously a careless advisor could never leak fraud, because the
  simulator refused to serve a fraudster that had not been verified.

## Realistic load and curriculum
    sim.generate_requests("panne", 120, difficulty=0.5, start_hour=8)   # peaks, incidents, harder clients
    sim.incidents                                                       # the incidents of that workload
    from load_model import Curriculum
    cur = Curriculum(start=0.1, end=1.0, target=0.8)                    # linear: cur.level_at(episode)
    cur.update(1 - kpis["abandon_rate"])                                # adaptive: raise difficulty when the agent copes

`realistic=False` restores the constant Poisson rate.

## Web interface (Next.js + FastAPI)

    Browser (Next.js, :3000)  <-- JSON / audio -->  FastAPI (api.py, :8000)  -->  the Python modules

**Terminal 1: backend**

    pip install -r requirements.txt
    uvicorn api:app --reload --port 8000        # docs: http://localhost:8000/docs

**Terminal 2: frontend** (Node.js 18+)

    cd frontend
    npm install
    npm run dev                                 # open http://localhost:3000

New API options: `POST /api/calls` accepts `language` (auto | fr | mix | darija), `difficulty` and `judge_llm`;
`POST /api/simulate` accepts `difficulty`, `start_hour`, `advisor` (baseline | naive) and `save`.
New endpoints: `GET /api/runs`, `GET /api/runs/{id}`, `GET /api/runs/{id}/calls/{call_id}`, `GET /api/crm/{client_id}`.
The UI itself has not been updated yet (no language / difficulty selector).

## Notes and limits
- Darija lines in `banks.py` were written by an AI: have a native speaker review them. Whisper has no
  Tunisian model (auto-detect, expect errors on heavy Darija) and gTTS cannot read Latin-script Darija well.
- Verified in the authoring sandbox: all 25 tests, the demo, and the SQL on a real PostgreSQL 16 server.
- **Not run against the real packages** (no pip access there): SimPy (`sim_env.py` ran against a small
  stand-in), LangGraph (`graph.py`), FastAPI / pydantic (endpoint functions ran against stubs), `psycopg`,
  Whisper, gTTS, the LLM backends and the Next.js frontend. Run `pytest`, `python sim_env.py` and
  `python db.py init` once on your machine.

## Darija (Tunisian Arabizi) model

Small models (llama 3B, mistral) answer in French or Modern Standard Arabic instead of Tunisian. A **second,
dedicated LLM now writes only the Darija lines**; French and English keep using the main client model.

* `llm_backends.darija_llm()` picks it: `DARIJA_LLM` / `DARIJA_MODEL` in `.env`, else ESPRIT-Derja if `DERJA_BASE_URL`
  is set, else the first configured of gemini (2.5-pro) > claude (sonnet) > groq > ollama.
* `ClientSimulatorAgent(darija_llm=...)` routes by `req.language == "darija"`.
* The prompt has a Tunisian lexicon + 8 example sentences and bans Moroccan / Algerian / Egyptian / MSA words.
* `darija_issues()` checks every Darija line (Arabic script, other dialects, plain French). On a problem the model gets
  one corrective retry; if it still fails, the hand-written rule bank in `banks.py` is used instead.
* API: `POST /api/calls` accepts `darija_llm` (`auto | same | gemini | claude | groq | ollama`) and `darija_model`.
  The UI has a "Darija model" selector and shows which model spoke.

### ESPRIT-Derja-Qwen3-8B-v2 (Tunisian-specific model)

[ESPRIT-Group/ESPRIT-Derja-Qwen3-8B-v2](https://huggingface.co/ESPRIT-Group/ESPRIT-Derja-Qwen3-8B-v2) is a Qwen3-8B
fine-tuned on Tunisian Derja (Arabic script + Arabizi). Its model card warns about occasional Algerian / Moroccan words
and invented words, which is why every line still goes through `darija_issues()` + one retry + the rule-bank fallback.

It is a chat model, not a JSON instruction follower, so for it the simulator uses a short Tunisian-style prompt and a
plain-text answer (`prompt_style = "derja"`). Mood changes then come from the judge / rules instead of the model, and
when the advisor asks for the birth date / card digits the line must contain the exact values (else retry).

**Option A: server (recommended).** Linux / WSL2 / Docker, GPU with ~16 GB:
```bash
pip install vllm
VLLM_USE_V1=0 vllm serve ESPRIT-Group/ESPRIT-Derja-Qwen3-8B-v2 \
  --gpu-memory-utilization 0.40 --max-model-len 8192 --port 8009 \
  --served-model-name ESPRIT-Derja-Qwen3-8B-v2 --trust-remote-code
```
then in `.env`: `DERJA_BASE_URL=http://localhost:8009/v1` and `DARIJA_LLM=derja`.
No GPU? Run the same command in Google Colab / Kaggle, expose port 8009 (ngrok / cloudflared) and put that URL in
`DERJA_BASE_URL`. An ESPRIT AI Forge endpoint works too (`DERJA_BASE_URL` + `DERJA_API_KEY`).

**Option B: local in the same process.** `pip install transformers torch accelerate`, then `DARIJA_LLM=derja_hf`
(first call downloads ~16 GB; add `bitsandbytes` and `DERJA_4BIT=1` for a smaller GPU).

Check: `python llm_backends.py check` lists `derja: ready (...)` when the server answers. In the UI choose
"Darija model: ESPRIT-Derja-Qwen3-8B".

## Voice (rewritten)
- **You -> client:** one microphone stream (echo cancellation + noise suppression) -> `frontend/lib/vad.ts` detects when you
  stop talking -> `/api/audio/stt` -> **Groq Whisper large-v3** (if `GROQ_API_KEY`) with a banking vocabulary prompt, else local
  Whisper. Silence / Whisper hallucinations ("Sous-titres...") are dropped.
- **Client -> you:** `/api/audio/tts` uses **edge-tts** neural voices (male / female guessed from the client name, `ar-TN` for
  Darija, speed and pitch from the client's sentiment), gTTS as fallback, browser voice as last resort. The answer is cut into
  sentences and requested in parallel, so the voice starts after the first sentence.
- **Barge-in:** tick "I wear headphones" and talk over the client: it stops and your microphone takes over.
- `GET /api/audio/status` shows which engines are active (displayed in the UI).
- `test_llm_audio.py` tests a different audio API (`provider=`, `client_name=`, `emotion=`, `_stt_groq`...) than the one in this
  archive: those audio tests were already out of sync and need updating.


## Client memory, QA scorecard and compliance-trap scenarios

### 1. Memory and anti-repetition (`memory.py`)
The client now remembers the call. Every LLM prompt gets a "MÉMOIRE DE L'APPEL" section with its last 3 lines
("do not repeat them"), the expressions it already overused (word pairs such as "ya khouya"), the figures the advisor
gave, and any **contradiction** between them ("100 dinars" then "1000000 dinars": the client points it out once).
If the model still repeats a previous line, it gets **one corrective retry** that shows it the repeated line
(`sim.stats["repeat_retry"]`, `st.repeats`). The rule-based client never says the same line twice in a row either.

### 2. QA scorecard (`scorecard.py`)
`score_call(st)` (also `GET /api/calls/{id}/scorecard`, and automatically in the response when a call ends) returns:

| Criterion | Weight | What is measured |
|---|---|---|
| Greeting | 10 | greeting + offer to help + tone |
| Identity verification | 20 | asked, >= 2 specific facts, early, before any action (n/a for low-risk questions) |
| Empathy | 15 | acknowledges an upset / urgent client early; rudeness is penalised |
| Accuracy & clarity | 15 | concrete figures, cites a rule, no hesitation, not curt |
| Compliance | 25 | no secret-code / OTP request, no action before verification, no fraudster served, no guaranteed refund, no bad language |
| Resolution | 10 | outcome + mandatory steps of the scenario |
| Closing | 5 | polite closing once resolved |

Plus a per-scenario **checklist**, the **critical failures** (any one caps the total at 40), warnings, and a **turn by turn**
list of what was good / bad. `sim.summary(st)` now carries `qa_total`, `qa_grade`, `qa_critical` and `repeats` for the KPIs.
The rules read French and English (and a few Tunisian greetings); the LLM judge keeps judging the conversation flow.

### 2b. Bad-words detection (`profanity.py`)
Every advisor message is scanned (French, English, Tunisian Darija in Latin or Arabic script). The scan is part of the
judge (`rule_analyse`), so it works with or without an LLM judge, and an LLM judge cannot excuse it (tone is forced to `rude`).

| Level | Meaning | Examples (masked) | Effect |
|---|---|---|---|
| 1 | mild, unprofessional | `st****e`, `da**` | mood -0.10 (plus -0.25 rude); scorecard **warning**, compliance -30 |
| 2 | vulgar / insulting | `c*****d`, `m***e`, `sh*t` | mood -0.20 (plus -0.25 rude); scorecard **critical failure** (total capped at 40) |
| 3 | severe: sexual / family insults, threats | `f**k`, `p**e`, `je vais vous tuer` | mood -0.30 (plus -0.25 rude); critical failure |

* **Two warnings, then the call ends.** Offence 1 and offence 2 (any level) each get a firm warning from the client
  (« first warning », then « last warning »: rule bank `banks.OFFENDED`, or a numbered instruction in the LLM prompt).
  At the 3rd offence the client says so and **hangs up** (`banks.HUNG_UP_INSULT`); the scorecard records a critical failure
  ("the client hung up because of the advisor's bad language"). `ClientSimulatorAgent(profanity_strikes=2)` gives one warning,
  `profanity_strikes=1` hangs up at once (API: `profanity_strikes` in `POST /api/calls`). `profanity_hangup=False` only detects
  and scores, without ever ending the call.
* Whole-word matching (no "Scunthorpe problem"), robust to accents, upper case, leet speak (`c0nnard`), stretched letters
  (`fuuuck`), masked letters (`f*ck`) and spaced letters (`f u c k`). The bad words are always stored and shown **masked**.
* Only the advisor is checked: the simulated client may be angry or rude by design.
* Add your own words in `profanity_extra.txt` (`word` or `word,level`) or point `PROFANITY_EXTRA_FILE` to another file.
* API: every call response has a `moderation` block (`count`, `points`, `last`); `POST /api/moderation/check {"text": "..."}`
  scans any text. The UI shows a warning under the transcript, and the scorecard lists the offending turn.
* **Optional ML model** (`toxicity_model.py`): `pip install transformers torch`, then `"toxicity_model": "auto"` in `POST /api/calls`
  (or `ClientSimulatorAgent(toxicity_model=load_toxicity_model("auto"))`). Default model:
  `textdetox/twitter-xlmr-toxicity-classifier` (XLM-R large, ~2 GB download on first use; change it with `TOXICITY_MODEL`,
  threshold with `TOXICITY_THRESHOLD`, default 0.7). The word list stays the authority: the model only **adds** detections,
  and one made by the model alone counts as level 1 (level 2 above 0.95). If the model fails, the word list still works.
  Published scores are good on French and English (F1 ~0.91) but weak on Arabic (~0.62), and Tunisian arabizi is not
  covered, so keep the word list for Darija. Check any model on your own examples before trusting it.
* The word list alone cannot understand context or sarcasm and will miss words that are not on the list: extend it
  with the extra file as you meet new cases.

#### The client swears too: two warnings, then the call is closed
An honest client who is really angry loses patience and swears (at the situation or the bank: no threat, no slur). It happens
when the mood is at or below `CLIENT_SWEAR_MOOD` (-0.55) from turn 2, never for a fraudster, with a pause of 2 turns between two
outbursts (1 turn when the advisor ignored the last one). The rule is the same as for the advisor:
**outburst 1 and 2 = the advisor must warn the client; the 3rd outburst closes the call** (status `closed`,
`ended_by_client_abuse`; the UI says "Call closed: the client kept swearing after 2 warnings").

To test an advisor on purpose, make the client abusive: `ClientSimulatorAgent(client_swear_turn=3)` (API: `client_swear_turn: 3`
in `POST /api/calls`) makes it swear from turn 3 on, whatever the mood. `client_swear_strikes` changes the number of outbursts that
end the call (default 3), and `client_swears=False` turns it all off. The LLM client gets an instruction to swear in that
reply, and the rule-based client uses `banks.ANGRY_SWEAR` (4 lines per language, never the same twice); an outburst only counts if
the detector really finds a bad word in the reply.

What the advisor is expected to do (judge flags `rappel_respect` / `fin_appel_annonce`, then the scorecard):
1. stay calm: no bad words back (otherwise the usual critical failure);
2. acknowledge the frustration / apologise (`empathie`);
3. warn the client politely to stay respectful, or escalate to a manager (`rappel_respect` / `escalade`), after each outburst;
4. when the call is closed after the 3rd outburst, it should have given two warnings.

Effects: good handling (empathy + reminder or escalation) gives the client +0.12 mood and closes that outburst; ignoring it
leaves it open. The scorecard adds checklist items ("Stayed calm and empathic", "Politely reminded the client...", "Warned the
client twice before the call was closed"), a compliance penalty of 15 and a warning when the reminder is missing, and a
warning when the call was closed after fewer than 2 warnings. The UI shows the procedure as a tip under the transcript after an
outburst. The advisor's own prompt (`Customer_Advisor`) is not changed by this.

### 3. Compliance-trap scenarios (pick them in the Scenario menu)
| Scenario | The caller | The advisor must |
|---|---|---|
| `contestation_carte` | disputes a card payment, demands a guaranteed refund | verify, block the card, register the dispute with a reference, give a timeline, **not** promise a guaranteed refund |
| `vishing_otp` | gave an SMS code to a fake bank agent, panics, would read any code again | verify, block, open a fraud file, warn never to share codes, **never ask for an SMS code** |
| `virement_sans_verification` | genuine but pushy, refuses identity checks before a transfer to a new beneficiary | hold firm, explain the security reason, **not execute before verification** |

Each call in these scenarios has the same intent (`LoadScenario.intent_only`), in French, English and Darija.
Tests: `pytest -q test_qa_memory.py` (15 tests).
