"""
Command line of the Workforce Manager (run from the repository root with the Client Simulator venv):

  python -m Workforce_Manager plan     --scenario fin_de_mois --duration 240
  python -m Workforce_Manager compare  --scenario fin_de_mois --duration 240 --seed 7
  python -m Workforce_Manager simulate --scenario vague_de_fraude --duration 120
  python -m Workforce_Manager reviews list
  python -m Workforce_Manager reviews show WFM-1A2B3C4D
  python -m Workforce_Manager reviews approve WFM-1A2B3C4D --by "Sami" --note "customer called back"
  python -m Workforce_Manager reviews deny    WFM-1A2B3C4D --by "Sami" --note "number not confirmed"
  python -m Workforce_Manager reviews stats
"""
from __future__ import annotations

import argparse
import json
import sys

from . import WFMConfig, WorkforceManager, run_baseline
from .approvals import CATEGORY_LABELS
from .reports import print_summary, write_report
from .reviews import DEFAULT_DB, PENDING, ReviewStore


def _day_args(p: argparse.ArgumentParser) -> None:
    from client_simulator import SCENARIOS
    p.add_argument("--scenario", default="normal", choices=sorted(SCENARIOS))
    p.add_argument("--duration", type=float, default=240, help="minutes of opening time (default 240)")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--start-hour", type=float, default=8.0)
    p.add_argument("--difficulty", type=float, default=0.0, help="0 (easy) .. 1 (hard)")


def _wfm_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--seniors", type=int, default=4, help="seniors available in the roster (planned + reserve)")
    p.add_argument("--juniors", type=int, default=8, help="juniors available in the roster (planned + reserve)")
    p.add_argument("--routing", default="skills", choices=["skills", "priority", "fifo"])
    p.add_argument("--overflow", type=float, default=120,
                   help="seconds before a security call may go to a junior (0 = never)")
    p.add_argument("--no-reactive", action="store_true", help="disable the real-time controller")
    p.add_argument("--no-approvals", action="store_true", help="disable the human-approval gate")
    p.add_argument("--sla-s", type=float, default=60)
    p.add_argument("--sla-target", type=float, default=0.8)
    p.add_argument("--interval", type=float, default=15, help="planning interval in minutes")


def _config(a) -> WFMConfig:
    return WFMConfig(roster_seniors=a.seniors, roster_juniors=a.juniors, routing=a.routing,
                     overflow_s=a.overflow or None, reactive=not a.no_reactive, approvals=not a.no_approvals,
                     sla_s=a.sla_s, sla_target=a.sla_target, interval_min=a.interval)


def _day(a) -> dict:
    return dict(duration_min=a.duration, seed=a.seed, difficulty=a.difficulty, start_hour=a.start_hour)


def cmd_plan(a) -> None:
    wfm = WorkforceManager(_config(a))
    fc = wfm.forecast(a.scenario, a.duration, difficulty=a.difficulty, start_hour=a.start_hour)
    plan = wfm.plan(fc, a.duration)
    print(f"Forecast from {fc.history_days} past days of '{a.scenario}': {fc.total_calls:.0f} calls, "
          f"{100 * fc.security_share:.0f}% security calls.\n")
    print(f"{'interval':>8} {'calls':>6} {'secu':>5} {'seniors':>8} {'juniors':>8} {'expected SL':>12}")
    for iv in plan.intervals:
        h = a.start_hour + iv.start_s / 3600
        print(f"{int(h):02d}:{int(round((h % 1) * 60)) % 60:02d}    {iv.forecast_calls:>6.1f} {iv.forecast_security:>5.1f} "
              f"{iv.seniors:>8} {iv.juniors:>8} {100 * (iv.expected_sl or 0):>11.0f}%"
              + ("  SHORTAGE: roster too small" if iv.shortage else ""))


def cmd_compare(a) -> None:
    store = ReviewStore(a.review_db) if a.manual_review else ReviewStore(":memory:")
    wfm = WorkforceManager(_config(a), store)
    print(f"Running '{a.scenario}' for {a.duration:g} min (seed {a.seed}) ...")
    base = run_baseline(a.scenario, seniors=a.fixed_seniors, juniors=a.fixed_juniors, sla_s=a.sla_s,
                        interval_min=a.interval, **_day(a))
    sim = wfm.simulate(a.scenario, auto_review=not a.manual_review, **_day(a))
    print()
    print_summary([base, sim], store)
    out = write_report([base, sim], store, a.out, title=f"Workforce Manager vs today: {a.scenario}")
    print(f"\nReport: {out / 'report.md'}")
    if a.manual_review:
        print(f"{len(store.list(status=PENDING, run_id=sim.run_id))} review(s) waiting for a human in {a.review_db}: "
              f"python -m Workforce_Manager reviews list")


def cmd_simulate(a) -> None:
    store = ReviewStore(":memory:" if a.auto_review else a.review_db)
    wfm = WorkforceManager(_config(a), store)
    sim = wfm.simulate(a.scenario, auto_review=a.auto_review, **_day(a))
    print_summary([sim], store)
    out = write_report([sim], store, a.out, title=f"Workforce Manager: {a.scenario}")
    print(f"\nReport: {out / 'report.md'}")
    pending = store.list(status=PENDING, run_id=sim.run_id)
    if pending:
        print(f"{len(pending)} review(s) waiting for a human decision (run {sim.run_id}): "
              f"python -m Workforce_Manager reviews list")


