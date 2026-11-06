"use client";

import { useEffect, useMemo, useState } from "react";

const CLIENT_API = process.env.NEXT_PUBLIC_CLIENT_API_URL || "http://127.0.0.1:8001";

type Breakdown = { name: string; calls: number; avg_score: number | null; errors_per_call: number; critical_rate: number | null };
type Dashboard = {
  generated_at?: string;
  window_days: number | null;
  thresholds: { pass: number; at_risk: number };
  kpis: {
    total_reviews: number; scored_reviews: number; partial_reviews: number;
    avg_score: number | null; score_delta: number | null; pass_rate: number | null; at_risk_rate: number | null;
    critical_call_rate: number | null; errors_per_call: number | null; resolution_rate: number | null;
    escalation_rate: number | null; avg_wait_s: number | null; avg_duration_s: number | null; rule_error_share: number | null;
  };
  trend: { date: string; avg_score: number | null; calls: number }[];
  criteria: { name: string; weight: number; avg_score: number | null; points_lost: number | null; below_threshold_rate: number | null }[];
  distribution: { label: string; count: number }[];
  errors_by_type: { type: string; count: number; calls_affected: number; critique: number; majeure: number; mineure: number }[];
  errors_by_severity: { severity: string; count: number }[];
  rule_checks: { id: string; label: string; passed: number; failed: number; pass_rate: number | null }[];
  warnings: { type: string; count: number }[];
  by_intent: Breakdown[]; by_profile: Breakdown[]; by_language: Breakdown[]; by_model?: Breakdown[];
  top_recommendations: { text: string; count: number }[];
  timeline?: { report_id: number; score: number; intent: string; date: string | null }[];
  worst_calls: { report_id: number; call_id: string; score: number; intent: string; profile: string; errors: string[] }[];
  models: { name: string; count: number }[];
  insights: { level: "alert" | "action" | "info"; text: string }[];
};

/* ───────────────────────── palette & labels ───────────────────────── */

// Severity is a status, not a series: fixed colours that always ship with a text label.
const SEV: Record<string, { color: string; label: string }> = {
  critique: { color: "#b42318", label: "Critical" },
  majeure: { color: "#d9822b", label: "Major" },
  mineure: { color: "#9aa8a0", label: "Minor" },
};
const FOREST = "#0d4c3b";
const GOOD = "#1f7a4d";
const WARN = "#b7791f";
const BAD = "#b42318";
const GRID = "#e7ebe5";
const MUTED = "#748078";

const CRITERIA_LABEL: Record<string, string> = {
  exactitude: "Accuracy", conformite: "Compliance", resolution: "Resolution", empathie: "Empathy", pertinence: "Relevance",
};
const ERROR_LABEL: Record<string, string> = {
  identite_non_verifiee: "Identity not verified", information_erronee: "Wrong information", reponse_sans_source: "No source",
  hallucination: "Hallucination", procedure_non_suivie: "Procedure not followed", ton_inapproprie: "Inappropriate tone",
  escalade_manquee: "Missed escalation",
};

/* ───────────────────────── helpers ───────────────────────── */

const fmt = (v: number | null | undefined, suffix = "", digits = 1) =>
  v === null || v === undefined ? "—" : `${Number.isInteger(v) ? v : v.toFixed(digits)}${suffix}`;
const seconds = (v: number | null) => (v === null ? "—" : v >= 60 ? `${Math.floor(v / 60)}m ${Math.round(v % 60)}s` : `${Math.round(v)}s`);
const scoreColor = (s: number | null, t: Dashboard["thresholds"]) => (s === null ? MUTED : s >= t.pass ? GOOD : s >= t.at_risk ? WARN : BAD);
const rateColor = (v: number | null, goodAbove = 80, warnAbove = 60) => (v === null ? MUTED : v >= goodAbove ? GOOD : v >= warnAbove ? WARN : BAD);
const polar = (cx: number, cy: number, r: number, angle: number) => [cx + r * Math.cos(angle), cy + r * Math.sin(angle)] as const;
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
const verdict = (s: number | null, t: Dashboard["thresholds"]) =>
  s === null ? "No score yet" : s >= t.pass ? "On target" : s >= t.at_risk ? "Needs attention" : "At risk";

/* ───────────────────────── icons ───────────────────────── */

type IconName = "calls" | "check" | "alert" | "bug" | "target" | "escalate" | "clock" | "timer";
const ICON_PATHS: Record<IconName, string> = {
  calls: "M4 5h16v11H8l-4 4V5z",
  check: "M5 12.5l4.5 4.5L19 7.5",
  alert: "M12 4l9 16H3L12 4zM12 10v4M12 17v.5",
  bug: "M9 9h6v7a3 3 0 01-6 0V9zM9 9a3 3 0 016 0M5 11h4M15 11h4M5 17h4M15 17h4",
  target: "M12 3a9 9 0 100 18 9 9 0 000-18zM12 8a4 4 0 100 8 4 4 0 000-8z",
  escalate: "M7 17L17 7M9 7h8v8",
  clock: "M12 3a9 9 0 100 18 9 9 0 000-18zM12 7v5l3 2",
  timer: "M9 3h6M12 8v5l3 2M12 6a8 8 0 100 16 8 8 0 000-16z",
};
function Icon({ name, className = "h-4 w-4" }: { name: IconName; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={ICON_PATHS[name]} />
    </svg>
  );
}

/* ───────────────────────── layout primitives ───────────────────────── */

function Card({ title, subtitle, children, className = "", action }: {
  title: string; subtitle?: string; children: React.ReactNode; className?: string; action?: React.ReactNode;
}) {
  return (
    <section className={`rounded-2xl border border-[#e5e9e3] bg-white p-5 shadow-panel ${className}`}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-xs font-semibold uppercase tracking-[.14em] text-[#4f5d54]">{title}</h2>
          {subtitle && <p className="mt-1 text-[11px] leading-4 text-[#89938d]">{subtitle}</p>}
        </div>
        {action}
      </div>
      <div className="mt-4">{children}</div>
    </section>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-3 pt-3">
      <h2 className="text-[11px] font-semibold uppercase tracking-[.2em] text-forest">{children}</h2>
      <span className="h-px flex-1 bg-gradient-to-r from-[#d5ded6] to-transparent" />
    </div>
  );
}

function Legend({ items }: { items: { label: string; color: string; value?: string }[] }) {
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1.5 text-[11px] text-[#56645b]">
      {items.map((i) => (
        <li key={i.label} className="inline-flex items-center gap-1.5">
          <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: i.color }} />
          {i.label}{i.value !== undefined && <span className="font-semibold tabular-nums text-[#34473b]">{i.value}</span>}
        </li>
      ))}
    </ul>
  );
}

/* ───────────────────────── small visuals ───────────────────────── */

