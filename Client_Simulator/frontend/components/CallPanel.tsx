"use client";

import { useEffect, useRef, useState } from "react";
import {
  CallState,
  Scenario,
  endCall,
  getLlmModels,
  getScenarios,
  sendReply,
  speechToText,
  startCall,
  textToSpeech,
} from "@/lib/api";
import MoodStrip from "./MoodStrip";

const STATUS_LABEL: Record<string, string> = {
  active: "In progress",
  resolved: "Resolved",
  escalated: "Escalated to a supervisor",
  abandoned: "The client left the queue",
  hung_up: "The client hung up",
  max_turns: "Turn limit reached",
  closed: "Closed by the advisor",
};

const LLM_OPTIONS = [
  { value: "auto", label: "Auto (first configured LLM)" },
  { value: "none", label: "Rule-based (offline)" },
  { value: "claude", label: "Claude" },
  { value: "gemini", label: "Gemini" },
  { value: "groq", label: "Groq" },
  { value: "ollama", label: "Ollama (local)" },
];

// Client languages. "auto" follows whichever language the simulator picks for the call.
const LANG_OPTIONS = [
  { value: "auto", label: "Auto (mix of all)" },
  { value: "fr", label: "Français" },
  { value: "darija", label: "Darija (Tunisian)" },
  { value: "en", label: "English" },
];

// Map a client language to the three codes the voice stack needs:
// bcp47 for the browser speech APIs, gtts for the server voice, stt for Whisper.
function speechLang(language: string, clientLanguage?: string): { bcp47: string; gtts: string; stt: string } {
  const lang = language === "auto" ? clientLanguage ?? "fr" : language;
  if (lang === "en") return { bcp47: "en-US", gtts: "en", stt: "en" };
  if (lang === "darija") return { bcp47: "ar-TN", gtts: "ar", stt: "ar" };
  return { bcp47: "fr-FR", gtts: "fr", stt: "fr" };
}

// Behavioural sentiment returned by the backend (client_simulator.SENTIMENTS) -> flag shown in the UI.
const SENTIMENT_META: Record<string, { label: string; color: string }> = {
  calm: { label: "Calm", color: "#2e7d32" },
  in_a_rush: { label: "In a rush", color: "#ef6c00" },
  stressed: { label: "Stressed", color: "#c62828" },
  irritated: { label: "Irritated", color: "#f9a825" },
  angry: { label: "Angry", color: "#d84315" },
  violent: { label: "Violent", color: "#b71c1c" },
  rude: { label: "Rude", color: "#8e24aa" },
  confused: { label: "Not understanding", color: "#1565c0" },
  neutral: { label: "Neutral", color: "#546e7a" },
  satisfied: { label: "Satisfied", color: "#00897b" },
};

function sentimentMeta(key?: string) {
  return SENTIMENT_META[key ?? "neutral"] ?? SENTIMENT_META.neutral;
}

// How each sentiment colours the voice. Browser speech uses the full prosody; the server voice (gTTS)
// can only be slowed down or not, so `slow` is the only knob it gets.
const SENTIMENT_VOICE: Record<string, { rate: number; pitch: number; volume: number; slow: boolean }> = {
  calm: { rate: 0.95, pitch: 1.0, volume: 0.9, slow: true },
  in_a_rush: { rate: 1.35, pitch: 1.05, volume: 0.95, slow: false },
  stressed: { rate: 1.2, pitch: 1.12, volume: 0.9, slow: false },
  irritated: { rate: 1.08, pitch: 1.02, volume: 0.95, slow: false },
  angry: { rate: 1.15, pitch: 1.05, volume: 1.0, slow: false },
  violent: { rate: 1.25, pitch: 1.1, volume: 1.0, slow: false },
  rude: { rate: 1.1, pitch: 0.95, volume: 1.0, slow: false },
  confused: { rate: 0.85, pitch: 1.0, volume: 0.9, slow: true },
  neutral: { rate: 1.0, pitch: 1.0, volume: 0.95, slow: false },
  satisfied: { rate: 1.0, pitch: 1.06, volume: 0.95, slow: false },
};

