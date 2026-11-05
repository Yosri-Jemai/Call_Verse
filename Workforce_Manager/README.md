# Workforce Manager

The Workforce Manager organizes the advisors of the call-center Digital Twin.
The Client Simulator creates the callers and the advisors answer them. The
Workforce Manager decides:

1. **How many advisors work in each interval.** It forecasts calls from past
   days, then sizes the team with Erlang-C.
2. **When to call in extra advisors.** During the day it watches the queue and
   calls in reserve advisors when an incident or a spike makes it grow. It
   releases them once the queue is empty.
3. **Who takes which call.** This is skill-based routing:
   - Security calls go to **seniors**, who are careful and verify identity.
     Security calls are fraud, identity changes, transfers and disputes.
   - The other calls go to **juniors**, who are fast and cheaper.
   - The longer someone waits, the higher their priority.
4. **What a human must approve.** Nothing sensitive is executed until a person
   approves it (see below).
5. **Reports.** It compares "today" (a fixed team and first-come-first-served)
   with the Workforce Manager, on exactly the same callers.

It reuses the Client Simulator (callers, advisors, judge, KPIs) and does **not**
modify it.

## Data-driven: everything is learned from a call log

```powershell
.\Client_Simulator\.venv\Scripts\python.exe -m Workforce_Manager build-dataset   # data\call_log.csv + data\days.csv
.\Client_Simulator\.venv\Scripts\python.exe -m Workforce_Manager learn           # what was learned, as JSON
```

1. **Dataset** (`dataset.py`). This is a call log of 30 past days: 5 day types
   × 6 days, open 8:00 to 19:00. It comes from the project's call center as it
   works today: Client Simulator customers, the project's advisors, a fixed team
   of 2 seniors and 3 juniors, first come first served. One row per call:
   arrival, reason, wait, hung up or not, conversation length, who answered,
   outcome, and the hidden fraud label (used for evaluation only). For each day
   type, 5 days are used for **training** and 1 day is kept for **testing**.
2. **Model** (`model.py`), learned only from the training days:
   - *Forecast*: mean calls per 15-minute slot and per day type. It is
     evaluated on the test day (error on the day's total, and the mean
     absolute error per slot).
   - *Call length*: mean conversation time of answered calls, for risky calls
     answered by a senior and simple calls answered by a junior.
   - *Patience*: a **Kaplan-Meier** survival curve of the waits. A customer
     who hung up gives their patience. A customer who was answered is
     censored: we only know they would have waited at least that long.
3. **Rules derived** from the model and from the **service goal you set**
   (for example 80% answered within 60 s). No other number is chosen by hand.
   - Team size: Erlang-C, the smallest team that reaches the goal.
   - Call in a reserve advisor when the wait announced to a new caller
     (queue × call length ÷ advisors working), or the longest wait, passes the
     goal.
   - A risky caller waits for a senior up to the goal. After that, any advisor
     takes the call, and the approval check still applies.
   - Offer a call-back when the announced wait is longer than the learned
     median patience. The customer accepts if that wait is longer than their
     own patience, which is the same rule the simulator uses for hanging up.
4. **Evaluation.** The control room replays a **new day**, never in the call
   log. It reports the goal reached, hang-ups and call-backs. The approval
   check is scored against the hidden labels after the day (precision and
   recall). No simulated supervisor decides anything.

## Human approval (profile changes and fraud)

Every advisor goes through an approval gate. When the advisor is about to
execute an action, the gate checks whether the request is one of these:

| Type | Examples | What happens |
|---|---|---|
| Profile / account change | phone, e-mail, address, name, card limit, online access, beneficiary | **held** |
| Sensitive transaction | transfer to a new beneficiary, "urgent" transfer | **held** |
| Suspected fraud | risk score ≥ 3: identity mismatch, time pressure, refuses checks, fraud alert... | **held** |
| Fraud reported by the customer | hacked account, vishing, disputed payment | protective action done, fraud case opened |

When a request is **held**:
- The advisor's "C'est fait" is replaced by: *"I opened a validation ticket,
  nothing will be changed before approval, you will get an SMS. Your reference
  is WFM-XXXXXXXX."*
- A review is filed in the review queue, a SQLite file at
  `Workforce_Manager/data/reviews.db`.
- A person approves or denies it. Each decision is final and is written to an
  audit trail (who, when, note).
- If the advisor refused and escalated, the case is still filed so that a
  supervisor confirms it.

The gate only uses what a real bank knows: what the caller said, the
phone-menu reason, the CRM record and the active incident. It never uses the
simulator's hidden "is fraudster" flag. That flag is stored separately and is
only used to score the decisions in the reports.

## In the Relay dashboard

`.\Agent_Communication\launch_all.ps1` also starts the Workforce Manager API on port **8002**. Then open:

- **http://127.0.0.1:3000/workforce**
  - **Run a day:** choose a scenario and the teams, then compare today with the Workforce Manager. You get the summary table, the staffing per interval, the real-time decisions and the advisors. You can also preview the plan only.
  - **Human review:** the approval queue, for both simulations and live calls. Each request shows why it was flagged, what the caller said and the full conversation. Write your name, then **Approve** or **Deny**. Decisions are signed and final. For simulated calls, once you've decided, the page tells you whether the caller was really an impostor and whether your decision was right.
  - **Past runs:** every run, with its result.
