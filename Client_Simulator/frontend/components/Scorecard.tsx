import { Scorecard } from "@/lib/api";

// QA scorecard shown when a call ends: total + grade, one bar per criterion, the checklist of mandatory
// steps, the critical failures, and what was good / bad on each advisor turn.
export default function ScorecardView({ sc }: { sc: Scorecard }) {
  const crit = Object.entries(sc.criteria);
  return (
    <div className="scorecard">
      <div className="sc-head">
        <div className={`sc-grade g-${sc.grade}`}>{sc.grade}</div>
        <div>
          <div className="sc-total num">{sc.total === null ? "–" : `${sc.total} / 100`}</div>
          <div className="hint">QA score of the advisor</div>
        </div>
      </div>

      {sc.critical_failures.length > 0 && (
        <ul className="sc-critical">
          {sc.critical_failures.map((c) => (
            <li key={c}>⛔ {c}</li>
          ))}
        </ul>
      )}
      {sc.warnings.length > 0 && (
        <ul className="sc-warn">
          {sc.warnings.map((w) => (
            <li key={w}>⚠ {w}</li>
          ))}
        </ul>
      )}

      <h3>Criteria</h3>
      <div className="sc-bars">
        {crit.map(([key, c]) => (
          <div key={key} className="sc-row" title={c.evidence.join(" · ")}>
            <span className="sc-label">
              {c.label} <small>({c.weight}%)</small>
            </span>
            <span className="sc-bar">
              <span
                className={`sc-fill ${c.na ? "na" : (c.score ?? 0) >= 70 ? "ok" : (c.score ?? 0) >= 40 ? "mid" : "ko"}`}
                style={{ width: `${c.na ? 0 : c.score}%` }}
              />
            </span>
            <span className="sc-val num">{c.na ? "n/a" : c.score}</span>
            <span className="sc-ev">{c.evidence[0]}</span>
          </div>
        ))}
      </div>

      <h3>Checklist</h3>
      <ul className="sc-check">
        {sc.checklist.map((c) => (
          <li key={c.step} className={c.done ? "done" : "todo"}>
            {c.done ? "✓" : "✗"} {c.step}
          </li>
        ))}
      </ul>

      <h3>Turn by turn</h3>
      <ol className="sc-turns">
        {sc.per_turn.map((t) => (
          <li key={t.turn}>
            <div className="sc-adv">“{t.advisor}”</div>
            <div className="sc-tags">
              {t.good.map((g) => (
                <span key={g} className="tag good">
                  {g}
                </span>
              ))}
              {t.bad.map((b) => (
                <span key={b} className="tag bad">
                  {b}
                </span>
              ))}
              {!t.good.length && !t.bad.length && <span className="tag">neutral</span>}
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}
