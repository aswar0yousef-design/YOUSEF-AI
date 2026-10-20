"use strict";

// Voice for the YOUSEF AI dashboard:
// - mic button: say one request (push to talk)
// - always-listening mode: say "يوسف" then the request
// - spoken approvals: "وافق" / "ارفض" when a step waits for approval
// - spoken replies for voice requests, with the browser's (local) voices
// Speech-to-text runs on the local server (faster-whisper); audio never leaves the PC.
(() => {
  const Y = window.Yousef;
  const $ = (sel) => document.querySelector(sel);

  const store = {
    get(key, fallback) {
      try {
        const raw = localStorage.getItem(`yousef.${key}`);
        return raw === null ? fallback : JSON.parse(raw);
      } catch (e) { return fallback; }
    },
    set(key, value) {
      try { localStorage.setItem(`yousef.${key}`, JSON.stringify(value)); } catch (e) { /* storage blocked */ }
    },
  };

  const v = {
    hands: false,          // always-listening mode
    pttActive: false,      // a push-to-talk recording is in progress
    pttTimer: null,
    listenState: "wake",   // wake | command | approval
    stateTimer: null,
    busy: 0,               // transcriptions in flight
    pending: null,         // {stepId, taskId} waiting for a spoken yes/no
    muteUntil: 0,
    level: 0,
    heard: "",
    heardTimer: null,
    queue: Promise.resolve(),
  };

  // ---------------------------------------------------------------- text to speech
  function isArabic(text) {
    const letters = text.match(/[A-Za-z؀-ۿ]/g) || [];
    if (!letters.length) return true;
    return letters.filter((c) => c >= "؀").length / letters.length > 0.3;
  }

  function speakable(text) {
    let t = String(text || "");
    const ar = isArabic(t);
    t = t.replace(/```[\s\S]*?(```|$)/g, ar ? "\nالكود معروض على الشاشة.\n" : "\nThe code is on screen.\n");
    let tableSaid = false;
    t = t.split("\n").map((line) => {
      const s = line.trim();
      if (s.startsWith("|")) {
        if (tableSaid) return "";
        tableSaid = true;
        return ar ? "الجدول معروض على الشاشة." : "The table is on screen.";
      }
      return s.replace(/^#{1,6}\s+/, "").replace(/^[-*•]\s+/, "").replace(/^[\d٠-٩]+[.)]\s+/, "");
    }).join("\n");
    t = t.replace(/https?:\/\/\S+/g, "").replace(/[*_`>#|]/g, "").replace(/\s+/g, " ").trim();
    const limit = 700;
    if (t.length > limit) {
      let cut = t.slice(0, limit);
      const end = Math.max(...[".", "؟", "!", "?", "،"].map((p) => cut.lastIndexOf(p)));
      if (end > 200) cut = cut.slice(0, end + 1);
      t = `${cut} ${ar ? "والتفاصيل على الشاشة." : "The rest is on screen."}`;
    }
    return t;
  }

  // best first: natural (neural) voices, then Gulf and Saudi Arabic, then the rest
  const GULF = ["ar-om", "ar-ae", "ar-sa", "ar-kw", "ar-qa", "ar-bh"];
  function voiceRank(x) {
    const name = (x.name || "").toLowerCase();
    const lang = (x.lang || "").toLowerCase().replace("_", "-");
    let score = 0;
    if (name.includes("natural") || name.includes("neural")) score += 4;
    if (name.includes("google")) score += 2;
    const gulf = GULF.indexOf(lang);
    if (gulf >= 0) score += 2 - gulf * 0.1;
    if (lang === "en-us" || lang === "en-gb") score += 1;
    return score;
  }

  function usableVoices(voices, lang) {
    const online = store.get("ttsOnline", false);
    return voices
      .filter((x) => (x.lang || "").toLowerCase().startsWith(lang) && (online || x.localService))
      .sort((a, b) => voiceRank(b) - voiceRank(a));
  }

  function chunks(text) {
    const sentences = text.match(/[^.!?؟،\n]+[.!?؟،]*/g) || [text];
    const out = [];
    let cur = "";
    for (const s of sentences) {
      if (cur && (cur + s).length > 220) { out.push(cur.trim()); cur = ""; }
      cur += s;
    }
    if (cur.trim()) out.push(cur.trim());
    return out;
  }

  const tts = {
    speaking: false,
    token: 0,
    warned: false,
    supported: "speechSynthesis" in window,
    voices() { return this.supported ? speechSynthesis.getVoices() : []; },
    pick(lang) {
      const usable = usableVoices(this.voices(), lang);
      const wanted = store.get(lang === "ar" ? "ttsAr" : "ttsEn", "");
      return usable.find((x) => x.name === wanted) || usable[0] || null;
    },
    // Calls done() when finished, or right away if nothing can be spoken.
    speak(text, done, force = false) {
      const finish = () => { if (done) done(); };
      if (!this.supported || (!force && !store.get("ttsOn", true))) return finish();
      const clean = speakable(text);
      if (!clean) return finish();
      const lang = isArabic(clean) ? "ar" : "en";
      const voice = this.pick(lang);
      if (!voice) {
        if (!this.warned) {
          this.warned = true;
          Y.showHint(lang === "ar"
            ? "لا يوجد صوت عربي محلي للقراءة. أضفه من إعدادات ويندوز (الوقت واللغة، ثم الكلام)، أو اسمح بالأصوات عبر الإنترنت."
            : "No local English voice is available for reading replies.", true);
        }
        return finish();
      }
      speechSynthesis.cancel();
      const token = ++this.token;
      const parts = chunks(clean);
      this.speaking = true;
      ui.update();
      parts.forEach((part, i) => {
        const u = new SpeechSynthesisUtterance(part);
        u.voice = voice;
        u.lang = voice.lang;
        if (i === parts.length - 1) {
          u.onend = u.onerror = () => {
            if (token !== this.token) return;
            this.speaking = false;
            v.muteUntil = performance.now() + 400;  // don't hear the tail of our own voice
            ui.update();
            finish();
          };
        }
        speechSynthesis.speak(u);
      });
    },
    stop() {
      this.token++;
      if (this.supported) speechSynthesis.cancel();
      this.speaking = false;
      ui.update();
    },
  };

  // ---------------------------------------------------------------- microphone
  const mic = {
    ctx: null, stream: null, node: null, rate: 48000, onFrame: null,
    async start() {
      if (this.ctx) return;
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        throw new Error("هذا المتصفح لا يدعم الميكروفون.");
      }
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      this.ctx = new AudioContext();
      await this.ctx.audioWorklet.addModule("/voice-worklet.js");
      const source = this.ctx.createMediaStreamSource(this.stream);
      this.node = new AudioWorkletNode(this.ctx, "yousef-capture");
      const mute = this.ctx.createGain();
      mute.gain.value = 0;
      source.connect(this.node);
      this.node.connect(mute);
      mute.connect(this.ctx.destination);
      this.node.port.onmessage = (e) => { if (this.onFrame) this.onFrame(e.data); };
      this.rate = this.ctx.sampleRate;
      if (this.ctx.state === "suspended") await this.ctx.resume();
    },
    stop() {
      if (this.stream) this.stream.getTracks().forEach((t) => t.stop());
      if (this.ctx) this.ctx.close();
      this.ctx = this.stream = this.node = null;
    },
  };

  function micErrorMessage(e) {
    if (e && e.name === "NotAllowedError") return "اسمح للمتصفح باستخدام الميكروفون من أيقونة القفل بجانب العنوان، ثم حاول مجددًا.";
    if (e && e.name === "NotFoundError") return "لا يوجد ميكروفون متصل بالجهاز.";
    return (e && e.message) || "تعذّر تشغيل الميكروفون.";
  }

  // Splits the microphone stream into utterances using loudness (20 ms frames).
  class Segmenter {
    constructor(onSegment, onLevel) {
      this.onSegment = onSegment;
      this.onLevel = onLevel;
      this.noise = 0.004;
      this.hang = 40;       // frames of quiet that end an utterance (800 ms)
      this.maxFrames = 1000; // 20 s
      this.preRoll = 15;     // keep 300 ms before speech starts
      this.reset();
    }
    reset() {
      this.ring = [];
      this.frames = [];
      this.speaking = false;
      this.loud = 0;
      this.quiet = 0;
      this.voiced = 0;
    }
    push(frame) {
      let sum = 0;
      for (let i = 0; i < frame.length; i++) sum += frame[i] * frame[i];
      const rms = Math.sqrt(sum / frame.length);
      const threshold = Math.max(this.noise * 3, 0.012);
      this.onLevel(Math.min(1, rms / 0.12));
      if (!this.speaking) {
        this.ring.push(frame);
        if (this.ring.length > this.preRoll) this.ring.shift();
        if (rms > threshold) {
          this.loud += 1;
          if (this.loud >= 3) {
            this.speaking = true;
            this.frames = this.ring.slice();
            this.ring = [];
            this.quiet = 0;
            this.voiced = this.loud;
          }
        } else {
          this.loud = 0;
          this.noise = Math.min(0.05, Math.max(0.001, this.noise * 0.95 + rms * 0.05));
        }
        return;
      }
      this.frames.push(frame);
      if (rms > threshold * 0.7) { this.quiet = 0; this.voiced += 1; } else { this.quiet += 1; }
      if (this.quiet >= this.hang || this.frames.length >= this.maxFrames) {
        const frames = this.frames;
        const voiced = this.voiced;
        this.reset();
        if (voiced >= 10) this.onSegment(frames);
      }
    }
  }

  function toWav(frames, rate) {
    const total = frames.reduce((n, f) => n + f.length, 0);
    const input = new Float32Array(total);
    let offset = 0;
    for (const f of frames) { input.set(f, offset); offset += f.length; }
    const target = 16000;
    const ratio = rate / target;
    const n = Math.floor(total / ratio);
    const pcm = new Int16Array(n);
    for (let i = 0; i < n; i++) {
      const start = Math.floor(i * ratio);
      const end = Math.max(start + 1, Math.min(total, Math.floor((i + 1) * ratio)));
      let acc = 0;
      for (let j = start; j < end; j++) acc += input[j];
      const s = Math.max(-1, Math.min(1, acc / (end - start)));
      pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    const buffer = new ArrayBuffer(44 + pcm.length * 2);
    const view = new DataView(buffer);
    const text = (at, s) => { for (let i = 0; i < s.length; i++) view.setUint8(at + i, s.charCodeAt(i)); };
    text(0, "RIFF"); view.setUint32(4, 36 + pcm.length * 2, true); text(8, "WAVE");
    text(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
    view.setUint32(24, target, true); view.setUint32(28, target * 2, true); view.setUint16(32, 2, true);
    view.setUint16(34, 16, true); text(36, "data"); view.setUint32(40, pcm.length * 2, true);
    new Int16Array(buffer, 44).set(pcm);
    return new Blob([buffer], { type: "audio/wav" });
  }

  let seg = null;

  async function openMic() {
    if (mic.ctx) return;
    await mic.start();
    seg = new Segmenter(onSegment, (level) => { v.level = level; meter(); });
    mic.onFrame = (frame) => {
      if (!seg) return;
      if (tts.speaking || performance.now() < v.muteUntil) {
        seg.reset();
        v.level = 0;
        meter();
        return;
      }
      seg.hang = v.pttActive ? 50 : 40;
      seg.push(frame);
    };
  }

  function closeMic() {
    mic.stop();
    seg = null;
    v.level = 0;
    meter();
  }

  let meterQueued = false;
  function meter() {
    if (meterQueued) return;
    meterQueued = true;
    requestAnimationFrame(() => {
      meterQueued = false;
      const bar = $("#voice-level");
      if (bar) bar.style.transform = `scaleX(${v.level.toFixed(3)})`;
    });
  }

  function chime() {
    try {
      const c = new AudioContext();
      const o = c.createOscillator();
      const g = c.createGain();
      o.frequency.value = 880;
      g.gain.setValueAtTime(0.0001, c.currentTime);
      g.gain.exponentialRampToValueAtTime(0.12, c.currentTime + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, c.currentTime + 0.25);
      o.connect(g).connect(c.destination);
      o.start();
      o.stop(c.currentTime + 0.3);
      v.muteUntil = performance.now() + 350;
      setTimeout(() => c.close(), 600);
    } catch (e) { /* no audio output */ }
  }

  // ---------------------------------------------------------------- server
  async function transcribe(blob) {
    let res;
    try {
      res = await fetch("/api/voice/transcribe", {
        method: "POST", headers: { "X-Yousef-Client": "1", "Content-Type": "audio/wav" }, body: blob,
      });
    } catch (e) {
      throw new Error("تعذّر الاتصال بيوسف.");
    }
    let data = {};
    try { data = await res.json(); } catch (e) { /* empty */ }
    if (!res.ok) {
      const err = new Error(data.error || `خطأ ${res.status}`);
      err.status = res.status;
      throw err;
    }
    return data;
  }

  async function ensureReady() {
    let status;
    try {
      status = await Y.api("GET", "/api/voice/status");
    } catch (e) {
      Y.showHint(e.message, true);
      return false;
    }
    if (status.state === "not_installed") {
      Y.showHint("التعرّف على الكلام غير مثبّت. شغّل install-voice.bat مرة واحدة من مجلد يوسف، ثم أعد تشغيله.", true);
      return false;
    }
    if (status.state === "error") {
      Y.showHint(`تعذّر تحميل نموذج الصوت: ${status.error}`, true);
      return false;
    }
    if (status.state === "idle") {
      try { await Y.api("POST", "/api/voice/load"); } catch (e) { /* reported on first use */ }
      Y.showHint("يُحمَّل نموذج الصوت الآن؛ أول مرة قد تستغرق بضع دقائق للتنزيل.");
    } else if (status.state === "loading") {
      Y.showHint("نموذج الصوت يُحمَّل… سيعمل الميكروفون خلال لحظات.");
    }
    return true;
  }

  // ---------------------------------------------------------------- flow
  function setListen(state, ms) {
    clearTimeout(v.stateTimer);
    v.listenState = state;
    if (ms) {
      v.stateTimer = setTimeout(() => { v.listenState = "wake"; ui.update(); }, ms);
    }
    ui.update();
  }

  function showHeard(text) {
    v.heard = text;
    clearTimeout(v.heardTimer);
    v.heardTimer = setTimeout(() => { v.heard = ""; ui.update(); }, 5000);
    ui.update();
  }

  function onSegment(frames) {
    const direct = v.pttActive;
    if (direct) {
      v.pttActive = false;
      clearTimeout(v.pttTimer);
      if (!v.hands) closeMic();
    } else if (!v.hands) {
      return;
    }
    const context = direct ? "direct" : v.listenState;
    const blob = toWav(frames, mic.rate || 48000);
    v.busy += 1;
    ui.update();
    v.queue = v.queue.then(() => handle(blob, context)).catch(() => {}).finally(() => {
      v.busy -= 1;
      ui.update();
    });
  }

  async function handle(blob, context) {
    let r;
    try {
      r = await transcribe(blob);
    } catch (e) {
      Y.showHint(e.message, true);
      if (e.status === 501) stopHands();
      return;
    }
    if (!r.text) {
      if (context === "direct") Y.showHint("لم أفهم الكلام. اضغط الميكروفون وحاول مرة أخرى.", true);
      return;
    }
    showHeard(r.text);

    if (v.pending && r.reply && (context !== "wake" || r.wake)) {
      await decide(r.reply === "approve");
      return;
    }
    if (context === "direct" || context === "command") {
      const command = (r.wake ? r.command : r.text).trim();
      if (command) send(command); else armCommand();
      return;
    }
    if (r.wake) {
      if (r.command) send(r.command); else armCommand();
    }
    // anything else was not addressed to Yousef
  }

  function armCommand() {
    chime();
    if (v.hands) {
      setListen("command", 8000);
    } else {
      startPtt();
    }
  }

  async function send(command) {
    setListen("wake");
    try {
      const task = await Y.submit(command, "voice");
      if (task) mine.add(task.id);
    } catch (e) {
      Y.showHint(e.message, true);
    }
  }

  async function decide(approve) {
    const pending = v.pending;
    v.pending = null;
    setListen("wake");
    try {
      await Y.api("POST", `/api/steps/${pending.stepId}/decision`, { approve });
      Y.poll();
      tts.speak(approve ? "حسنًا، أنفّذ الخطوة." : "حسنًا، لن أنفّذ هذه الخطوة.", null, true);
    } catch (e) {
      Y.showHint(e.message, true);
    }
  }

  async function startPtt() {
    if (v.pttActive) {
      // second press: stop waiting
      v.pttActive = false;
      clearTimeout(v.pttTimer);
      if (seg) seg.reset();
      if (!v.hands) closeMic();
      ui.update();
      return;
    }
    tts.stop();
    if (!(await ensureReady())) return;
    try {
      await openMic();
    } catch (e) {
      Y.showHint(micErrorMessage(e), true);
      return;
    }
    if (seg) seg.reset();
    v.pttActive = true;
    clearTimeout(v.pttTimer);
    v.pttTimer = setTimeout(() => {
      if (v.pttActive && !(seg && seg.speaking)) {
        v.pttActive = false;
        if (!v.hands) closeMic();
        ui.update();
        Y.showHint("لم أسمع شيئًا. اضغط الميكروفون وتكلّم.", true);
      }
    }, 7000);
    ui.update();
  }

  async function toggleHands() {
    if (v.hands) { stopHands(); return; }
    if (Y.state.listenerOn) {  // Windows is already listening; two listeners would answer twice
      Y.showHint(`يوسف يستمع من ويندوز الآن. قل «${Y.state.voiceWake || "يوسف"}» ثم طلبك.`);
      return;
    }
    if (!(await ensureReady())) return;
    try {
      await openMic();
    } catch (e) {
      Y.showHint(micErrorMessage(e), true);
      return;
    }
    v.hands = true;
    setListen("wake");
    const word = (Y.state.voiceWake || "يوسف");
    Y.showHint(`أنا أستمع. قل «${word}» ثم طلبك.`);
  }

  function stopHands() {
    v.hands = false;
    clearTimeout(v.stateTimer);
    v.listenState = "wake";
    if (!v.pttActive) closeMic();
    ui.update();
  }

  // Called by the dashboard whenever a task changes.
  // voice tasks this page started; the others (Windows listener, phone) are read aloud by whoever started them
  const mine = new Set();

  function onTask(task, prev) {
    if (task.source !== "voice") return;
    if (Y.state.listenerOn && !mine.has(task.id)) return;
    const before = prev ? prev.status : null;
    if (task.status === "waiting") {
      const step = (task.steps || []).find((s) => s.status === "awaiting_approval");
      if (step && (!v.pending || v.pending.stepId !== step.id)) {
        v.pending = { stepId: step.id, taskId: task.id };
        tts.speak(`هذه الخطوة تحتاج موافقتك: ${step.instruction}. قل وافق أو ارفض.`, () => {
          if (v.hands && v.pending && v.pending.stepId === step.id) setListen("approval", 20000);
        }, true);
      }
    } else if (v.pending && v.pending.taskId === task.id) {
      v.pending = null;
      if (v.listenState === "approval") setListen("wake");
    }
    if (before && before !== task.status) {
      if (task.status === "done") tts.speak(task.final || "");
      else if (task.status === "failed") tts.speak(`تعثّرت المهمة. ${task.error || ""}`);
    }
  }

  // ---------------------------------------------------------------- UI
  const ui = {
    update() {
      const bar = $("#voicebar");
      if (!bar) return;
      let label = "";
      let state = "";
      if (tts.speaking) { label = "يتحدّث…"; state = "speaking"; }
      else if (v.busy) { label = "أحوّل كلامك إلى نص…"; state = "busy"; }
      else if (v.pttActive) { label = "أسمعك… تكلّم الآن"; state = "listening"; }
      else if (v.hands) {
        label = { wake: `يستمع لكلمة «${Y.state.voiceWake || "يوسف"}»`, command: "نعم؟ قل طلبك…", approval: "قل: وافق أو ارفض" }[v.listenState];
        state = v.listenState === "wake" ? "waiting" : "listening";
      }
      bar.hidden = !label;
      bar.dataset.state = state;
      $("#voice-text").textContent = label;
      $("#voice-heard").textContent = v.heard ? `سمعت: «${v.heard}»` : "";
      const micBtn = $("#mic");
      micBtn.classList.toggle("is-live", v.pttActive);
      micBtn.setAttribute("aria-pressed", String(v.pttActive));
      const hands = $("#btn-hands");
      hands.classList.toggle("is-on", v.hands);
      hands.setAttribute("aria-pressed", String(v.hands));
      hands.textContent = v.hands ? "يستمع الآن" : "الاستماع الدائم";
      $("#voice-stop-speech").hidden = !tts.speaking;
      $("#voice-stop-listen").hidden = !v.hands;
    },
  };

  // ---------------------------------------------------------------- settings panel
  const MODEL_LABEL = {
    tiny: "tiny — الأسرع، دقة ضعيفة",
    base: "base — سريع، دقة مقبولة",
    small: "small — سريع، ضعيف في العربية",
    medium: "medium — أدق من small، أبطأ",
    "large-v3-turbo": "large-v3-turbo — الأدق للعربية (موصى به)",
  };
  const STATE_TEXT = {
    not_installed: "التعرّف على الكلام غير مثبّت. شغّل install-voice.bat مرة واحدة من مجلد يوسف، ثم أعد تشغيله.",
    idle: "النموذج غير محمّل بعد.",
    loading: "يُحمَّل النموذج… أول مرة يُنزَّل من الإنترنت ثم يعمل بدونه.",
    ready: "جاهز للاستماع.",
    error: "تعذّر تحميل النموذج.",
  };
  let statusTimer = null;

  async function refreshVoiceStatus() {
    clearTimeout(statusTimer);
    let s;
    try { s = await Y.api("GET", "/api/voice/status"); } catch (e) { return; }
    const note = $("#voice-state");
    note.textContent = STATE_TEXT[s.state] + (s.state === "error" && s.error ? ` ${s.error}` : "")
      + (s.state === "ready" && s.device_used ? (s.device_used === "cuda" ? " (على كرت الشاشة)" : " (على المعالج)") : "")
      + (s.note ? ` ${s.note}` : "");
    note.classList.toggle("is-error", s.state === "error");
    $("#btn-voice-load").hidden = !["idle", "error"].includes(s.state);
    if (s.state === "loading" && !$("#drawer").hidden) statusTimer = setTimeout(refreshVoiceStatus, 1500);
    return s;
  }

  function fillVoices() {
    const voices = tts.voices();
    const online = store.get("ttsOnline", false);
    for (const [id, lang, key] of [["#tts-ar", "ar", "ttsAr"], ["#tts-en", "en", "ttsEn"]]) {
      const sel = $(id);
      const wanted = store.get(key, "");
      const list = usableVoices(voices, lang);
      sel.replaceChildren();
      const auto = document.createElement("option");
      auto.value = "";
      auto.textContent = list.length ? "تلقائي" : "لا يوجد صوت متاح";
      sel.appendChild(auto);
      for (const x of list) {
        const o = document.createElement("option");
        o.value = x.name;
        o.textContent = `${x.name}${x.localService ? "" : " (عبر الإنترنت)"}`;
        o.selected = x.name === wanted;
        sel.appendChild(o);
      }
    }
    const hasAr = voices.some((x) => (x.lang || "").toLowerCase().startsWith("ar") && (online || x.localService));
    $("#tts-note").textContent = !tts.supported
      ? "هذا المتصفح لا يدعم قراءة النص بصوت."
      : hasAr ? (online ? "" : "إن كان الصوت العربي آليًا وغير واضح، فعّل «الأصوات عبر الإنترنت» وافتح يوسف في Microsoft Edge: فيه أصوات عربية طبيعية مجانية، منها العُمانية.")
        : "لا يوجد صوت عربي هنا. الأوضح: افتح يوسف في Microsoft Edge وفعّل «الأصوات عبر الإنترنت» (أصوات طبيعية، منها العُمانية). أو أضف صوتًا عربيًا من ويندوز: الإعدادات، ثم الوقت واللغة، ثم الكلام.";
  }

  async function renderSettings() {
    const voice = (Y.state.settings || {}).voice;
    if (!voice) return;
    const s = await refreshVoiceStatus();
    const sizes = (s && s.model_sizes) || {};
    const model = $("#voice-model");
    model.replaceChildren();
    for (const key of Object.keys(MODEL_LABEL)) {
      const o = document.createElement("option");
      o.value = key;
      o.textContent = sizes[key] ? `${MODEL_LABEL[key]} (~${sizes[key]}MB)` : MODEL_LABEL[key];
      o.selected = key === voice.model;
      model.appendChild(o);
    }
    $("#voice-device").value = voice.device;
    $("#voice-language").value = voice.language;
    modelNote();
    $("#voice-wake").value = voice.wake_words.join("، ");
    $("#voice-preload").checked = voice.preload;
    $("#voice-status").textContent = "";
    renderListener(s && s.listener, voice);
    $("#tts-on").checked = store.get("ttsOn", true);
    $("#tts-online").checked = store.get("ttsOnline", false);
    fillVoices();
  }

  const LISTEN_MODE = { wake: "ينتظر كلمة التنبيه", command: "ينتظر طلبك", approval: "ينتظر «وافق» أو «ارفض»" };

  function renderListener(l, voice) {
    if (!l) return;
    Y.state.listenerOn = l.running;
    $("#listener-on").checked = l.running;
    $("#listener-speak").checked = voice.speak !== false;
    const line = $("#listener-status");
    line.classList.toggle("is-error", Boolean(l.error) || (!l.mic && !l.running));
    if (l.error) line.textContent = l.error;
    else if (l.running) {
      line.textContent = `يستمع الآن: ${LISTEN_MODE[l.mode] || ""}${l.heard ? ` — آخر ما سمعه: «${l.heard}»` : ""}`
        + (l.arabic_voice ? "" : ". لا يوجد صوت عربي في ويندوز، فالردود تظهر في اللوحة.");
    } else line.textContent = l.mic ? "" : "يحتاج تثبيت الصوت مرة أخرى (install-voice.bat) ليصل إلى الميكروفون.";
  }

  async function toggleListener(e) {
    const line = $("#listener-status");
    try {
      const s = await Y.api("PUT", "/api/voice/listener", { enabled: e.target.checked });
      renderListener(s.listener, (Y.state.settings || {}).voice || {});
      if (s.listener.running && v.hands) stopHands();  // one listener is enough
    } catch (err) {
      e.target.checked = false;
      line.textContent = err.message;
      line.classList.add("is-error");
    }
  }

  async function toggleSpeak(e) {
    try {
      const data = await Y.api("PUT", "/api/settings", { voice: { speak: e.target.checked } });
      Y.state.settings = data.settings;
    } catch (err) {
      e.target.checked = !e.target.checked;
      Y.showHint(err.message, true);
    }
  }

  function modelNote() {
    const weak = ["tiny", "base", "small"].includes($("#voice-model").value);
    $("#voice-model-note").hidden = !(weak && $("#voice-language").value !== "en");
  }

  async function saveVoice() {
    const line = $("#voice-status");
    line.classList.remove("is-error");
    try {
      const data = await Y.api("PUT", "/api/settings", {
        voice: {
          model: $("#voice-model").value,
          device: $("#voice-device").value,
          language: $("#voice-language").value,
          wake_words: $("#voice-wake").value,
          preload: $("#voice-preload").checked,
        },
      });
      Y.state.settings = data.settings;
      Y.state.voiceWake = data.settings.voice.wake_words[0];
      $("#voice-wake").value = data.settings.voice.wake_words.join("، ");
      line.textContent = "حُفظت إعدادات الصوت.";
      const s = await refreshVoiceStatus();
      if (s && s.state === "idle" && s.installed) {
        await Y.api("POST", "/api/voice/load");
        refreshVoiceStatus();
      }
    } catch (e) {
      line.textContent = e.message;
      line.classList.add("is-error");
    }
  }

  // ---------------------------------------------------------------- boot
  document.addEventListener("DOMContentLoaded", () => {
    $("#mic").addEventListener("click", startPtt);
    $("#btn-hands").addEventListener("click", toggleHands);
    $("#voice-stop-speech").addEventListener("click", () => tts.stop());
    $("#voice-stop-listen").addEventListener("click", stopHands);
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && tts.speaking) tts.stop(); });

    $("#btn-save-voice").addEventListener("click", saveVoice);
    $("#voice-model").addEventListener("change", modelNote);
    $("#voice-language").addEventListener("change", modelNote);
    $("#btn-voice-load").addEventListener("click", async () => {
      try { await Y.api("POST", "/api/voice/load"); } catch (e) { Y.showHint(e.message, true); }
      refreshVoiceStatus();
    });
    $("#tts-on").addEventListener("change", (e) => store.set("ttsOn", e.target.checked));
    $("#tts-online").addEventListener("change", (e) => { store.set("ttsOnline", e.target.checked); fillVoices(); });
    $("#tts-ar").addEventListener("change", (e) => store.set("ttsAr", e.target.value));
    $("#tts-en").addEventListener("change", (e) => store.set("ttsEn", e.target.value));
    $("#listener-on").addEventListener("change", toggleListener);
    $("#listener-speak").addEventListener("change", toggleSpeak);
    $("#btn-tts-test").addEventListener("click", () => {
      const ar = tts.pick("ar");
      tts.speak(ar ? "مرحبًا، أنا يوسف. هكذا سأقرأ ردودي عليك." : "Hello, I am Yousef. This is how I will read my replies.", null, true);
    });
    if (tts.supported) speechSynthesis.onvoiceschanged = () => { if (!$("#drawer").hidden) fillVoices(); };

    Y.api("GET", "/api/voice/status").then((st) => {
      Y.state.voiceWake = st.wake_words[0];
      Y.state.listenerOn = Boolean(st.listener && st.listener.running);
      ui.update();
    }).catch(() => {});
    Y.taskHooks.push(onTask);
    Y.drawerHooks.push(renderSettings);
    window.YousefVoice = { tts, speakable, toWav, Segmenter, state: v };
    ui.update();
  });
})();