- **http://127.0.0.1:3000** (live conversation)
  - Every advisor reply goes through the approval gate before the caller hears it.
  - If Cashy (or you, in manual mode) is about to execute a profile change, a sensitive transfer or a suspected-fraud request, the reply is replaced by a validation ticket. A red banner links to the review.
  - The **Flag this call as suspected fraud** button sends the call to the queue by hand.
  - If the Workforce Manager API is not running, the conversation works exactly as before.

API only: `.\Client_Simulator\.venv\Scripts\python.exe -m uvicorn Workforce_Manager.api:app --port 8002` (from the project folder). Runs are saved in `Workforce_Manager/data/runs/`. The review queue is `Workforce_Manager/data/reviews.db`, the same one the command line uses.

## Run it

Everything runs offline. It uses the rule-based callers and the scripted
advisors: no LLM, no API key, no PostgreSQL, no dashboard. Run the commands from
the **repository root** with the Client Simulator's Python environment:

```powershell
# once, only if Client_Simulator\.venv does not exist yet
py -3.11 -m venv .\Client_Simulator\.venv
.\Client_Simulator\.venv\Scripts\python.exe -m pip install -r .\Client_Simulator\requirements.txt

$py = ".\Client_Simulator\.venv\Scripts\python.exe"

# 1. the staffing plan for a day
& $py -m Workforce_Manager plan --scenario fin_de_mois --duration 240

# 2. today vs the Workforce Manager, same callers -> report in Workforce_Manager\reports\
& $py -m Workforce_Manager compare --scenario fin_de_mois --duration 240 --seed 7
& $py -m Workforce_Manager compare --scenario vague_de_fraude --duration 120

# 3. one day whose reviews wait for YOU (a real person) to decide
& $py -m Workforce_Manager simulate --scenario vague_de_fraude --duration 120
& $py -m Workforce_Manager reviews list
& $py -m Workforce_Manager reviews show WFM-1A2B3C4D
& $py -m Workforce_Manager reviews approve WFM-1A2B3C4D --by "Your name" --note "customer called back"
& $py -m Workforce_Manager reviews deny    WFM-1A2B3C4D --by "Your name" --note "number not confirmed"
& $py -m Workforce_Manager reviews stats

# tests
& $py -m pytest .\Workforce_Manager\tests -q
```

In `compare`, a **simulated supervisor** decides the reviews so that the report
can show the final result. It follows the real call-back procedure: it calls
the number on file, the real account holder confirms their own request and
denies one made by an impostor. Add `--manual-review` to leave the reviews for
a real person instead.

Useful options:
- `--seniors 4 --juniors 8`: available roster, planned advisors plus reserve
- `--fixed-seniors 2 --fixed-juniors 3`: today's team
- `--routing skills|priority|fifo`
- `--overflow 120`: seconds before a security call may go to a junior (0 = never)
- `--no-reactive`, `--no-approvals`
- `--sla-s 60 --sla-target 0.8`
- `--difficulty 0..1`, `--start-hour 8`

## Report

Each `compare` or `simulate` run writes a folder in `Workforce_Manager/reports/`:

| File | Content |
|---|---|
| `report.md` | summary table (today vs WFM with better/worse), staffing per interval, advisors, real-time decisions, human reviews |
| `report.json` | the same data, plus the forecast |
| `calls_<run>.csv` | one line per call: intent, advisor, routing reason, wait, outcome, review id |
| `staffing_<run>.csv` | per interval: forecast, actual calls, planned and on-duty advisors, expected and measured SLA |
| `reviews.csv` | every review with its decision |

The fraud KPIs take approvals into account. A fraudster whose request was held
only counts as **succeeded** if a human approved it. While the review is
pending, it counts as *awaiting human decision*.

## Files

| File | Role |
|---|---|
| `manager.py` | `WorkforceManager`, `WFMConfig`, `run_baseline` (the public API) |
| `forecast.py` | call forecast per interval, learned from past days (other seeds) |
| `erlang.py` | Erlang-C: probability of waiting, service level, required advisors |
| `staffing.py` | Erlang plan, fixed plan, real-time `ReactiveController` |
| `routing.py` | `fifo`, `priority`, `skills` routers; call categories |
| `team.py` | named advisors (S1.., J1..), shifts, cost |
| `approvals.py` | detection of changes, transfers and fraud signals; the approval gate; simulated supervisor |
| `reviews.py` | SQLite review queue and audit trail |
| `simulation.py` | the SimPy day: queue, staff on and off duty, routing, gate |
| `reports.py` | KPIs and report files |
| `__main__.py` | command line |

## In code

```python
from Workforce_Manager import WorkforceManager, WFMConfig, run_baseline, ReviewStore

store = ReviewStore("Workforce_Manager/data/reviews.db")
wfm = WorkforceManager(WFMConfig(roster_seniors=4, roster_juniors=8), store)
sim = wfm.simulate("fin_de_mois", duration_min=240, seed=7)      # reviews stay pending
today = run_baseline("fin_de_mois", duration_min=240, seed=7, seniors=2, juniors=3)
```

Any `AdvisorView -> str` function can play a skill, for example
`senior_fn=llm_advisor(my_llm)`.

## Limits

- Everything is synthetic, and talk time is the simulator's 30 s per exchange.
- The live Relay dashboard runs one call with Cashy, who already asks for human
  approval of its own write actions (Customer Advisor approvals inbox). This
  module covers the multi-caller simulation. A next step would be to expose the
  review queue and the reports through an API and a dashboard page.
- The reactive controller is rule-based. It is the natural place to plug in an
  RL agent later (observation = queue and staff, action = add or release an
  advisor).
