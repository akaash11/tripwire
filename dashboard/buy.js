/* Tripwire No-Install Request Page (/buy) — shares /r/{token} look, redirects to /r/{token} */
(function () {
  "use strict";

  const pathMatch = window.location.pathname.match(/^(.*)\/buy\/?$/);
  const ROOT_PREFIX = pathMatch ? pathMatch[1] : "";

  const shellEl = document.getElementById("buy-shell");
  const version = (shellEl && shellEl.dataset.version) || "";
  const themeToggleEl = document.getElementById("theme-toggle");
  const formEl = document.getElementById("buy-form");
  const photoEl = document.getElementById("photo");
  const previewEl = document.getElementById("photo-preview");
  const recBtn = document.getElementById("rec");
  const recStateEl = document.getElementById("rec-state");
  const budgetEl = document.getElementById("budget");
  const textEl = document.getElementById("text");
  const errEl = document.getElementById("buy-error");
  const goBtn = document.getElementById("go");

  let audioBlob = null;
  let mediaRecorder = null;
  let chunks = [];
  let previewObjUrl = null;

  function initTheme() {
    const saved = localStorage.getItem("tripwire-theme");
    if (saved === "light" || saved === "dark") {
      document.documentElement.setAttribute("data-theme", saved);
    }
    updateThemeLabel();
    themeToggleEl.addEventListener("click", () => {
      const current = document.documentElement.getAttribute("data-theme");
      const prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
      const isDark = current ? current === "dark" : prefersDark;
      const next = isDark ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      localStorage.setItem("tripwire-theme", next);
      updateThemeLabel();
    });
  }

  function updateThemeLabel() {
    const current = document.documentElement.getAttribute("data-theme");
    const prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    const isDark = current ? current === "dark" : prefersDark;
    themeToggleEl.textContent = isDark ? "Light" : "Dark";
  }

  function showError(msg) {
    if (!msg) {
      errEl.hidden = true;
      errEl.textContent = "";
      return;
    }
    errEl.textContent = msg;
    errEl.hidden = false;
  }

  photoEl.addEventListener("change", () => {
    const f = photoEl.files && photoEl.files[0];
    if (previewObjUrl) {
      URL.revokeObjectURL(previewObjUrl);
      previewObjUrl = null;
    }
    if (f) {
      previewObjUrl = URL.createObjectURL(f);
      previewEl.src = previewObjUrl;
      previewEl.hidden = false;
      showError("");
    } else {
      previewEl.hidden = true;
      previewEl.removeAttribute("src");
    }
  });

  async function startRec(ev) {
    ev.preventDefault();
    showError("");
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      recStateEl.textContent = "Microphone not available here — type your request below.";
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      chunks = [];
      mediaRecorder = new MediaRecorder(stream);
      mediaRecorder.ondataavailable = (e) => {
        if (e.data && e.data.size) chunks.push(e.data);
      };
      mediaRecorder.onstop = () => {
        audioBlob = new Blob(chunks, { type: mediaRecorder.mimeType || "audio/mp4" });
        stream.getTracks().forEach((t) => t.stop());
        recBtn.classList.remove("recording");
        recBtn.textContent = "Hold to re-record";
        recStateEl.textContent = "Voice recorded (" + Math.max(1, Math.round(audioBlob.size / 1024)) + " KB).";
      };
      mediaRecorder.start();
      recBtn.classList.add("recording");
      recBtn.textContent = "Listening… release to stop";
      recStateEl.textContent = "Recording voice note…";
    } catch (_) {
      recBtn.classList.remove("recording");
      recStateEl.textContent = "Mic not available — type your request below.";
    }
  }

  function stopRec() {
    if (mediaRecorder && mediaRecorder.state === "recording") {
      mediaRecorder.stop();
    }
  }

  recBtn.addEventListener("pointerdown", startRec);
  recBtn.addEventListener("pointerup", stopRec);
  recBtn.addEventListener("pointercancel", stopRec);
  recBtn.addEventListener("pointerleave", stopRec);

  formEl.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    showError("");

    const fd = new FormData();
    const photo = photoEl.files && photoEl.files[0];
    if (photo) fd.append("photo", photo);
    if (audioBlob) {
      const ext = (audioBlob.type || "").includes("webm") ? "webm" : "m4a";
      fd.append("audio", audioBlob, "voice." + ext);
    }

    const bud = budgetEl.value.trim();
    let text = textEl.value.trim();
    if (bud) {
      text = (text ? text + ". " : "") + "Budget: $" + bud + " delivered, including shipping and tax.";
    }
    if (text) fd.append("text", text);
    fd.append("client", version ? ("web " + version) : "web");

    if (!photo && !audioBlob && !text) {
      showError("Add a photo, record your voice, or type what you want to buy.");
      return;
    }

    goBtn.disabled = true;
    goBtn.textContent = "Sending to Tripwire…";

    try {
      const resp = await fetch(ROOT_PREFIX + "/request", { method: "POST", body: fd });
      const data = await resp.json();
      if (!resp.ok || !data.token) {
        showError(data.speak || "Could not send request. Please try again.");
        goBtn.disabled = false;
        goBtn.textContent = "Send to Tripwire";
        return;
      }
      window.location.href = ROOT_PREFIX + "/r/" + encodeURIComponent(data.token);
    } catch (_) {
      showError("Network error while sending. Please check your connection and try again.");
      goBtn.disabled = false;
      goBtn.textContent = "Send to Tripwire";
    }
  });

  initTheme();
})();
