"use client";

import { useEffect, useState } from "react";

const CLIENT_API = process.env.NEXT_PUBLIC_CLIENT_API_URL || "http://127.0.0.1:8001";

type ReportSummary = {
  database_id: number;
  call_id: string;
  created_at: string;
  scenario: string | null;
  score_global?: number | null;
  overall_score_0_to_100: number | null;
  summary: string | null;
  status: string | null;
  version_grille?: string;
};

type QualityError = {
  type: string;
  gravite: string;
  tour: number | null;
  description: string;
  origine: string;
};

type QualityReport = ReportSummary & {
  conversation_id?: string;
  version_grille?: string;
  stored_at?: string;
  score_global?: number | null;
  criteres?: Record<string, { score: number; explanation: string; evidence_turns: number[] }>;
  erreurs?: QualityError[];
  explication?: string;
  avertissements?: { type: string; tour?: number; description: string }[];
  rule_evaluation: {
    score_0_to_10?: number | null;
    score_0_to_5?: number | null;
    checks: { id: string; label: string; passed: boolean | null; turns?: number[]; severity?: string }[];
    errors?: QualityError[];
    warnings?: { type: string; tour?: number; description: string }[];
    findings?: { id: string; severity: string; turns: number[]; explanation: string }[];
    recommendations: string[];
    evidence_scope: string;
  };
  llm_judgment: {
    status: string;
    provider: string;
    model?: string;
    summary?: string;
    criteria?: Record<string, { score: number; explanation: string; evidence_turns: number[] }>;
  };
  advisor_trace: {
    turn: number;
    tools_called: string[];
    tool_calls?: { id?: string; name: string; arguments?: unknown; result?: unknown }[];
    policy_sources: { source?: string; title?: string; source_url?: string; text?: string }[];
  }[];
  errors?: QualityError[];
  recommendations: string[];
};

async function requestJson<T>(url: string): Promise<T> {
  const response = await fetch(url);
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = payload && typeof payload.detail === "string" ? payload.detail : `Request failed (${response.status}).`;
    throw new Error(detail);
  }
  if (payload === null) throw new Error("The Client Simulator API returned an empty response.");
  return payload as T;
}

function displayDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

