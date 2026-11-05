"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

const WFM_API = process.env.NEXT_PUBLIC_WFM_API_URL || "http://127.0.0.1:8002";
const ADVISOR_API = "/api/advisor";

type Tab = "room" | "approvals";
type Ev = { t: number; kind: string; [key: string]: unknown };
type Room = {
  id: string; scenario: string; start_hour: number; duration_s: number; end_s: number; callers: number; seed: number;
  advisors: { name: string; skill: string }[]; events: Ev[]; names: Record<string, string>;
  goal: { sla_s: number; sla_target_pct: number; reached_pct: number };
  gate: { thieves: number; held: number; held_thieves: number; held_genuine: number; thieves_through: number; thieves_refused_by_advisor: number; thieves_hung_up: number; precision_pct: number | null; recall_pct: number | null };
  learned: { train_days: number; logged_calls: number; aht_senior_s: number; aht_junior_s: number; aht_all_s: number; aht_n: Record<string, number>;
    patience_median_s: number | null; patience_n: { customers: number; hung_up: number }; offer_after_s: number;
    test_day: string | null; test_total_actual: number | null; test_total_forecast: number | null; test_total_error_pct: number | null; test_mae: number | null; test_mean_per_slot: number | null };
  summary: { answered: number; hung_up: number; avg_wait_s: number; thieves: number; thieves_succeeded: number; held: number; advisors_called_in: number;
    callbacks_offered: number; callbacks_accepted: number; called_back: number; avg_callback_min: number; callback_tomorrow: number; alerts: number };
  team_check: { ok: boolean; have_seniors: number; have_juniors: number; need_seniors: number; need_juniors: number; need_total: number;
    peak_start_s: number; short_intervals: number; intervals: number; calls_over_capacity: number; forecast_calls: number; aht_senior_s: number; aht_junior_s: number };
};
type Waiting = { call: string; name: string; label: string; risky: boolean; since: number };
type Desk = { name: string; skill: string; state: "off" | "free" | "busy"; leaving: boolean; call?: string; caller?: string; label?: string; risky?: boolean; since?: number };
type Line = { t: number; tag: string; text: string; tone: "good" | "bad" | "warn" | "info"; call?: string };
type CallDetail = { name: string; reason: string; status: string; advisor: string | null; advisor_skill: string | null; waited_s: number; thief: boolean; held: boolean; transcript: { role: string; text: string }[] };
type Review = { review_id: string; client_name: string; requested: string; category_label: string; signals: string[]; evidence: string; status: "pending" | "approved" | "denied"; decided_by: string | null;
  created_at: string; decided_at: string | null; risk_score: number | null; intent_label?: string };
type ReviewDetail = Review & { transcript: { role: string; text: string }[]; truth: { is_fraudster: boolean; decision_correct: boolean } | null };
type CashyApproval = { id: string; action: string; message: string; status: string; customer_id: number };

const DAYS: Record<string, string> = {
  fin_de_mois: "End of the month (many calls)", vague_de_fraude: "Fraud wave (many thieves)", panne: "App outage (angry customers)",
  normal: "Normal day", campagne: "Marketing campaign",
};
const SPEEDS: [string, number][] = [["Slow", 20], ["Normal", 60], ["Fast", 240]];
const OUTCOME: Record<string, string> = { resolved: "solved", escalated: "sent to a supervisor", hung_up: "customer hung up", max_turns: "too long", closed: "closed", abandoned: "hung up while waiting" };
const FIELD_WORDS: Record<string, string> = { phone: "phone number", email: "e-mail", address: "address", limit: "card limit", beneficiary: "beneficiary", name: "name", online_access: "online password", "money transfer": "send money" };

async function call<T>(base: string, path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try { response = await fetch(`${base}${path}`, init); } catch { throw new Error("A service is not running. Start the project with .\\Agent_Communication\\launch_all.ps1"); }
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) throw new Error(typeof data?.detail === "string" ? data.detail : "Something went wrong.");
  return data as T;
}
const post = (body: unknown): RequestInit => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

