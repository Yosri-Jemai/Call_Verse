"use client";

import Link from "next/link";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";

const CLIENT_API = process.env.NEXT_PUBLIC_CLIENT_API_URL || "http://127.0.0.1:8001";
const ADVISOR_API = process.env.NEXT_PUBLIC_ADVISOR_API_URL || "http://127.0.0.1:8000";

type Role = "client" | "conseiller";
type Message = { role: Role; text: string };
type Scenario = { name: string; description: string };
type CallSnapshot = {
  call_id: string;
  status: string;
  turn: number;
  mood: number;
  sentiment: string;
  client: { name: string; language: string; intent: string; urgency: string; profile: string };
  history: Message[];
  summary: Record<string, unknown> | null;
};
type AdvisorResponse = {
  thread_id: string;
  message?: string | null;
  pending_approval: boolean;
  confirmation?: { message?: string; details?: Record<string, unknown> } | null;
};
type ApprovalRequest = {
  id: string;
  threadId: string;
};
type RunMode = "autopilot" | "manual";
type SpeechEngine = "advisor" | "client";

const defaultScenarios: Scenario[] = [
  { name: "normal", description: "Everyday account or service request" },
  { name: "fin_de_mois", description: "A customer under end-of-month pressure" },
  { name: "panne", description: "A card or banking service outage" },
  { name: "campagne", description: "A seasonal campaign inquiry" },
  { name: "vague_de_fraude", description: "A suspected wave of fraud" },
  { name: "contestation_carte", description: "A disputed card transaction" },
  { name: "vishing_otp", description: "A caller pressured to share an OTP" },
  { name: "virement_sans_verification", description: "A rushed transfer with skipped checks" },
];

const scenarioNames: Record<string, string> = {
  normal: "Everyday request",
  fin_de_mois: "End-of-month pressure",
  panne: "Service outage",
  campagne: "Campaign inquiry",
  vague_de_fraude: "Fraud wave",
  contestation_carte: "Card dispute",
  vishing_otp: "Vishing · OTP",
  virement_sans_verification: "Unverified transfer",
};

const scenarioIcons: Record<string, string> = {
  normal: "◌",
  fin_de_mois: "◷",
  panne: "⌁",
  campagne: "✳",
  vague_de_fraude: "⌑",
  contestation_carte: "▣",
  vishing_otp: "⌁",
  virement_sans_verification: "↗",
};

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const raw = await response.text();
  let payload: unknown;
  try {
    payload = raw ? JSON.parse(raw) : null;
  } catch {
    throw new Error(`Unexpected response from ${url}.`);
  }
  if (!response.ok) {
    const body = payload as { detail?: unknown; error?: unknown } | null;
    const detail = body?.detail ?? body?.error;
    throw new Error(typeof detail === "string" ? detail : `Request failed (${response.status}).`);
  }
  if (payload === null) throw new Error(`The service returned an empty response from ${url}.`);
  return payload as T;
}

function moodName(mood: number): string {
  if (mood <= -0.65) return "Very upset";
  if (mood <= -0.25) return "Frustrated";
  if (mood < 0.25) return "Neutral";
  if (mood < 0.65) return "Reassured";
  return "Very satisfied";
}

function languageName(language: string): string {
  return ({ fr: "French", en: "English", darija: "Tunisian Darija" } as Record<string, string>)[language] || "Auto-detected";
}

function Icon({ name, size = 18 }: { name: "play" | "stop" | "mic" | "send" | "volume" | "spark" | "check" | "arrow"; size?: number }) {
  const common = { width: size, height: size, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true as const };
  if (name === "play") return <svg {...common}><path d="m8 5 12 7-12 7z" fill="currentColor" stroke="none" /></svg>;
  if (name === "stop") return <svg {...common}><rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" stroke="none" /></svg>;
  if (name === "mic") return <svg {...common}><rect x="9" y="3" width="6" height="12" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3m-4 0h8" /></svg>;
  if (name === "send") return <svg {...common}><path d="m22 2-7 20-4-9-9-4Z" /><path d="M22 2 11 13" /></svg>;
  if (name === "volume") return <svg {...common}><path d="M11 5 6 9H3v6h3l5 4z" /><path d="M15.5 8.5a5 5 0 0 1 0 7m3-10a9 9 0 0 1 0 13" /></svg>;
  if (name === "spark") return <svg {...common}><path d="m12 3 1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z" /><path d="m19 15 .9 2.1L22 18l-2.1.9L19 21l-.9-2.1L16 18l2.1-.9z" /></svg>;
  if (name === "check") return <svg {...common}><path d="m5 12 4 4L19 6" /></svg>;
  return <svg {...common}><path d="M5 12h14m-7-7 7 7-7 7" /></svg>;
}

