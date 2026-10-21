const form = document.querySelector("#chat-form");
const input = document.querySelector("#message-input");
const messages = document.querySelector("#messages");
const sendButton = document.querySelector("#send-button");
const newChatButton = document.querySelector("#new-chat");
const errorMessage = document.querySelector("#error-message");
const healthDot = document.querySelector("#health-dot");
const healthLabel = document.querySelector("#health-label");
const speechControls = document.querySelector("#speech-controls");
const speechToggle = document.querySelector("#speech-toggle");
const speechStop = document.querySelector("#speech-stop");
const speechWarning = document.querySelector("#speech-warning");

let threadId = null;
let busy = false;
let awaitingApproval = false;
let speechEnabled = false;
let speechRequestId = 0;
let activeSpeechButton = null;

function setSpeechButtonState(button, isSpeaking) {
  if (!button) return;
  button.setAttribute("aria-pressed", String(isSpeaking));
  button.setAttribute(
    "aria-label",
    isSpeaking ? "Arrêter la lecture de ce message" : "Lire ce message à voix haute",
  );
  button.textContent = isSpeaking ? "⏹ Arrêter" : "🔊 Lire";
}

function resetActiveSpeechButton() {
  setSpeechButtonState(activeSpeechButton, false);
  activeSpeechButton = null;
}

function cancelSpeech() {
  speechRequestId += 1;
  if ("speechSynthesis" in window) window.speechSynthesis.cancel();
  resetActiveSpeechButton();
}

function cleanForSpeech(text) {
  const lines = text
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, " ")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/`[^`]*`/g, " ")
    .split("\n");
  const readableLines = [];

  for (let index = 0; index < lines.length; index += 1) {
    if (/^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(lines[index])) {
      if (readableLines.length && readableLines[readableLines.length - 1].includes("|")) {
        readableLines.pop();
      }
      while (index + 1 < lines.length && lines[index + 1].includes("|")) index += 1;
      continue;
    }
    readableLines.push(lines[index]);
  }

  return readableLines
    .join("\n")
    .replace(/(?:https?:\/\/|www\.)[^\s<>()]+/gi, " ")
    .replace(/[*_`#>]/g, "")
    .replace(/[ \t]+/g, " ")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function detectSpeechLanguage(text) {
  if (/[àâçéèêëîïôùûüÿœ]/i.test(text)
    || /\b(bonjour|merci|votre|vos|compte|solde|transaction|dépense|épargne|pour|avec|est|les|des)\b/i.test(text)) {
    return "fr-FR";
  }
  if (/\b(hello|your|you|account|balance|transaction|spending|thank|please|the|with|is|are)\b/i.test(text)) {
    return "en-US";
  }
  const browserLanguage = navigator.language || "";
  if (/^fr\b/i.test(browserLanguage)) return browserLanguage;
  if (/^en\b/i.test(browserLanguage)) return browserLanguage;
  return "fr-FR";
}

function getSpeechVoices() {
  const voices = window.speechSynthesis.getVoices();
  if (voices.length) return Promise.resolve(voices);

  return new Promise((resolve) => {
    const onVoicesChanged = () => {
      const availableVoices = window.speechSynthesis.getVoices();
      if (!availableVoices.length) return;
      window.speechSynthesis.removeEventListener("voiceschanged", onVoicesChanged);
      resolve(availableVoices);
    };
    window.speechSynthesis.addEventListener("voiceschanged", onVoicesChanged);
  });
}

