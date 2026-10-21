// Hands-free listening: records one utterance and stops by itself when you stop talking.
// Voice activity detection with an adaptive threshold (it calibrates on the room noise first).

export type ListenOptions = {
  silenceMs?: number; // silence that ends an utterance
  minSpeechMs?: number; // shorter sounds are ignored (clicks, breaths)
  maxMs?: number; // hard cap for one utterance
  idleMs?: number; // give up if nobody speaks for this long (the loop then listens again)
};

function pickMime(): string | undefined {
  const wanted = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];
  return wanted.find((m) => typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported(m));
}

export function listenOnce(
  stream: MediaStream,
  signal: AbortSignal,
  onLevel?: (rms: number, speaking: boolean) => void,
  opts: ListenOptions = {},
): Promise<Blob | null> {
  const { silenceMs = 1100, minSpeechMs = 300, maxMs = 25000, idleMs = 20000 } = opts;

  return new Promise((resolve) => {
    const AC: typeof AudioContext =
      window.AudioContext ?? (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    const ctx = new AC();
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 1024;
    ctx.createMediaStreamSource(stream).connect(analyser);
    const samples = new Float32Array(analyser.fftSize);

    const mime = pickMime();
    const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
    const chunks: Blob[] = [];
    rec.ondataavailable = (e) => e.data.size && chunks.push(e.data);

    let keep = false;
    let finished = false;
    let speechMs = 0;
    let silentMs = 0;
    let heard = false;
    let noise = 0;
    let calibrated = 0;
    const t0 = performance.now();
    let last = t0;
    let timer: ReturnType<typeof setTimeout>;

    const finish = (save: boolean) => {
      if (finished) return;
      finished = true;
      keep = save;
      clearTimeout(timer);
      if (rec.state !== "inactive") rec.stop();
      else resolve(null);
    };

    rec.onstop = () => {
      ctx.close().catch(() => {});
      resolve(keep && chunks.length ? new Blob(chunks, { type: rec.mimeType || "audio/webm" }) : null);
    };
    signal.addEventListener("abort", () => finish(false), { once: true });

    const tick = () => {
      if (finished) return;
      analyser.getFloatTimeDomainData(samples);
      let sum = 0;
      for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
      const rms = Math.sqrt(sum / samples.length);
      const now = performance.now();
      const dt = now - last;
      last = now;

      if (now - t0 < 400) {
        // calibrate on the first 400 ms of room noise
        noise = (noise * calibrated + rms) / (calibrated + 1);
        calibrated++;
      }
      const threshold = Math.max(0.02, noise * 2.5);
      const speaking = rms > threshold;
      onLevel?.(rms, speaking);

      if (speaking) {
        speechMs += dt;
        silentMs = 0;
        if (speechMs >= minSpeechMs) heard = true;
      } else if (heard) {
        silentMs += dt;
      }

      if (heard && silentMs >= silenceMs) return finish(true);
      if (heard && now - t0 >= maxMs) return finish(true);
      if (!heard && now - t0 >= idleMs) return finish(false);
      timer = setTimeout(tick, 50);
    };

    rec.start(100);
    timer = setTimeout(tick, 50);
  });
}