/** Tiny circular progress used inside KPI cards. */
function MiniRing({ value, color }: { value: number | null; color: string }) {
  const r = 17, c = 2 * Math.PI * r, pct = value === null ? 0 : clamp(value, 0, 100) / 100;
  return (
    <svg viewBox="0 0 44 44" className="h-11 w-11 shrink-0 -rotate-90" aria-hidden="true">
      <circle cx="22" cy="22" r={r} fill="none" stroke="#eef2ed" strokeWidth="5" />
      <circle cx="22" cy="22" r={r} fill="none" stroke={color} strokeWidth="5" strokeLinecap="round" strokeDasharray={`${c * pct} ${c}`} />
    </svg>
  );
}

function Sparkline({ values, color = FOREST }: { values: number[]; color?: string }) {
  if (values.length < 2) return null;
  const W = 84, H = 28, min = Math.min(...values), max = Math.max(...values), span = max - min || 1;
  const pts = values.map((v, i) => [(i * (W - 4)) / (values.length - 1) + 2, H - 4 - ((v - min) / span) * (H - 8)] as const);
  const d = pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-7 w-[84px]" aria-hidden="true">
      <path d={`${d} L${pts[pts.length - 1][0]},${H} L${pts[0][0]},${H} Z`} fill={color} opacity=".1" />
      <path d={d} fill="none" stroke={color} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx={pts[pts.length - 1][0]} cy={pts[pts.length - 1][1]} r="2.5" fill={color} />
    </svg>
  );
}

function Kpi({ label, value, hint, tone, icon, ring, spark }: {
  label: string; value: string; hint?: string; tone?: string; icon: IconName;
  ring?: { value: number | null; color: string }; spark?: number[];
}) {
  return (
    <div className="group rounded-2xl border border-[#e5e9e3] bg-white p-4 shadow-panel transition hover:-translate-y-0.5 hover:border-[#cbd8cc]">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[.14em] text-[#7a867e]">
            <span className="grid h-5 w-5 place-items-center rounded-md bg-[#eff6f0] text-forest"><Icon name={icon} className="h-3 w-3" /></span>
            {label}
          </p>
          <p className="mt-3 text-[28px] font-semibold leading-none tracking-[-.04em]" style={{ color: tone || "#172420" }}>{value}</p>
        </div>
        {ring ? <MiniRing value={ring.value} color={ring.color} /> : spark ? <Sparkline values={spark} /> : null}
      </div>
      {hint && <p className="mt-2.5 text-[11px] leading-4 text-[#748078]">{hint}</p>}
    </div>
  );
}

/* ───────────────────────── hero visuals ───────────────────────── */

/** Semicircle gauge 0–10 with pass / at-risk markers. */
function ScoreGauge({ score, t }: { score: number | null; t: Dashboard["thresholds"] }) {
  const cx = 120, cy = 112, r = 92, sw = 16;
  const pos = (v: number, radius = r) => polar(cx, cy, radius, Math.PI * (1 + clamp(v, 0, 10) / 10));
  const arc = (from: number, to: number) => {
    const [x1, y1] = pos(from), [x2, y2] = pos(to);
    return `M${x1.toFixed(2)},${y1.toFixed(2)} A${r},${r} 0 0 1 ${x2.toFixed(2)},${y2.toFixed(2)}`;
  };
  const color = scoreColor(score, t);
  return (
    <div className="relative mx-auto w-full max-w-[300px]">
      <svg viewBox="0 0 240 140" className="w-full" role="img" aria-label={`Average score ${score ?? "unavailable"} out of 10`}>
        <path d={arc(0, t.at_risk)} fill="none" stroke={BAD} strokeOpacity=".16" strokeWidth={sw} />
        <path d={arc(t.at_risk, t.pass)} fill="none" stroke={WARN} strokeOpacity=".2" strokeWidth={sw} />
        <path d={arc(t.pass, 10)} fill="none" stroke={GOOD} strokeOpacity=".2" strokeWidth={sw} />
        {score !== null && score > 0 && <path d={arc(0, Math.min(score, 9.999))} fill="none" stroke={color} strokeWidth={sw} strokeLinecap="round" />}
        {[t.at_risk, t.pass].map((v) => {
          const [x1, y1] = pos(v, r - sw / 2 - 3), [x2, y2] = pos(v, r + sw / 2 + 3), [lx, ly] = pos(v, r + sw / 2 + 12);
          return (
            <g key={v}>
              <line x1={x1} y1={y1} x2={x2} y2={y2} stroke="#4f5d54" strokeWidth="1.5" />
              <text x={lx} y={ly + 3} textAnchor="middle" fontSize="9" fill={MUTED}>{v}</text>
            </g>
          );
        })}
        <text x={cx - r - 2} y={cy + 16} textAnchor="middle" fontSize="9" fill={MUTED}>0</text>
        <text x={cx + r + 2} y={cy + 16} textAnchor="middle" fontSize="9" fill={MUTED}>10</text>
      </svg>
      <div className="absolute inset-x-0 bottom-1 text-center">
        <p className="text-[40px] font-semibold leading-none tracking-[-.05em]" style={{ color }}>{score === null ? "—" : score.toFixed(2)}</p>
        <p className="mt-1 text-[11px] font-semibold uppercase tracking-[.14em]" style={{ color }}>{verdict(score, t)}</p>
      </div>
    </div>
  );
}

type Slice = { label: string; value: number; color: string };

function Donut({ slices, center, centerLabel }: { slices: Slice[]; center: string; centerLabel: string }) {
  const r = 52, c = 2 * Math.PI * r, total = slices.reduce((a, s) => a + s.value, 0);
  let acc = 0;
  return (
    <div className="relative mx-auto h-40 w-40">
      <svg viewBox="0 0 140 140" className="h-full w-full -rotate-90" role="img" aria-label={`${centerLabel}: ${slices.map((s) => `${s.label} ${s.value}`).join(", ")}`}>
        <circle cx="70" cy="70" r={r} fill="none" stroke="#eef2ed" strokeWidth="18" />
        {total > 0 && slices.map((s) => {
          const len = (s.value / total) * c, off = acc;
          acc += len;
          if (s.value <= 0) return null;
          return (
            <circle key={s.label} cx="70" cy="70" r={r} fill="none" stroke={s.color} strokeWidth="18"
              strokeDasharray={`${Math.max(len - 2, 0.5)} ${c}`} strokeDashoffset={-off}>
              <title>{`${s.label}: ${s.value}`}</title>
            </circle>
          );
        })}
      </svg>
      <div className="absolute inset-0 grid place-content-center text-center">
        <p className="text-2xl font-semibold leading-none tracking-[-.04em] text-ink">{center}</p>
        <p className="mt-1 text-[10px] font-semibold uppercase tracking-[.12em] text-[#89938d]">{centerLabel}</p>
      </div>
    </div>
  );
}

/** Radar of the five weighted criteria with the pass threshold as a dashed ring. */
function CriteriaRadar({ criteria, pass, t }: { criteria: Dashboard["criteria"]; pass: number; t: Dashboard["thresholds"] }) {
  const size = 280, c = size / 2, R = 88, n = criteria.length;
  if (n < 3) return <p className="text-sm text-[#89938d]">Not enough criteria to draw a radar.</p>;
  const angle = (i: number) => -Math.PI / 2 + (i * 2 * Math.PI) / n;
  const ring = (v: number) => criteria.map((_, i) => polar(c, c, (v / 10) * R, angle(i)).map((p) => p.toFixed(1)).join(",")).join(" ");
  const shape = criteria.map((k, i) => polar(c, c, ((k.avg_score ?? 0) / 10) * R, angle(i)).map((p) => p.toFixed(1)).join(",")).join(" ");
  return (
    <svg viewBox={`0 0 ${size} ${size}`} className="mx-auto w-full max-w-[300px]" role="img" aria-label="Average score for each criterion">
      {[2.5, 5, 7.5, 10].map((v) => <polygon key={v} points={ring(v)} fill="none" stroke={GRID} />)}
      {criteria.map((_, i) => {
        const [x, y] = polar(c, c, R, angle(i));
        return <line key={i} x1={c} y1={c} x2={x} y2={y} stroke={GRID} />;
      })}
      <polygon points={ring(pass)} fill="none" stroke={GOOD} strokeDasharray="4 3" strokeWidth="1.2" />
      <polygon points={shape} fill={FOREST} fillOpacity=".14" stroke={FOREST} strokeWidth="2" strokeLinejoin="round" />
      {criteria.map((k, i) => {
        const [px, py] = polar(c, c, ((k.avg_score ?? 0) / 10) * R, angle(i));
        const [lx, ly] = polar(c, c, R + 24, angle(i));
        const anchor = Math.abs(lx - c) < 6 ? "middle" : lx > c ? "start" : "end";
        return (
          <g key={k.name}>
            <circle cx={px} cy={py} r="4" fill={scoreColor(k.avg_score, t)} stroke="#fff" strokeWidth="2"><title>{`${CRITERIA_LABEL[k.name] || k.name}: ${fmt(k.avg_score, "/10", 2)}`}</title></circle>
            <text x={lx} y={ly - 2} textAnchor={anchor} fontSize="10" fontWeight="600" fill="#34473b">{CRITERIA_LABEL[k.name] || k.name}</text>
            <text x={lx} y={ly + 10} textAnchor={anchor} fontSize="10" fill={scoreColor(k.avg_score, t)} fontWeight="700">{fmt(k.avg_score, "", 2)}</text>
          </g>
        );
      })}
    </svg>
  );
}

/* ───────────────────────── trend / distribution ───────────────────────── */

/** Fallback when there are fewer than 2 days of data: one point per call, in evaluation order. */
function CallsChart({ data, t }: { data: NonNullable<Dashboard["timeline"]>; t: Dashboard["thresholds"] }) {
  const W = 680, H = 230, L = 32, R = 14, T = 14, B = 34;
  if (data.length < 2) return <p className="text-sm text-[#89938d]">Not enough calls yet for a trend.</p>;
  const x = (i: number) => L + (i * (W - L - R)) / (data.length - 1);
  const y = (v: number) => T + (1 - v / 10) * (H - T - B);
  const line = data.map((d, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(d.score).toFixed(1)}`).join(" ");
  const step = Math.ceil(data.length / 10);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="Score of each call in order">
      <rect x={L} y={y(10)} width={W - L - R} height={y(t.pass) - y(10)} fill={GOOD} opacity=".05" />
      {[0, 2.5, 5, 7.5, 10].map((tick) => (
        <g key={tick}>
          <line x1={L} x2={W - R} y1={y(tick)} y2={y(tick)} stroke={GRID} />
          <text x={L - 6} y={y(tick) + 3} textAnchor="end" fontSize="9" fill={MUTED}>{tick}</text>
        </g>
      ))}
      <line x1={L} x2={W - R} y1={y(t.pass)} y2={y(t.pass)} stroke={GOOD} strokeDasharray="4 4" />
      <text x={W - R} y={y(t.pass) - 5} textAnchor="end" fontSize="9" fontWeight="600" fill={GOOD}>Target {t.pass}</text>
      <line x1={L} x2={W - R} y1={y(t.at_risk)} y2={y(t.at_risk)} stroke={BAD} strokeDasharray="2 4" opacity=".6" />
      <path d={line} fill="none" stroke={FOREST} strokeWidth="2" strokeLinejoin="round" opacity=".5" />
      {data.map((d, i) => (
        <g key={d.report_id}>
          <circle cx={x(i)} cy={y(d.score)} r="6" fill={scoreColor(d.score, t)} stroke="#fff" strokeWidth="2">
            <title>{`Report #${d.report_id} · ${d.intent} · ${d.score}/10`}</title>
          </circle>
          <text x={x(i)} y={y(d.score) - 11} textAnchor="middle" fontSize="9" fontWeight="700" fill={scoreColor(d.score, t)}>{d.score}</text>
          {i % step === 0 && <text x={x(i)} y={H - 12} textAnchor="middle" fontSize="9" fill={MUTED}>#{d.report_id}</text>}
        </g>
      ))}
    </svg>
  );
}