function splitSpeechIntoSentences(text) {
  return text.match(/[^.!?。！？]+[.!?。！？]+(?:["'”’)\]]+)?|[^.!?。！？]+$/g)
    ?.map((sentence) => sentence.trim())
    .filter(Boolean) || [];
}

async function speak(text, button = null, automatic = false) {
  if ((automatic && !speechEnabled) || !("speechSynthesis" in window)) return;
  const spokenText = cleanForSpeech(text);
  if (!spokenText) return;

  cancelSpeech();
  const requestId = speechRequestId;
  const sentences = splitSpeechIntoSentences(spokenText);
  if (!sentences.length) return;

  activeSpeechButton = button;
  setSpeechButtonState(button, true);
  const language = detectSpeechLanguage(spokenText);
  const voices = await getSpeechVoices();
  if (requestId !== speechRequestId || activeSpeechButton !== button) return;
  const voice = voices.find((candidate) => candidate.lang.toLowerCase().startsWith(language.slice(0, 2).toLowerCase())) || null;

  function speakSentence(index) {
    if (requestId !== speechRequestId || activeSpeechButton !== button) return;
    if (index >= sentences.length) {
      resetActiveSpeechButton();
      return;
    }

    const utterance = new SpeechSynthesisUtterance(sentences[index]);
    utterance.lang = language;
    utterance.voice = voice;
    utterance.onend = () => {
      if (requestId === speechRequestId && activeSpeechButton === button) {
        speakSentence(index + 1);
      }
    };
    utterance.onerror = (event) => {
      if (requestId !== speechRequestId || activeSpeechButton !== button) return;
      if (event.error === "canceled" || event.error === "interrupted") return;
      resetActiveSpeechButton();
    };
    window.speechSynthesis.speak(utterance);
  }

  speakSentence(0);
}

function updateSpeechControls() {
  speechToggle.setAttribute("aria-pressed", String(speechEnabled));
  speechToggle.textContent = `🔊 Read aloud: ${speechEnabled ? "On" : "Off"}`;
  speechWarning.hidden = !speechEnabled;
}

if ("speechSynthesis" in window) {
  speechControls.hidden = false;
  try {
    speechEnabled = localStorage.getItem("cashy_tts_enabled") === "true";
  } catch {
    speechEnabled = false;
  }
  updateSpeechControls();

  speechToggle.addEventListener("click", () => {
    speechEnabled = !speechEnabled;
    try {
      localStorage.setItem("cashy_tts_enabled", String(speechEnabled));
    } catch {
      // Keep the setting active for this page even when storage is unavailable.
    }
    if (!speechEnabled) cancelSpeech();
    updateSpeechControls();
  });
  speechStop.addEventListener("click", cancelSpeech);
}

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
  let speechButton = null;
  if (role === "assistant" && "speechSynthesis" in window) {
    speechButton = document.createElement("button");
    speechButton.type = "button";
    speechButton.className = "message-speech-button";
    speechButton.setAttribute("aria-pressed", "false");
    speechButton.setAttribute("aria-label", "Lire ce message à voix haute");
    speechButton.textContent = "🔊 Lire";
    speechButton.addEventListener("click", () => {
      if (activeSpeechButton === speechButton) {
        cancelSpeech();
      } else {
        void speak(text, speechButton);
      }
    });
    bubble.append(speechButton);
  }
  messages.append(row);
  messages.scrollTop = messages.scrollHeight;
  if (speechButton && speechEnabled) void speak(text, speechButton, true);
  return row;
}

function setBusy(value) {
  busy = value;
  sendButton.disabled = value || awaitingApproval;
  newChatButton.disabled = value;
  input.disabled = value || awaitingApproval;
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

function addApproval(response) {
  const message = response.message || "Please confirm this action.";
  const assistantRow = addMessage("assistant", message);
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
      const message = response.message || "I couldn't produce a response.";
      addMessage("assistant", message);
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
      const message = response.message || "Done.";
      addMessage("assistant", message);
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
  if (!message || busy || awaitingApproval) return;
  cancelSpeech();
  input.value = "";
  input.style.height = "auto";
  void sendMessage(message);
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
  if (busy) return;
  cancelSpeech();
  threadId = null;
  awaitingApproval = false;
  messages.replaceChildren();
  addMessage("assistant", "New conversation started. What can I help you with?");
  errorMessage.hidden = true;
  setBusy(false);
  input.value = "";
  input.focus();
});

window.addEventListener("pagehide", cancelSpeech);

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