export default function Home() {
  const [scenario, setScenario] = useState("normal");
  const [mood, setMood] = useState(0);
  const [language, setLanguage] = useState("auto");
  const [difficulty, setDifficulty] = useState(0.25);
  const [seed, setSeed] = useState("");
  const [clientEngine, setClientEngine] = useState("auto");
  const [mode, setMode] = useState<RunMode>("autopilot");
  const [speechEngine, setSpeechEngine] = useState<SpeechEngine>("advisor");
  const [scenarios, setScenarios] = useState(defaultScenarios);
  const [call, setCall] = useState<CallSnapshot | null>(null);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [composer, setComposer] = useState("");
  const [busy, setBusy] = useState(false);
  const [autopilot, setAutopilot] = useState(false);
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const [autoplay, setAutoplay] = useState(true);
  const [advisorOnline, setAdvisorOnline] = useState(false);
  const [clientOnline, setClientOnline] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [safetyNote, setSafetyNote] = useState("");
  const [approvalRequest, setApprovalRequest] = useState<ApprovalRequest | null>(null);
  const transcriptEnd = useRef<HTMLDivElement>(null);
  const stopRequested = useRef(false);
  const autoplayRef = useRef(true);
  const approvalPollRef = useRef(false);
  const callRef = useRef<CallSnapshot | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);

  useEffect(() => {
    let active = true;
    void Promise.all([
      requestJson<{ status: string }>(`${ADVISOR_API}/health`).then(() => active && setAdvisorOnline(true)).catch(() => active && setAdvisorOnline(false)),
      requestJson<{ ok: boolean }>(`${CLIENT_API}/api/health`).then(() => active && setClientOnline(true)).catch(() => active && setClientOnline(false)),
      requestJson<Scenario[]>(`${CLIENT_API}/api/scenarios`).then((items) => active && setScenarios(items)).catch(() => undefined),
    ]);
    return () => {
      active = false;
      recorder.current?.stop();
      stream.current?.getTracks().forEach((track) => track.stop());
    };
  }, []);

  useEffect(() => {
    transcriptEnd.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [call?.history.length, busy, autopilot]);

  const selectedScenario = useMemo(
    () => scenarios.find((item) => item.name === scenario),
    [scenario, scenarios],
  );
  const canStart = advisorOnline && clientOnline && !busy;
  callRef.current = call;

  async function sendToAdvisor(
    message: string,
    activeThread: string | null,
  ): Promise<{ answer: string; thread: string } | { approval: ApprovalRequest }> {
    const response = await requestJson<AdvisorResponse>(`${ADVISOR_API}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, customer_id: 1, ...(activeThread ? { thread_id: activeThread } : {}) }),
    });
    const currentThread = response.thread_id || activeThread;
    if (response.pending_approval) {
      if (!currentThread) throw new Error("The advisor requested approval without a conversation ID.");
      const queued = await requestJson<{ id: string; thread_id: string }>(
        `${ADVISOR_API}/api/admin/approvals`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            thread_id: currentThread,
            customer_id: 1,
            confirmation: response.confirmation || {},
          }),
        },
      );
      return {
        approval: { id: queued.id, threadId: queued.thread_id },
      };
    }
    if (!response.message?.trim()) throw new Error("The Customer Advisor returned no reply.");
    if (!currentThread) throw new Error("The Customer Advisor returned no conversation ID.");
    return { answer: response.message.trim(), thread: currentThread };
  }

  async function startConversation(event?: FormEvent) {
    event?.preventDefault();
    if (!canStart) return;
    setError("");
    setNotice("");
    setSafetyNote("");
    setApprovalRequest(null);
    setThreadId(null);
    setCall(null);
    setComposer("");
    setBusy(true);
    stopRequested.current = false;
    try {
      const body = {
        scenario,
        mood,
        language,
        difficulty,
        llm: clientEngine,
        max_turns: 8,
        ...(seed.trim() ? { seed: Number(seed) } : {}),
      };
      if (seed.trim() && !Number.isInteger(Number(seed))) throw new Error("Seed must be a whole number.");
      const started = await requestJson<CallSnapshot>(`${CLIENT_API}/api/calls`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      setCall(started);
      setNotice(`Session ${started.call_id} · ${started.client.name} joined`);
      const opening = [...started.history].reverse().find((message) => message.role === "client");
      if (autoplayRef.current && opening) await playIfEnabled(opening, started.client);
      if (mode === "autopilot") {
        setAutopilot(true);
        await runAutopilot(started, null);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The conversation could not be started.");
      setAutopilot(false);
    } finally {
      setBusy(false);
      setAutopilot(false);
    }
  }

  async function playMessage(message: Message, client = call?.client) {
    const voiceUrl = message.role === "client" ? `${CLIENT_API}/api/audio/tts` : `${ADVISOR_API}/api/tts`;
    const response = await fetch(voiceUrl, message.role === "client"
      ? {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: message.text, lang: client?.language === "darija" ? "ar" : client?.language || "fr", name: client?.name || "" }),
        }
      : { method: "POST", headers: { "Content-Type": "text/plain" }, body: message.text });
    if (!response.ok) {
      const detail = await response.text();
      throw new Error(detail || `Speech synthesis failed (${response.status}).`);
    }
    const audioUrl = URL.createObjectURL(await response.blob());
    const audio = new Audio(audioUrl);
    await new Promise<void>((resolve, reject) => {
      audio.onended = () => resolve();
      audio.onerror = () => reject(new Error("The generated audio could not be played."));
      audio.play().catch(reject);
    }).finally(() => URL.revokeObjectURL(audioUrl));
  }

  async function playIfEnabled(message: Message, client: CallSnapshot["client"]) {
    if (!autoplayRef.current) return;
    try {
      await playMessage(message, client);
    } catch (reason) {
      setNotice(reason instanceof Error
        ? `Conversation continues in text. Voice unavailable: ${reason.message}`
        : "Conversation continues in text. Voice is unavailable.");
    }
  }

  async function advanceSimulation(current: CallSnapshot, advisorText: string): Promise<CallSnapshot> {
    const advisorMessage: Message = { role: "conseiller", text: advisorText };
    setCall((previous) => previous ? { ...previous, history: [...previous.history, advisorMessage] } : previous);
    await playIfEnabled(advisorMessage, current.client);
    const updated = await requestJson<CallSnapshot>(`${CLIENT_API}/api/calls/${current.call_id}/reply`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: advisorText }),
    });
    setCall(updated);
    const clientReply = [...updated.history].reverse().find((message) => message.role === "client");
    if (clientReply && updated.status === "active") await playIfEnabled(clientReply, updated.client);
    return updated;
  }

  async function runAutopilot(initialCall: CallSnapshot, initialThread: string | null) {
    let current = initialCall;
    let currentThread = initialThread;
    for (let turn = 0; turn < 8 && current.status === "active"; turn += 1) {
      if (stopRequested.current) break;
      const latestClient = [...current.history].reverse().find((message) => message.role === "client");
      if (!latestClient) throw new Error("The simulator did not provide a customer message.");
      const advisor = await sendToAdvisor(latestClient.text, currentThread);
      if ("approval" in advisor) {
        setThreadId(advisor.approval.threadId);
        setApprovalRequest(advisor.approval);
        setSafetyNote("The advisor sent this financial action to the admin approval inbox. The conversation is paused.");
        return;
      }
      currentThread = advisor.thread;
      setThreadId(currentThread);
      current = await advanceSimulation(current, advisor.answer);
    }
    if (stopRequested.current && current.status === "active") {
      current = await requestJson<CallSnapshot>(`${CLIENT_API}/api/calls/${current.call_id}/end`, { method: "POST" });
      setCall(current);
    }
  }

  async function submitManualReply(event: FormEvent) {
    event.preventDefault();
    if (!call || call.status !== "active" || !composer.trim() || busy) return;
    const text = composer.trim();
    setComposer("");
    setError("");
    setBusy(true);
    try {
      const updated = await requestJson<CallSnapshot>(`${CLIENT_API}/api/calls/${call.call_id}/reply`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text }),
      });
      setCall(updated);
      await playIfEnabled({ role: "conseiller", text }, call.client);
      const clientReply = [...updated.history].reverse().find((message) => message.role === "client");
      if (clientReply && updated.status === "active") await playIfEnabled(clientReply, updated.client);
    } catch (reason) {
      setComposer(text);
      setError(reason instanceof Error ? reason.message : "Your reply could not be sent.");
    } finally {
      setBusy(false);
    }
  }

  async function stopConversation() {
    stopRequested.current = true;
    if (!autopilot && call?.status === "active") {
      setBusy(true);
      try {
        const ended = await requestJson<CallSnapshot>(`${CLIENT_API}/api/calls/${call.call_id}/end`, { method: "POST" });
        setCall(ended);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "The call could not be ended.");
      } finally {
        setBusy(false);
      }
    }
  }

  function toggleAutoplay() {
    const enabled = !autoplayRef.current;
    autoplayRef.current = enabled;
    setAutoplay(enabled);
  }

  useEffect(() => {
    if (!approvalRequest) return;
    let active = true;
    const pollApproval = async () => {
      if (!active || approvalPollRef.current) return;
      approvalPollRef.current = true;
      try {
        const request = await requestJson<{
          status: string;
          result?: { message?: string | null; thread_id?: string; pending_approval?: boolean } | null;
        }>(`${ADVISOR_API}/api/admin/approvals/${approvalRequest.id}`);
        if (!active || request.status === "pending" || request.status === "processing") return;
        if (request.status === "failed") {
          setError("The admin decision could not be applied. Check the approval inbox for details.");
          setApprovalRequest(null);
          return;
        }
        if (!request.result?.message?.trim()) {
          setError("The approval was resolved, but the advisor returned no message to continue this call.");
          setApprovalRequest(null);
          return;
        }
        const pausedCall = callRef.current;
        setApprovalRequest(null);
        setSafetyNote(request.status === "approved"
          ? "The admin approved the action. The conversation is continuing."
          : "The admin declined the action. The conversation is continuing.");
        if (!pausedCall || pausedCall.status !== "active") return;
        setBusy(true);
        setAutopilot(true);
        try {
          const updated = await advanceSimulation(pausedCall, request.result.message.trim());
          await runAutopilot(updated, request.result.thread_id || approvalRequest.threadId);
        } catch (reason) {
          setError(reason instanceof Error ? reason.message : "The conversation could not continue after review.");
        } finally {
          setBusy(false);
          setAutopilot(false);
        }
      } catch (reason) {
        if (active) setError(reason instanceof Error ? reason.message : "Could not check the approval status.");
      } finally {
        approvalPollRef.current = false;
      }
    };
    void pollApproval();
    const interval = window.setInterval(() => void pollApproval(), 2500);
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, [approvalRequest]);

  async function toggleRecording() {
    if (recording) {
      recorder.current?.stop();
      setRecording(false);
      return;
    }
    setError("");
    try {
      if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
        throw new Error("Audio recording is not supported in this browser.");
      }
      const audioStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      stream.current = audioStream;
      const mediaRecorder = new MediaRecorder(audioStream);
      const chunks: Blob[] = [];
      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size) chunks.push(event.data);
      };
      mediaRecorder.onstop = async () => {
        audioStream.getTracks().forEach((track) => track.stop());
        stream.current = null;
        if (chunks.length === 0) return;
        setTranscribing(true);
        try {
          const audio = new Blob(chunks, { type: mediaRecorder.mimeType || "audio/webm" });
          const form = new FormData();
          const file = new File([audio], "advisor-voice.webm", { type: audio.type });
          const endpoint = speechEngine === "advisor" ? `${ADVISOR_API}/api/transcribe` : `${CLIENT_API}/api/audio/stt`;
          const field = speechEngine === "advisor" ? "audio" : "file";
          form.append(field, file);
          const sttLanguage = language === "darija" ? "ar" : language;
          if (sttLanguage !== "auto") form.append("language", sttLanguage);
          if (speechEngine === "client") form.append("model_size", "base");
          const result = await requestJson<{ text: string }>(endpoint, { method: "POST", body: form });
          if (!result.text.trim()) throw new Error("No speech was detected. Try recording again.");
          setComposer((value) => value ? `${value} ${result.text}` : result.text);
        } catch (reason) {
          setError(reason instanceof Error ? reason.message : "Speech recognition failed.");
        } finally {
          setTranscribing(false);
        }
      };
      recorder.current = mediaRecorder;
      mediaRecorder.start();
      setRecording(true);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not access the microphone.");
    }
  }

  const moodPercent = Math.round(((call?.mood ?? mood) + 1) * 50);
  const displayMessages = call?.history ?? [];
  const callActive = call?.status === "active";
  const completed = Boolean(call && call.status !== "active");

  return (
    <main className="min-h-screen bg-paper text-ink">
      <header className="border-b border-[#e7eae4] bg-white/80">
        <div className="mx-auto flex max-w-[1440px] items-center justify-between px-5 py-4 sm:px-8">
          <div className="flex items-center gap-3">
            <div className="grid h-10 w-10 place-items-center rounded-2xl bg-forest text-white shadow-sm">
              <span className="text-xl font-semibold">r</span>
            </div>
            <div>
              <div className="text-[17px] font-semibold tracking-[-.04em]">relay<span className="text-forest">.</span></div>
              <div className="text-[10px] font-semibold uppercase tracking-[.2em] text-[#89938d]">Agent communication studio</div>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <Link href="/admin/approvals" className="rounded-xl border border-[#e6ebe5] bg-white px-3 py-2 text-[11px] font-semibold text-[#56645b] transition hover:border-[#cbd8cc] hover:text-forest">
              Admin inbox{approvalRequest ? " · 1" : ""}
            </Link>
            <div className="hidden items-center gap-2 rounded-full border border-[#e6ebe5] bg-white px-3 py-2 text-xs font-medium text-[#68756d] sm:flex">
              <span className="relative flex h-2 w-2">
                <span className={`absolute inline-flex h-full w-full rounded-full opacity-50 ${advisorOnline && clientOnline ? "animate-ping bg-emerald-500" : "bg-rose-400"}`} />
                <span className={`relative inline-flex h-2 w-2 rounded-full ${advisorOnline && clientOnline ? "bg-emerald-500" : "bg-rose-400"}`} />
              </span>
              {advisorOnline && clientOnline ? "Systems connected" : "Waiting for services"}
            </div>
          </div>
        </div>
      </header>

      <div className="mx-auto grid max-w-[1440px] gap-6 px-5 py-7 sm:px-8 lg:grid-cols-[350px_minmax(0,1fr)] lg:gap-8 lg:py-10">
        <aside className="space-y-5">
          <section className="pb-1">
            <p className="mb-2 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[.2em] text-forest">
              <span className="h-px w-5 bg-forest" /> Live simulation
            </p>
            <h1 className="text-[30px] font-semibold leading-[1.08] tracking-[-.055em] sm:text-[34px]">Make every<br />conversation count.</h1>
            <p className="mt-3 max-w-sm text-[13px] leading-6 text-[#748078]">Shape a customer scenario and watch your banking advisor respond in real time.</p>
          </section>

          <form onSubmit={startConversation} className="rounded-[22px] border border-[#e5e9e3] bg-white p-5 shadow-panel sm:p-6">
            <div className="mb-5 flex items-center justify-between">
              <div>
                <h2 className="text-sm font-semibold">Set the scene</h2>
                <p className="mt-1 text-[11px] text-[#8a948d]">Choose your caller and situation</p>
              </div>
              <div className="grid h-9 w-9 place-items-center rounded-xl bg-[#eff6f0] text-forest"><Icon name="spark" /></div>
            </div>

            <label className="mb-2 block text-[11px] font-semibold uppercase tracking-[.12em] text-[#76827a]" htmlFor="scenario">Scenario</label>
            <div className="relative">
              <select id="scenario" value={scenario} onChange={(event) => setScenario(event.target.value)} disabled={busy || Boolean(callActive)} className="w-full appearance-none rounded-xl border border-[#e6eae4] bg-[#fbfcfa] px-3 py-3 pr-9 text-sm font-medium text-ink disabled:opacity-60">
                {scenarios.map((item) => <option key={item.name} value={item.name}>{scenarioIcons[item.name] || "◌"}  {scenarioNames[item.name] || item.name.replaceAll("_", " ")}</option>)}
              </select>
              <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-xs text-[#8a948d]">⌄</span>
            </div>
            <p className="mt-2 min-h-8 text-[11px] leading-4 text-[#89938d]">{selectedScenario?.description || "Choose a conversation scenario."}</p>

            <div className="mt-5">
              <div className="mb-2 flex items-center justify-between">
                <label htmlFor="mood" className="text-[11px] font-semibold uppercase tracking-[.12em] text-[#76827a]">Starting mood</label>
                <span className="rounded-full bg-[#f2f5f0] px-2.5 py-1 text-[10px] font-semibold text-[#56645b]">{moodName(mood)}</span>
              </div>
              <input id="mood" type="range" min="-1" max="1" step="0.05" value={mood} onChange={(event) => setMood(Number(event.target.value))} disabled={busy || Boolean(callActive)} className="range-slider" />
              <div className="mt-2 flex justify-between text-[10px] text-[#969f99]"><span>Upset</span><span>Neutral</span><span>Satisfied</span></div>
            </div>

            <div className="mt-5 grid grid-cols-2 gap-3">
              <label className="block">
                <span className="mb-2 block text-[11px] font-semibold uppercase tracking-[.12em] text-[#76827a]">Language</span>
                <select value={language} onChange={(event) => setLanguage(event.target.value)} disabled={busy || Boolean(callActive)} className="w-full rounded-xl border border-[#e6eae4] bg-[#fbfcfa] px-3 py-2.5 text-xs font-medium">
                  <option value="auto">Auto</option><option value="fr">French</option><option value="darija">Tunisian Darija</option><option value="en">English</option>
                </select>
              </label>
              <label className="block">
                <span className="mb-2 block text-[11px] font-semibold uppercase tracking-[.12em] text-[#76827a]">Difficulty</span>
                <select value={difficulty} onChange={(event) => setDifficulty(Number(event.target.value))} disabled={busy || Boolean(callActive)} className="w-full rounded-xl border border-[#e6eae4] bg-[#fbfcfa] px-3 py-2.5 text-xs font-medium">
                  <option value={0}>Relaxed</option><option value={0.25}>Balanced</option><option value={0.6}>Challenging</option><option value={1}>High pressure</option>
                </select>
              </label>
            </div>

            <label className="mt-4 block">
              <span className="mb-2 block text-[11px] font-semibold uppercase tracking-[.12em] text-[#76827a]">Client response engine</span>
              <select value={clientEngine} onChange={(event) => setClientEngine(event.target.value)} disabled={busy || Boolean(callActive)} className="w-full rounded-xl border border-[#e6eae4] bg-[#fbfcfa] px-3 py-2.5 text-xs font-medium">
                <option value="auto">Auto · configured model or rules</option><option value="none">Rules engine</option><option value="claude">Claude</option><option value="gemini">Gemini</option><option value="groq">Groq</option><option value="ollama">Ollama</option>
              </select>
            </label>

            <label className="mt-4 block">
              <span className="mb-2 block text-[11px] font-semibold uppercase tracking-[.12em] text-[#76827a]">Random seed <span className="font-normal normal-case tracking-normal text-[#a2aaa4]">· optional</span></span>
              <input value={seed} onChange={(event) => setSeed(event.target.value)} disabled={busy || Boolean(callActive)} inputMode="numeric" placeholder="Keep empty for a new caller" className="w-full rounded-xl border border-[#e6eae4] bg-[#fbfcfa] px-3 py-2.5 text-xs placeholder:text-[#b2bab4]" />
            </label>

            <div className="mt-5 rounded-xl bg-[#f5f7f3] p-1">
              <div className="grid grid-cols-2 gap-1">
                <button type="button" onClick={() => setMode("autopilot")} disabled={busy || Boolean(callActive)} className={`rounded-lg px-2 py-2.5 text-[11px] font-semibold transition ${mode === "autopilot" ? "bg-white text-forest shadow-sm" : "text-[#89938d]"}`}>Advisor autopilot</button>
                <button type="button" onClick={() => setMode("manual")} disabled={busy || Boolean(callActive)} className={`rounded-lg px-2 py-2.5 text-[11px] font-semibold transition ${mode === "manual" ? "bg-white text-forest shadow-sm" : "text-[#89938d]"}`}>Manual replies</button>
              </div>
            </div>

            {error && <div role="alert" className="mt-4 rounded-xl border border-rose-100 bg-rose-50 px-3 py-2.5 text-xs leading-5 text-rose-700">{error}</div>}
            {!advisorOnline || !clientOnline ? (
              <p className="mt-4 rounded-xl bg-amber-50 px-3 py-2.5 text-[11px] leading-5 text-amber-800">
                Start both APIs first: Customer Advisor on {ADVISOR_API} and Client Simulator on {CLIENT_API}.
              </p>
            ) : null}
            <div className="mt-5 flex gap-2">
              {call ? (
                <button type="button" onClick={() => void startConversation()} disabled={busy} className="flex w-full items-center justify-center gap-2 rounded-xl bg-forest px-4 py-3.5 text-xs font-semibold text-white transition hover:bg-[#0a3f32] disabled:cursor-not-allowed disabled:opacity-50">
                  <Icon name="spark" size={16} /> New conversation
                </button>
              ) : (
                <button type="submit" disabled={!canStart} className="group flex w-full items-center justify-center gap-2 rounded-xl bg-forest px-4 py-3.5 text-xs font-semibold text-white transition hover:bg-[#0a3f32] disabled:cursor-not-allowed disabled:opacity-50">
                  {busy ? <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white" /> : <Icon name="play" size={15} />}
                  {busy ? "Starting conversation…" : "Start conversation"}
                  {!busy && <span className="ml-auto opacity-70 transition group-hover:translate-x-0.5"><Icon name="arrow" size={15} /></span>}
                </button>
              )}
              {call && callActive && <button type="button" onClick={stopConversation} disabled={busy} title="End call" className="grid h-12 w-12 shrink-0 place-items-center rounded-xl border border-[#e4e9e2] text-[#68756d] hover:bg-[#f5f7f3]"><Icon name="stop" /></button>}
            </div>
            {call && <button type="button" onClick={() => { stopRequested.current = true; setCall(null); setThreadId(null); setError(""); setNotice(""); }} disabled={busy} className="mt-3 w-full text-center text-[11px] font-medium text-[#8b958e] hover:text-forest disabled:opacity-40">Reset session</button>}
          </form>

          <section className="rounded-[20px] border border-[#e5e9e3] bg-white p-5 shadow-panel">
            <div className="mb-4 flex items-center justify-between">
              <div><h2 className="text-xs font-semibold">Speech studio</h2><p className="mt-1 text-[10px] text-[#8a948d]">Existing project speech engines</p></div>
              <div className="sound-bars flex h-5 items-center gap-[3px] text-forest"><span /><span /><span /><span /></div>
            </div>
            <div className="space-y-2.5">
              <div className="flex items-center justify-between rounded-xl bg-[#f7f8f5] px-3 py-2.5">
                <span className="text-[11px] text-[#647168]">Client voice · gTTS / Edge</span><span className="rounded-full bg-[#e6f4e9] px-2 py-1 text-[9px] font-semibold text-forest">TTS</span>
              </div>
              <div className="flex items-center justify-between rounded-xl bg-[#f7f8f5] px-3 py-2.5">
                <span className="text-[11px] text-[#647168]">Advisor voice · Edge neural</span><span className="rounded-full bg-[#e6f4e9] px-2 py-1 text-[9px] font-semibold text-forest">TTS</span>
              </div>
              <label className="block pt-1">
                <span className="mb-1.5 block text-[10px] font-medium text-[#88938b]">Transcription model</span>
                <select value={speechEngine} onChange={(event) => setSpeechEngine(event.target.value as SpeechEngine)} className="w-full rounded-lg border border-[#e6eae4] bg-white px-2.5 py-2 text-[10px]">
                  <option value="advisor">Customer Advisor · Faster-Whisper</option>
                  <option value="client">Client Simulator · Whisper / Groq</option>
                </select>
              </label>
              <button type="button" onClick={toggleAutoplay} aria-pressed={autoplay} className="flex w-full items-center justify-between pt-1 text-[11px] text-[#67746b]">
                <span>Automatically play generated voices</span>
                <span className="flex items-center gap-2"><span className="text-[9px] font-semibold uppercase tracking-wide text-[#89938d]">{autoplay ? "On" : "Off"}</span><span className={`relative h-5 w-9 rounded-full transition ${autoplay ? "bg-forest" : "bg-[#dce2dc]"}`}><span className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow-sm transition ${autoplay ? "left-[18px]" : "left-0.5"}`} /></span></span>
              </button>
            </div>
          </section>
          <p className="px-2 text-[10px] leading-5 text-[#99a19b]">Local demo · Financial actions pause for human approval before they can proceed.</p>
        </aside>

        <section className="flex min-h-[760px] flex-col overflow-hidden rounded-[24px] border border-[#e4e9e2] bg-white shadow-panel">
          <div className="soft-grid relative border-b border-[#edf0eb] bg-[#fbfcfa] px-5 py-5 sm:px-7">
            <div className="relative flex flex-wrap items-center justify-between gap-4">
              <div className="flex items-center gap-3">
                <div className="relative grid h-12 w-12 place-items-center rounded-[17px] bg-[#e9f2e9] text-lg font-semibold text-forest">
                  {call?.client.name?.slice(0, 1).toUpperCase() || "C"}
                  {callActive && <span className="absolute -bottom-0.5 -right-0.5 h-3.5 w-3.5 rounded-full border-[3px] border-[#fbfcfa] bg-emerald-500" />}
                </div>
                <div>
                  <div className="flex flex-wrap items-center gap-2">
                    <h2 className="text-sm font-semibold">{call?.client.name || "Conversation room"}</h2>
                    <span className={`rounded-full px-2 py-1 text-[9px] font-semibold uppercase tracking-[.1em] ${callActive ? "bg-[#e6f4e9] text-forest" : completed ? "bg-[#f0f2ee] text-[#707b73]" : "bg-white text-[#9aa39d]"}`}>
                      {callActive ? (autopilot ? "In progress" : "Live call") : completed ? call?.status.replaceAll("_", " ") : "No active session"}
                    </span>
                  </div>
                  <p className="mt-1 text-[10px] text-[#87928a]">
                    {call ? `${languageName(call.client.language)} · ${scenarioNames[scenario] || scenario}` : "Your simulation will appear here"}
                  </p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                <div className="rounded-xl border border-[#e7ebe5] bg-white px-3 py-2 text-right">
                  <div className="text-[9px] font-semibold uppercase tracking-[.13em] text-[#9aa39d]">Exchange</div>
                  <div className="mt-0.5 text-xs font-semibold tabular-nums">{call?.turn ?? 0}<span className="font-normal text-[#9aa39d]"> / 8</span></div>
                </div>
                <div className="rounded-xl border border-[#e7ebe5] bg-white px-3 py-2 text-right">
                  <div className="text-[9px] font-semibold uppercase tracking-[.13em] text-[#9aa39d]">Mood</div>
                  <div className="mt-0.5 text-xs font-semibold tabular-nums">{(call?.mood ?? mood).toFixed(2)}</div>
                </div>
              </div>
            </div>
            {call && (
              <div className="relative mt-5 grid grid-cols-3 gap-2 sm:gap-3">
                <div className="rounded-xl border border-[#ebeee9] bg-white/90 px-3 py-2.5">
                  <p className="text-[9px] font-semibold uppercase tracking-[.12em] text-[#9aa39d]">Customer mood</p><p className="mt-1 text-[11px] font-semibold text-[#3d5748]">{moodName(call.mood)}</p>
                </div>
                <div className="rounded-xl border border-[#ebeee9] bg-white/90 px-3 py-2.5">
                  <p className="text-[9px] font-semibold uppercase tracking-[.12em] text-[#9aa39d]">Sentiment</p><p className="mt-1 truncate text-[11px] font-semibold capitalize text-[#3d5748]">{call.sentiment || "—"}</p>
                </div>
                <div className="rounded-xl border border-[#ebeee9] bg-white/90 px-3 py-2.5">
                  <p className="text-[9px] font-semibold uppercase tracking-[.12em] text-[#9aa39d]">Caller intent</p><p className="mt-1 truncate text-[11px] font-semibold capitalize text-[#3d5748]">{call.client.intent.replaceAll("_", " ")}</p>
                </div>
              </div>
            )}
          </div>

          <div className="flex items-center gap-3 border-b border-[#edf0eb] px-5 py-3 sm:px-7">
            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-[#edf0eb]">
              <div className="h-full rounded-full bg-gradient-to-r from-[#cf8069] via-[#d7bd77] to-[#55a67b] transition-all duration-700" style={{ width: `${moodPercent}%` }} />
            </div>
            <span className="shrink-0 text-[9px] font-medium text-[#8b958e]">{moodName(call?.mood ?? mood)}</span>
          </div>

          {safetyNote && <div role="status" className="mx-5 mt-4 rounded-xl border border-amber-100 bg-amber-50 px-4 py-2.5 text-[11px] leading-5 text-amber-800 sm:mx-7">{safetyNote}</div>}
          {notice && <div role="status" className="mx-5 mt-4 flex items-center gap-2 text-[10px] text-[#859087] sm:mx-7"><span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />{notice}</div>}
          {approvalRequest && (
            <section aria-label="Waiting for administrator review" className="mx-5 mt-4 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-amber-200 bg-amber-50/80 p-4 sm:mx-7">
              <div>
                <p className="text-[10px] font-semibold text-[#493815]">Waiting for admin review</p>
                <p className="mt-1 text-[10px] leading-5 text-[#826d43]">The advisor paused this conversation and sent the request with its explanation to the approval inbox.</p>
              </div>
              <Link href="/admin/approvals" className="rounded-xl bg-forest px-4 py-2.5 text-[10px] font-semibold text-white transition hover:bg-[#0a3f32]">Open admin inbox</Link>
            </section>
          )}

          <div className="transcript-scroll flex-1 overflow-y-auto px-5 py-5 sm:px-7 sm:py-6">
            {displayMessages.length ? (
              <div className="mx-auto max-w-[740px] space-y-5">
                <div className="py-2 text-center"><span className="rounded-full bg-[#f4f6f2] px-3 py-1.5 text-[9px] font-medium text-[#97a098]">TODAY · PRIVATE SIMULATION</span></div>
                {displayMessages.map((message, index) => {
                  const isClient = message.role === "client";
                  return (
                    <div key={`${index}-${message.role}`} className={`flex items-end gap-2.5 ${isClient ? "" : "flex-row-reverse"}`}>
                      <div className={`grid h-8 w-8 shrink-0 place-items-center rounded-xl text-[10px] font-semibold ${isClient ? "bg-[#eaf2e9] text-forest" : "bg-[#253b31] text-white"}`}>{isClient ? (call?.client.name?.slice(0, 1).toUpperCase() || "C") : "CA"}</div>
                      <div className={`max-w-[84%] ${isClient ? "" : "text-right"}`}>
                        <div className={`mb-1.5 flex items-center gap-2 text-[9px] font-semibold uppercase tracking-[.12em] text-[#9aa39d] ${isClient ? "" : "justify-end"}`}>
                          <span>{isClient ? call?.client.name || "Customer" : "Customer Advisor"}</span><span>·</span><span>{isClient ? "Client Simulator" : "Advisor agent"}</span>
                        </div>
                        <div className={`group rounded-2xl px-4 py-3 text-left text-[13px] leading-[1.75] shadow-sm ${isClient ? "rounded-bl-md border border-[#e9eee8] bg-[#f7f9f6] text-[#35463b]" : "rounded-br-md bg-forest text-[#f4f8f4]"}`}>
                          {message.text}
                          <button type="button" title={`Play ${isClient ? "customer" : "advisor"} voice`} onClick={() => { void playMessage(message).catch((reason) => setError(reason instanceof Error ? reason.message : "Speech synthesis failed.")); }} className={`mt-2 flex items-center gap-1.5 text-[9px] font-medium opacity-70 transition hover:opacity-100 ${isClient ? "text-forest" : "text-white"}`}>
                            <Icon name="volume" size={13} /> Listen
                          </button>
                        </div>
                      </div>
                    </div>
                  );
                })}
                {(busy || autopilot) && <div className="flex items-center gap-2.5"><div className="grid h-8 w-8 place-items-center rounded-xl bg-[#eaf2e9] text-forest"><span className="h-3 w-3 animate-pulse rounded-full bg-current" /></div><div className="rounded-2xl rounded-bl-md bg-[#f7f9f6] px-4 py-3 text-[11px] text-[#8d9990]">{autopilot ? "The agents are talking…" : "Connecting the customer…"}</div></div>}
                {completed && call?.summary && <div className="rounded-2xl border border-[#e7ebe5] bg-[#f8faf7] p-4"><div className="flex items-center gap-2 text-xs font-semibold text-forest"><Icon name="check" size={15} /> Session complete</div><p className="mt-2 text-[11px] leading-5 text-[#7b877e]">Conversation status: {call.status.replaceAll("_", " ")}. Start a new conversation to try another scenario.</p></div>}
                <div ref={transcriptEnd} />
              </div>
            ) : (
              <div className="soft-grid flex h-full min-h-[390px] flex-col items-center justify-center rounded-[20px] border border-dashed border-[#dfe6de] bg-[#fcfdfa] px-5 text-center">
                <div className="mb-5 grid h-[72px] w-[72px] place-items-center rounded-[25px] bg-white text-forest shadow-[0_8px_30px_rgba(27,46,37,0.07)]">
                  <div className="relative flex items-center gap-1.5"><span className="h-8 w-8 rounded-xl bg-[#e9f2e9]" /><span className="h-8 w-8 rounded-xl bg-[#24493a]" /><span className="absolute left-[27px] top-1/2 h-0.5 w-5 -translate-y-1/2 bg-forest" /></div>
                </div>
                <h3 className="text-[15px] font-semibold tracking-[-.02em]">A better conversation starts here.</h3>
                <p className="mt-2 max-w-xs text-[11px] leading-5 text-[#8b968e]">Configure a caller on the left. Relay will connect the client simulator and banking advisor in this shared room.</p>
                <div className="mt-6 flex items-center gap-2 rounded-full border border-[#e7ebe5] bg-white px-3 py-2 text-[9px] font-medium text-[#8c978f]"><span className={`h-1.5 w-1.5 rounded-full ${clientOnline ? "bg-emerald-500" : "bg-rose-400"}`} />Client simulator <span className="mx-1 text-[#d3d9d3]">/</span><span className={`h-1.5 w-1.5 rounded-full ${advisorOnline ? "bg-emerald-500" : "bg-rose-400"}`} />Customer advisor</div>
              </div>
            )}
          </div>

          {call && mode === "manual" && callActive && (
            <form onSubmit={submitManualReply} className="border-t border-[#edf0eb] bg-[#fbfcfa] px-5 py-4 sm:px-7">
              <div className="mx-auto max-w-[740px]">
                <div className="mb-2 flex items-center justify-between">
                  <label htmlFor="reply" className="text-[10px] font-semibold uppercase tracking-[.12em] text-[#829087]">Your reply as the advisor</label>
                  <span className="text-[9px] text-[#a1aaa3]">{transcribing ? "Transcribing audio…" : recording ? "Recording · click microphone to finish" : "Type a reply or use voice"}</span>
                </div>
                <div className="flex items-end gap-2 rounded-2xl border border-[#e3e9e2] bg-white p-2 shadow-sm">
                  <textarea id="reply" value={composer} onChange={(event) => setComposer(event.target.value)} disabled={busy || transcribing} rows={2} placeholder="Write a reassuring, helpful response…" className="max-h-32 min-h-12 flex-1 resize-y border-0 bg-transparent px-2 py-1.5 text-xs leading-5 outline-none placeholder:text-[#abb4ac] focus:outline-none focus:ring-0" />
                  <button type="button" onClick={() => void toggleRecording()} disabled={busy || transcribing} title={recording ? "Stop recording" : "Record advisor response"} className={`grid h-10 w-10 shrink-0 place-items-center rounded-xl transition ${recording ? "animate-pulse bg-rose-600 text-white" : "bg-[#f2f6f1] text-forest hover:bg-[#e6f0e5]"}`}><Icon name="mic" size={17} /></button>
                  <button type="submit" disabled={!composer.trim() || busy || transcribing} className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-forest text-white transition hover:bg-[#0a3f32] disabled:cursor-not-allowed disabled:opacity-40"><Icon name="send" size={16} /></button>
                </div>
              </div>
            </form>
          )}
          {call && mode === "autopilot" && autopilot && (
            <div className="flex items-center justify-between border-t border-[#edf0eb] bg-[#fbfcfa] px-5 py-4 sm:px-7">
              <div className="flex items-center gap-2 text-[10px] text-[#829087]"><span className="h-2 w-2 animate-pulse rounded-full bg-emerald-500" />Advisor autopilot is handling this conversation</div>
              <button type="button" onClick={stopConversation} className="flex items-center gap-1.5 rounded-lg border border-[#e2e8e1] bg-white px-3 py-2 text-[10px] font-semibold text-[#657168] hover:bg-[#f5f7f3]"><Icon name="stop" size={13} /> Stop run</button>
            </div>
          )}
        </section>
      </div>

      <footer className="mx-auto flex max-w-[1440px] flex-wrap items-center justify-between gap-2 px-5 pb-6 text-[9px] text-[#a0a9a2] sm:px-8">
        <span>RELAY STUDIO · LOCAL DEVELOPMENT</span><span>Use synthetic customer records and test banking data only.</span>
      </footer>
    </main>
  );
}