/** Daily average score (line) with call volume (bars) and a pass-threshold line. */
function TrendChart({ data, pass }: { data: Dashboard["trend"]; pass: number }) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 680, H = 250, L = 32, R = 14, T = 14, B = 28, VOL = 44;
  if (data.length < 2) return <p className="text-sm text-[#89938d]">Not enough days of data yet for a trend.</p>;
  const plotB = H - B - VOL - 6;
  const x = (i: number) => L + (i * (W - L - R)) / (data.length - 1);
  const y = (v: number) => T + (1 - v / 10) * (plotB - T);
  const maxCalls = Math.max(1, ...data.map((d) => d.calls));
  const barW = Math.max(3, Math.min(18, ((W - L - R) / data.length) * 0.55));
  const points = data.map((d, i) => [x(i), y(d.avg_score ?? 0)] as const);
  const line = points.map(([px, py], i) => `${i ? "L" : "M"}${px.toFixed(1)},${py.toFixed(1)}`).join(" ");
  const area = `${line} L${points[points.length - 1][0]},${y(0)} L${points[0][0]},${y(0)} Z`;
  const active = hover !== null ? data[hover] : null;
  return (
    <div className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="Average quality score and call volume per day"
        onMouseLeave={() => setHover(null)}
        onMouseMove={(e) => {
          const box = e.currentTarget.getBoundingClientRect();
          const ratio = (e.clientX - box.left) / box.width;
          setHover(clamp(Math.round(((ratio * W - L) / (W - L - R)) * (data.length - 1)), 0, data.length - 1));
        }}>
        <defs>
          <linearGradient id="trendFill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor={FOREST} stopOpacity=".22" />
            <stop offset="100%" stopColor={FOREST} stopOpacity="0" />
          </linearGradient>
        </defs>
        <rect x={L} y={y(10)} width={W - L - R} height={y(pass) - y(10)} fill={GOOD} opacity=".05" />
        {[0, 2.5, 5, 7.5, 10].map((tick) => (
          <g key={tick}>
            <line x1={L} x2={W - R} y1={y(tick)} y2={y(tick)} stroke={GRID} />
            <text x={L - 6} y={y(tick) + 3} textAnchor="end" fontSize="9" fill={MUTED}>{tick}</text>
          </g>
        ))}
        <line x1={L} x2={W - R} y1={y(pass)} y2={y(pass)} stroke={GOOD} strokeDasharray="4 4" />
        <text x={W - R} y={y(pass) - 5} textAnchor="end" fontSize="9" fontWeight="600" fill={GOOD}>Target {pass}</text>
        <path d={area} fill="url(#trendFill)" />
        <path d={line} fill="none" stroke={FOREST} strokeWidth="2.4" strokeLinejoin="round" strokeLinecap="round" />
        {data.length <= 31 && points.map(([px, py], i) => <circle key={i} cx={px} cy={py} r="2.6" fill="#fff" stroke={FOREST} strokeWidth="1.6" />)}
        {/* call volume */}
        <text x={L - 6} y={H - B - VOL + 2} textAnchor="end" fontSize="8" fill={MUTED}>calls</text>
        {data.map((d, i) => {
          const h = (d.calls / maxCalls) * (VOL - 6);
          return <rect key={d.date} x={x(i) - barW / 2} y={H - B - h} width={barW} height={h} rx="2" fill={FOREST} opacity={hover === i ? 0.55 : 0.2} />;
        })}
        <line x1={L} x2={W - R} y1={H - B} y2={H - B} stroke={GRID} />
        {[0, Math.floor(data.length / 2), data.length - 1].map((i) => (
          <text key={i} x={x(i)} y={H - 10} textAnchor={i === 0 ? "start" : i === data.length - 1 ? "end" : "middle"} fontSize="9" fill={MUTED}>
            {data[i].date.slice(5)}
          </text>
        ))}
        {hover !== null && (
          <g>
            <line x1={points[hover][0]} x2={points[hover][0]} y1={T} y2={H - B} stroke="#b9c4bc" />
            <circle cx={points[hover][0]} cy={points[hover][1]} r="5" fill={FOREST} stroke="#fff" strokeWidth="2" />
          </g>
        )}
      </svg>
      {active && (
        <div className="pointer-events-none absolute right-2 top-0 rounded-lg border border-[#e5e9e3] bg-white px-3 py-2 text-[11px] shadow-panel">
          <p className="font-semibold text-[#34473b]">{active.date}</p>
          <p className="text-[#56645b]">Avg {fmt(active.avg_score, "/10", 2)} · {active.calls} call{active.calls > 1 ? "s" : ""}</p>
        </div>
      )}
    </div>
  );
}