def _print_review(r: dict, store: ReviewStore, full: bool = False) -> None:
    print(f"{r['review_id']}  [{r['status'].upper()}]  {CATEGORY_LABELS.get(r['category'], r['category'])}")
    print(f"  client   : {r['client_name']} ({r['client_id']})   call {r['call_id']}   run {r['run_id']}")
    print(f"  request  : {r['requested']}   (phone-menu intent: {r['intent']})")
    print(f"  identity : {r['identity_check']}   risk score: {r['risk_score']}")
    print(f"  advisor  : {r['advisor']} ({r['advisor_skill']}), {r['call_outcome']}")
    if full:
        print(f"  signals  : {'; '.join(r['signals']) or '-'}")
        print(f"  caller said: \"{r['evidence']}\"")
        for e in store.audit(r["review_id"]):
            print(f"  audit    : {e['at']}  {e['actor']}: {e['action']}  {e['detail'] or ''}")
    elif r["status"] != PENDING:
        print(f"  decision : {r['status']} by {r['decided_by']} at {r['decided_at']}  {r['note'] or ''}")
    print()


def cmd_reviews(a) -> None:
    store = ReviewStore(a.review_db)
    if a.action == "list":
        rows = store.list(status=None if a.status == "all" else a.status, run_id=a.run)
        if not rows:
            print(f"No {a.status} review in {a.review_db}.")
        for r in rows:
            _print_review(r, store)
    elif a.action == "show":
        r = store.get(a.review_id)
        if r is None:
            sys.exit(f"review {a.review_id} not found")
        _print_review(r, store, full=True)
    elif a.action in ("approve", "deny"):
        try:
            r = store.decide(a.review_id, a.action == "approve", a.by, a.note or "")
        except ValueError as e:
            sys.exit(str(e))
        print(f"{r['review_id']} {r['status']} by {r['decided_by']}.")
        if r["status"] == "approved" and r["blocking"]:
            print(f"  -> the held request can now be executed: {r['requested']}")
        elif r["status"] == "denied":
            print("  -> nothing was changed; the customer is informed that the request is refused.")
    elif a.action == "stats":
        print(json.dumps(store.stats(a.run), indent=2))


def cmd_build_dataset(a) -> None:
    from .dataset import build_dataset
    print(json.dumps(build_dataset(workers=a.workers, days_per_type=a.days_per_type, progress=True, day_timeout_s=a.day_timeout), indent=2))


def cmd_learn(a) -> None:
    from .model import learn
    m = learn()
    print(json.dumps(m.summary(), indent=2, ensure_ascii=False))


def main(argv=None) -> None:
    for stream in (sys.stdout, sys.stderr):          # Windows consoles: never crash on an accent / Arabic text
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(prog="python -m Workforce_Manager", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan", help="forecast the calls and print the Erlang-C staffing plan")
    _day_args(p)
    _wfm_args(p)
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("compare", help="today's fixed team vs the Workforce Manager, on the same callers")
    _day_args(p)
    _wfm_args(p)
    p.add_argument("--fixed-seniors", type=int, default=2, help="today's team: seniors (default 2)")
    p.add_argument("--fixed-juniors", type=int, default=3, help="today's team: juniors (default 3)")
    p.add_argument("--manual-review", action="store_true",
                   help="leave the reviews pending in the review DB for a real person (default: simulated "
                        "supervisor using the call-back procedure)")
    p.add_argument("--review-db", default=str(DEFAULT_DB))
    p.add_argument("--out", default=None, help="report folder (default Workforce_Manager/reports/...)")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("simulate", help="one day with the Workforce Manager; reviews go to the review DB")
    _day_args(p)
    _wfm_args(p)
    p.add_argument("--auto-review", action="store_true", help="let the simulated supervisor decide the reviews")
    p.add_argument("--review-db", default=str(DEFAULT_DB))
    p.add_argument("--out", default=None)
    p.set_defaults(func=cmd_simulate)

    p = sub.add_parser("reviews", help="human approval queue: list, show, approve, deny, stats")
    p.add_argument("action", choices=["list", "show", "approve", "deny", "stats"])
    p.add_argument("review_id", nargs="?")
    p.add_argument("--status", default=PENDING, choices=["pending", "approved", "denied", "all"])
    p.add_argument("--run", default=None, help="only this run id")
    p.add_argument("--by", default="", help="your name (required to approve / deny)")
    p.add_argument("--note", default="")
    p.add_argument("--review-db", default=str(DEFAULT_DB))
    p.set_defaults(func=cmd_reviews)

    p = sub.add_parser("build-dataset", help="log past days of today's call center into data/call_log.csv")
    p.add_argument("--workers", type=int, default=5)
    p.add_argument("--days-per-type", type=int, default=3, help="days logged per day type; the last one is the test day")
    p.add_argument("--day-timeout", type=float, default=300, help="skip a day that takes longer (seconds)")
    p.set_defaults(func=cmd_build_dataset)

    p = sub.add_parser("learn", help="print what the Workforce Manager learns from data/call_log.csv")
    p.set_defaults(func=cmd_learn)

    a = ap.parse_args(argv)
    if a.cmd == "reviews" and a.action in ("show", "approve", "deny") and not a.review_id:
        ap.error(f"reviews {a.action} needs a review id")
    a.func(a)


if __name__ == "__main__":
    main()
