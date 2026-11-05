"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

const ADVISOR_API = "/api/advisor";

type Approval = {
  id: string;
  thread_id: string;
  customer_id: number;
  action: string;
  message: string;
  details: Record<string, unknown>;
  status: "pending" | "processing" | "approved" | "declined" | "failed";
  created_at: string | null;
  reviewed_at: string | null;
  result: { message?: string; detail?: string } | null;
};

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (response.status === 401 && typeof window !== "undefined") {
    window.location.assign("/login");
  }
  const raw = await response.text();
  let payload: unknown;
  try {
    payload = raw ? JSON.parse(raw) : null;
  } catch {
    throw new Error("The advisor service returned an unexpected response.");
  }
  if (!response.ok) {
    const body = payload as { detail?: unknown } | null;
    throw new Error(typeof body?.detail === "string" ? body.detail : `Request failed (${response.status}).`);
  }
  if (payload === null) throw new Error("The advisor service returned an empty response.");
  return payload as T;
}

function actionTitle(action: string): string {
  const titles: Record<string, string> = {
    block_account: "Block a bank account",
    create_transaction: "Record a transaction",
    update_transaction: "Change a transaction",
    delete_transaction: "Delete a transaction",
    manual_service_review: "Manual service follow-up",
  };
  return titles[action] || action.replaceAll("_", " ");
}

function explanation(action: string): string {
  const explanations: Record<string, string> = {
    block_account: "Blocking a bank account is a high-impact account change. Approval marks the selected account inactive in PostgreSQL; it does not block a card.",
    create_transaction: "The advisor wants to add a transaction record to the active customer's demo account. Review the amount, account, date, and description before deciding.",
    update_transaction: "The advisor wants to change an existing transaction record. Compare the current values with the proposed changes before deciding.",
    delete_transaction: "The advisor wants to remove a transaction record. This may be difficult to reverse; confirm the transaction details before deciding.",
    manual_service_review: "This prototype cannot execute the requested bank service. Approval authorizes an administrator to follow up manually; it does not block a card, file a dispute, transfer money, change a limit, or update profile details.",
  };
  return explanations[action] || "Review the advisor's requested action and details before deciding.";
}

function approvalDisposition(details: Record<string, unknown>): string | null {
  const actionDetails = details.details;
  if (!actionDetails || typeof actionDetails !== "object" || Array.isArray(actionDetails)) return null;
  const disposition = (actionDetails as Record<string, unknown>).disposition;
  return disposition === "review_only" ? "review_only" : null;
}