const ago = (iso?: string | null) => {
  if (!iso) return "";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" }) + " " + new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
};
const clock = (startHour: number, t: number) => {
  const m = Math.floor(startHour * 60 + t / 60);
  return `${String(Math.floor(m / 60) % 24).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
};
const mins = (s: number) => (s < 60 ? `${Math.round(s)} s` : `${Math.floor(s / 60)} min ${String(Math.round(s % 60)).padStart(2, "0")}`);
const deskName = (d: { name: string; skill: string }) => `${d.skill === "senior" ? "Senior" : "Junior"} ${d.name.slice(1)}`;
const plainRequest = (r: string) => r.replace("change ", "change their ").replace("; ", " and ").replace(/money transfer|[a-z_]+/g, (w) => FIELD_WORDS[w] || w);
const plainReason = (s: string) => s.replace(/^\+\d+\s*/, "").replace("identity details do not match the CRM", "the identity details do not match the bank's records")
  .replace("sensitive request without a complete identity check", "identity was not fully checked")
  .replace("contact / access details change (account-takeover pattern)", "changing contact details is how thieves take over an account")
  .replace("time pressure", "the customer pushes to do it fast").replace("caller pushes to skip the checks", "the customer refuses the security checks")
  .replace("sensitive request", "it changes the account or moves money").replace("active fraud alert", "a fraud alert is active");

/** Rebuilds the whole call center at time `now` from the event log. */
function stateAt(room: Room, now: number) {
  const desks = new Map<string, Desk>(room.advisors.map((a) => [a.name, { ...a, state: "off", leaving: false }]));
  const queue = new Map<string, Waiting>();
  const arrivals = new Map<string, Waiting>();
  const feed: Line[] = [];
  const count = { answered: 0, hungUp: 0, held: 0, calledIn: 0, sentHome: 0, calledBack: 0, picked: 0, onTime: 0 };
  const callbacks = new Map<string, Waiting>();
  let plan = { seniors: 0, juniors: 0 };
  const incidents = new Set<string>();
  for (const e of room.events) {
    if (e.t > now) break;
    const t = e.t;
    switch (e.kind) {
      case "plan":
        plan = { seniors: e.seniors as number, juniors: e.juniors as number };
        feed.push({ t, tag: "plan", tone: "info", text: `Planned for this quarter hour: ${plan.seniors} senior${plan.seniors > 1 ? "s" : ""} + ${plan.juniors} junior${plan.juniors > 1 ? "s" : ""} (about ${Math.round(e.forecast as number)} calls expected).` });
        break;
      case "incident":
        if (e.on) { incidents.add(e.label as string); feed.push({ t, tag: "incident", tone: "bad", text: `${e.label} started: about ${(e.mult as number).toFixed(1)}× more calls.` }); }
        else { incidents.delete(e.label as string); feed.push({ t, tag: "back to normal", tone: "good", text: `${e.label} is over.` }); }
        break;
      case "decision":
        if (e.add) { count.calledIn += 1; feed.push({ t, tag: "staff +1", tone: "warn", text: `${e.waiting} customer${(e.waiting as number) > 1 ? "s" : ""} waiting (longest ${e.longest} s): the Workforce Manager calls in one more ${String(e.text).includes("senior") ? "senior" : "junior"}.` }); }
        else { count.sentHome += 1; feed.push({ t, tag: "staff −1", tone: "info", text: `Nobody is waiting: one ${String(e.text).includes("senior") ? "senior" : "junior"} goes home to save money.` }); }
        break;
      case "on": { const d = desks.get(e.advisor as string)!; d.state = d.state === "busy" ? "busy" : "free"; d.leaving = false; break; }
      case "stay": desks.get(e.advisor as string)!.leaving = false; break;
      case "leaving": desks.get(e.advisor as string)!.leaving = true; break;
      case "off": { const d = desks.get(e.advisor as string)!; Object.assign(d, { state: "off", leaving: false, call: undefined }); break; }
      case "arrive": {
        const w = { call: e.call as string, name: e.name as string, label: e.label as string, risky: e.category === "security", since: t };
        queue.set(w.call, w); arrivals.set(w.call, w);
        break;
      }
      case "assign": {
        const w = arrivals.get(e.call as string);
        queue.delete(e.call as string);
        const d = desks.get(e.advisor as string)!;
        Object.assign(d, { state: "busy", call: e.call, caller: w?.name, label: w?.label, risky: w?.risky, since: t });
        count.picked += 1;
        if ((e.waited as number) <= room.goal.sla_s) count.onTime += 1;
        const reason = String(e.reason);
        if (reason.startsWith("security")) feed.push({ t, tag: "routing", tone: "good", call: e.call as string, text: `${w?.name} wants “${w?.label}”, a risky call: sent to ${deskName(d)}, who checks identity carefully.` });
        else if (reason.startsWith("overflow")) feed.push({ t, tag: "overflow", tone: "warn", call: e.call as string, text: `${w?.name} (risky) waited ${e.waited} s with no senior free: given to ${deskName(d)}, protected by the approval check.` });
        break;
      }
      case "end": {
        const d = desks.get(e.advisor as string)!;
        Object.assign(d, { state: "free", call: undefined });
        count.answered += 1;
        if (e.held) { count.held += 1; feed.push({ t, tag: "held", tone: "warn", call: e.call as string, text: `${room.names[e.call as string]} asked for something risky. ${deskName(d)} wanted to do it, but the Workforce Manager stopped it before anything changed: it now waits for a human decision.` }); }
        break;
      }
      case "abandon": {
        queue.delete(e.call as string);
        if (e.advisor) Object.assign(desks.get(e.advisor as string)!, { state: "free", call: undefined });
        count.hungUp += 1;
        feed.push({ t, tag: "hung up", tone: "bad", call: e.call as string, text: `${room.names[e.call as string]} hung up after waiting ${mins(e.waited as number)}.` });
        break;
      }
      case "offer": {
        const w = arrivals.get(e.call as string);
        if (e.accepted) {
          queue.delete(e.call as string);
          if (w) callbacks.set(w.call, { ...w, since: t });
          feed.push({ t, tag: "call-back offer", tone: "warn", call: e.call as string, text: `${w?.name} would wait about ${mins(e.estimated as number)}. The Workforce Manager offers a call-back instead of waiting on hold: accepted, ${w?.name} hangs up and will be called back.` });
        } else feed.push({ t, tag: "call-back offer", tone: "info", call: e.call as string, text: `${w?.name} was offered a call-back (about ${mins(e.estimated as number)} wait) but prefers to stay on the line.` });
        break;
      }
      case "callback": {
        const w = callbacks.get(e.call as string) || arrivals.get(e.call as string);
        callbacks.delete(e.call as string);
        const d = desks.get(e.advisor as string)!;
        Object.assign(d, { state: "busy", call: e.call, caller: w?.name, label: `call-back · ${w?.label}`, risky: false, since: t });
        count.calledBack += 1;
        feed.push({ t, tag: "call-back", tone: "good", call: e.call as string, text: `Nobody is on hold now, so ${deskName(d)} calls back ${w?.name} (asked ${mins(e.after as number)} ago).` });
        break;
      }
      case "alert":
        feed.push({ t, tag: "overload", tone: "bad", text: `Overloaded: ${e.waiting} on hold (longest ${mins(e.longest as number)}) and all ${e.working} advisors are already working, no reserve left. Simple callers are offered a call-back; risky and urgent calls keep priority. The team is too small for this day.` });
        break;
    }
  }
  return { desks: [...desks.values()], queue: [...queue.values()], callbacks: [...callbacks.values()], feed: feed.reverse().slice(0, 60), count, plan, incidents: [...incidents] };
}

export default function WorkforcePage() {
  const [tab, setTab] = useState<Tab>("room");
  const [error, setError] = useState("");

  // control room
  const [day, setDay] = useState("fin_de_mois");
  const [start, setStart] = useState(9);
  const [hours, setHours] = useState(2);
  const [seniors, setSeniors] = useState(4);
  const [juniors, setJuniors] = useState(6);
  const [loading, setLoading] = useState(false);
  const [room, setRoom] = useState<Room | null>(null);
  const [now, setNow] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(60);
  const [openCall, setOpenCall] = useState<CallDetail | null>(null);
  const last = useRef<number | null>(null);

  // approvals (live conversations + Cashy)
  const [name, setName] = useState("");
  const [reviews, setReviews] = useState<Review[]>([]);
  const [cashy, setCashy] = useState<CashyApproval[]>([]);
  const [openId, setOpenId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ReviewDetail | null>(null);

  useEffect(() => {
    const t = new URLSearchParams(window.location.search).get("tab");
    if (t === "approvals" || t === "reviews") setTab("approvals");
    setName(window.localStorage.getItem("wfm-reviewer") || "");
  }, []);

  // playback clock
  useEffect(() => {
    if (!playing || !room) return;
    let frame = 0;
    const step = (ts: number) => {
      if (last.current !== null) {
        const dt = (ts - last.current) / 1000;
        setNow((n) => {
          const next = Math.min(room.end_s, n + dt * speed);
          if (next >= room.end_s) setPlaying(false);
          return next;
        });
      }
      last.current = ts;
      frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => { cancelAnimationFrame(frame); last.current = null; };
  }, [playing, room, speed]);

  const view = useMemo(() => (room ? stateAt(room, now) : null), [room, now]);

  async function startDay() {
    setLoading(true);
    setError("");
    setPlaying(false);
    try {
      const data = await call<Room>(WFM_API, "/api/control-room", post({ scenario: day, start_hour: start, duration_min: hours * 60, seniors, juniors }));
      setRoom(data);
      setNow(0);
      setPlaying(true);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  async function showCall(id?: string) {
    if (!id || !room) return;
    try { setOpenCall(await call<CallDetail>(WFM_API, `/api/control-room/${room.id}/calls/${id}`)); } catch (e) { setError((e as Error).message); }
  }

  // approvals polling
  const loadApprovals = useCallback(() => {
    call<Review[]>(WFM_API, "/api/reviews?source=live&status=all&limit=30").then(setReviews).catch(() => setReviews([]));
    call<CashyApproval[]>(ADVISOR_API, "/api/admin/approvals?status=pending").then(setCashy).catch(() => setCashy([]));
  }, []);
  useEffect(() => {
    loadApprovals();
    const timer = window.setInterval(loadApprovals, 3000);
    return () => window.clearInterval(timer);
  }, [loadApprovals]);
  const waiting = reviews.filter((r) => r.status === "pending").length + cashy.filter((c) => c.status === "pending").length;

  async function decide(r: Review, approved: boolean) {
    if (!name.trim()) { setError("Write your name first: every decision is signed."); return; }
    setError("");
    try {
      window.localStorage.setItem("wfm-reviewer", name.trim());
      const updated = await call<ReviewDetail>(WFM_API, `/api/reviews/${r.review_id}/decision`, post({ approved, by: name.trim(), note: "" }));
      setOpenId(updated.review_id); setDetail(updated); loadApprovals();
    } catch (e) { setError((e as Error).message); }
  }
  async function openReview(id: string) {
    if (openId === id) { setOpenId(null); return; }
    setOpenId(id); setDetail(null);
    try { setDetail(await call<ReviewDetail>(WFM_API, `/api/reviews/${id}`)); } catch (e) { setError((e as Error).message); }
  }
  async function clearHistory() {
    const n = reviews.filter((r) => r.status !== "pending").length;
    if (!n || !window.confirm(`Clear ${n} decided request${n > 1 ? "s" : ""} from the history?\n\nRequests still waiting are kept. The signed decisions stay recorded in the database.`)) return;
    try { await call(WFM_API, "/api/reviews/clear-history", post({ by: name.trim() || "dashboard", source: "live" })); setOpenId(null); loadApprovals(); } catch (e) { setError((e as Error).message); }
  }
  async function decideCashy(c: CashyApproval, approved: boolean) {
    try { await call(ADVISOR_API, `/api/admin/approvals/${c.id}/decision`, post({ approved })); loadApprovals(); } catch (e) { setError((e as Error).message); }
  }

  const box = "rounded-[22px] border border-[#e5e9e3] bg-white p-4 shadow-panel sm:p-5";
  const field = "mt-1 w-full rounded-xl border border-[#e3e8e1] bg-[#fbfcfa] px-3 py-2 text-[13px] outline-none focus:border-forest";
  const onDuty = view ? view.desks.filter((d) => d.state !== "off").length : 0;
  const finished = room && now >= room.end_s;

  return (
    <main className="min-h-screen bg-paper text-ink">
      <header className="border-b border-[#e7eae4] bg-white/90">
        <div className="mx-auto flex max-w-[1300px] items-center justify-between gap-4 px-5 py-3 sm:px-8">
          <Link href="/" className="flex items-center gap-3">
            <div className="grid h-10 w-10 place-items-center rounded-2xl bg-forest text-xl font-semibold text-white">r</div>
            <div><div className="text-[17px] font-semibold tracking-[-.04em]">relay<span className="text-forest">.</span></div><div className="text-[10px] font-semibold uppercase tracking-[.2em] text-[#89938d]">Workforce manager</div></div>
          </Link>
          <div className="flex gap-2 rounded-2xl bg-[#eceee9] p-1">
            <button type="button" onClick={() => setTab("room")} className={`rounded-xl px-4 py-2 text-[13px] font-semibold ${tab === "room" ? "bg-white text-forest shadow-sm" : "text-[#6c786f]"}`}>Control room</button>
            <button type="button" onClick={() => setTab("approvals")} className={`rounded-xl px-4 py-2 text-[13px] font-semibold ${tab === "approvals" ? "bg-white text-forest shadow-sm" : waiting ? "text-amber-700" : "text-[#6c786f]"}`}>Approvals{waiting ? ` (${waiting})` : ""}</button>
          </div>
          <a href="/" className="rounded-xl border border-[#e6ebe5] bg-white px-3 py-2 text-[12px] font-semibold text-[#56645b] hover:text-forest">← Conversation page</a>
        </div>
      </header>

      <div className="mx-auto max-w-[1300px] px-5 py-6 sm:px-8">
        {error && <p role="alert" className="mb-4 rounded-xl bg-rose-50 px-4 py-3 text-[13px] text-rose-700">{error}</p>}

        {tab === "room" && (
          <>
            {/* setup */}
            <section className={`${box} flex flex-wrap items-end gap-3`}>
              <div className="mr-auto max-w-sm">
                <h1 className="text-[20px] font-semibold tracking-[-.03em]">Watch the Workforce Manager run the call center</h1>
                <p className="mt-1 text-[12px] leading-5 text-[#7d887f]">Customers call in, wait in line, and get an advisor. The Workforce Manager decides who works, who takes which call, and stops risky requests.</p>
              </div>
              <label className="text-[11px] font-semibold text-[#56645b]">Day<select className={field} value={day} onChange={(e) => setDay(e.target.value)}>{Object.entries(DAYS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
              <label className="text-[11px] font-semibold text-[#56645b]">Starts at<select className={field} value={start} onChange={(e) => setStart(Number(e.target.value))}>{[8, 9, 10, 11, 12, 13, 14, 15].map((h) => <option key={h} value={h}>{h}:00</option>)}</select></label>
              <label className="text-[11px] font-semibold text-[#56645b]">Lasts<select className={field} value={hours} onChange={(e) => setHours(Number(e.target.value))}>{[1, 2, 3, 4].map((h) => <option key={h} value={h}>{h} hour{h > 1 ? "s" : ""}</option>)}</select></label>
              <label className="w-20 text-[11px] font-semibold text-[#56645b]">Seniors<input type="number" min={1} max={20} className={field} value={seniors} onChange={(e) => setSeniors(Number(e.target.value))} /></label>
              <label className="w-20 text-[11px] font-semibold text-[#56645b]">Juniors<input type="number" min={0} max={30} className={field} value={juniors} onChange={(e) => setJuniors(Number(e.target.value))} /></label>
              <button type="button" onClick={() => void startDay()} disabled={loading} className="rounded-xl bg-forest px-5 py-2.5 text-[13px] font-semibold text-white hover:bg-[#0a3f32] disabled:opacity-50">{loading ? "Preparing the day…" : room ? "▶ New day" : "▶ Start the day"}</button>
            </section>

            {!room && !loading && (
              <section className={`${box} mt-5 py-14 text-center`}>
                <p className="mt-2 text-[15px] font-semibold">Choose a day and press “Start the day”</p>
                <p className="mx-auto mt-1 max-w-lg text-[12px] leading-6 text-[#7d887f]">You will watch, minute by minute: the customers waiting, each advisor at work, and every decision the Workforce Manager takes, explained in plain words. Click a line to read that customer’s conversation.</p>
              </section>
            )}

            {room && view && (
              <>
                {/* clock + controls */}
                <section className="mt-5 flex flex-wrap items-center gap-4 rounded-[22px] bg-[#16302a] px-5 py-4 text-white shadow-panel">
                  <div><div className="text-[10px] font-semibold uppercase tracking-[.2em] text-[#9cc5b1]">{DAYS[room.scenario]?.split(" (")[0]}</div><div className="text-[40px] font-semibold tabular-nums leading-none tracking-[-.04em]">{clock(room.start_hour, now)}</div></div>
                  <div className="flex gap-1.5">
                    <button type="button" onClick={() => { if (finished) setNow(0); setPlaying(!playing); }} className="rounded-lg bg-white/15 px-3 py-2 text-[13px] font-semibold hover:bg-white/25">{playing ? "⏸ Pause" : finished ? "↺ Replay" : "▶ Play"}</button>
                    {SPEEDS.map(([label, v]) => <button key={label} type="button" onClick={() => setSpeed(v)} className={`rounded-lg px-3 py-2 text-[12px] font-semibold ${speed === v ? "bg-white text-[#16302a]" : "bg-white/10 hover:bg-white/20"}`}>{label}</button>)}
                    <button type="button" onClick={() => { setNow(room.end_s); setPlaying(false); }} className="rounded-lg bg-white/10 px-3 py-2 text-[12px] font-semibold hover:bg-white/20">⏭ End</button>
                  </div>
                  <input type="range" min={0} max={room.end_s} value={now} onChange={(e) => { setNow(Number(e.target.value)); setPlaying(false); }} className="min-w-[160px] flex-1 accent-[#9cc5b1]" aria-label="Time" />
                  {view.incidents.map((i) => <span key={i} className="animate-pulse rounded-full bg-rose-500 px-3 py-1 text-[12px] font-semibold">{i}</span>)}
                </section>

                {/* is the team big enough? */}
                {(() => {
                  const c = room.team_check;
                  if (c.ok) return (
                    <section className="mt-4 rounded-[22px] border border-emerald-200 bg-emerald-50 px-5 py-3 text-[13px] leading-6">
                      <b>Your team is big enough for this day.</b> About {c.forecast_calls} calls are expected; at the busiest moment ({clock(room.start_hour, c.peak_start_s)}) the Workforce Manager needs {c.need_seniors} seniors + {c.need_juniors} juniors, and you have {c.have_seniors} + {c.have_juniors}. The ones not needed stay at home as a reserve.
                    </section>);
                  return (
                    <section className="mt-4 rounded-[22px] border border-rose-200 bg-rose-50 px-5 py-4 text-[13px] leading-6">
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <div className="max-w-3xl">
                          <b className="text-rose-800">Your team is too small for this day.</b> About <b>{c.forecast_calls}</b> calls are expected. At the busiest moment ({clock(room.start_hour, c.peak_start_s)}) the Workforce Manager needs <b>{c.need_seniors} seniors + {c.need_juniors} juniors</b>; you have <b>{c.have_seniors} + {c.have_juniors}</b>. Short-staffed in {c.short_intervals} of {c.intervals} quarter hours.
                          {c.calls_over_capacity > 0 && <> Your team can physically answer about <b>{c.forecast_calls - c.calls_over_capacity}</b> calls (one call ≈ {c.aht_junior_s}–{c.aht_senior_s} s): about <b>{c.calls_over_capacity}</b> calls are more than it can handle, whatever the plan.</>}
                          <div className="mt-1 text-[12px] text-[#6b4b4b]">What the Workforce Manager does to limit the damage: every advisor works · risky and urgent calls are answered first · simple callers facing a long wait are offered a call-back instead of hanging up · it raises an alert when no reserve is left.</div>
                        </div>
                        <button type="button" onClick={() => { setSeniors(Math.min(20, c.need_seniors)); setJuniors(Math.min(30, c.need_juniors)); }} className="rounded-xl bg-rose-700 px-4 py-2 text-[12px] font-semibold text-white hover:bg-rose-800">Use the team it recommends ({c.need_seniors} + {c.need_juniors}), then ▶ New day</button>
                      </div>
                    </section>);
                })()}

                {/* what was learned */}
                <details className="mt-3 rounded-[22px] border border-[#e5e9e3] bg-white px-5 py-3 text-[13px] leading-6 shadow-panel" open>
                  <summary className="cursor-pointer font-semibold">Learned from the call log: {room.learned.logged_calls.toLocaleString()} past calls, {room.learned.train_days} past days of this type (nothing set by hand)</summary>
                  <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                    <div className="rounded-xl bg-[#f6f8f5] px-3 py-2"><div className="text-[11px] font-semibold text-[#7d887f]">CALLS PER 15 MIN</div>Average of the {room.learned.train_days} training days, by time of day. Checked on a test day it never saw: forecast {room.learned.test_total_forecast} calls, real {room.learned.test_total_actual} (<b>{room.learned.test_total_error_pct}%</b> off; ± {room.learned.test_mae} calls per quarter hour).</div>
                    <div className="rounded-xl bg-[#f6f8f5] px-3 py-2"><div className="text-[11px] font-semibold text-[#7d887f]">CALL LENGTH</div>Risky call with a senior: <b>{room.learned.aht_senior_s} s</b> ({room.learned.aht_n.risky_by_senior} calls). Simple call with a junior: <b>{room.learned.aht_junior_s} s</b> ({room.learned.aht_n.simple_by_junior} calls).</div>
                    <div className="rounded-xl bg-[#f6f8f5] px-3 py-2"><div className="text-[11px] font-semibold text-[#7d887f]">CUSTOMER PATIENCE</div>Half of the customers hang up after <b>{room.learned.patience_median_s ?? "—"} s</b> on hold (Kaplan-Meier on {room.learned.patience_n.customers} waits, {room.learned.patience_n.hung_up} hang-ups).</div>
                    <div className="rounded-xl bg-[#f6f8f5] px-3 py-2"><div className="text-[11px] font-semibold text-[#7d887f]">RULES DERIVED</div>Team size: Erlang-C for your goal ({room.goal.sla_target_pct}% within {room.goal.sla_s} s). Call in a reserve when a wait passes {room.goal.sla_s} s. Offer a call-back when the wait is over {room.learned.offer_after_s} s.</div>
                  </div>
                </details>

                {/* counters */}
                <section className="mt-4 grid grid-cols-2 gap-2 text-center sm:grid-cols-4 lg:grid-cols-7">
                  {[
                    ["waiting now", view.queue.length, view.queue.length > 3 ? "text-rose-700" : ""],
                    ["advisors working", onDuty, ""],
                    ["customers served", view.count.answered, "text-forest"],
                    ["hung up (waited too long)", view.count.hungUp, view.count.hungUp ? "text-rose-700" : ""],
                    ["to call back", view.callbacks.length, view.callbacks.length ? "text-amber-700" : ""],
                    ["risky requests stopped", view.count.held, "text-amber-700"],
                    [`answered within ${room.goal.sla_s} s (goal ${room.goal.sla_target_pct}%)`, `${view.count.picked ? Math.round(100 * view.count.onTime / view.count.picked) : 100}%`,
                      view.count.picked && 100 * view.count.onTime / view.count.picked < room.goal.sla_target_pct ? "text-rose-700" : "text-forest"],
                  ].map(([label, value, tone]) => (
                    <div key={label as string} className="rounded-2xl border border-[#e5e9e3] bg-white px-2 py-3">
                      <div className={`text-[24px] font-semibold tabular-nums ${tone}`}>{value}</div>
                      <div className="text-[11px] text-[#7d887f]">{label as string}</div>
                    </div>
                  ))}
                </section>

                <div className="mt-4 grid gap-4 lg:grid-cols-[250px_minmax(0,1fr)_380px]">
                  {/* queue */}
                  <section className={box}>
                    <h2 className="text-[14px] font-semibold">Waiting line ({view.queue.length})</h2>
                    <p className="text-[11px] text-[#9aa39d]">Customers on hold, oldest first</p>
                    <div className="mt-3 space-y-1.5">
                      {view.queue.length === 0 && <p className="rounded-xl bg-[#f3f7f2] px-3 py-3 text-center text-[12px] text-forest">Nobody is waiting</p>}
                      {view.queue.slice(0, 14).map((w) => {
                        const waited = now - w.since;
                        return (
                          <div key={w.call} className={`rounded-xl px-3 py-2 text-[12px] ${waited > 60 ? "bg-rose-50" : "bg-[#f6f8f5]"}`}>
                            <div className="flex justify-between font-semibold"><span>{w.risky && <span className="mr-1 rounded bg-amber-200 px-1 py-px text-[9px] font-bold uppercase tracking-wide text-amber-900">risky</span>}{w.name}</span><span className={`tabular-nums ${waited > 60 ? "text-rose-700" : "text-[#7d887f]"}`}>{mins(waited)}</span></div>
                            <div className="text-[11px] text-[#7d887f]">{w.label}</div>
                          </div>
                        );
                      })}
                      {view.queue.length > 14 && <p className="text-center text-[11px] text-[#7d887f]">+ {view.queue.length - 14} more</p>}
                    </div>
                    {(view.callbacks.length > 0 || view.count.calledBack > 0) && (
                      <div className="mt-4 border-t border-[#eef1ec] pt-3">
                        <h3 className="text-[13px] font-semibold">To call back ({view.callbacks.length})</h3>
                        <p className="text-[11px] text-[#9aa39d]">Hung up on purpose, called back when nobody is on hold · {view.count.calledBack} already called back</p>
                        <div className="mt-2 space-y-1">
                          {view.callbacks.slice(0, 8).map((w) => (
                            <div key={w.call} className="flex justify-between rounded-lg bg-amber-50 px-3 py-1.5 text-[11px]"><span className="font-semibold">{w.name}</span><span className="tabular-nums text-[#7d887f]">{mins(now - w.since)}</span></div>
                          ))}
                          {view.callbacks.length > 8 && <p className="text-center text-[11px] text-[#7d887f]">+ {view.callbacks.length - 8} more</p>}
                        </div>
                      </div>
                    )}
                  </section>

                  {/* desks */}
                  <section className={box}>
                    <div className="flex flex-wrap items-baseline justify-between gap-2">
                      <h2 className="text-[14px] font-semibold">Advisors</h2>
                      <span className="text-[11px] text-[#7d887f]">Plan for now: {view.plan.seniors} seniors + {view.plan.juniors} juniors · <span className="mx-0.5 inline-grid h-4 w-4 place-items-center rounded bg-forest text-[9px] font-bold text-white">S</span> senior = careful, checks identity · <span className="mx-0.5 inline-grid h-4 w-4 place-items-center rounded bg-sky-600 text-[9px] font-bold text-white">J</span> junior = fast</span>
                    </div>
                    <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-4">
                      {view.desks.map((d) => (
                        <button type="button" key={d.name} onClick={() => void showCall(d.call)} disabled={!d.call}
                          className={`rounded-2xl border px-3 py-2.5 text-left transition ${d.state === "off" ? "border-dashed border-[#dfe4dd] bg-[#f6f7f4] text-[#a3aca5]" : d.state === "free" ? "border-emerald-200 bg-emerald-50" : d.risky ? "border-amber-300 bg-amber-50" : "border-sky-200 bg-sky-50"}`}>
                          <div className="flex items-center justify-between text-[12px] font-semibold"><span><span className={`mr-1.5 inline-grid h-4 w-4 place-items-center rounded text-[9px] font-bold text-white ${d.skill === "senior" ? "bg-forest" : "bg-sky-600"}`}>{d.skill === "senior" ? "S" : "J"}</span>{deskName(d)}</span>{d.leaving && <span className="text-[10px] text-[#7d887f]">last call</span>}</div>
                          <div className="mt-1 text-[11px] leading-4">
                            {d.state === "off" ? "at home" : d.state === "free" ? <span className="font-semibold text-forest">free, ready</span> : <>{d.risky && <span className="mr-1 rounded bg-amber-200 px-1 py-px text-[9px] font-bold uppercase tracking-wide text-amber-900">risky</span>}<b>{d.caller}</b><br /><span className="text-[#5f6d64]">{d.label} · {mins(now - (d.since || now))}</span></>}
                          </div>
                        </button>
                      ))}
                    </div>
                  </section>

                  {/* feed */}
                  <section className={box}>
                    <h2 className="text-[14px] font-semibold">What the Workforce Manager is doing</h2>
                    <p className="text-[11px] text-[#9aa39d]">Newest first · click a line to read the conversation</p>
                    <ul className="mt-3 max-h-[520px] space-y-1.5 overflow-y-auto pr-1">
                      {view.feed.length === 0 && <li className="text-[12px] text-[#7d887f]">The day is starting…</li>}
                      {view.feed.map((l, k) => (
                        <li key={`${l.t}-${k}`}>
                          <button type="button" onClick={() => void showCall(l.call)} disabled={!l.call} className={`w-full rounded-xl px-3 py-2 text-left text-[12px] leading-5 ${l.tone === "bad" ? "bg-rose-50" : l.tone === "warn" ? "bg-amber-50" : l.tone === "good" ? "bg-emerald-50" : "bg-[#f6f8f5]"} ${l.call ? "hover:ring-1 hover:ring-[#cbd8cc]" : ""}`}>
                            <span className="mr-1 font-semibold tabular-nums text-[#7d887f]">{clock(room.start_hour, l.t)}</span> <span className="mr-1 rounded bg-white/80 px-1.5 py-px text-[9px] font-bold uppercase tracking-wider text-[#56645b] ring-1 ring-black/5">{l.tag}</span> {l.text}
                          </button>
                        </li>
                      ))}
                    </ul>
                  </section>
                </div>

                {finished && (
                  <section className={`${box} mt-4`}>
                    <h2 className="text-[16px] font-semibold">End of the day: {room.callers} customers called</h2>
                    <ul className="mt-2 list-disc space-y-1 pl-5 text-[14px] leading-7 marker:text-[#9aa39d]">
                      <li><b>{room.summary.answered}</b> were served; <b>{room.summary.hung_up}</b> hung up; they waited <b>{room.summary.avg_wait_s} s</b> on average.</li>
                      <li>The Workforce Manager called in a reserve advisor <b>{room.summary.advisors_called_in}</b> time(s) when the line grew.</li>
                      {room.summary.callbacks_offered > 0 && <li><b>{room.summary.callbacks_offered}</b> customers were offered a call-back instead of a long wait; <b>{room.summary.callbacks_accepted}</b> accepted. <b>{room.summary.called_back}</b> were called back (after {room.summary.avg_callback_min} min on average){room.summary.callback_tomorrow > 0 && <>; <b className="text-rose-700">{room.summary.callback_tomorrow}</b> could not be called back before closing and are first on tomorrow&apos;s list</>}.</li>}
                      {room.summary.alerts > 0 && <li>The Workforce Manager raised <b>{room.summary.alerts}</b> overload alert(s): no reserve advisor left. Recommended team for this day: <b>{room.team_check.need_seniors} seniors + {room.team_check.need_juniors} juniors</b>.</li>}
                      <li>Goal {room.goal.sla_target_pct}% answered within {room.goal.sla_s} s: <b className={room.goal.reached_pct >= room.goal.sla_target_pct ? "text-forest" : "text-rose-700"}>{room.goal.reached_pct}% reached</b>.</li>
                      <li><b>{room.gate.held}</b> risky requests were stopped before anything was changed and wait for a human decision.</li>
                      <li>Checked afterwards against the simulator&apos;s hidden labels (never used to decide): <b>{room.gate.held_thieves}</b> of the stopped requests were thieves, <b>{room.gate.held_genuine}</b> were genuine customers{room.gate.precision_pct !== null && <> (precision <b>{room.gate.precision_pct}%</b>)</>}. Thieves who got what they wanted without being stopped: <b className={room.gate.thieves_through ? "text-rose-700" : "text-forest"}>{room.gate.thieves_through}</b>{room.gate.recall_pct !== null && <> (recall <b>{room.gate.recall_pct}%</b>)</>}. Out of {room.gate.thieves} thieves, {room.gate.thieves_refused_by_advisor} were refused by the advisor and {room.gate.thieves_hung_up} hung up.</li>
                    </ul>
                  </section>
                )}
                <p className="mt-3 text-[11px] leading-5 text-[#9aa39d]">Where this comes from: the call log (Workforce_Manager/data/call_log.csv) records past days of the project&apos;s call center as it works today (Client Simulator customers, the project&apos;s advisors, a fixed team of 2 seniors + 3 juniors). This replay is a new day the model never saw (seed {room.seed}). Stopped requests wait for a person: your own decisions on live calls are in “Approvals”.</p>
              </>
            )}
          </>
        )}

        {tab === "approvals" && (() => {
          const pending = reviews.filter((r) => r.status === "pending");
          const history = reviews.filter((r) => r.status !== "pending");
          const cashyPending = cashy.filter((c) => c.status === "pending");
          const approvedN = history.filter((r) => r.status === "approved").length;
          const refusedN = history.length - approvedN;
          const conversation = (r: Review) => {
            const d = openId === r.review_id ? detail : null;
            if (openId !== r.review_id) return null;
            if (!d) return <p className="mt-3 text-[12px] text-[#7d887f]">Loading the conversation…</p>;
            return (
              <div className="mt-3 space-y-2">
                {d.truth && <p className={`rounded-xl px-4 py-2.5 text-[13px] font-semibold ${d.truth.decision_correct ? "bg-emerald-50 text-forest" : "bg-rose-50 text-rose-700"}`}>{d.truth.decision_correct ? "Good decision." : "Wrong decision."} This caller was {d.truth.is_fraudster ? "a thief" : "the real customer"}.</p>}
                <div className="max-h-[320px] space-y-2 overflow-y-auto rounded-2xl bg-[#f6f8f5] p-3">
                  {d.transcript.length === 0 && <p className="text-center text-[12px] text-[#7d887f]">No conversation saved for this request.</p>}
                  {d.transcript.map((m, k) => (
                    <div key={k} className={`flex ${m.role === "client" ? "" : "justify-end"}`}>
                      <p className={`max-w-[85%] rounded-2xl px-3 py-2 text-[13px] leading-5 ${m.role === "client" ? "bg-white" : "bg-forest text-white"}`}><span className="block text-[10px] font-semibold opacity-60">{m.role === "client" ? "Customer" : "Cashy"}</span>{m.text}</p>
                    </div>
                  ))}
                </div>
              </div>
            );
          };
          return (
            <div className="mx-auto max-w-[980px] space-y-5">
              {/* header */}
              <section className={`${box} flex flex-wrap items-end justify-between gap-4`}>
                <div className="max-w-xl">
                  <h1 className="text-[22px] font-semibold tracking-[-.03em]">Approvals</h1>
                  <p className="mt-1 text-[13px] leading-6 text-[#5f6d64]">When a customer in a <b>live conversation</b> asks to change personal data, to move money, or looks like a thief, the conversation <b>pauses</b> and the request waits here. Approve and the conversation continues; refuse and the customer gets a polite refusal.</p>
                </div>
                <label className="text-[11px] font-semibold text-[#56645b]">Signed as
                  <input className={`${field} w-52`} value={name} onChange={(e) => setName(e.target.value)} placeholder="Your name, e.g. Chaima" />
                </label>
              </section>

              {/* counters */}
              <section className="grid grid-cols-3 gap-3">
                {[
                  ["waiting for you", pending.length + cashyPending.length, "border-amber-200 bg-amber-50 text-amber-800"],
                  ["approved", approvedN, "border-emerald-200 bg-emerald-50 text-forest"],
                  ["refused", refusedN, "border-rose-200 bg-rose-50 text-rose-700"],
                ].map(([label, n, tone]) => (
                  <div key={label as string} className={`rounded-2xl border px-4 py-3 ${tone}`}>
                    <div className="text-[26px] font-semibold tabular-nums leading-none">{n as number}</div>
                    <div className="mt-1 text-[12px] font-medium opacity-80">{label as string}</div>
                  </div>
                ))}
              </section>

              {/* waiting */}
              <section>
                <h2 className="mb-2 flex items-center gap-2 text-[15px] font-semibold">Waiting for you
                  {pending.length + cashyPending.length > 0 && <span className="relative flex h-2.5 w-2.5"><span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-400 opacity-75" /><span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-amber-500" /></span>}
                </h2>
                {pending.length + cashyPending.length === 0 && (
                  <div className={`${box} flex items-center gap-4`}>
                    <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-emerald-50 text-[18px] font-semibold text-forest">✓</span>
                    <div>
                      <p className="text-[14px] font-semibold">Nothing is waiting.</p>
                      <p className="text-[12px] leading-5 text-[#7d887f]">To try it: on the <Link href="/" className="font-semibold text-forest underline">conversation page</Link>, start a call where the customer wants to change a phone number or e-mail (or choose a fraud day). The call pauses and the request appears here.</p>
                    </div>
                  </div>
                )}
                <div className="space-y-3">
                  {pending.map((r) => (
                    <article key={r.review_id} className="overflow-hidden rounded-[22px] border border-amber-200 bg-white shadow-panel">
                      <div className="flex">
                        <div className="w-1.5 shrink-0 bg-amber-400" />
                        <div className="flex-1 p-4 sm:p-5">
                          <div className="flex flex-wrap items-center gap-2 text-[11px] font-semibold">
                            <span className="rounded-full bg-amber-100 px-2.5 py-1 text-amber-800">⏸ Conversation paused</span>
                            <span className="rounded-full bg-[#eef1ec] px-2.5 py-1 text-[#56645b]">{r.category_label}</span>
                            {(r.risk_score ?? 0) >= 3 && <span className="rounded-full bg-rose-100 px-2.5 py-1 text-rose-700">high risk</span>}
                            <span className="ml-auto font-medium text-[#9aa39d]">{ago(r.created_at)}</span>
                          </div>
                          <p className="mt-2 text-[17px] font-semibold leading-6">{r.client_name || "A customer"} wants to <span className="text-forest">{plainRequest(r.requested)}</span></p>
                          {r.signals.length > 0 && <div className="mt-2 flex flex-wrap gap-1.5">{r.signals.slice(0, 4).map((sg, k) => <span key={k} className="rounded-lg bg-[#f6f8f5] px-2.5 py-1 text-[12px] text-[#46564c]">{plainReason(sg)}</span>)}</div>}
                          {r.evidence && <blockquote className="mt-3 border-l-2 border-[#cbd8cc] pl-3 text-[13px] italic text-[#46564c]">“{r.evidence}”</blockquote>}
                          <div className="mt-4 flex flex-wrap items-center gap-2">
                            <button type="button" onClick={() => void decide(r, true)} className="rounded-xl bg-forest px-5 py-2.5 text-[13px] font-semibold text-white transition hover:bg-[#0a3f32]">Approve</button>
                            <button type="button" onClick={() => void decide(r, false)} className="rounded-xl border border-rose-200 bg-white px-5 py-2.5 text-[13px] font-semibold text-rose-700 transition hover:bg-rose-50">Refuse</button>
                            <button type="button" onClick={() => void openReview(r.review_id)} className="ml-auto rounded-xl px-3 py-2.5 text-[12px] font-semibold text-[#56645b] hover:bg-[#f3f6f2]">{openId === r.review_id ? "▲ Hide conversation" : "▼ Read the conversation"}</button>
                          </div>
                          {conversation(r)}
                        </div>
                      </div>
                    </article>
                  ))}
                  {cashyPending.map((c) => (
                    <article key={c.id} className="overflow-hidden rounded-[22px] border border-amber-200 bg-white shadow-panel">
                      <div className="flex">
                        <div className="w-1.5 shrink-0 bg-sky-400" />
                        <div className="flex-1 p-4 sm:p-5">
                          <div className="flex flex-wrap items-center gap-2 text-[11px] font-semibold">
                            <span className="rounded-full bg-sky-100 px-2.5 py-1 text-sky-800">Cashy is waiting</span>
                            <span className="rounded-full bg-[#eef1ec] px-2.5 py-1 text-[#56645b]">customer #{c.customer_id}</span>
                          </div>
                          <p className="mt-2 text-[17px] font-semibold">Cashy wants to <span className="text-forest">{c.action.replaceAll("_", " ")}</span></p>
                          <p className="mt-1 text-[13px] leading-6 text-[#46564c]">{c.message}</p>
                          <div className="mt-4 flex gap-2">
                            <button type="button" onClick={() => void decideCashy(c, true)} className="rounded-xl bg-forest px-5 py-2.5 text-[13px] font-semibold text-white transition hover:bg-[#0a3f32]">Approve</button>
                            <button type="button" onClick={() => void decideCashy(c, false)} className="rounded-xl border border-rose-200 bg-white px-5 py-2.5 text-[13px] font-semibold text-rose-700 transition hover:bg-rose-50">Refuse</button>
                          </div>
                        </div>
                      </div>
                    </article>
                  ))}
                </div>
              </section>

              {/* history */}
              <section className={box}>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <h2 className="text-[15px] font-semibold">History ({history.length})</h2>
                    <p className="text-[11px] text-[#9aa39d]">Your past decisions, newest first · click one to read the conversation</p>
                  </div>
                  <button type="button" onClick={() => void clearHistory()} disabled={history.length === 0}
                    className="rounded-xl border border-[#e3e8e1] bg-white px-3.5 py-2 text-[12px] font-semibold text-[#56645b] transition hover:border-rose-200 hover:text-rose-700 disabled:cursor-not-allowed disabled:opacity-40">Clear history</button>
                </div>
                {history.length === 0
                  ? <p className="mt-4 rounded-xl bg-[#f6f8f5] px-4 py-6 text-center text-[12px] text-[#7d887f]">No decision yet.</p>
                  : (
                    <ul className="mt-3 divide-y divide-[#eef1ec]">
                      {history.map((r) => (
                        <li key={r.review_id} className="py-2.5">
                          <button type="button" onClick={() => void openReview(r.review_id)} className="flex w-full items-center gap-3 rounded-xl px-2 py-1 text-left hover:bg-[#f6f8f5]">
                            <span className={`grid h-8 w-8 shrink-0 place-items-center rounded-full text-[14px] font-semibold ${r.status === "approved" ? "bg-emerald-100 text-forest" : "bg-rose-100 text-rose-700"}`}>{r.status === "approved" ? "✓" : "✕"}</span>
                            <span className="min-w-0 flex-1">
                              <span className="block truncate text-[13px] font-semibold">{r.client_name || "A customer"} · {plainRequest(r.requested)}</span>
                              <span className="block text-[11px] text-[#7d887f]">{r.status === "approved" ? "Approved" : "Refused"} by {r.decided_by} · {ago(r.decided_at || r.created_at)} · {r.category_label}</span>
                            </span>
                            <span className="text-[11px] text-[#9aa39d]">{openId === r.review_id ? "▲" : "▼"}</span>
                          </button>
                          {conversation(r)}
                        </li>
                      ))}
                    </ul>
                  )}
              </section>
            </div>
          );
        })()}
      </div>

      {openCall && (
        <div className="fixed inset-0 z-50 grid place-items-center bg-black/30 p-4" onClick={() => setOpenCall(null)}>
          <div className="max-h-[85vh] w-full max-w-[620px] overflow-y-auto rounded-[22px] bg-white p-5 shadow-panel" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-start justify-between gap-3">
              <div>
                <h3 className="text-[16px] font-semibold">{openCall.name}: {openCall.reason}</h3>
                <p className="mt-1 text-[12px] text-[#7d887f]">Waited {mins(openCall.waited_s)} · {openCall.advisor ? `served by ${deskName({ name: openCall.advisor, skill: openCall.advisor_skill || "" })}` : "never served"} · {OUTCOME[openCall.status] || openCall.status}{openCall.held ? " · stopped for approval" : ""}{openCall.thief ? " · this caller was a thief" : ""}</p>
              </div>
              <button type="button" onClick={() => setOpenCall(null)} className="rounded-lg px-2 py-1 text-[18px] text-[#7d887f]">✕</button>
            </div>
            <div className="mt-4 space-y-2">
              {openCall.transcript.map((m, k) => (
                <div key={k} className={`flex ${m.role === "client" ? "" : "justify-end"}`}>
                  <p className={`max-w-[85%] rounded-2xl px-3 py-2 text-[13px] leading-5 ${m.role === "client" ? "bg-[#f2f5f1]" : "bg-forest text-white"}`}><span className="block text-[10px] font-semibold opacity-60">{m.role === "client" ? "Customer" : "Advisor"}</span>{m.text}</p>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