function Histogram({ data, t }: { data: Dashboard["distribution"]; t: Dashboard["thresholds"] }) {
  const max = Math.max(1, ...data.map((d) => d.count));
  const total = data.reduce((a, d) => a + d.count, 0) || 1;
  // Bands are [0-2) [2-4) [4-6) [6-8) [8-10]; colour each by where its midpoint falls vs the thresholds.
  const bandColor = (i: number) => scoreColor(i * 2 + 1, t);
  return (
    <div className="flex h-[210px] items-end gap-3 pt-2" role="img" aria-label="Distribution of call scores">
      {data.map((d, i) => (
        <div key={d.label} className="flex h-full flex-1 flex-col items-center justify-end gap-1.5" title={`${d.count} calls scored ${d.label}`}>
          <span className="text-xs font-semibold tabular-nums text-[#34473b]">{d.count}</span>
          <div className="w-full rounded-t-lg transition hover:opacity-80" style={{ height: `${(d.count / max) * 78}%`, minHeight: 3, background: `linear-gradient(180deg, ${bandColor(i)}, ${bandColor(i)}b3)` }} />
          <span className="text-[10px] font-medium text-[#6b786f]">{d.label}</span>
          <span className="text-[10px] tabular-nums text-[#98a29c]">{Math.round((d.count / total) * 100)}%</span>
        </div>
      ))}
    </div>
  );
}

/* ───────────────────────── bars ───────────────────────── */

/** One horizontal bar per row; value 0–max, optional reference tick. */
function HBars({ rows, max, tick, color = FOREST, format = (v: number) => String(v) }: {
  rows: { label: string; value: number | null; note?: string; color?: string }[]; max: number; tick?: number; color?: string; format?: (v: number) => string;
}) {
  return (
    <ul className="space-y-3.5">
      {rows.map((row) => (
        <li key={row.label} title={row.note}>
          <div className="mb-1.5 flex justify-between text-xs text-[#45584b]">
            <span className="font-medium">{row.label}</span>
            <span className="font-semibold tabular-nums text-[#34473b]">{row.value === null ? "—" : format(row.value)}</span>
          </div>
          <div className="relative h-2.5 rounded-full bg-[#eef2ed]">
            <div className="h-2.5 rounded-full transition-all" style={{ width: `${row.value === null ? 0 : Math.min(100, (row.value / max) * 100)}%`, background: row.color || color }} />
            {tick !== undefined && <span className="absolute top-[-3px] h-[16px] w-[2px] rounded bg-[#1f7a4d]" style={{ left: `${(tick / max) * 100}%` }} />}
          </div>
          {row.note && <p className="mt-1 text-[10px] text-[#89938d]">{row.note}</p>}
        </li>
      ))}
    </ul>
  );
}