function voiceProsody(sentiment?: string) {
  return SENTIMENT_VOICE[sentiment ?? "neutral"] ?? SENTIMENT_VOICE.neutral;
}

export default function CallPanel() {
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [scenario, setScenario] = useState("normal");
  const [llm, setLlm] = useState("auto");
  const [model, setModel] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [modelsError, setModelsError] = useState("");
  const [waitSeconds, setWaitSeconds] = useState(0);
  const [seed, setSeed] = useState("");

  const [call, setCall] = useState<CallState | null>(null);
  const [trail, setTrail] = useState<number[]>([]);
  const [input, setInput] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const [language, setLanguage] = useState("fr");
  // Phone call mode: the client TALKS (voice), then your microphone opens by itself, like a real call
  const [phone, setPhone] = useState(true);
  const [ringing, setRinging] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [speaking, setSpeaking] = useState(false);
  const recognition = useRef<any>(null);
  // refs: callbacks fired later by the speech APIs must see the CURRENT values, not stale renders
  const callRef = useRef<CallState | null>(null);
  const busyRef = useRef(false);
  const phoneRef = useRef(true);
  const utterRef = useRef<SpeechSynthesisUtterance | null>(null); // keeps it alive (Chrome GC bug: onend never fires)
  callRef.current = call;
  phoneRef.current = phone;
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const recorder = useRef<MediaRecorder | null>(null);
  const transcript = useRef<HTMLDivElement>(null);
  const muteUntil = useRef<number>(0);
  const lastClientText = useRef<string>("");

  useEffect(() => {
    getScenarios()
      .then(setScenarios)
      .catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    // list the models of the chosen backend (installed ones for Ollama, available ones for Groq)
    setModel("");
    setModels([]);
    setModelsError("");
    if (llm === "auto" || llm === "none") return;
    getLlmModels(llm)
      .then((r) => {
        setModels(r.models);
        setModelsError(r.error ?? "");
        if (r.models.length) setModel(r.models[0]);
      })
      .catch((e: Error) => setModelsError(e.message));
  }, [llm]);

  useEffect(() => {
    const synth = typeof window !== "undefined" ? window.speechSynthesis : undefined;
    if (!synth) return;
    synth.getVoices(); // starts loading the voice list (Chrome loads it lazily)
    return () => {
      synth.cancel();
      recognition.current?.abort?.();
    };
  }, []);

  useEffect(() => {
    transcript.current?.scrollTo({ top: transcript.current.scrollHeight, behavior: "smooth" });
  }, [call?.history.length, pending]);

  // Fallback voice: the server turns the text into an mp3 (gTTS, needs internet + `pip install gTTS`).
  function speakOnServer(text: string): Promise<void> {
    const { gtts } = speechLang(language, callRef.current?.client.language);
    const { slow } = voiceProsody(callRef.current?.sentiment);
    return new Promise<void>((resolve) => {
      textToSpeech(text, gtts, slow)
        .then((blob) => {
          const url = URL.createObjectURL(blob);
          const audio = new Audio(url);
          audio.onended = () => {
            URL.revokeObjectURL(url);
            setSpeaking(false);
            resolve();
          };
          setSpeaking(true);
          return audio.play();
        })
        .catch((e: Error) => {
          setSpeaking(false);
          setError(`No voice for the client: the browser has no voice and the server voice failed (${e.message}).`);
          resolve();
        });
    });
  }

  // The client talks. Browser voice first; if it makes no sound (no voice installed, Chrome dropping it,
  // an error) the server voice takes over. Resolves when the client has finished talking.
  function speak(text: string): Promise<void> {
    if (!phoneRef.current || !text) return Promise.resolve();
    const target = language === "auto" ? callRef.current?.client.language ?? "fr" : language;
    const synth = typeof window !== "undefined" ? window.speechSynthesis : undefined;
    // Darija is written in Latin (Arabizi): no browser voice can read it, so we always use the server
    // voice, which transliterates to Arabic script first and speaks it with an Arabic voice.
    if (target === "darija" || !synth) return speakOnServer(text);
    return new Promise<void>((resolve) => {
      let started = false;
      let done = false;
      const finish = () => {
        if (!done) {
          done = true;
          setSpeaking(false);
          resolve();
        }
      };
      const fallback = () => {
        if (done) return;
        done = true;
        synth.cancel();
        speakOnServer(text).then(resolve);
      };
      synth.cancel();
      const { bcp47 } = speechLang(language, callRef.current?.client.language);
      const pros = voiceProsody(callRef.current?.sentiment);
      const u = new SpeechSynthesisUtterance(text);
      u.lang = bcp47;
      u.rate = pros.rate;       // the client's sentiment colours how it talks (rushed = fast, confused = slow)
      u.pitch = pros.pitch;
      u.volume = pros.volume;
      const prefix = bcp47.slice(0, 2);
      const v = synth.getVoices().find((x) => x.lang.toLowerCase().replace("_", "-").startsWith(prefix));
      if (v) u.voice = v;
      u.onstart = () => {
        started = true;
        setSpeaking(true);
      };
      u.onend = finish;
      u.onerror = (e) => {
        if (e.error === "canceled" || e.error === "interrupted") finish();
        else fallback();
      };
      utterRef.current = u; // keeps it alive (Chrome GC bug: onend never fires)
      setTimeout(() => {
        synth.resume();
        synth.speak(u);
      }, 60);
      setTimeout(() => !started && fallback(), 2500); // no sound after 2.5 s: use the server voice
      setTimeout(finish, 30000 + text.length * 150); // never block the call loop
    });
  }

  // Ringing before the client picks up (two rings).
  function ring(): Promise<void> {
    return new Promise<void>((resolve) => {
      try {
        const Ctx = (window as any).AudioContext || (window as any).webkitAudioContext;
        const ctx = new Ctx();
        const t0 = ctx.currentTime;
        [0, 1.3].forEach((dt) => {
          const g = ctx.createGain();
          g.gain.setValueAtTime(0.0001, t0 + dt);
          g.gain.exponentialRampToValueAtTime(0.12, t0 + dt + 0.05);
          g.gain.setValueAtTime(0.12, t0 + dt + 0.9);
          g.gain.exponentialRampToValueAtTime(0.0001, t0 + dt + 1.0);
          g.connect(ctx.destination);
          [440, 480].forEach((f) => {
            const o = ctx.createOscillator();
            o.frequency.value = f;
            o.connect(g);
            o.start(t0 + dt);
            o.stop(t0 + dt + 1.0);
          });
        });
        setTimeout(() => {
          ctx.close();
          resolve();
        }, 2500);
      } catch {
        resolve();
      }
    });
  }

  // After the client's line: speak it, then (hands-free) open the microphone like a phone call.
  async function clientTurn(c: CallState) {
    const clientText = c.history[c.history.length - 1]?.text ?? "";
    lastClientText.current = clientText;
    await speak(clientText);
    muteUntil.current = Date.now() + 600;
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (phoneRef.current && c.status === "active" && SR) {
      await new Promise((r) => setTimeout(r, 250));
      listen(SR, true);
    }
  }

  async function onStart() {
    setBusy(true);
    setError("");
    let started: CallState | null = null;
    try {
      started = await startCall({
        scenario,
        llm,
        model: model || null,
        language,
        wait_seconds: waitSeconds,
        seed: seed.trim() === "" ? null : Number(seed),
      });
      setElapsed(0);
      setCall(started);
      callRef.current = started;
      setTrail([started.mood]);
      setInput("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
    if (started) {
      if (phoneRef.current && started.status === "active") {
        setRinging(true); // the phone rings, then the client picks up and talks
        await ring();
        setRinging(false);
      }
      await clientTurn(started);
    }
  }

  async function sendMessage(text: string) {
    const message = text.trim();
    const cur = callRef.current;
    if (!cur || cur.status !== "active" || !message || busyRef.current) return;
    window.speechSynthesis?.cancel(); // the advisor interrupts the client
    busyRef.current = true;
    setBusy(true);
    setError("");
    setPending(message);
    setInput("");
    let reply: CallState | null = null;
    try {
      reply = await sendReply(cur.call_id, message);
      setCall(reply);
      callRef.current = reply;
      setTrail((t) => [...t, reply!.mood]);
    } catch (e) {
      setError((e as Error).message);
      setInput(message);
    } finally {
      setPending(null);
      busyRef.current = false;
      setBusy(false);
    }
    if (reply) await clientTurn(reply);
  }

  const onSend = () => sendMessage(input);

  async function onEnd() {
    if (!call) return;
    window.speechSynthesis?.cancel();
    recognition.current?.abort?.();
    try {
      setCall(await endCall(call.call_id));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  // Speech to text, browser first (Chrome / Edge / Safari): no Whisper, no ffmpeg needed.
  // auto = hands-free: what you say is sent as soon as you stop talking.
  function listen(SR: any, auto = false) {
    if (recognition.current && recording) return;
    setError("");
    const rec = new SR();
    rec.lang = speechLang(language, callRef.current?.client.language).bcp47;
    rec.interimResults = true;
    rec.continuous = false;
    const base = !auto && input ? input + " " : "";
    let heard = "";
    let fatal = false;
    rec.onresult = (e: any) => {
      let text = "";
      for (let i = 0; i < e.results.length; i++) text += e.results[i][0].transcript;
      heard = text;
      setInput(base + text);
    };
    rec.onerror = (e: any) => {
      const msgs: Record<string, string> = {
        "not-allowed": "Microphone blocked: click the lock icon in the address bar and allow the microphone.",
        "service-not-allowed": "Speech recognition is blocked by the browser or the OS.",
        "no-speech": "",
        "aborted": "",
        "audio-capture": "No microphone found.",
        network: "Speech recognition needs an internet connection (the browser sends audio to its speech service).",
        "language-not-supported": "The browser does not support speech recognition for this language.",
      };
      const msg = e.error in msgs ? msgs[e.error] : `Speech recognition error: ${e.error}`;
      if (msg) {
        setError(msg);
        fatal = true;
      }
    };
    rec.onend = () => {
      setRecording(false);
      recognition.current = null;
      const cur = callRef.current;
      if (!auto || !cur || cur.status !== "active") return;
      if (fatal) {
        setPhone(false);
      } else if (heard.trim()) {
        if (Date.now() < muteUntil.current) {
          setInput(base);
          if (phoneRef.current) setTimeout(() => phoneRef.current && listen(SR, true), 300);
          return;
        }
        const h = heard.trim();
        const lc = lastClientText.current.trim().toLowerCase();
        if (lc && h.length >= 4 && (h.toLowerCase().includes(lc) || lc.includes(h.toLowerCase()))) {
          setInput(base);
          if (phoneRef.current) setTimeout(() => phoneRef.current && listen(SR, true), 300);
          return;
        }
        sendMessage(h);
      } else if (phoneRef.current) {
        setTimeout(() => phoneRef.current && listen(SR, true), 300);
      }
    };
    try {
      rec.start();
      recognition.current = rec;
      setRecording(true);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  function toggleBrowserRecognition(SR: any) {
    if (recording) {
      recognition.current?.stop();
      return;
    }
    listen(SR, false);
  }

  function togglePhone(on: boolean) {
    setPhone(on);
    phoneRef.current = on;
    if (!on) {
      window.speechSynthesis?.cancel();
      recognition.current?.abort?.();
      return;
    }
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SR) {
      setError("Your browser cannot listen to you: use Chrome or Edge to talk. You can still type your answers.");
    } else if (callRef.current?.status === "active" && !busyRef.current) {
      setTimeout(() => listen(SR, true), 250);
    }
  }

  async function toggleRecording() {
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (SR) return toggleBrowserRecognition(SR);
    // fallback: record audio and send it to Whisper on the server
    if (recording) {
      recorder.current?.stop();
      return;
    }
    setError("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          channelCount: 1,
          suppressLocalAudioPlayback: true,
        } as any,
      });
      const rec = new MediaRecorder(stream);
      const chunks: Blob[] = [];
      rec.ondataavailable = (e) => chunks.push(e.data);
      rec.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        setRecording(false);
        setTranscribing(true);
        try {
          const { stt } = speechLang(language, callRef.current?.client.language);
          const text = await speechToText(new Blob(chunks, { type: rec.mimeType || "audio/webm" }), stt);
          setInput((prev) => (prev ? prev + " " : "") + text);
        } catch (e) {
          setError((e as Error).message);
        } finally {
          setTranscribing(false);
        }
      };
      rec.start();
      recorder.current = rec;
      setRecording(true);
    } catch {
      setError("Microphone access was denied. Allow the microphone in your browser and try again.");
    }
  }

  const active = call?.status === "active";
  const s = call?.summary;

  useEffect(() => {
    if (!active || ringing) return;
    const t0 = Date.now() - elapsed * 1000;
    const id = setInterval(() => setElapsed(Math.floor((Date.now() - t0) / 1000)), 1000);
    return () => clearInterval(id);
  }, [active, ringing, call?.call_id]);
  const clock = `${String(Math.floor(elapsed / 60)).padStart(2, "0")}:${String(elapsed % 60).padStart(2, "0")}`;

  return (
    <div className="call-layout">
      <aside className="side">
        <section className="block">
          <h2>New call</h2>
          <label>
            Scenario
            <select value={scenario} onChange={(e) => setScenario(e.target.value)}>
              {(scenarios.length ? scenarios : [{ name: "normal", calls_per_min: 1 }]).map((sc) => (
                <option key={sc.name} value={sc.name}>
                  {sc.name} ({sc.calls_per_min}/min)
                </option>
              ))}
            </select>
          </label>
          <label>
            Client language
            <select value={language} onChange={(e) => setLanguage(e.target.value)}>
              {LANG_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Client replies from
            <select value={llm} onChange={(e) => setLlm(e.target.value)}>
              {LLM_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
          {llm !== "auto" && llm !== "none" && (
            <label>
              Model
              <select value={model} onChange={(e) => setModel(e.target.value)} disabled={!models.length}>
                {models.length === 0 && <option value="">(none available)</option>}
                {models.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
              {modelsError && <span className="hint" style={{ color: "#b3261e" }}>{modelsError}</span>}
            </label>
          )}
          <div className="row2">
            <label>
              Queue wait (s)
              <input
                type="number"
                min={0}
                value={waitSeconds}
                onChange={(e) => setWaitSeconds(Math.max(0, Number(e.target.value)))}
              />
            </label>
            <label>
              Seed
              <input
                type="number"
                placeholder="random"
                value={seed}
                onChange={(e) => setSeed(e.target.value)}
              />
            </label>
          </div>
          <button className="primary" onClick={onStart} disabled={busy}>
            {call ? "Start another call" : "Start call"}
          </button>
          <label className="check">
            <input type="checkbox" checked={phone} onChange={(e) => togglePhone(e.target.checked)} />
            Phone call mode: the client talks, then your microphone opens by itself
          </label>
        </section>

        {call && (
          <section className="block">
            <h2>Client file</h2>
            <dl className="facts">
              <dt>Name</dt>
              <dd>{call.client.name}</dd>
              <dt>Channel</dt>
              <dd>{call.client.channel}</dd>
              <dt>Profile</dt>
              <dd>{call.client.profile}</dd>
              <dt>Reason</dt>
              <dd>{call.client.intent.replace(/_/g, " ")}</dd>
              <dt>Urgency</dt>
              <dd>{call.client.urgency} of 3</dd>
              <dt>Patience</dt>
              <dd className="num">{Math.round(call.client.patience_s)} s</dd>
              <dt>Waited</dt>
              <dd className="num">{call.waited_s} s</dd>
            </dl>
            <p className="hint">This is the simulator&apos;s view. A real advisor would not see profile or reason.</p>
          </section>
        )}
      </aside>

      <section className="stage">
        {!call ? (
          <div className="empty">
            <h1>Play the advisor</h1>
            <p>
              Start a call, then answer the client in French, Tunisian Darija or English, by typing or with the
              microphone. The client&apos;s mood moves with every answer, and the call ends when the client is
              resolved, escalated, or gone.
            </p>
          </div>
        ) : (
          <>
            <MoodStrip mood={call.mood} trail={trail} />

            <div className="sentiment-row" role="status" aria-live="polite">
              <span className="sentiment-label">Client sentiment</span>
              <span className="sentiment-badge" style={{ background: sentimentMeta(call.sentiment).color }}>
                {sentimentMeta(call.sentiment).label}
              </span>
            </div>

            {call.engine && (
              <p className="hint" role="status">
                Client engine: {call.engine.backend}
                {call.engine.model ? ` (${call.engine.model})` : ""} · {call.engine.llm_turns} LLM turns,{" "}
                {call.engine.rule_turns} rule-based
                {(call.engine.last_error || call.engine.warning) && (
                  <span style={{ color: "#b3261e" }}>
                    {" "}
                    — LLM not used: {call.engine.last_error || call.engine.warning}
                  </span>
                )}
              </p>
            )}

            <p className="hint" role="status" style={{ fontWeight: 600 }}>
              {ringing
                ? "📞 Ringing…"
                : active
                  ? `📞 Call in progress ${clock}${speaking ? " · 🔊 the client is talking…" : recording ? " · 🎤 your turn, speak now" : ""}`
                  : `📞 Call ended ${clock}`}
            </p>

            <div className="transcript" ref={transcript} aria-live="polite">
              {(ringing ? [] : call.history).map((t, i) => (
                <div key={i} className={`msg ${t.role === "client" ? "client" : "advisor"}`}>
                  <span className="who">{t.role === "client" ? call.client.name : "You"}</span>
                  <p>{t.text}</p>
                </div>
              ))}
              {pending && (
                <div className="msg advisor">
                  <span className="who">You</span>
                  <p>{pending}</p>
                </div>
              )}
              {pending && <div className="typing">{call.client.name} is answering…</div>}
            </div>

            {active ? (
              <div className="composer">
                <textarea
                  rows={2}
                  value={input}
                  placeholder="Speak when the microphone is on, or type your answer"
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      onSend();
                    }
                  }}
                  disabled={busy}
                />
                <div className="composer-actions">
                  <button
                    className={`mic ${recording ? "rec" : ""}`}
                    onClick={toggleRecording}
                    disabled={busy || transcribing}
                    aria-pressed={recording}
                  >
                    {recording ? "Stop recording" : transcribing ? "Transcribing…" : "Record"}
                  </button>
                  <button className="ghost" onClick={onEnd} disabled={busy}>
                    End call
                  </button>
                  <button className="primary" onClick={onSend} disabled={busy || !input.trim()}>
                    Send
                  </button>
                </div>
              </div>
            ) : (
              s && (
                <div className="outcome">
                  <h2>{STATUS_LABEL[call.status] ?? call.status}</h2>
                  <dl className="facts wide">
                    <dt>Satisfaction</dt>
                    <dd className="num">{s.satisfaction} / 5</dd>
                    <dt>Turns</dt>
                    <dd className="num">{s.turns}</dd>
                    <dt>Waited</dt>
                    <dd className="num">{s.waited_s} s</dd>
                    <dt>Hidden truth</dt>
                    <dd>
                      {s.is_fraudster
                        ? s.resolved
                          ? "Fraudster, and you served them: a leak."
                          : "Fraudster, and you did not serve them."
                        : "Genuine client."}
                    </dd>
                  </dl>
                </div>
              )
            )}
          </>
        )}
        {error && (
          <div className="error" role="alert">
            {error}
            <button className="link" onClick={() => setError("")}>
              Dismiss
            </button>
          </div>
        )}
      </section>
    </div>
  );
}
