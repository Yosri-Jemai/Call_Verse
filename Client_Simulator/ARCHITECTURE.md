# Client Simulator Agent: architecture

## 1. Place in the multi-agent system
```mermaid
flowchart LR
  LM[Load model<br/>hourly profile, incidents, difficulty] --> CS
  subgraph CS[Client Simulator Agent]
    GEN[Request generator<br/>profile x intent x language x persona] --> CONV[Conversation agent<br/>LangGraph]
  end
  CS -- ClientRequest queue --> WFM[Workforce Manager / SimPy env]
  WFM -- assigns --> ADV[Customer Advisor agent]
  ADV -- AdvisorView only --> CONV
  CONV -- client replies --> ADV
  CONV -- summary + ground truth --> QA[Quality Analyst / KPIs]
  CS <--> CRM[(CRM + runs + transcripts<br/>PostgreSQL / SQLite)]
```
The advisor never sees the hidden state: only `AdvisorView` (history, caller id, `crm_lookup`).

## 2. The LangGraph agent (graph.py), one call
```mermaid
flowchart TD
  S((START)) --> init
  init -- active --> advisor
  init -- abandoned in queue --> finish
  advisor --> perceive
  perceive -- active --> update
  perceive -- over --> finish
  update --> speak
  speak -- active --> advisor
  speak -- over --> finish
  finish --> E((END))
```
| Node | Kind | Does |
|---|---|---|
| init | code | `sim.start`: queue wait lowers mood, abandon if patience exceeded, opening message |
| advisor | external | any `AdvisorView -> str` (scripted, `llm_advisor`, a human, another graph) |
| perceive | **LLM judge** (rules fallback) | reads the advisor message: empathy, identity check, secret-code request, refusal, resolution, escalation, tone |
| update | code | mood, fraud pressure and strategy switch, end-of-call rules (resolved / escalated / hung up / max turns) |
| speak | **LLM client** (rules fallback) | writes the next line from hidden goal, persona, emotion; fraudster uses only the facts it really knows; then identity claims are checked against the CRM **in code** |
| finish | code | `summary()`: status, satisfaction, `is_fraudster`, `identity_verified`, `mood_trace`, `engine`, `llm_error` |

State (`CallState`): `conv` (hidden ground truth), `advisor_msg`, `features`, `summary`, `trace`.

## 3. Inside the client's brain
```mermaid
flowchart LR
  P[Persona: goal, true CRM facts,<br/>known facts, leak level 0-2] --> PR
  M[Mood and emotion] --> PR
  F[Fraud state: strategy, pressure 0-3] --> PR
  H[History] --> PR
  PR[Prompt builder<br/>language fr / mix / darija] --> LLM[LLM backend<br/>claude / gemini / ollama]
  LLM --> J[extract_json + truncation recovery]
  J -- ok --> OUT[reply, emotion, mood_adjust clamped +-0.15, hang_up]
  J -- error --> R[Rule engine banks.py] --> OUT
  OUT --> V[Claim check vs CRM in code]
```
Safety nets: JSON extraction tolerates fences and comments and recovers a cut-off reply; any failure is
recorded in `st.llm_error` and `sim.llm_errors` (shown in the UI), never silent; `llm_strict=True` raises.

## 4. Layers
| Layer | Files |
|---|---|
| Agent orchestration | `graph.py` (LangGraph, with a built-in runner if the package is missing) |
| Agent logic | `client_simulator.py` (`perceive` / `update` / `speak`), `persona.py`, `judge.py`, `banks.py` |
| LLM access | `llm_backends.py` (.env loading, `auto_llm`, `check_llm`, JSON helpers) |
| Environment | `load_model.py`, `sim_env.py` (SimPy: queue, advisors, KPIs), `advisors.py` |
| Storage | `db.py` (PostgreSQL / SQLite: CRM, runs, calls, turns, KPIs) |
| Interfaces | `api.py` (FastAPI, `/api/llm/status`), `frontend/` (Next.js), `audio.py` |