function ErrorStack({ rows }: { rows: Dashboard["errors_by_type"] }) {
  if (!rows.length) return <p className="text-sm text-[#89938d]">No errors recorded in this period.</p>;
  const max = Math.max(...rows.map((r) => r.count));
  return (
    <ul className="space-y-3">
      {rows.map((row) => (
        <li key={row.type}>
          <div className="mb-1 flex justify-between text-xs">
            <span className="font-medium text-[#45584b]">{ERROR_LABEL[row.type] || row.type}</span>
            <span className="tabular-nums text-[#748078]">{row.count} · {row.calls_affected} calls</span>
          </div>
          <div className="flex h-2.5 gap-[2px]" style={{ width: `${(row.count / max) * 100}%` }}>
            {(["critique", "majeure", "mineure"] as const).map((s) =>
              row[s] > 0 ? <div key={s} title={`${SEV[s].label}: ${row[s]}`} className="h-full first:rounded-l-full last:rounded-r-full" style={{ flex: row[s], background: SEV[s].color }} /> : null,
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}

/** Ranked list of segments with an inline score bar, call volume and risk chips. */
function BreakdownList({ rows, t }: { rows: Breakdown[]; t: Dashboard["thresholds"] }) {
  const maxCalls = Math.max(1, ...rows.map((r) => r.calls));
  return (
    <ul className="space-y-1">
      <li className="grid grid-cols-[minmax(0,1.3fr)_minmax(0,1.6fr)_52px_52px] items-center gap-3 px-2 pb-1 text-[10px] font-semibold uppercase tracking-[.1em] text-[#89938d]">
        <span>Segment</span><span>Score</span><span className="text-right">Err/call</span><span className="text-right">Critical</span>
      </li>
      {rows.map((r) => {
        const color = scoreColor(r.avg_score, t);
        return (
          <li key={r.name} className="grid grid-cols-[minmax(0,1.3fr)_minmax(0,1.6fr)_52px_52px] items-center gap-3 rounded-xl px-2 py-2 text-xs transition hover:bg-[#f7f9f6]">
            <span className="min-w-0">
              <span className="block truncate font-medium text-[#34473b]">{r.name}</span>
              <span className="mt-1 block h-1 rounded-full bg-[#eef2ed]" title={`${r.calls} call${r.calls === 1 ? "" : "s"}`}>
                <span className="block h-1 rounded-full bg-[#b9c7bd]" style={{ width: `${(r.calls / maxCalls) * 100}%` }} />
              </span>
              <span className="text-[10px] text-[#98a29c]">{r.calls} call{r.calls === 1 ? "" : "s"}</span>
            </span>
            <span className="flex items-center gap-2">
              <span className="relative h-2.5 flex-1 rounded-full bg-[#eef2ed]">
                <span className="block h-2.5 rounded-full" style={{ width: `${((r.avg_score ?? 0) / 10) * 100}%`, background: color }} />
                <span className="absolute top-[-2px] h-[14px] w-[2px] rounded bg-[#1f7a4d]/70" style={{ left: `${(t.pass / 10) * 100}%` }} />
              </span>
              <span className="w-9 text-right text-sm font-bold tabular-nums" style={{ color }}>{fmt(r.avg_score, "", 2)}</span>
            </span>
            <span className="text-right tabular-nums text-[#56645b]">{r.errors_per_call}</span>
            <span className="text-right">
              <span className="inline-block rounded-md px-1.5 py-0.5 text-[11px] font-semibold tabular-nums"
                style={{ background: (r.critical_rate ?? 0) > 10 ? "#fdecea" : "#f1f5f0", color: (r.critical_rate ?? 0) > 10 ? BAD : "#56645b" }}>
                {fmt(r.critical_rate, "%")}
              </span>
            </span>
          </li>
        );
      })}
    </ul>
  );
}

function ScoreChip({ score, t }: { score: number; t: Dashboard["thresholds"] }) {
  const color = scoreColor(score, t);
  return (
    <span className="grid h-10 w-12 shrink-0 place-items-center rounded-xl text-base font-bold tabular-nums" style={{ background: `${color}14`, color }}>
      {score}
    </span>
  );
}

/* ───────────────────────── page ───────────────────────── */

export default function QualityDashboardPage() {
  const [days, setDays] = useState<number | null>(30);
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [dimension, setDimension] = useState<"by_intent" | "by_profile" | "by_language" | "by_model">("by_intent");
  const [pending, setPending] = useState(0);
  const [backfilling, setBackfilling] = useState(false);
  const [backfillNote, setBackfillNote] = useState("");
  const [reload, setReload] = useState(0);

  useEffect(() => {
    fetch(`${CLIENT_API}/api/quality/pending`)
      .then((r) => (r.ok ? r.json() : null))
      .then((p) => setPending(p?.count ?? 0))
      .catch(() => setPending(0));
  }, [reload]);

  async function analyzePending() {
    setBackfilling(true);
    setBackfillNote("");
    try {
      const res = await fetch(`${CLIENT_API}/api/quality/backfill`, { method: "POST" });
      const out = await res.json().catch(() => null);
      if (!res.ok) throw new Error(out?.detail || `Request failed (${res.status}).`);
      setBackfillNote(`${out.analyzed} call(s) analyzed${out.failed?.length ? `, ${out.failed.length} failed (${out.failed[0].error})` : ""}.`);
      setReload((n) => n + 1);
    } catch (e) {
      setBackfillNote(e instanceof Error ? e.message : "Analysis failed.");
    } finally {
      setBackfilling(false);
    }
  }

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    fetch(`${CLIENT_API}/api/quality/dashboard${days ? `?days=${days}` : ""}`)
      .then(async (res) => {
        const payload = await res.json().catch(() => null);
        if (!res.ok) throw new Error(payload?.detail || `Request failed (${res.status}).`);
        return payload as Dashboard;
      })
      .then((d) => active && setData(d))
      .catch((e) => active && setError(e instanceof Error ? e.message : "Could not load the dashboard."))
      .finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [days, reload]);

  const t = data?.thresholds ?? { pass: 7, at_risk: 5 };
  const k = data?.kpis;
  const delta = k?.score_delta ?? null;
  const dimensionLabel = { by_intent: "Reason", by_profile: "Customer profile", by_language: "Language", by_model: "Judge model" }[dimension];

  const criteriaRows = useMemo(
    () => (data?.criteria ?? []).map((c) => ({
      label: `${CRITERIA_LABEL[c.name] || c.name} · ${Math.round(c.weight * 100)}%`,
      value: c.avg_score,
      note: c.points_lost !== null ? `${fmt(c.below_threshold_rate, "%")} of calls under ${t.pass}` : undefined,
      color: scoreColor(c.avg_score, t),
    })),
    [data, t],
  );

  const pointsLostRows = useMemo(() => {
    const rows = (data?.criteria ?? []).filter((c) => c.points_lost !== null)
      .sort((a, b) => (b.points_lost ?? 0) - (a.points_lost ?? 0));
    const top = rows[0]?.points_lost ?? 1;
    return rows.map((c, i) => ({
      label: CRITERIA_LABEL[c.name] || c.name,
      value: c.points_lost as number,
      top,
      priority: i === 0,
    }));
  }, [data]);

  // Outcome split: pass / in between / at risk (derived from the two rates the API already gives).
  const outcome: Slice[] = useMemo(() => {
    if (!k || k.pass_rate === null || k.at_risk_rate === null) return [];
    const pass = k.pass_rate, risk = k.at_risk_rate, mid = Math.max(0, +(100 - pass - risk).toFixed(1));
    return [
      { label: `Pass (≥ ${t.pass})`, value: pass, color: GOOD },
      { label: `Borderline (${t.at_risk}–${t.pass})`, value: mid, color: "#e0b45a" },
      { label: `At risk (< ${t.at_risk})`, value: risk, color: BAD },
    ];
  }, [k, t]);

  const severitySlices: Slice[] = (data?.errors_by_severity ?? []).map((s) => ({
    label: SEV[s.severity]?.label ?? s.severity, value: s.count, color: SEV[s.severity]?.color ?? MUTED,
  }));
  const totalErrors = severitySlices.reduce((a, s) => a + s.value, 0);

  // Per-call scores in evaluation order. Uses the API's `timeline` when present, otherwise rebuilds it from
  // `worst_calls` (the 6 lowest scores: that is every scored call while there are 6 or fewer).
  const callTimeline = useMemo(() => {
    if (data?.timeline?.length) return data.timeline;
    return [...(data?.worst_calls ?? [])]
      .sort((a, b) => a.report_id - b.report_id)
      .map((c) => ({ report_id: c.report_id, score: c.score, intent: c.intent, date: null }));
  }, [data]);

  const trendScores = (data?.trend ?? []).map((d) => d.avg_score ?? 0);
  const trendCalls = (data?.trend ?? []).map((d) => d.calls);

  // KPI cards: always show the ones that carry information; fold the empty/zero ones into one quiet line.
  const scoredCalls = k?.scored_reviews ?? 0;
  const passedCalls = k && k.pass_rate !== null ? Math.round((k.pass_rate / 100) * scoredCalls) : 0;
  const riskCalls = k && k.at_risk_rate !== null ? Math.round((k.at_risk_rate / 100) * scoredCalls) : 0;
  const rankedCriteria = [...(data?.criteria ?? [])].filter((c) => c.avg_score !== null).sort((a, b) => (a.avg_score ?? 0) - (b.avg_score ?? 0));
  const weakest = rankedCriteria[0];
  const strongest = rankedCriteria.length > 1 ? rankedCriteria[rankedCriteria.length - 1] : undefined;
  const optionalKpis: { label: string; value: string; hint: string; icon: IconName; tone?: string; ring?: { value: number | null; color: string }; has: boolean }[] = k ? [
    { label: "Critical-error calls", value: fmt(k.critical_call_rate, "%"), hint: "Score capped at 5/10", icon: "alert", tone: BAD, ring: { value: k.critical_call_rate, color: BAD }, has: (k.critical_call_rate ?? 0) > 0 },
    { label: "Errors per call", value: fmt(k.errors_per_call, "", 2), hint: `${fmt(k.rule_error_share, "%")} caught by deterministic rules`, icon: "bug", has: (k.errors_per_call ?? 0) > 0 },
    { label: "Resolution rate", value: fmt(k.resolution_rate, "%"), hint: "From simulator ground truth", icon: "target", tone: rateColor(k.resolution_rate), ring: { value: k.resolution_rate, color: rateColor(k.resolution_rate) }, has: (k.resolution_rate ?? 0) > 0 },
    { label: "Escalation rate", value: fmt(k.escalation_rate, "%"), hint: "Calls handed to a human", icon: "escalate", ring: { value: k.escalation_rate, color: FOREST }, has: (k.escalation_rate ?? 0) > 0 },
    { label: "Avg wait", value: seconds(k.avg_wait_s), hint: "Before the advisor answered", icon: "clock", has: (k.avg_wait_s ?? 0) > 0 },
    { label: "Avg duration", value: seconds(k.avg_duration_s), hint: "Whole conversation", icon: "timer", has: (k.avg_duration_s ?? 0) > 0 },
  ] : [];
  const shownOptional = optionalKpis.filter((o) => o.has);
  const hiddenOptional = optionalKpis.filter((o) => !o.has);

  const generated = data?.generated_at ? new Date(data.generated_at) : null;
  const periodLabel = days ? `Last ${days} days` : "All time";

  return (
    <main className="min-h-screen bg-paper text-ink">
      <header className="sticky top-0 z-20 border-b border-[#e7eae4] bg-white/85 backdrop-blur">
        <div className="mx-auto flex max-w-[1440px] items-center justify-between px-5 py-3.5 sm:px-8">
          <div className="flex items-center gap-3">
            <span className="grid h-9 w-9 place-items-center rounded-xl bg-forest text-white shadow-sm">
              <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M4 19V9M10 19V5M16 19v-7M22 19H2" />
              </svg>
            </span>
            <div>
              <p className="text-[17px] font-semibold leading-tight tracking-[-.04em]">Quality Analyst</p>
              <p className="text-[10px] font-semibold uppercase tracking-[.2em] text-[#89938d]">Performance dashboard</p>
            </div>
          </div>
          <nav className="flex gap-2 text-[11px] font-semibold">
            <a href="/quality" className="rounded-xl border border-[#e6ebe5] bg-white px-3 py-2 text-[#56645b] transition hover:border-[#cbd8cc] hover:text-forest">Call reviews</a>
            <a href="/" className="rounded-xl border border-[#e6ebe5] bg-white px-3 py-2 text-[#56645b] transition hover:border-[#cbd8cc] hover:text-forest">Back to Relay</a>
          </nav>
        </div>
      </header>

      <div className="mx-auto max-w-[1440px] px-5 py-7 sm:px-8 lg:py-9">
        <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[.2em] text-forest">Advisor quality · {periodLabel}</p>
            <h1 className="mt-2 text-3xl font-semibold tracking-[-.05em]">Quality dashboard</h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-[#748078]">
              Aggregated from the latest evaluation of each call. A call passes at {t.pass}/10 or more and is at risk below {t.at_risk}/10.
            </p>
          </div>
          <div className="flex flex-col items-end gap-2">
            <div role="group" aria-label="Period" className="flex rounded-xl border border-[#e6ebe5] bg-white p-1 text-xs font-semibold shadow-sm">
              {([[7, "7 days"], [30, "30 days"], [90, "90 days"], [null, "All"]] as const).map(([value, label]) => (
                <button key={label} type="button" onClick={() => setDays(value)} aria-pressed={days === value}
                  className={`rounded-lg px-3 py-1.5 transition ${days === value ? "bg-forest text-white shadow-sm" : "text-[#56645b] hover:bg-[#f1f5f0]"}`}>{label}</button>
              ))}
            </div>
            {generated && !Number.isNaN(generated.getTime()) && (
              <p className="text-[10px] text-[#98a29c]">Updated {generated.toLocaleString([], { dateStyle: "medium", timeStyle: "short" })} · UI v2f</p>
            )}
          </div>
        </div>

        {pending > 0 && (
          <div className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-950">
            <span><b>{pending}</b> finished simulation{pending > 1 ? "s" : ""} not analyzed yet — they are missing from these numbers.{backfillNote && <span className="ml-2 text-xs text-amber-800">{backfillNote}</span>}</span>
            <button type="button" onClick={analyzePending} disabled={backfilling}
              className="rounded-xl bg-forest px-3.5 py-2 text-xs font-semibold text-white transition hover:bg-[#0a3f32] disabled:opacity-60">
              {backfilling ? "Analyzing…" : "Analyze now"}
            </button>
          </div>
        )}
        {pending === 0 && backfillNote && <p className="mb-5 text-xs text-[#748078]">{backfillNote}</p>}

        {error && <div role="alert" className="mb-5 rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800">{error}</div>}
        {loading && !data && (
          <div className="grid animate-pulse gap-4 lg:grid-cols-3" aria-label="Loading dashboard">
            {[0, 1, 2].map((i) => <div key={i} className="h-72 rounded-2xl border border-[#e5e9e3] bg-white" />)}
          </div>
        )}

        {data && k && k.total_reviews === 0 && (
          <div className="rounded-2xl border border-[#e5e9e3] bg-white p-8 text-center text-sm leading-6 text-[#748078]">
            No evaluations in this period. Finish a simulation in Relay, or run <code className="rounded bg-[#f1f5f0] px-1.5 py-0.5">python seed_demo.py</code> in <code className="rounded bg-[#f1f5f0] px-1.5 py-0.5">Quality_Analyst/</code> to load demo data.
          </div>
        )}

        {data && k && k.total_reviews > 0 && (
          <div className={`space-y-5 transition-opacity ${loading ? "opacity-60" : ""}`} aria-busy={loading}>
            {/* ── headline visuals ───────────────────────────── */}
            <div className="grid gap-5 lg:grid-cols-3">
              <Card title="Overall quality" subtitle="Average global score out of 10">
                <ScoreGauge score={k.avg_score} t={t} />
                <div className="mt-4 flex flex-wrap items-center justify-center gap-2 text-[11px] font-semibold">
                  {delta !== null && (
                    <span className="rounded-full px-2.5 py-1" style={{ background: delta >= 0 ? "#e8f5ee" : "#fdecea", color: delta >= 0 ? GOOD : BAD }}>
                      {delta >= 0 ? "▲ +" : "▼ "}{delta} pt vs first half
                    </span>
                  )}
                  <span className="rounded-full bg-[#f1f5f0] px-2.5 py-1 text-[#56645b]">{k.scored_reviews} scored calls</span>
                </div>
              </Card>

              <Card title="Call outcomes" subtitle="Share of calls by result against the thresholds">
                {outcome.length === 0 ? <p className="text-sm text-[#89938d]">No scored calls yet.</p> : (
                  <div className="flex flex-col items-center gap-4">
                    <Donut slices={outcome} center={fmt(k.pass_rate, "%")} centerLabel="pass rate" />
                    <Legend items={outcome.map((s) => ({ label: `${s.label} · `, color: s.color, value: `${fmt(s.value, "%")}` }))} />
                  </div>
                )}
              </Card>

              <Card title="Criteria profile" subtitle="Dashed ring = pass threshold. Closer to the edge is better.">
                <CriteriaRadar criteria={data.criteria} pass={t.pass} t={t} />
              </Card>
            </div>

            {/* ── KPI strip ──────────────────────────────────── */}
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              <Kpi icon="calls" label="Reviews" value={String(k.total_reviews)} spark={trendCalls}
                hint={k.partial_reviews ? `${k.partial_reviews} partial (rules only, no score)` : "All fully scored"} />
              <Kpi icon="check" label="Calls passing" value={`${passedCalls} / ${scoredCalls}`} tone={rateColor(k.pass_rate)}
                ring={{ value: k.pass_rate, color: rateColor(k.pass_rate) }} hint={`Scoring ≥ ${t.pass}/10`} />
              <Kpi icon="alert" label="Calls at risk" value={`${riskCalls} / ${scoredCalls}`} tone={riskCalls > 0 ? BAD : GOOD}
                ring={{ value: k.at_risk_rate, color: riskCalls > 0 ? BAD : GOOD }} hint={`Scoring below ${t.at_risk}/10`} />
              {weakest && <Kpi icon="bug" label="Weakest criterion" value={fmt(weakest.avg_score, "/10", 1)} tone={scoreColor(weakest.avg_score, t)}
                hint={`${CRITERIA_LABEL[weakest.name] || weakest.name} · first to coach`} />}
              {strongest && <Kpi icon="target" label="Strongest criterion" value={fmt(strongest.avg_score, "/10", 1)} tone={scoreColor(strongest.avg_score, t)}
                hint={`${CRITERIA_LABEL[strongest.name] || strongest.name} · keep it up`} />}
              {delta !== null && <Kpi icon="escalate" label="Score change" value={`${delta >= 0 ? "+" : ""}${delta}`} tone={delta >= 0 ? GOOD : BAD}
                hint="Second half vs first half of the period" />}
              {shownOptional.map((o) => (
                <Kpi key={o.label} icon={o.icon} label={o.label} value={o.value} tone={o.tone} ring={o.ring} hint={o.hint} />
              ))}
            </div>
            {hiddenOptional.length > 0 && (
              <div className="flex flex-wrap items-center gap-2 rounded-2xl border border-dashed border-[#d9e1da] bg-white/60 px-4 py-3 text-[11px] text-[#748078]">
                <span className="font-semibold uppercase tracking-[.14em] text-[#89938d]">Nothing to report yet</span>
                {hiddenOptional.map((h) => <span key={h.label} className="rounded-full bg-[#f1f5f0] px-2.5 py-1">{h.label}</span>)}
                <span className="text-[#98a29c]">— these cards appear as soon as there is data</span>
              </div>
            )}

            {data.insights.length > 0 && (
              <Card title="What to act on" subtitle="Generated automatically from the numbers below.">
                <ul className="grid gap-2 md:grid-cols-2">
                  {data.insights.map((insight, i) => (
                    <li key={i} className={`flex gap-3 rounded-xl border px-3 py-2.5 text-sm leading-5 ${
                      insight.level === "alert" ? "border-rose-200 bg-rose-50 text-rose-900" : insight.level === "action" ? "border-amber-200 bg-amber-50 text-amber-950" : "border-[#e5e9e3] bg-[#f7f9f6] text-[#45584b]"}`}>
                      <span className={`mt-0.5 h-fit shrink-0 rounded px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider ${
                        insight.level === "alert" ? "bg-rose-600 text-white" : insight.level === "action" ? "bg-amber-500 text-white" : "bg-[#d6e2d8] text-[#34473b]"}`}>
                        {insight.level === "alert" ? "Alert" : insight.level === "action" ? "Action" : "Info"}
                      </span>
                      <span>{insight.text}</span>
                    </li>
                  ))}
                </ul>
              </Card>
            )}

            {/* ── trends ─────────────────────────────────────── */}
            <SectionLabel>Trends</SectionLabel>
            <div className="grid gap-5 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
              <Card title="Score trend" subtitle={data.trend.length >= 2 ? "Average global score per day (line) and number of calls (bars)" : "Score of each call, oldest to newest (a daily trend appears once there are 2+ days of data)"}>
                {data.trend.length >= 2 ? <TrendChart data={data.trend} pass={t.pass} /> : <CallsChart data={callTimeline} t={t} />}
              </Card>
              <Card title="Score distribution" subtitle="Number of calls per score band"><Histogram data={data.distribution} t={t} /></Card>
            </div>

            {/* ── quality drivers ────────────────────────────── */}
            <SectionLabel>What drives the score</SectionLabel>
            <div className={`grid gap-5 ${totalErrors > 0 ? "lg:grid-cols-3" : "lg:grid-cols-2"}`}>
              <Card title="Criteria" subtitle="Average score out of 10 · green tick = pass threshold">
                <HBars rows={criteriaRows} max={10} tick={t.pass} format={(v) => v.toFixed(2)} />
              </Card>
              <Card title="Points lost" subtitle="Weighted points lost vs a perfect 10 — where coaching pays off most">
                <ul className="space-y-3.5">
                  {pointsLostRows.map((r) => (
                    <li key={r.label}>
                      <div className="mb-1.5 flex items-center justify-between text-xs">
                        <span className="flex items-center gap-2 font-medium text-[#45584b]">
                          {r.label}
                          {r.priority && <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wider text-amber-800">Priority</span>}
                        </span>
                        <span className="font-semibold tabular-nums text-[#34473b]">−{r.value.toFixed(2)}</span>
                      </div>
                      <div className="h-2.5 rounded-full bg-[#eef2ed]">
                        <div className="h-2.5 rounded-full" style={{ width: `${(r.value / r.top) * 100}%`, background: r.priority ? "#d9822b" : "#c9b98a" }} />
                      </div>
                    </li>
                  ))}
                </ul>
              </Card>
              {totalErrors > 0 && (
              <Card title="Errors by severity" subtitle={`${totalErrors} error${totalErrors === 1 ? "" : "s"} recorded in total`}>
                {totalErrors === 0 ? <p className="text-sm text-[#89938d]">No errors recorded in this period.</p> : (
                  <div className="flex flex-col items-center gap-4">
                    <Donut slices={severitySlices} center={String(totalErrors)} centerLabel="errors" />
                    <Legend items={severitySlices.map((s) => ({ label: `${s.label} · `, color: s.color, value: String(s.value) }))} />
                  </div>
                )}
              </Card>
              )}
            </div>

            {totalErrors > 0 ? (
              <Card title="Errors by type" subtitle="Bar length = occurrences, split by severity">
                <ErrorStack rows={data.errors_by_type} />
              </Card>
            ) : (
              <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-[#e5e9e3] bg-white px-5 py-4 text-sm text-[#45584b] shadow-panel">
                <span className="grid h-7 w-7 place-items-center rounded-full bg-[#e8f5ee] text-[#1f7a4d]"><Icon name="check" className="h-4 w-4" /></span>
                <span><b>No errors recorded</b> in this period.</span>
                <span className="text-xs text-[#89938d]">Errors only appear when an automatic rule or the judge logs one. Low criterion scores can exist without a logged error.</span>
              </div>
            )}

            {/* ── segments & rules ───────────────────────────── */}
            <SectionLabel>Segments &amp; rules</SectionLabel>
            <div className="grid gap-5 lg:grid-cols-2">
              <Card title="Where it goes wrong" subtitle="Sorted from weakest to strongest average score · green tick = pass threshold"
                action={
                  <div role="tablist" className="flex shrink-0 gap-1 rounded-xl bg-[#f3f6f2] p-1 text-[11px] font-semibold">
                    {([["by_intent", "Reason"], ["by_profile", "Profile"], ["by_language", "Language"], ["by_model", "Judge"]] as const).map(([key, label]) => (
                      <button key={key} type="button" role="tab" aria-selected={dimension === key} onClick={() => setDimension(key)}
                        className={`rounded-lg px-2.5 py-1 transition ${dimension === key ? "bg-white text-forest shadow-sm" : "text-[#748078] hover:text-forest"}`}>{label}</button>
                    ))}
                  </div>
                }>
                <p className="sr-only">{dimensionLabel}</p>
                <BreakdownList rows={data[dimension] ?? []} t={t} />
              </Card>
              <Card title="Deterministic rule checks" subtitle="Pass rate of each reproducible check (lowest first)">
                {data.rule_checks.length === 0 ? <p className="text-sm text-[#89938d]">No rule check was run.</p> : (
                  <HBars rows={data.rule_checks.map((c) => ({ label: c.label, value: c.pass_rate, note: `${c.passed} passed · ${c.failed} failed`, color: rateColor(c.pass_rate, 95, 80) }))} max={100} format={(v) => `${v}%`} />
                )}
              </Card>
            </div>

            {/* ── actions ────────────────────────────────────── */}
            <SectionLabel>Next steps</SectionLabel>
            <div className="grid gap-5 lg:grid-cols-2">
              <Card title="Recurring coaching advice" subtitle="Same recommendation across several calls = systemic fix for Cashy">
                <ol className="space-y-2.5 text-sm leading-5 text-[#45584b]">
                  {data.top_recommendations.map((r) => (
                    <li key={r.text} className="flex gap-3 rounded-xl px-1 py-1 transition hover:bg-[#f7f9f6]">
                      <span className="h-fit w-9 shrink-0 rounded-md bg-[#eff6f0] py-0.5 text-center text-xs font-bold tabular-nums text-forest">×{r.count}</span>
                      <span>{r.text}</span>
                    </li>
                  ))}
                  {data.top_recommendations.length === 0 && <li className="text-[#89938d]">No recommendation yet.</li>}
                </ol>
              </Card>
              <Card title="Calls to review first" subtitle="Lowest scores — open them in Call reviews">
                <ul className="space-y-2">
                  {data.worst_calls.map((c) => (
                    <li key={c.report_id}>
                      <a href="/quality" className="flex items-center justify-between gap-3 rounded-xl border border-[#edf0eb] px-3 py-2 text-xs transition hover:border-[#cbd8cc] hover:bg-[#f8faf7]">
                        <span className="min-w-0">
                          <span className="font-semibold text-[#34473b]">Report #{c.report_id}</span> · {c.intent} · {c.profile}
                          {c.errors.length > 0 && <span className="mt-0.5 block truncate text-[10px] text-[#89938d]">{c.errors.map((e) => ERROR_LABEL[e] || e).join(" · ")}</span>}
                        </span>
                        <ScoreChip score={c.score} t={t} />
                      </a>
                    </li>
                  ))}
                </ul>
              </Card>
            </div>

            {(data.warnings.length > 0 || data.models.length > 0) && (
              <Card title="Audit health" subtitle="How trustworthy the evaluations themselves are">
                <div className="grid gap-6 text-xs text-[#56645b] sm:grid-cols-2">
                  <div>
                    <p className="mb-2 font-semibold text-[#34473b]">Warnings raised</p>
                    {data.warnings.length === 0 ? <span className="rounded-md bg-[#e8f5ee] px-2 py-1 font-semibold text-[#1f7a4d]">None</span> : (
                      <div className="flex flex-wrap gap-2">
                        {data.warnings.map((w) => <span key={w.type} className="rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-1 text-amber-900">{w.type} · <b>{w.count}</b></span>)}
                      </div>
                    )}
                  </div>
                  <div>
                    <p className="mb-2 font-semibold text-[#34473b]">Judge model</p>
                    <div className="flex flex-wrap gap-2">
                      {data.models.map((m) => <span key={m.name} className="rounded-lg border border-[#e5e9e3] bg-[#f7f9f6] px-2.5 py-1">{m.name} · <b>{m.count}</b></span>)}
                    </div>
                  </div>
                </div>
              </Card>
            )}
          </div>
        )}
      </div>
    </main>
  );
}
