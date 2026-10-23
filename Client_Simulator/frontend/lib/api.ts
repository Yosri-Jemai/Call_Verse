export const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Turn = { role: "client" | "conseiller"; text: string };

export type CallSummary = {
  request_id: string;
  profile: string;
  intent: string;
  is_fraudster: boolean;
  status: string;
  turns: number;
  waited_s: number;
  satisfaction: number;
  resolved: boolean;
  escalated: boolean;
  abandoned: boolean;
};

export type ScoreCriterion = { label: string; weight: number; na: boolean; score: number | null; evidence: string[] };
export type Scorecard = {
  total: number | null;
  grade: string;
  criteria: Record<string, ScoreCriterion>;
  critical_failures: string[];
  warnings: string[];
  checklist: { step: string; done: boolean }[];
  per_turn: { turn: number; advisor: string; good: string[]; bad: string[]; bad_words?: string[]; client_bad_words?: string[]; client: string }[];
};

export type Moderation = {
  count: number; // advisor messages with bad words so far
  points: number; // sum of their levels (1 mild, 2 vulgar, 3 severe)
  ended?: boolean; // the client hung up because of the advisor's bad language
  strikes_left?: number; // offences left before the client hangs up
  warnings_max?: number | null; // warnings the advisor gets before the call ends (2); null = the call is never ended
  client_warnings?: number; // reminders of respect the advisor gave to the client
  client_abuse_ended?: boolean; // the call was closed after the client's 3rd outburst
  client_swears?: number; // times the CLIENT lost patience and swore
  client_swore_last?: boolean; // the client's last reply contained bad words
  last: { level: number; level_name: string; words: string[]; categories: string[] } | null; // last message only
};

export type CallState = {
  call_id: string;
  client: {
    name: string;
    profile: string;
    intent: string;
    urgency: number;
    channel: string;
    patience_s: number;
    language?: string;
    client_id?: string;
  };
  status: string;
  turn: number;
  waited_s: number;
  mood: number;
  emotion?: string;
  sentiment?: string;
  satisfaction: number;
  history: Turn[];
  summary: CallSummary | null;
  scorecard?: Scorecard | null;
  moderation?: Moderation;
  engine?: {
    mode: "rules" | "llm" | "mixed" | "llm_failed";
    backend: string;
    model: string;
    darija_backend?: string;
    darija_model?: string;
    darija_retries?: number;
    llm_turns: number;
    rule_turns: number;
    last_error: string;
    warning: string;
  };
};

export type Kpis = {
  calls: number;
  abandon_rate: number;
  avg_wait_s: number;
  "sla_%": number;
  avg_satisfaction: number;
  "resolved_%": number;
  fraud_leaked: number;
};

export type PolicyResult = { kpis: Kpis; statuses: Record<string, number> };

export type SimResult = {
  scenario: string;
  n_advisors: number;
  duration_min: number;
  seed: number;
  results: { fifo: PolicyResult; priority: PolicyResult };
};

export type Scenario = { name: string; calls_per_min: number; description?: string };

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(API + path, init);
  } catch {
    throw new Error(`Cannot reach the backend at ${API}. Is "uvicorn api:app --port 8000" running?`);
  }
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {}
    throw new Error(msg);
  }
  return res.json() as Promise<T>;
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const getScenarios = () => request<Scenario[]>("/api/scenarios");

export const startCall = (body: {
  scenario: string;
  seed?: number | null;
  wait_seconds: number;
  llm: string;
  model?: string | null;
  darija_llm?: string;
  darija_model?: string | null;
  language?: string;
  difficulty?: number;
}) => request<CallState>("/api/calls", json(body));

export const sendReply = (id: string, message: string) =>
  request<CallState>(`/api/calls/${id}/reply`, json({ message }));

export const endCall = (id: string) =>
  request<CallState>(`/api/calls/${id}/end`, { method: "POST" });

export const simulate = (body: {
  scenario: string;
  n_advisors: number;
  duration_min: number;
  seed: number;
}) => request<SimResult>("/api/simulate", json(body));

export async function speechToText(blob: Blob, language?: string): Promise<string> {
  const ext = blob.type.includes("mp4") ? "mp4" : blob.type.includes("ogg") ? "ogg" : "webm";
  const form = new FormData();
  form.append("file", blob, `voice.${ext}`);
  const q = language ? `?language=${encodeURIComponent(language)}` : "";
  const r = await request<{ text: string }>(`/api/audio/stt${q}`, { method: "POST", body: form });
  return r.text;
}

export type TtsOptions = { lang?: string; slow?: boolean; name?: string; gender?: "f" | "m"; rate?: number; pitch?: number };

export async function textToSpeech(text: string, o: TtsOptions = {}): Promise<Blob> {
  let res: Response;
  try {
    res = await fetch(API + "/api/audio/tts", json({ text, ...o }));
  } catch {
    throw new Error(`Cannot reach the backend at ${API}.`);
  }
  if (!res.ok) {
    let msg = res.statusText;
    try {
      msg = (await res.json()).detail ?? msg;
    } catch {}
    throw new Error(msg);
  }
  return res.blob();
}

export const getAudioStatus = () => request<{ stt: string; tts: string }>("/api/audio/status");

export const getLlmModels = (backend: string) =>
  request<{ backend: string; models: string[]; error?: string }>(`/api/llm/models?backend=${encodeURIComponent(backend)}`);