function humanize(value: string): string {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

function timestamp(value: string | null): string {
  if (!value) return "Just now";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function customerDetails(value: unknown): { name: string; email: string | null } | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  if (typeof record.name !== "string") return null;
  return {
    name: record.name,
    email: typeof record.email === "string" ? record.email : null,
  };
}

export default function AdminApprovalsPage() {
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [filter, setFilter] = useState<"pending" | "all">("pending");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);

  const loadApprovals = useCallback(async () => {
    try {
      const items = await requestJson<Approval[]>(
        `${ADVISOR_API}/api/admin/approvals?status=${filter}`,
      );
      setApprovals(items);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The approval inbox could not be loaded.");
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => {
    void loadApprovals();
    const interval = window.setInterval(() => void loadApprovals(), 4000);
    return () => window.clearInterval(interval);
  }, [loadApprovals]);

  const pendingCount = useMemo(
    () => approvals.filter((item) => item.status === "pending" || item.status === "processing").length,
    [approvals],
  );

  async function decide(approval: Approval, approved: boolean) {
    if (busyId) return;
    setBusyId(approval.id);
    setError("");
    try {
      const updated = await requestJson<Approval>(
        `${ADVISOR_API}/api/admin/approvals/${approval.id}/decision`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ approved }),
        },
      );
      setApprovals((current) => current.map((item) => item.id === updated.id ? updated : item));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The decision could not be submitted.");
      await loadApprovals();
    } finally {
      setBusyId(null);
    }
  }

  return (
    <main className="min-h-screen bg-paper text-ink">
      <header className="border-b border-[#e7eae4] bg-white/90">
        <div className="mx-auto flex max-w-[1080px] items-center justify-between gap-4 px-5 py-4 sm:px-8">
          <Link href="/" className="flex items-center gap-3">
            <div className="grid h-10 w-10 place-items-center rounded-2xl bg-forest text-xl font-semibold text-white">r</div>
            <div>
              <div className="text-[17px] font-semibold tracking-[-.04em]">relay<span className="text-forest">.</span></div>
              <div className="text-[10px] font-semibold uppercase tracking-[.2em] text-[#89938d]">Admin review</div>
            </div>
          </Link>
          <div className="flex items-center gap-2">
            <a href="/" className="rounded-xl border border-[#e6ebe5] bg-white px-3 py-2 text-[11px] font-semibold text-[#56645b] transition hover:border-[#cbd8cc] hover:text-forest">Back to conversation</a>
            <button
              type="button"
              onClick={async () => {
                await fetch("/api/auth/logout", { method: "POST" });
                window.location.assign("/login");
              }}
              className="rounded-xl px-2 py-2 text-[10px] font-semibold text-[#718076] hover:bg-[#f5f7f3]"
            >
              Sign out
            </button>
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-[900px] px-5 py-8 sm:px-8 sm:py-12">
        <div className="mb-8">
          <p className="mb-2 flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[.2em] text-forest"><span className="h-px w-5 bg-forest" />Human in the loop</p>
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div>
              <h1 className="text-[32px] font-semibold tracking-[-.055em] sm:text-[38px]">Approval inbox</h1>
              <p className="mt-2 max-w-xl text-[12px] leading-6 text-[#748078]">Review high-impact actions the advisor has paused and sent here. Each request includes a short reason and the details needed to decide.</p>
            </div>
            <span className="rounded-full border border-[#e4e9e2] bg-white px-3 py-2 text-[10px] font-semibold text-[#647168]">{pendingCount} awaiting review</span>
          </div>
        </div>

        <div role="note" className="mb-6 rounded-2xl border border-[#e7e9dc] bg-[#fffdf3] px-4 py-3.5 text-[10px] leading-5 text-[#77704f]">
          <strong className="font-semibold text-[#615a3e]">Approver access.</strong> Only signed-in approver accounts can open this inbox. Keep using synthetic data; backend approval authorization is being rolled out separately.
        </div>

        <div className="mb-5 flex items-center justify-between border-b border-[#e4e9e2]">
          <div className="flex gap-5">
            <button type="button" onClick={() => setFilter("pending")} className={`border-b-2 px-1 pb-3 text-[11px] font-semibold ${filter === "pending" ? "border-forest text-forest" : "border-transparent text-[#89938d]"}`}>Pending</button>
            <button type="button" onClick={() => setFilter("all")} className={`border-b-2 px-1 pb-3 text-[11px] font-semibold ${filter === "all" ? "border-forest text-forest" : "border-transparent text-[#89938d]"}`}>All requests</button>
          </div>
          <button type="button" onClick={() => void loadApprovals()} className="mb-2 rounded-lg px-2.5 py-1.5 text-[10px] font-semibold text-[#718076] hover:bg-white">Refresh</button>
        </div>

        {error && <div role="alert" className="mb-5 rounded-xl border border-rose-100 bg-rose-50 px-4 py-3 text-[11px] leading-5 text-rose-700">{error}</div>}

        {loading ? (
          <div className="rounded-[22px] border border-[#e5e9e3] bg-white p-8 text-center text-xs text-[#8a948d] shadow-panel">Loading approval requests…</div>
        ) : approvals.length === 0 ? (
          <div className="rounded-[22px] border border-dashed border-[#dfe6de] bg-white px-6 py-16 text-center shadow-panel">
            <div className="mx-auto mb-4 grid h-12 w-12 place-items-center rounded-2xl bg-[#eff6f0] text-xl text-forest">✓</div>
            <h2 className="text-sm font-semibold">{filter === "pending" ? "Nothing needs your attention" : "No approval requests yet"}</h2>
            <p className="mx-auto mt-2 max-w-sm text-[11px] leading-5 text-[#89938d]">{filter === "pending" ? "When the advisor requests a financial action, the conversation will pause and its request will appear here." : "Requests created by advisor conversations will appear here."}</p>
            <Link href="/" className="mt-5 inline-flex rounded-xl bg-forest px-4 py-2.5 text-[10px] font-semibold text-white hover:bg-[#0a3f32]">Return to dashboard</Link>
          </div>
        ) : (
          <div className="space-y-4">
            {approvals.map((approval) => {
              const isPending = approval.status === "pending";
              const isBusy = busyId === approval.id || approval.status === "processing";
              const customer = customerDetails(approval.details.customer);
              const isReviewOnly = approvalDisposition(approval.details) === "review_only";
              const actionDetails = Object.fromEntries(
                Object.entries(approval.details).filter(([key]) => key !== "customer"),
              );
              return (
                <article key={approval.id} className="overflow-hidden rounded-[22px] border border-[#e5e9e3] bg-white shadow-panel">
                  <div className="border-b border-[#edf0eb] bg-[#fbfcfa] px-5 py-4 sm:px-6">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div>
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="rounded-full bg-amber-100 px-2.5 py-1 text-[9px] font-semibold uppercase tracking-[.11em] text-amber-900">{humanize(approval.action)}</span>
                          <span className={`rounded-full px-2.5 py-1 text-[9px] font-semibold uppercase tracking-[.11em] ${approval.status === "pending" ? "bg-[#fff4dc] text-[#916411]" : approval.status === "processing" ? "bg-blue-50 text-blue-700" : approval.status === "approved" ? "bg-[#e6f4e9] text-forest" : approval.status === "declined" ? "bg-[#f0f2ee] text-[#707b73]" : "bg-rose-50 text-rose-700"}`}>{approval.status}</span>
                        </div>
                        <h2 className="mt-3 text-[15px] font-semibold tracking-[-.02em]">{approval.message}</h2>
                        <p className="mt-1 text-[10px] text-[#929c94]">
                          Customer #{approval.customer_id}
                          {customer && <> · {customer.name}{customer.email ? ` · ${customer.email}` : ""}</>}
                          {" · Submitted "}{timestamp(approval.created_at)}
                        </p>
                      </div>
                      <span className="text-[9px] tabular-nums text-[#a0a9a2]">Request {approval.id.slice(0, 8)}</span>
                    </div>
                  </div>

                  <div className="px-5 py-5 sm:px-6">
                    <div className="rounded-xl border border-[#e8eee7] bg-[#f7f9f6] p-4">
                      <p className="text-[9px] font-semibold uppercase tracking-[.14em] text-forest">Why the advisor sent this</p>
                      <p className="mt-2 text-[11px] leading-5 text-[#637168]">{isReviewOnly
                        ? "This request cannot be approved for execution. A decision here only acknowledges or declines specialist review; no operation will be performed."
                        : explanation(approval.action)}</p>
                      <p className="mt-2 border-t border-[#e7ece5] pt-2 text-[11px] leading-5 text-[#3d4c42]">{approval.message}</p>
                    </div>

                    <div className="mt-4">
                      <p className="mb-2 text-[9px] font-semibold uppercase tracking-[.14em] text-[#89938d]">Action details from the advisor</p>
                      {Object.keys(actionDetails).length ? (
                        <dl className="divide-y divide-[#edf0eb] rounded-xl border border-[#e9ede8] px-3">
                          {Object.entries(actionDetails).map(([group, value]) => (
                            <div key={group} className="py-3">
                              <dt className="mb-2 text-[9px] font-semibold uppercase tracking-[.12em] text-[#748078]">{humanize(group)}</dt>
                              {value && typeof value === "object" && !Array.isArray(value) ? (
                                <dl className="grid gap-x-4 gap-y-2 sm:grid-cols-2">
                                  {Object.entries(value as Record<string, unknown>).map(([key, item]) => (
                                    <div key={key} className="min-w-0">
                                      <dt className="text-[9px] text-[#929c94]">{humanize(key)}</dt>
                                      <dd className="mt-0.5 whitespace-pre-wrap break-words text-[10px] font-medium leading-4 text-[#38483d]">{displayValue(item)}</dd>
                                    </div>
                                  ))}
                                </dl>
                              ) : (
                                <dd className="whitespace-pre-wrap break-words text-[10px] leading-4 text-[#38483d]">{displayValue(value)}</dd>
                              )}
                            </div>
                          ))}
                        </dl>
                      ) : (
                        <p className="rounded-xl border border-[#e9ede8] px-3 py-3 text-[10px] text-[#89938d]">The advisor did not provide additional structured details.</p>
                      )}
                    </div>

                    {approval.result?.message && !isPending && (
                      <p role="status" className="mt-4 rounded-xl bg-[#f5f7f3] px-3 py-2.5 text-[10px] leading-5 text-[#647168]">Advisor response: {approval.result.message}</p>
                    )}
                    {approval.result?.detail && (
                      <p role="alert" className="mt-4 rounded-xl bg-rose-50 px-3 py-2.5 text-[10px] leading-5 text-rose-700">Review failed: {approval.result.detail}</p>
                    )}

                    {isPending && (
                      <div className="mt-5 flex flex-wrap items-center justify-between gap-3 border-t border-[#edf0eb] pt-4">
                        <p className="max-w-md text-[9px] leading-4 text-[#929c94]">{isReviewOnly
                          ? "This restricted request is recorded for specialist review only; approval cannot authorize the operation."
                          : approval.action === "manual_service_review"
                            ? "Approval authorizes manual follow-up only; no bank-service operation is performed by this prototype."
                            : "Approving resumes the advisor’s paused operation. Declining resumes the conversation without allowing that action."}</p>
                        <div className="flex gap-2">
                          <button type="button" onClick={() => void decide(approval, false)} disabled={Boolean(busyId)} className="rounded-xl border border-[#e2e8e1] bg-white px-4 py-2.5 text-[10px] font-semibold text-[#67746b] transition hover:bg-[#f5f7f3] disabled:opacity-50">Decline</button>
                          <button type="button" onClick={() => void decide(approval, true)} disabled={Boolean(busyId)} className="rounded-xl bg-forest px-4 py-2.5 text-[10px] font-semibold text-white transition hover:bg-[#0a3f32] disabled:opacity-50">{isBusy ? "Submitting…" : isReviewOnly ? "Acknowledge review" : approval.action === "manual_service_review" ? "Authorize follow-up" : "Approve action"}</button>
                        </div>
                      </div>
                    )}
                    {approval.status === "processing" && <p className="mt-4 text-right text-[10px] text-blue-700">Applying decision…</p>}
                  </div>
                </article>
              );
            })}
          </div>
        )}

        <p className="mt-8 text-center text-[9px] leading-5 text-[#a0a9a2]">Relay local demo · Approval decisions resume the advisor conversation; manual service approvals authorize follow-up only. Use test data only.</p>
      </div>
    </main>
  );
}
