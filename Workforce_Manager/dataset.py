"""
The Workforce Manager's dataset: call logs of past days, like the ACD log a real call center exports.

Each past day (3 per day type: 2 to learn from, 1 to test) is produced by the project's own call center as it works today (Client Simulator
customers, the project's advisors, a fixed team, first come first served, no Workforce Manager),
and every call is logged: when it arrived, what it was about, how long the customer waited,
whether he hung up, how long the conversation lasted, who answered, how it ended.

    python -m Workforce_Manager build-dataset          (writes data/call_log.csv and data/days.csv)

Nothing in the model is chosen by hand: forecast, call length and customer patience are all
estimated from this file (see model.py). The last day of each day type is kept apart as a test day.
"""
from __future__ import annotations

import csv
import json
import multiprocessing as mp
import time
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
CALL_LOG = DATA_DIR / "call_log.csv"
DAYS = DATA_DIR / "days.csv"

DAY_TYPES = ("normal", "fin_de_mois", "panne", "campagne", "vague_de_fraude")
DAYS_PER_TYPE = 3                 # 2 training days + 1 test day per day type
OPEN_HOUR, CLOSE_HOUR = 8, 19     # opening hours of the logged days
TODAY_TEAM = dict(seniors=2, juniors=3)   # run_baseline's team: the call center as it is today
SEED0 = 20_000                    # seeds of the logged days (never used by the control room)
SLOT_MIN = 15

CALL_FIELDS = ["day_id", "day_type", "split", "call_id", "arrival_s", "clock_min", "slot", "intent", "category",
               "urgency", "profile", "waited_s", "abandoned", "talk_s", "turns", "advisor_skill", "status",
               "resolved", "escalated", "is_fraudster", "satisfaction"]
DAY_FIELDS = ["day_id", "day_type", "seed", "split", "calls", "hung_up", "incidents"]


def day_seeds(days_per_type: int = DAYS_PER_TYPE) -> list[tuple[str, int, str]]:
    out = []
    for k, day_type in enumerate(DAY_TYPES):
        for d in range(days_per_type):
            out.append((day_type, SEED0 + 100 * k + d, "test" if d == days_per_type - 1 else "train"))
    return out


def is_dataset_seed(seed: int) -> bool:
    return any(seed == s for _, s, _ in day_seeds(20))


