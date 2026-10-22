const form = document.querySelector("#chat-form");
const input = document.querySelector("#message-input");
const messages = document.querySelector("#messages");
const sendButton = document.querySelector("#send-button");
const recordButton = document.querySelector("#record-button");
const newChatButton = document.querySelector("#new-chat");
const errorMessage = document.querySelector("#error-message");
const recordingStatus = document.querySelector("#recording-status");
const healthDot = document.querySelector("#health-dot");
const healthLabel = document.querySelector("#health-label");

let threadId = null;
let busy = false;
let awaitingApproval = false;
let recorder = null;
let recordingStream = null;
let audioChunks = [];
let recordingTimeout = null;
let transcribing = false;
let requestingMicrophone = false;
const maxRecordingMs = 60_000;

function addMessage(role, text) {
  const row = document.createElement("article");
  row.className = `message ${role}`;

  const avatar = document.createElement("span");
  avatar.className = "avatar";
  avatar.setAttribute("aria-hidden", "true");
  avatar.textContent = role === "user" ? "Y" : "C";

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;

  row.append(avatar, bubble);
  messages.append(row);
  messages.scrollTop = messages.scrollHeight;
  return row;
}

function setBusy(value) {
  busy = value;
  updateControls();
}

function updateControls() {
  const locked = busy || transcribing || requestingMicrophone || recorder?.state === "recording";
  sendButton.disabled = locked || awaitingApproval;
  recordButton.disabled = busy || transcribing || requestingMicrophone || awaitingApproval;
  newChatButton.disabled = locked;
  input.disabled = locked || awaitingApproval;
}

function showError(message) {
  errorMessage.textContent = message;
  errorMessage.hidden = false;
}

async function postChat(payload) {
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const contentType = response.headers.get("Content-Type") || "";
  const isJson = contentType.toLowerCase().includes("application/json");
  if (!response.ok && !isJson) {
    throw new Error("Le service est indisponible, réessayez dans quelques instants.");
  }
  if (!isJson) {
    throw new Error("Le service est indisponible, réessayez dans quelques instants.");
  }

  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error("Le service est indisponible, réessayez dans quelques instants.");
  }
  if (!response.ok) {
    const detail = typeof data.detail === "string"
      ? data.detail
      : "Le service est indisponible, réessayez dans quelques instants.";
    throw new Error(detail);
  }
  if (typeof data.detail === "string") throw new Error(data.detail);
  return data;
}

async function transcribeRecording(blob) {
  const body = new FormData();
  const extension = blob.type.includes("mp4") ? "mp4" : "webm";
  body.append("audio", blob, `recording.${extension}`);

  const response = await fetch("/api/transcribe", { method: "POST", body });
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error("The speech recognition service returned an invalid response.");
  }
  if (!response.ok) {
    throw new Error(typeof data.detail === "string" ? data.detail : "Audio transcription failed.");
  }
  if (typeof data.text !== "string" || !data.text.trim()) {
    throw new Error("No speech was detected in the recording.");
  }
  return data.text.trim();
}

function stopRecording() {
  if (recorder?.state === "recording") recorder.stop();
}

async function startRecording() {
  errorMessage.hidden = true;
  if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
    showError("This browser does not support microphone recording.");
    return;
  }

  requestingMicrophone = true;
  updateControls();
  try {
    recordingStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    requestingMicrophone = false;
    const preferredType = ["audio/webm;codecs=opus", "audio/mp4"]
      .find((type) => MediaRecorder.isTypeSupported(type));
    recorder = preferredType
      ? new MediaRecorder(recordingStream, { mimeType: preferredType })
      : new MediaRecorder(recordingStream);
    audioChunks = [];

    recorder.addEventListener("dataavailable", (event) => {
      if (event.data.size) audioChunks.push(event.data);
    });
    recorder.addEventListener("stop", async () => {
      clearTimeout(recordingTimeout);
      recordingStream?.getTracks().forEach((track) => track.stop());
      recordingStream = null;
      recordButton.classList.remove("recording");
      recordButton.setAttribute("aria-pressed", "false");
      recordButton.title = "Record a voice message";
      recordButton.setAttribute("aria-label", "Record a voice message");
      recordingStatus.hidden = true;
      updateControls();

      const blob = new Blob(audioChunks, { type: recorder.mimeType || "audio/webm" });
      audioChunks = [];
      if (!blob.size) {
        showError("No audio was captured. Please try again.");
        return;
      }

      transcribing = true;
      recordingStatus.textContent = "Transcribing audio locally…";
      recordingStatus.hidden = false;
      updateControls();
      try {
        const transcript = await transcribeRecording(blob);
        recordingStatus.textContent = "Sending your voice message to Cashy…";
        await sendMessage(transcript);
        recordingStatus.hidden = true;
      } catch (error) {
        showError(error.message || "Audio transcription failed.");
        recordingStatus.hidden = true;
      } finally {
        transcribing = false;
        updateControls();
      }
    }, { once: true });

    recorder.start();
    recordButton.classList.add("recording");
    recordButton.setAttribute("aria-pressed", "true");
    recordButton.title = "Stop recording";
    recordButton.setAttribute("aria-label", "Stop recording");
    recordingStatus.textContent = "Recording… Select the microphone again to stop (60-second limit).";
    recordingStatus.hidden = false;
    updateControls();
    recordingTimeout = setTimeout(stopRecording, maxRecordingMs);
  } catch (error) {
    requestingMicrophone = false;
    recordingStream?.getTracks().forEach((track) => track.stop());
    recordingStream = null;
    showError(error.name === "NotAllowedError"
      ? "Microphone access was denied. Allow microphone access and try again."
      : "Unable to start microphone recording.");
    updateControls();
  }
}

