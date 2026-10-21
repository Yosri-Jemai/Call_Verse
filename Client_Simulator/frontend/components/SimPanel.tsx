"use client";

import { useEffect, useState } from "react";
import { Kpis, PolicyResult, Scenario, SimResult, getScenarios, simulate } from "@/lib/api";

type Metric = { key: keyof Kpis; label: string; better: 1 | -1 | 0; fmt: (v: number) => string };

const METRICS: Metric[] = [
  { key: "calls", label: "Calls", better: 0, fmt: (v) => String(v) },
  { key: "abandon_rate", label: "Abandon rate", better: -1, fmt: (v) => (v * 100).toFixed(1) + "%" },
  { key: "avg_wait_s", label: "Average wait", better: -1, fmt: (v) => v.toFixed(1) + " s" },
  { key: "sla_%", label: "Answered within 60 s", better: 1, fmt: (v) => v.toFixed(1) + "%" },
  { key: "avg_satisfaction", label: "Satisfaction", better: 1, fmt: (v) => v.toFixed(2) + " / 5" },
  { key: "resolved_%", label: "Resolved", better: 1, fmt: (v) => v.toFixed(1) + "%" },
  { key: "fraud_leaked", label: "Fraudsters served", better: -1, fmt: (v) => String(v) },
];

const OUTCOMES: { key: string; label: string; color: string }[] = [
  { key: "resolved", label: "Resolved", color: "var(--good)" },
  { key: "escalated", label: "Escalated", color: "var(--accent)" },
  { key: "max_turns", label: "Turn limit", color: "var(--muted)" },
  { key: "hung_up", label: "Hung up", color: "var(--warn)" },
  { key: "abandoned", label: "Abandoned in queue", color: "var(--bad)" },
];

function OutcomeBar({ r }: { r: PolicyResult }) {
  const total = Object.values(r.statuses).reduce((a, b) => a + b, 0) || 1;
  return (
    <div className="outcome-bar" role="img" aria-label="Call outcomes">
      {OUTCOMES.map((o) => {
        const n = r.statuses[o.key] ?? 0;
        return n ? (
          <span key={o.key} style={{ width: `${(n / total) * 100}%`, background: o.color }} title={`${o.label}: ${n}`} />
        ) : null;
      })}
    </div>
  );
}

function ResultCard({ r }: { r: SimResult }) {
  const { fifo, priority } = r.results;
  return (
    <article className="block result">
      <h2>{r.scenario.replace(/_/g, " ")}</h2>
      <p className="hint">
        {r.n_advisors} advisors, {r.duration_min} min, seed {r.seed}
      </p>
      <table>
        <thead>
          <tr>
            <th scope="col">Metric</th>
            <th scope="col">First come, first served</th>
            <th scope="col">Most urgent first</th>
          </tr>
        </thead>
        <tbody>
          {METRICS.map((m) => {
            const a = fifo.kpis[m.key];
            const b = priority.kpis[m.key];
            const win = m.better === 0 || a === b ? null : (b - a) * m.better > 0 ? "priority" : "fifo";
            return (
              <tr key={m.key}>
                <th scope="row">{m.label}</th>
                <td className={`num ${win === "fifo" ? "win" : ""}`}>{m.fmt(a)}</td>
                <td className={`num ${win === "priority" ? "win" : ""}`}>{m.fmt(b)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="outcomes">
        <div>
          <span className="hint">First come, first served</span>
          <OutcomeBar r={fifo} />
        </div>
        <div>
          <span className="hint">Most urgent first</span>
          <OutcomeBar r={priority} />
        </div>
      </div>
      <ul className="legend">
        {OUTCOMES.map((o) => (
          <li key={o.key}>
            <i style={{ background: o.color }} />
            {o.label}
          </li>
        ))}
      </ul>
    </article>
  );
}

export default function SimPanel() {
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [scenario, setScenario] = useState("all");
  const [advisors, setAdvisors] = useState(3);
  const [duration, setDuration] = useState(30);
  const [seed, setSeed] = useState(7);
  const [results, setResults] = useState<SimResult[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    getScenarios()
      .then(setScenarios)
      .catch((e: Error) => setError(e.message));
  }, []);

  async function run() {
    setBusy(true);
    setError("");
    setResults([]);
    try {
      const names = scenario === "all" ? scenarios.map((s) => s.name) : [scenario];
      const out: SimResult[] = [];
      for (const name of names) {
        out.push(await simulate({ scenario: name, n_advisors: advisors, duration_min: duration, seed }));
        setResults([...out]);
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="sim-layout">
      <section className="block controls">
        <h2>Run a simulation</h2>
        <p className="hint">
          The same clients arrive in both runs. Only the order in which advisors pick them up changes.
        </p>
        <div className="controls-row">
          <label>
            Scenario
            <select value={scenario} onChange={(e) => setScenario(e.target.value)}>
              <option value="all">All scenarios</option>
              {scenarios.map((s) => (
                <option key={s.name} value={s.name}>
                  {s.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Advisors
            <input type="number" min={1} max={50} value={advisors} onChange={(e) => setAdvisors(Number(e.target.value))} />
          </label>
          <label>
            Duration (min)
            <input type="number" min={1} max={240} value={duration} onChange={(e) => setDuration(Number(e.target.value))} />
          </label>
          <label>
            Seed
            <input type="number" value={seed} onChange={(e) => setSeed(Number(e.target.value))} />
          </label>
          <button className="primary" onClick={run} disabled={busy}>
            {busy ? "Running…" : "Run simulation"}
          </button>
        </div>
      </section>

      {error && (
        <div className="error" role="alert">
          {error}
          <button className="link" onClick={() => setError("")}>
            Dismiss
          </button>
        </div>
      )}

      {results.length === 0 && !busy && !error && (
        <div className="empty">
          <h1>Compare two queue policies</h1>
          <p>Pick a scenario and run it. The better value in each row is highlighted.</p>
        </div>
      )}

      <div className="results">
        {results.map((r) => (
          <ResultCard key={r.scenario} r={r} />
        ))}
      </div>
    </div>
  );
}