def _one_day(args: tuple) -> tuple[dict, list[dict]]:
    from . import run_baseline                       # imported in the worker (Windows spawns processes)
    day_type, seed, split, *hours = args
    open_hour, close_hour = hours if hours else (OPEN_HOUR, CLOSE_HOUR)
    sim = run_baseline(day_type, (close_hour - open_hour) * 60, seed, start_hour=open_hour, **TODAY_TEAM)
    urgency = {q.request_id: q.urgency for q in sim.requests}
    day_id = f"{day_type}-{seed}"
    rows = []
    for r in sorted(sim.results, key=lambda x: x["arrival_s"]):
        clock = open_hour * 60 + r["arrival_s"] / 60
        rows.append(dict(day_id=day_id, day_type=day_type, split=split, call_id=r["request_id"],
                         arrival_s=r["arrival_s"], clock_min=round(clock, 2), slot=int(clock // SLOT_MIN),
                         intent=r["intent"], category=r["category"], urgency=urgency.get(r["request_id"], 1),
                         profile=r["profile"], waited_s=r["waited_s"], abandoned=int(r["abandoned"]),
                         talk_s=r["talk_s"], turns=r["turns"], advisor_skill=r["advisor_skill"] or "",
                         status=r["status"], resolved=int(bool(r["resolved"])), escalated=int(bool(r["escalated"])),
                         is_fraudster=int(bool(r["is_fraudster"])), satisfaction=r["satisfaction"]))
    day = dict(day_id=day_id, day_type=day_type, seed=seed, split=split, calls=len(rows),
               hung_up=sum(x["abandoned"] for x in rows),
               incidents="; ".join(f"{i.name}@{open_hour * 60 + i.start / 60:.0f}min" for i in sim.incidents))
    return day, rows


def _day_worker(args: tuple, part: str) -> None:
    """Runs in its own process: logs one day and saves it at once (data/days/<day_id>.json)."""
    day, rows = _one_day(args)
    tmp = Path(part + ".tmp")
    tmp.write_text(json.dumps({"day": day, "rows": rows}), encoding="utf-8")
    tmp.replace(part)


def build_dataset(workers: int = 5, out_dir: Path = DATA_DIR, days: list | None = None,
                  days_per_type: int = DAYS_PER_TYPE, progress: bool = False, day_timeout_s: float = 300) -> dict:
    """Each day is saved as soon as it is logged, so nothing is lost and a re-run only does the missing days.
    A day that takes longer than day_timeout_s is stopped and skipped (reported), never blocking the others."""
    parts = out_dir / "days"
    parts.mkdir(parents=True, exist_ok=True)
    todo = days or day_seeds(days_per_type)
    part_of = lambda a: parts / f"{a[0]}-{a[1]}.json"  # noqa: E731
    say = (lambda m: print(m, flush=True)) if progress else (lambda m: None)
    waiting = [a for a in todo if not part_of(a).exists()]
    say(f"  {len(todo) - len(waiting)}/{len(todo)} days already logged, {len(waiting)} to do")
    running: dict = {}
    skipped = []
    ctx = mp.get_context("spawn")
    while waiting or running:
        while waiting and len(running) < workers:
            a = waiting.pop(0)
            proc = ctx.Process(target=_day_worker, args=(a, str(part_of(a))), daemon=True)
            proc.start()
            running[proc] = (a, time.time())
        for proc, (a, t0) in list(running.items()):
            if not proc.is_alive():
                proc.join()
                del running[proc]
                ok = part_of(a).exists()
                if not ok:
                    skipped.append(f"{a[0]}-{a[1]} (failed)")
                say(f"  {a[0]}-{a[1]} ({a[2]}): {'logged' if ok else 'FAILED'} in {time.time() - t0:.0f} s")
            elif time.time() - t0 > day_timeout_s:
                proc.terminate()
                proc.join()
                del running[proc]
                skipped.append(f"{a[0]}-{a[1]} (over {day_timeout_s:.0f} s)")
                say(f"  {a[0]}-{a[1]} ({a[2]}): skipped, took more than {day_timeout_s:.0f} s")
        time.sleep(0.5)
    done = [json.loads(part_of(a).read_text(encoding="utf-8")) for a in todo if part_of(a).exists()]
    with open(out_dir / CALL_LOG.name, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, CALL_FIELDS)
        w.writeheader()
        for d in done:
            w.writerows(d["rows"])
    with open(out_dir / DAYS.name, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, DAY_FIELDS)
        w.writeheader()
        w.writerows(d["day"] for d in done)
    return dict(days=len(done), calls=sum(len(d["rows"]) for d in done), skipped=skipped,
                path=str(out_dir / CALL_LOG.name))


_NUM = {"arrival_s", "clock_min", "waited_s", "talk_s", "satisfaction"}
_INT = {"slot", "urgency", "abandoned", "turns", "resolved", "escalated", "is_fraudster"}


def load_call_log(path: Path = CALL_LOG) -> list[dict]:
    if not Path(path).exists():
        raise FileNotFoundError(f"no dataset yet: run  python -m Workforce_Manager build-dataset  ({path})")
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            for k in _NUM:
                r[k] = float(r[k])
            for k in _INT:
                r[k] = int(r[k])
            rows.append(r)
    return rows


def load_days(path: Path = DAYS) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return [dict(r, seed=int(r["seed"]), calls=int(r["calls"]), hung_up=int(r["hung_up"])) for r in csv.DictReader(f)]