function addApproval(response) {
  const assistantRow = addMessage("assistant", response.message || "Please confirm this action.");
  const bubble = assistantRow.querySelector(".bubble");
  const card = document.createElement("div");
  card.className = "approval-card";

  const description = document.createElement("p");
  const details = response.confirmation?.details || {};
  description.textContent = Object.keys(details).length
    ? `${response.confirmation?.action || "Requested action"}: ${Object.entries(details).map(([key, value]) => `${key.replaceAll("_", " ")}: ${value}`).join(" · ")}`
    : "Review the requested action above before confirming.";

  const actions = document.createElement("div");
  actions.className = "approval-actions";
  for (const [label, approved, className] of [
    ["Confirm action", true, "approve-button"],
    ["Cancel", false, "reject-button"],
  ]) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = className;
    button.textContent = label;
    button.addEventListener("click", () => respondToApproval(approved, actions));
    actions.append(button);
  }

  card.append(description, actions);
  bubble.append(card);
  messages.scrollTop = messages.scrollHeight;
  awaitingApproval = true;
  setBusy(false);
}

async function sendMessage(message) {
  errorMessage.hidden = true;
  addMessage("user", message);
  const typing = addMessage("assistant typing", "Cashy is thinking…");
  setBusy(true);
  try {
    const response = await postChat({ message, thread_id: threadId });
    threadId = response.thread_id;
    typing.remove();
    if (response.pending_approval) {
      addApproval(response);
    } else {
      addMessage("assistant", response.message || "I couldn't produce a response.");
    }
  } catch (error) {
    typing.remove();
    showError(error.message || "Unable to reach the agent API.");
  } finally {
    setBusy(false);
    if (!awaitingApproval) input.focus();
  }
}

async function respondToApproval(approved, actions) {
  errorMessage.hidden = true;
  for (const button of actions.querySelectorAll("button")) button.disabled = true;
  awaitingApproval = false;
  setBusy(true);
  const typing = addMessage("assistant typing", approved ? "Confirming…" : "Cancelling…");
  try {
    const response = await postChat({ thread_id: threadId, approved });
    threadId = response.thread_id;
    typing.remove();
    if (response.pending_approval) {
      addApproval(response);
    } else {
      addMessage("assistant", response.message || "Done.");
    }
  } catch (error) {
    typing.remove();
    awaitingApproval = true;
    for (const button of actions.querySelectorAll("button")) button.disabled = false;
    showError(error.message || "Unable to resume the agent action.");
  } finally {
    setBusy(false);
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const message = input.value.trim();
  if (!message || busy || transcribing || recorder?.state === "recording" || awaitingApproval) return;
  input.value = "";
  input.style.height = "auto";
  void sendMessage(message);
});

recordButton.addEventListener("click", () => {
  if (recorder?.state === "recording") {
    stopRecording();
  } else {
    void startRecording();
  }
});

input.addEventListener("input", () => {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 132)}px`;
});

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

newChatButton.addEventListener("click", () => {
  if (busy || transcribing || recorder?.state === "recording") return;
  threadId = null;
  awaitingApproval = false;
  messages.replaceChildren();
  addMessage("assistant", "New conversation started. What can I help you with?");
  errorMessage.hidden = true;
  setBusy(false);
  input.value = "";
  input.focus();
});

fetch("/health")
  .then((response) => {
    if (!response.ok) throw new Error("Agent offline");
    return response.json();
  })
  .then((data) => {
    healthDot.classList.add("ready");
    healthLabel.textContent = `${data.mode} agent ready`;
  })
  .catch(() => {
    healthDot.classList.add("offline");
    healthLabel.textContent = "Agent unavailable";
  });