export default function QualityPage() {
  const [history, setHistory] = useState<ReportSummary[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [report, setReport] = useState<QualityReport | null>(null);
  const [loadingHistory, setLoadingHistory] = useState(true);
  const [loadingReport, setLoadingReport] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    requestJson<ReportSummary[]>(`${CLIENT_API}/api/quality/reports?limit=50`)
      .then((items) => {
        if (!active) return;
        setHistory(items);
        setSelectedId(items[0]?.database_id ?? null);
      })
      .catch((reason) => {
        if (active) setError(reason instanceof Error ? reason.message : "Could not load report history.");
      })
      .finally(() => {
        if (active) setLoadingHistory(false);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (selectedId === null) {
      setReport(null);
      return;
    }
    let active = true;
    setLoadingReport(true);
    setError("");
    requestJson<QualityReport>(`${CLIENT_API}/api/quality/reports/${selectedId}`)
      .then((item) => {
        if (active) setReport(item);
      })
      .catch((reason) => {
        if (active) setError(reason instanceof Error ? reason.message : "Could not load this report.");
      })
      .finally(() => {
        if (active) setLoadingReport(false);
      });
    return () => {
      active = false;
    };
  }, [selectedId]);

  return (
    <main className="min-h-screen bg-paper text-ink">
      <header className="border-b border-[#e7eae4] bg-white/80">
        <div className="mx-auto flex max-w-[1440px] items-center justify-between px-5 py-4 sm:px-8">
          <div>
            <p className="text-[17px] font-semibold tracking-[-.04em]">Quality Analyst</p>
            <p className="text-[10px] font-semibold uppercase tracking-[.2em] text-[#89938d]">Call review & advisor coaching</p>
          </div>
          <nav className="flex gap-2">
            <a href="/quality/dashboard" className="rounded-xl border border-[#e6ebe5] bg-white px-3 py-2 text-[11px] font-semibold text-[#56645b] transition hover:border-[#cbd8cc] hover:text-forest">
              Dashboard
            </a>
            <a href="/" className="rounded-xl border border-[#e6ebe5] bg-white px-3 py-2 text-[11px] font-semibold text-[#56645b] transition hover:border-[#cbd8cc] hover:text-forest">
              Back to Relay
            </a>
          </nav>
        </div>
      </header>

      <div className="mx-auto max-w-[1440px] px-5 py-7 sm:px-8 lg:py-10">
        <div className="mb-6">
          <p className="text-[11px] font-semibold uppercase tracking-[.2em] text-forest">Post-call reviews</p>
          <h1 className="mt-2 text-3xl font-semibold tracking-[-.05em]">Quality and coaching</h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-[#748078]">
            Review evidence, scores, and practical coaching suggestions. Recommendations are for the team to assess; they are not automatically applied to Cashy.
          </p>
        </div>

        {error && <div role="alert" className="mb-5 rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800">{error}</div>}

        <div className="grid gap-5 lg:grid-cols-[320px_minmax(0,1fr)]">
          <aside className="rounded-2xl border border-[#e5e9e3] bg-white p-4 shadow-panel">
            <h2 className="mb-3 text-xs font-semibold uppercase tracking-[.15em] text-[#69766d]">Saved reports</h2>
            {loadingHistory ? (
              <p className="text-sm text-[#89938d]">Loading report history…</p>
            ) : history.length === 0 ? (
              <p className="rounded-xl bg-[#f7f9f6] p-4 text-sm leading-6 text-[#748078]">
                No saved reviews yet. Complete a simulation in Relay to create the first report.
              </p>
            ) : (
              <ul className="space-y-2">
                {history.map((item) => (
                  <li key={item.database_id}>
                    <button
                      type="button"
                      onClick={() => setSelectedId(item.database_id)}
                      aria-current={selectedId === item.database_id ? "true" : undefined}
                      className={`w-full rounded-xl border p-3 text-left transition ${
                        selectedId === item.database_id
                          ? "border-[#b8d0bb] bg-[#f1f7f1]"
                          : "border-[#edf0eb] bg-white hover:bg-[#f8faf7]"
                      }`}
                    >
                      <span className="flex items-center justify-between gap-2 text-xs font-semibold text-[#34473b]">
                        <span>Report #{item.database_id}</span>
                        <span>
                          {item.score_global != null
                            ? `${item.score_global}/10`
                            : item.overall_score_0_to_100 != null
                              ? `${item.overall_score_0_to_100}/100`
                              : "Partial · no score"}
                        </span>
                      </span>
                      <span className="mt-1 block text-[10px] text-[#7b877e]">{item.scenario || "Scenario unavailable"} · {displayDate(item.created_at)}</span>
                      {item.summary && <span className="mt-2 block line-clamp-2 text-[11px] leading-4 text-[#68756d]">{item.summary}</span>}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </aside>

          <section aria-live="polite" className="min-h-80 rounded-2xl border border-[#e5e9e3] bg-white p-5 shadow-panel sm:p-7">
            {loadingReport ? (
              <p className="text-sm text-[#89938d]">Loading selected review…</p>
            ) : !report ? (
              <div className="flex min-h-64 items-center justify-center text-center text-sm text-[#89938d]">
                Select a saved report to see the evaluation and coaching.
              </div>
            ) : (
              <div>
                <div className="flex flex-wrap items-start justify-between gap-4 border-b border-[#edf0eb] pb-5">
                  <div>
                    <p className="text-[10px] font-semibold uppercase tracking-[.15em] text-forest">Report #{report.database_id} · {report.llm_judgment.provider || "rules"}</p>
                    <h2 className="mt-2 text-xl font-semibold">{report.scenario || "Call review"}</h2>
                    <p className="mt-1 text-xs text-[#859087]">{displayDate(report.stored_at || report.created_at)} · Call {report.call_id}</p>
                    <p className="mt-1 text-xs text-[#859087]">Grille {report.version_grille || "legacy"}{report.llm_judgment.model ? ` · Modèle: ${report.llm_judgment.model}` : ""}</p>
                    {report.llm_judgment.status === "skipped" && (
                      <p className="mt-2 text-xs font-medium text-amber-700">
                        Partial review: deterministic checks only; qualitative scoring was not run.
                      </p>
                    )}
                  </div>
                  <div className="rounded-2xl bg-[#eff6f0] px-5 py-3 text-center text-2xl font-bold text-forest">
                    {report.score_global != null
                      ? <>{report.score_global}<span className="ml-1 text-xs font-medium">/ 10</span></>
                      : report.overall_score_0_to_100 != null
                        ? <>{report.overall_score_0_to_100}<span className="ml-1 text-xs font-medium">/ 100</span></>
                        : <span className="text-sm">Partial · no score</span>}
                  </div>
                </div>

                {(report.explication || report.llm_judgment.summary) && <p className="mt-5 text-sm leading-6 text-[#56645b]">{report.explication || report.llm_judgment.summary}</p>}

                {(report.criteres || report.llm_judgment.criteria) && (
                  <section className="mt-6">
                    <h3 className="text-xs font-semibold uppercase tracking-[.14em] text-[#69766d]">Advisor assessment</h3>
                    <div className="mt-3 grid gap-3 sm:grid-cols-2">
                      {Object.entries(report.criteres || report.llm_judgment.criteria || {}).map(([name, criterion]) => (
                        <article key={name} className="rounded-xl bg-[#f7f9f6] p-4">
                          <div className="flex justify-between gap-3 text-sm font-semibold capitalize text-[#34473b]">
                            <span>{name}</span><span className="text-forest">{criterion.score}/{report.version_grille ? 10 : 5}</span>
                          </div>
                          <p className="mt-2 text-xs leading-5 text-[#718078]">{criterion.explanation}</p>
                          {criterion.evidence_turns.length > 0 && <p className="mt-2 text-[10px] text-[#929d95]">Advisor turns: {criterion.evidence_turns.join(", ")}</p>}
                        </article>
                      ))}
                    </div>
                  </section>
                )}

                <section className="mt-6">
                  <h3 className="text-xs font-semibold uppercase tracking-[.14em] text-[#69766d]">Rule checks</h3>
                  {report.rule_evaluation.checks.length > 0 ? (
                    <ul className="mt-3 space-y-2">
                      {report.rule_evaluation.checks.map((check) => (
                        <li key={check.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-[#edf0eb] px-3 py-2 text-xs">
                          <span className="text-[#56645b]">{check.label}</span>
                          <span className={check.passed === true ? "font-semibold text-emerald-700" : check.passed === false ? "font-semibold text-rose-700" : "text-[#89938d]"}>
                            {check.passed === true ? "Passed" : check.passed === false ? check.severity : "Not verified"}
                          </span>
                        </li>
                      ))}
                    </ul>
                  ) : <p className="mt-2 text-xs text-[#89938d]">No deterministic checks were available.</p>}
                  {((report.erreurs || report.rule_evaluation.errors || []).length > 0 || (report.rule_evaluation.findings || []).length > 0) && (
                    <ul className="mt-3 space-y-2">
                      {(report.erreurs || report.rule_evaluation.errors || []).map((finding, index) => (
                        <li key={`${finding.type}-${finding.tour}-${index}`} className="rounded-lg border border-amber-100 bg-amber-50/70 px-3 py-2 text-xs leading-5 text-[#725637]">
                          <span className="font-semibold uppercase">{finding.gravite} · {finding.type}</span>
                          {finding.tour !== null && <span> · Tour {finding.tour}</span>}
                          <p>{finding.description}</p>
                          <p className="text-[10px]">Détection : {finding.origine}</p>
                        </li>
                      ))}
                      {(report.erreurs || report.rule_evaluation.errors || []).length === 0 && (report.rule_evaluation.findings || []).map((finding, index) => (
                        <li key={`${finding.id}-${index}`} className="rounded-lg border border-amber-100 bg-amber-50/70 px-3 py-2 text-xs leading-5 text-[#725637]">
                          <span className="font-semibold uppercase">{finding.severity}</span>
                          {finding.turns.length > 0 && <span> · Tour {finding.turns.join(", ")}</span>}
                          <p>{finding.explanation}</p>
                        </li>
                      ))}
                    </ul>
                  )}
                </section>

                {(report.avertissements || report.rule_evaluation.warnings || []).length > 0 && (
                  <section className="mt-5 rounded-xl border border-sky-100 bg-sky-50/70 p-4">
                    <h3 className="text-xs font-semibold uppercase tracking-[.14em] text-sky-900">Audit warnings</h3>
                    <ul className="mt-2 space-y-2 text-xs leading-5 text-sky-900">
                      {(report.avertissements || report.rule_evaluation.warnings || []).map((warning, index) => (
                        <li key={`${warning.type}-${index}`}>{warning.tour != null ? `Tour ${warning.tour} · ` : ""}{warning.description}</li>
                      ))}
                    </ul>
                  </section>
                )}

                <section className="mt-6 rounded-2xl border border-[#dce9dc] bg-[#fbfdfb] p-4">
                  <h3 className="text-xs font-semibold uppercase tracking-[.14em] text-forest">Advisor coaching recommendations</h3>
                  <p className="mt-1 text-[11px] leading-5 text-[#7b877e]">These are suggestions for the team to review and use when improving Cashy; no changes are made automatically.</p>
                  {report.recommendations.length > 0 ? (
                    <ol className="mt-3 list-decimal space-y-2 pl-5 text-sm leading-5 text-[#56645b]">
                      {report.recommendations.map((recommendation, index) => <li key={`${index}-${recommendation}`}>{recommendation}</li>)}
                    </ol>
                  ) : <p className="mt-3 text-sm text-[#89938d]">No coaching recommendation was generated for this call.</p>}
                </section>

                <section className="mt-6">
                  <h3 className="text-xs font-semibold uppercase tracking-[.14em] text-[#69766d]">Cashy tool and policy evidence</h3>
                  {report.advisor_trace.length === 0 ? (
                    <p className="mt-2 text-xs leading-5 text-[#89938d]">No Cashy trace is attached. Manual operator replies do not produce Cashy tool traces.</p>
                  ) : (
                    <ul className="mt-3 space-y-3">
                      {report.advisor_trace.map((trace, index) => (
                        <li key={`${trace.turn}-${index}`} className="rounded-xl border border-[#edf0eb] p-3 text-xs">
                          <p className="font-semibold text-[#45584b]">Advisor turn {trace.turn}: {trace.tools_called.length ? trace.tools_called.join(", ") : "no tools reported"}</p>
                          {trace.tool_calls?.map((call, callIndex) => (
                            <details key={`${call.id || call.name}-${callIndex}`} className="mt-2 rounded-lg bg-[#f7f9f6] p-2">
                              <summary className="cursor-pointer font-medium">{call.name} · arguments and result</summary>
                              <pre className="mt-2 whitespace-pre-wrap break-words text-[10px] leading-4">Arguments: {JSON.stringify(call.arguments, null, 2)}{"\n"}Result: {JSON.stringify(call.result, null, 2)}</pre>
                            </details>
                          ))}
                          {trace.policy_sources.map((source, sourceIndex) => (
                            <div key={`${source.source}-${sourceIndex}`} className="mt-2 rounded-lg bg-[#f7f9f6] p-3 text-[#718078]">
                              <p className="font-medium">{source.title || source.source || "Policy source"}{source.source ? ` · ${source.source}` : ""}</p>
                              {source.source_url && <a className="mt-1 inline-block underline" href={source.source_url} target="_blank" rel="noreferrer">Open source</a>}
                              {source.text && <p className="mt-2 whitespace-pre-wrap leading-5">{source.text}</p>}
                            </div>
                          ))}
                        </li>
                      ))}
                    </ul>
                  )}
                  <p className="mt-3 text-[10px] leading-5 text-[#929d95]">{report.rule_evaluation.evidence_scope}</p>
                </section>
              </div>
            )}
          </section>
        </div>
      </div>
    </main>
  );
}
