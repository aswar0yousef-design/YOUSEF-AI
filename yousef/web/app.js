"use strict";

const PROVIDER_LABEL = { ollama: "محلي", anthropic: "Claude", openai: "OpenAI", gemini: "Gemini" };
const CLOUD = [["anthropic", "Claude (Anthropic)"], ["openai", "OpenAI"], ["gemini", "Gemini (Google)"]];
const TASK_TYPES = [
  ["chat", "المحادثة والأسئلة"], ["plan", "تخطيط الخطوات"], ["code", "البرمجة"],
  ["analysis", "التحليل"], ["writing", "الكتابة والمحتوى"],
];
const ROUTES = [["local", "محلي أولًا"], ["cloud", "سحابي أولًا"], ["local_only", "محلي فقط"]];
const TASK_STATE = {
  planning: "يخطّط للخطوات…", running: "يعمل…", waiting: "ينتظر موافقتك",
  failed: "تعثّرت المهمة", cancelled: "أُلغيت المهمة", interrupted: "توقّفت المهمة عند إغلاق البرنامج",
};
const STEP_STATE = {
  pending: "في الانتظار", running: "يعمل الآن", awaiting_approval: "ينتظر موافقتك",
  done: "تم", failed: "تعثّر", skipped: "تُخطّيت",
};
const LESSON_KIND = { rule: "قاعدة", success: "أفادت", failure: "لم تُفد" };
const TOOL_LABEL = {
  list_files: "عرض مجلد", read_file: "قراءة", search: "بحث", replace: "تعديل مقترح", write_file: "ملف مقترح",
  git_status: "حالة git", git_diff: "فروقات git", run: "تشغيل أمر", vercel_status: "حالة Vercel",
};
const CHANGE_STATUS = { pending: "بانتظار موافقتك", applied: "طُبّق", rejected: "مرفوض", conflict: "تعارض", error: "خطأ" };
const ACTION_STATUS = { pending: "بانتظار موافقتك", done: "تم", rejected: "مرفوض", failed: "تعذّر", skipped: "لم يُنفَّذ" };
const SERVICES = [
  ["github", "GitHub", "لفتح Pull Requests. الرفع (push) يستخدم git المثبّت عندك وصلاحياته."],
  ["vercel", "Vercel", "للقراءة فقط: حالة النشر وأخطاء البناء. النشر نفسه يحدث عند الرفع إلى GitHub."],
];
const ACTIVE = new Set(["planning", "running", "waiting"]);

const $ = (sel, root = document) => root.querySelector(sel);
const state = {
  tasks: new Map(), open: new Set(), status: null, settings: null, keys: null, agents: [], pollTimer: null, workspaces: [],
};

const store = {
  get(key, fallback) {
    try { const v = localStorage.getItem(`yousef.${key}`); return v === null ? fallback : JSON.parse(v); } catch (e) { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(`yousef.${key}`, JSON.stringify(value)); } catch (e) { /* storage blocked */ }
  },
};

const els = {};

// ---------------------------------------------------------------- helpers
async function api(method, path, body) {
  const opts = { method, headers: { "X-Yousef-Client": "1" } };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, opts);
  } catch (e) {
    throw new Error("تعذّر الاتصال بيوسف. تأكد أن نافذة التشغيل ما زالت مفتوحة.");
  }
  let data = {};
  try { data = await res.json(); } catch (e) { /* empty body */ }
  if (!res.ok) throw new Error(data.error || `خطأ ${res.status}`);
  return data;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function inline(s) {
  return esc(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
}

function mdText(text) {
  let html = "", para = [], list = null, table = null;
  const flushPara = () => {
    if (para.length) { html += `<p dir="auto">${para.map(inline).join("<br>")}</p>`; para = []; }
  };
  const flushList = () => {
    if (list) {
      html += `<${list.tag} dir="auto">${list.items.map((i) => `<li>${inline(i)}</li>`).join("")}</${list.tag}>`;
      list = null;
    }
  };
  const flushTable = () => {
    if (table) {
      const [head, ...rows] = table;
      html += `<table dir="auto"><thead><tr>${head.map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead>`
        + `<tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
      table = null;
    }
  };
  const flushAll = () => { flushPara(); flushList(); flushTable(); };

  for (const raw of text.split("\n")) {
    const t = raw.trim();
    let m;
    if (!t) { flushAll(); continue; }
    if (t.length > 1 && t.startsWith("|") && t.endsWith("|")) {
      flushPara(); flushList();
      const cells = t.slice(1, -1).split("|").map((c) => c.trim());
      if (cells.every((c) => /^:?-{2,}:?$/.test(c))) continue;
      (table ||= []).push(cells);
      continue;
    }
    flushTable();
    if ((m = t.match(/^(#{1,6})\s+(.*)$/))) {
      flushPara(); flushList();
      const tag = m[1].length <= 2 ? "h3" : "h4";
      html += `<${tag} dir="auto">${inline(m[2])}</${tag}>`;
      continue;
    }
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(t)) { flushAll(); continue; }
    if ((m = t.match(/^[-*•]\s+(.*)$/))) {
      flushPara();
      if (!list || list.tag !== "ul") { flushList(); list = { tag: "ul", items: [] }; }
      list.items.push(m[1]);
      continue;
    }
    if ((m = t.match(/^[\d٠-٩]+[.)]\s+(.*)$/))) {
      flushPara();
      if (!list || list.tag !== "ol") { flushList(); list = { tag: "ol", items: [] }; }
      list.items.push(m[1]);
      continue;
    }
    flushList();
    para.push(t);
  }
  flushAll();
  return html;
}

function md(src) {
  return String(src ?? "").split("```").map((part, i) => {
    if (i % 2 === 0) return mdText(part);
    let lang = "", code = part;
    const nl = part.indexOf("\n");
    if (nl > -1 && /^[\w+#.-]*$/.test(part.slice(0, nl).trim())) {
      lang = part.slice(0, nl).trim();
      code = part.slice(nl + 1);
    }
    return `<div class="code"><div class="code-head"><span>${esc(lang || "code")}</span>`
      + `<button type="button" class="copy">انسخ</button></div>`
      + `<pre dir="ltr"><code>${esc(code.replace(/\n$/, ""))}</code></pre></div>`;
  }).join("");
}

async function copyText(text, btn) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (e) {
    const area = document.createElement("textarea");
    area.value = text;
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    area.remove();
  }
  if (btn) {
    const old = btn.textContent;
    btn.textContent = "نُسخ";
    setTimeout(() => { btn.textContent = old; }, 1400);
  }
}

function showHint(message, isError = false) {
  els.hint.textContent = message;
  els.hint.classList.toggle("is-error", isError);
  clearTimeout(showHint.timer);
  showHint.timer = setTimeout(() => {
    els.hint.textContent = "للإرسال اضغط Enter، ولسطر جديد Shift+Enter.";
    els.hint.classList.remove("is-error");
  }, 6000);
}

function agentName(key) {
  const agent = state.agents.find((a) => a.key === key);
  return agent ? agent.name : key;
}

function nearBottom() {
  return window.innerHeight + window.scrollY >= document.body.scrollHeight - 160;
}

// ---------------------------------------------------------------- tasks
function stepHTML(step, task, hideOutput) {
  const tag = step.provider
    ? `<span class="tag ${step.provider === "ollama" ? "" : "cloud"}">${esc(PROVIDER_LABEL[step.provider] || step.provider)} <bdi dir="ltr">${esc(step.model || "")}</bdi></span>`
    : "";
  let h = `<li class="stop is-${esc(step.status)}"><div class="stop-head"><span class="agent">${esc(agentName(step.agent))}</span>`
    + `${tag}<span class="stop-state">${STEP_STATE[step.status] || ""}</span></div>`;
  if (step.instruction.trim() !== task.request.trim()) {
    h += `<p class="instr" dir="auto">${esc(step.instruction)}</p>`;
  }
  const trace = step.trace || [];
  if (trace.length) {
    h += `<details class="trace"><summary>ما فعله ${esc(agentName(step.agent))}: ${trace.length} ${trace.length > 10 ? "أداة" : "أدوات"}</summary><ul>`
      + trace.map((t) => `<li class="${t.ok ? "" : "bad"}"><span>${esc(TOOL_LABEL[t.tool] || t.tool)}</span> <bdi dir="ltr">${esc(t.target || "")}</bdi>${t.ok ? "" : ` <em>${esc(t.note || "تعذّر")}</em>`}</li>`).join("")
      + "</ul></details>";
  }
  const changes = step.changes || [];
  const actions = step.actions || [];
  const pendingChanges = changes.filter((c) => c.status === "pending").length;
  const pendingActions = actions.filter((a) => a.status === "pending");
  if ((pendingChanges || pendingActions.length) && step.output) {
    // the agent's explanation comes before the diffs it proposes
    h += `<div class="md explain">${md(step.output)}</div>`;
  }
  if (changes.length) h += changesHTML(changes);
  if (actions.length) h += actionsHTML(actions);
  if (step.status === "awaiting_approval" && (pendingChanges || pendingActions.length)) {
    const parts = [];
    if (pendingChanges) parts.push(`تعديل ${countFiles(pendingChanges)}`);
    const names = { git_commit: "commit", git_push: "push", open_pr: "Pull Request" };
    if (pendingActions.length) parts.push(pendingActions.map((a) => names[a.kind] || a.kind).join(" ثم "));
    h += `<div class="gate"><p>يقترح ${esc(agentName(step.agent))}: ${parts.join("، ثم ")}. راجعها، ثم قرّر. لن يُنفَّذ شيء قبل موافقتك.</p>`
      + `<button type="button" class="primary" data-act="approve" data-step="${step.id}">${pendingActions.length ? "طبّق الكل" : "طبّق التعديلات"}</button>`
      + (pendingChanges && pendingActions.length ? `<button type="button" class="ghost" data-act="approve-files" data-step="${step.id}">الملفات فقط</button>` : "")
      + `<button type="button" class="ghost danger" data-act="reject" data-step="${step.id}">ارفض</button></div>`;
  } else if (step.status === "awaiting_approval") {
    h += `<div class="gate"><p>هذه الخطوة فيها إجراء خارج المحادثة (نشر، إرسال، دفع، حذف…)، لذلك تنتظر موافقتك.</p>`
      + `<button type="button" class="primary" data-act="approve" data-step="${step.id}">وافق ونفّذ</button>`
      + `<button type="button" class="ghost danger" data-act="reject" data-step="${step.id}">ارفض الخطوة</button></div>`;
  }
  if (step.output && !hideOutput && !pendingChanges) {
    const open = state.open.has(String(step.id)) ? " open" : "";
    h += `<details class="out" data-step="${step.id}"${open}><summary>نتيجة هذه الخطوة</summary><div class="md">${md(step.output)}</div></details>`;
  }
  return h + "</li>";
}

function diffHTML(diff) {
  return diff.split("\n").map((line) => {
    let cls = "ctx";
    if (line.startsWith("+++") || line.startsWith("---")) cls = "meta";
    else if (line.startsWith("@@")) cls = "hunk";
    else if (line.startsWith("+")) cls = "add";
    else if (line.startsWith("-")) cls = "del";
    return `<span class="${cls}">${esc(line) || " "}</span>`;
  }).join("");
}

function countFiles(n) {
  if (n === 1) return "ملف واحد";
  if (n === 2) return "ملفين";
  return n <= 10 ? `${n} ملفات` : `${n} ملفًا`;
}

function changesHTML(changes) {
  // pending diffs start open when they are short enough to read in place
  const lines = changes.reduce((n, c) => n + (c.added || 0) + (c.removed || 0), 0);
  return `<div class="changes">${changes.map((c) => {
    const open = state.open.has(`c${c.id}`) || (c.status === "pending" && lines <= 300) ? " open" : "";
    return `<details class="change" data-change="${c.id}"${open}><summary>`
      + `<bdi dir="ltr" class="path">${esc(c.path)}</bdi>${c.new_file ? ` <span class="new">ملف جديد</span>` : ""}`
      + ` <bdi dir="ltr" class="counts"><span class="plus">+${c.added}</span> <span class="minus">−${c.removed}</span></bdi>`
      + ` <span class="cstatus is-${esc(c.status)}">${CHANGE_STATUS[c.status] || c.status}</span>`
      + `${c.note ? ` <span class="cnote">${esc(c.note)}</span>` : ""}</summary>`
      + `<pre class="diff" dir="ltr">${diffHTML(c.diff || "")}</pre></details>`;
  }).join("")}</div>`;
}

function actionLabel(a) {
  const p = a.payload || {};
  // message and branch are isolated so English text does not scramble the Arabic around it
  if (a.kind === "git_commit") {
    return `commit: <bdi>${esc(p.message || "")}</bdi>${p.branch ? `، على فرع <bdi dir="ltr">${esc(p.branch)}</bdi>` : ""}${p.all ? "، لكل التغييرات في المجلد" : ""}`;
  }
  if (a.kind === "git_push") return "push إلى origin";
  if (a.kind === "open_pr") return `Pull Request: <bdi>${esc(p.title || "")}</bdi>`;
  return esc(a.kind);
}

function actionsHTML(actions) {
  return `<ul class="actions">${actions.map((a) => {
    let result = "";
    if (a.result) {
      result = /^https:\/\/\S+$/.test(a.result)
        ? ` <a href="${esc(a.result)}" target="_blank" rel="noopener noreferrer">${esc(a.result)}</a>`
        : ` <span class="cnote" dir="auto">${esc(a.result)}</span>`;
    }
    return `<li><span class="akind">${actionLabel(a)}</span> <span class="cstatus is-${esc(a.status)}">${ACTION_STATUS[a.status] || esc(a.status)}</span>${result}</li>`;
  }).join("")}</ul>`;
}

function footHTML(t) {
  const parts = [];
  if (ACTIVE.has(t.status)) parts.push(`<button type="button" class="ghost" data-act="cancel" data-task="${t.id}">ألغِ المهمة</button>`);
  if (["failed", "cancelled", "interrupted"].includes(t.status)) {
    parts.push(`<button type="button" class="ghost" data-act="retry" data-task="${t.id}">أعد المحاولة</button>`);
  }
  if (t.status === "done") parts.push(`<button type="button" class="ghost" data-act="copy-answer" data-task="${t.id}">انسخ الإجابة</button>`);
  if (t.status === "done") {
    parts.push(`<span class="spacer"></span>`);
    if (t.feedback) {
      parts.push(`<span class="saved">${t.feedback > 0 ? "قيّمتها بأنها أفادت" : "قيّمتها بأنها لم تُفد"}، وسيتذكّر يوسف ذلك في الطلبات المشابهة.</span>`);
    } else {
      parts.push(`<span>هل أفادتك؟</span>`
        + `<button type="button" class="ghost" data-act="good" data-task="${t.id}">أفادت</button>`
        + `<button type="button" class="ghost" data-act="bad" data-task="${t.id}">لم تُفد</button>`);
    }
  }
  return parts.length ? `<div class="task-foot">${parts.join("")}</div>` : "";
}

function taskHTML(t) {
  const steps = t.steps || [];
  const singleDone = steps.length === 1 && t.status === "done";
  let h = `<h2 class="ask" dir="auto">${esc(t.request)}</h2>`;
  const ctx = t.context || {};
  const meta = [];
  if (t.source === "vscode") meta.push("من VS Code");
  if (t.workspace) meta.push(`المشروع <bdi>${esc(t.workspace)}</bdi>`);
  if (ctx.file) meta.push(`الملف <bdi dir="ltr">${esc(ctx.file)}</bdi>${ctx.start_line ? ` (الأسطر ${ctx.start_line}–${ctx.end_line})` : ""}`);
  if (meta.length) h += `<p class="task-meta">${meta.join("، ")}</p>`;
  if (TASK_STATE[t.status]) {
    h += `<p class="task-state${t.status === "waiting" ? " is-waiting" : ""}" role="status">${TASK_STATE[t.status]}</p>`;
  }
  if (steps.length) h += `<ol class="route">${steps.map((s) => stepHTML(s, t, singleDone)).join("")}</ol>`;
  if (t.error && t.status !== "done") h += `<div class="error-box"><p dir="auto">${esc(t.error)}</p></div>`;
  if (t.status === "done" && t.final) h += `<div class="answer md">${md(t.final)}</div>`;
  return h + footHTML(t);
}

function renderTask(task) {
  state.tasks.set(task.id, task);
  let el = document.getElementById(`task-${task.id}`);
  if (!el) {
    el = document.createElement("article");
    el.className = "task";
    el.id = `task-${task.id}`;
    els.tasks.appendChild(el);
  }
  const sig = JSON.stringify([task.status, task.final, task.error, task.feedback,
    (task.steps || []).map((s) => [s.status, s.output, s.model, (s.trace || []).length,
      (s.changes || []).map((c) => c.status), (s.actions || []).map((a) => a.status)])]);
  if (el.dataset.sig === sig) return el;
  el.querySelectorAll("details[data-step]").forEach((d) => {
    if (d.open) state.open.add(d.dataset.step); else state.open.delete(d.dataset.step);
  });
  el.querySelectorAll("details[data-change]").forEach((d) => {
    if (d.open) state.open.add(`c${d.dataset.change}`); else state.open.delete(`c${d.dataset.change}`);
  });
  el.dataset.sig = sig;
  el.innerHTML = taskHTML(task);
  els.empty.hidden = true;
  return el;
}

function schedulePoll() {
  if (!state.pollTimer) state.pollTimer = setTimeout(poll, 900);
}

function notifyTask(task, prev) {
  for (const hook of window.Yousef.taskHooks) {
    try { hook(task, prev); } catch (e) { console.error(e); }
  }
}

async function poll() {
  state.pollTimer = null;
  const active = [...state.tasks.values()].filter((t) => ACTIVE.has(t.status));
  for (const t of active) {
    try {
      const stick = nearBottom();
      const { task } = await api("GET", `/api/tasks/${t.id}`);
      const prev = state.tasks.get(task.id);
      renderTask(task);
      notifyTask(task, prev);
      if (stick) window.scrollTo({ top: document.body.scrollHeight });
    } catch (e) {
      showHint(e.message, true);
    }
  }
  if ([...state.tasks.values()].some((t) => ACTIVE.has(t.status))) schedulePoll();
}

async function submit(text, source = "text") {
  text = text.trim();
  if (!text) { showHint("اكتب طلبك أولًا.", true); els.input.focus(); return null; }
  els.send.disabled = true;
  try {
    const workspace = $("#workspace").value || null;
    const { task } = await api("POST", "/api/tasks", { request: text, source, workspace });
    if (source === "text") {
      els.input.value = "";
      autosize();
    }
    const el = renderTask(task);
    notifyTask(task, null);
    el.scrollIntoView({ block: "start" });
    schedulePoll();
    return task;
  } catch (e) {
    showHint(e.message, true);
    return null;
  } finally {
    els.send.disabled = false;
  }
}

async function loadTasks() {
  const { tasks } = await api("GET", "/api/tasks?limit=30");
  els.tasks.innerHTML = "";
  state.tasks.clear();
  tasks.forEach(renderTask);
  els.empty.hidden = tasks.length > 0;
  if (tasks.some((t) => ACTIVE.has(t.status))) schedulePoll();
  window.scrollTo({ top: document.body.scrollHeight });
}

async function onTaskClick(e) {
  const btn = e.target.closest("button");
  if (!btn) return;
  if (btn.classList.contains("copy")) {
    copyText(btn.closest(".code").querySelector("pre").innerText, btn);
    return;
  }
  const act = btn.dataset.act;
  const taskId = Number(btn.dataset.task);
  const task = state.tasks.get(taskId);
  try {
    if (act === "approve" || act === "reject" || act === "approve-files") {
      btn.disabled = true;
      await api("POST", `/api/steps/${btn.dataset.step}/decision`, {
        approve: act !== "reject", scope: act === "approve-files" ? "files" : "all",
      });
      schedulePoll();
    } else if (act === "cancel") {
      await api("POST", `/api/tasks/${taskId}/cancel`);
      schedulePoll();
    } else if (act === "retry" && task) {
      await submit(task.request);
    } else if (act === "copy-answer" && task) {
      copyText(task.final || "", btn);
    } else if (act === "good") {
      await api("POST", `/api/tasks/${taskId}/feedback`, { good: true });
      renderTask((await api("GET", `/api/tasks/${taskId}`)).task);
    } else if (act === "bad") {
      const foot = btn.closest(".task-foot");
      if (!foot.querySelector(".feedback-note")) {
        const box = document.createElement("div");
        box.className = "feedback-note";
        box.innerHTML = `<label class="sr" for="note-${taskId}">ما الذي كان يجب أن يختلف؟</label>`
          + `<input id="note-${taskId}" maxlength="1000" placeholder="ما الذي كان يجب أن يختلف؟ (اختياري)">`
          + `<button type="button" class="primary" data-act="bad-save" data-task="${taskId}">احفظ التقييم</button>`;
        foot.appendChild(box);
        box.querySelector("input").focus();
      }
    } else if (act === "bad-save") {
      const note = btn.parentElement.querySelector("input").value;
      await api("POST", `/api/tasks/${taskId}/feedback`, { good: false, note });
      renderTask((await api("GET", `/api/tasks/${taskId}`)).task);
    }
  } catch (err) {
    btn.disabled = false;
    showHint(err.message, true);
  }
}

// ---------------------------------------------------------------- status
function setEngine(el, cls, text) {
  el.classList.remove("is-ok", "is-warn");
  if (cls) el.classList.add(cls);
  el.querySelector(".label").textContent = text;
}

function readyCloud(status) {
  return Object.entries(status.cloud).filter(([, c]) => c.has_key && c.model).map(([p]) => PROVIDER_LABEL[p]);
}

function renderStatus() {
  const s = state.status;
  const o = s.ollama;
  const localOk = o.reachable && o.active_model;
  if (localOk) setEngine(els.engineLocal, "is-ok", `محلي: ${o.active_model}`);
  else if (o.reachable) setEngine(els.engineLocal, "is-warn", "Ollama يعمل بلا نماذج");
  else setEngine(els.engineLocal, "is-warn", "Ollama غير متصل");

  const cloud = readyCloud(s);
  if (s.mode === "local_only") setEngine(els.engineCloud, "", "وضع الخصوصية: محلي فقط");
  else if (cloud.length) setEngine(els.engineCloud, "is-ok", `السحابة: ${cloud.join("، ")}`);
  else setEngine(els.engineCloud, "", "السحابة غير مضافة");

  const usable = localOk || (s.mode !== "local_only" && cloud.length > 0);
  els.setupNote.hidden = usable;
}

async function loadStatus(refresh = false) {
  try {
    state.status = await api("GET", `/api/status${refresh ? "?refresh=1" : ""}`);
    renderStatus();
  } catch (e) {
    setEngine(els.engineLocal, "is-warn", "يوسف غير متصل");
    setEngine(els.engineCloud, "", "");
  }
}

// ---------------------------------------------------------------- settings
function option(value, label, selected) {
  const o = document.createElement("option");
  o.value = value;
  o.textContent = label;
  o.selected = selected;
  return o;
}

function fillLocalModels(models) {
  const sel = $("#local-model");
  const current = state.settings.local_model;
  sel.replaceChildren(option("", "تلقائي: أول نموذج مثبّت", current === ""));
  models.forEach((m) => sel.appendChild(option(m, m, m === current)));
  if (current && !models.includes(current)) sel.appendChild(option(current, `${current} (غير مثبّت)`, true));
}

function renderOllamaNote() {
  const o = state.status.ollama;
  $("#ollama-note").textContent = o.reachable
    ? (o.models.length ? `Ollama يعمل، وفيه ${o.models.length} نموذج جاهز.` : "Ollama يعمل لكن لا يوجد نموذج. ثبّت واحدًا من الطرفية، مثل: ollama pull qwen2.5:7b")
    : "Ollama غير متصل. ثبّته من ollama.com وشغّله، ثم ثبّت نموذجًا مثل: ollama pull qwen2.5:7b";
}

function renderProviders() {
  const box = $("#providers");
  box.replaceChildren();
  for (const [p, label] of CLOUD) {
    const has = state.keys[p];
    const div = document.createElement("div");
    div.className = "provider";
    div.dataset.provider = p;
    div.innerHTML = `<div class="provider-head"><strong>${label}</strong>`
      + `<span class="key-state${has ? " has-key" : ""}">${has ? "المفتاح محفوظ على هذا الجهاز" : "لا يوجد مفتاح"}</span></div>`
      + `<label for="key-${p}">مفتاح API</label>`
      + `<div class="row"><input id="key-${p}" type="password" dir="ltr" autocomplete="off" spellcheck="false" placeholder="${has ? "أدخل مفتاحًا جديدًا لاستبداله" : "الصق المفتاح هنا"}">`
      + `<button type="button" class="ghost" data-act="save-key">احفظ</button>`
      + (has ? `<button type="button" class="ghost danger" data-act="delete-key">احذف</button>` : "")
      + `</div><label for="model-${p}">النموذج</label>`
      + `<div class="row"><input id="model-${p}" list="list-${p}" dir="ltr" spellcheck="false" placeholder="اكتب اسم النموذج أو اجلب القائمة">`
      + `<button type="button" class="ghost" data-act="fetch-models"${has ? "" : " disabled"}>اجلب النماذج</button></div>`
      + `<datalist id="list-${p}"></datalist><p class="status-line" role="status"></p>`;
    div.querySelector(`#model-${p}`).value = state.settings.cloud_models[p] || "";
    box.appendChild(div);
  }
  $("#keys-note").textContent = state.status.keys_encrypted
    ? "تُشفَّر المفاتيح بحساب ويندوز الخاص بك ولا تغادر جهازك، ولا تظهر هنا بعد حفظها."
    : "على هذا النظام تُحفظ المفاتيح بترميز بسيط فقط؛ التشفير الكامل متاح على ويندوز.";
}

async function onProviderClick(e) {
  const btn = e.target.closest("button");
  if (!btn) return;
  const box = btn.closest(".provider");
  const p = box.dataset.provider;
  const line = box.querySelector(".status-line");
  line.classList.remove("is-error");
  try {
    if (btn.dataset.act === "save-key") {
      const input = box.querySelector(`#key-${p}`);
      if (!input.value.trim()) throw new Error("الصق المفتاح أولًا.");
      state.keys = (await api("PUT", `/api/keys/${p}`, { key: input.value })).keys;
      input.value = "";
      renderProviders();
      const fresh = $(`.provider[data-provider="${p}"] .status-line`);
      fresh.textContent = "حُفظ المفتاح. اختر النموذج الآن.";
      loadStatus();
    } else if (btn.dataset.act === "delete-key") {
      const label = CLOUD.find(([k]) => k === p)[1];
      if (!confirm(`حذف مفتاح ${label} من هذا الجهاز؟`)) return;
      state.keys = (await api("DELETE", `/api/keys/${p}`)).keys;
      renderProviders();
      loadStatus();
    } else if (btn.dataset.act === "fetch-models") {
      btn.disabled = true;
      line.textContent = "يجلب قائمة النماذج…";
      const { models } = await api("GET", `/api/models/${p}`);
      const list = box.querySelector(`#list-${p}`);
      list.replaceChildren(...models.map((m) => option(m, m, false)));
      line.textContent = `وُجد ${models.length} نموذج. اكتب أول حروف الاسم لتظهر القائمة.`;
      btn.disabled = false;
    }
  } catch (err) {
    btn.disabled = false;
    line.textContent = err.message;
    line.classList.add("is-error");
  }
}

async function onProviderChange(e) {
  if (!e.target.id.startsWith("model-")) return;
  const p = e.target.id.slice("model-".length);
  const line = e.target.closest(".provider").querySelector(".status-line");
  try {
    const data = await api("PUT", "/api/settings", { cloud_models: { [p]: e.target.value.trim() } });
    state.settings = data.settings;
    line.classList.remove("is-error");
    line.textContent = e.target.value.trim() ? "حُفظ النموذج." : "أُزيل النموذج.";
    loadStatus();
  } catch (err) {
    line.textContent = err.message;
    line.classList.add("is-error");
  }
}

function renderRouting() {
  const box = $("#routing");
  box.replaceChildren();
  for (const [type, label] of TASK_TYPES) {
    const row = document.createElement("div");
    row.className = "route-row";
    const lab = document.createElement("label");
    lab.htmlFor = `route-${type}`;
    lab.textContent = label;
    const sel = document.createElement("select");
    sel.id = `route-${type}`;
    ROUTES.forEach(([v, l]) => sel.appendChild(option(v, l, state.settings.routing[type] === v)));
    row.append(lab, sel);
    box.appendChild(row);
  }
  $("#mode").value = state.settings.mode;
}

async function saveSettings() {
  const line = $("#settings-status");
  line.classList.remove("is-error");
  const routing = {};
  TASK_TYPES.forEach(([t]) => { routing[t] = $(`#route-${t}`).value; });
  const cloud_models = {};
  CLOUD.forEach(([p]) => { cloud_models[p] = $(`#model-${p}`).value.trim(); });
  try {
    const data = await api("PUT", "/api/settings", {
      ollama_url: $("#ollama-url").value.trim(),
      local_model: $("#local-model").value,
      mode: $("#mode").value,
      routing,
      cloud_models,
    });
    state.settings = data.settings;
    line.textContent = "حُفظت الإعدادات.";
    await loadStatus(true);
    renderOllamaNote();
  } catch (err) {
    line.textContent = err.message;
    line.classList.add("is-error");
  }
}

function fillWorkspacePicker() {
  const sel = $("#workspace");
  const saved = store.get("workspace", "");
  sel.replaceChildren(option("", "بدون مشروع", false));
  state.workspaces.forEach((w) => sel.appendChild(option(w.name, w.name, w.name === saved)));
  if (!state.workspaces.some((w) => w.name === saved)) sel.value = "";
  $("#ws-pick").hidden = state.workspaces.length === 0;
}

function renderWorkspaces() {
  const ul = $("#ws-list");
  ul.replaceChildren();
  if (!state.workspaces.length) {
    const li = document.createElement("li");
    li.textContent = "لا توجد مجلدات بعد. أضف مجلد مشروعك ليعمل عليه المبرمج.";
    ul.appendChild(li);
  }
  for (const w of state.workspaces) {
    const li = document.createElement("li");
    li.className = "ws-item";
    const id = `ws-${state.workspaces.indexOf(w)}`;
    li.innerHTML = `<div class="ws-head"><p><strong>${esc(w.name)}</strong><br><bdi dir="ltr" class="muted">${esc(w.path)}</bdi>${w.exists ? "" : " <em>(المجلد غير موجود)</em>"}</p>`
      + `<button type="button" class="link" data-ws="${esc(w.name)}">أزل</button></div>`
      + `<details class="ws-more"><summary>الأوامر وVercel</summary>`
      + `<label for="${id}-cmd">أوامر يسمح لها بتشغيلها، أمر في كل سطر</label>`
      + `<textarea id="${id}-cmd" rows="3" dir="ltr" spellcheck="false" placeholder="npm test&#10;npm run build">${esc((w.commands || []).join("\n"))}</textarea>`
      + `<label for="${id}-vercel">اسم المشروع في Vercel (اختياري)</label>`
      + `<input id="${id}-vercel" dir="ltr" spellcheck="false" value="${esc(w.vercel_project || "")}">`
      + `<button type="button" class="ghost" data-ws-save="${esc(w.name)}" data-ws-id="${id}">احفظ</button>`
      + `<p class="status-line" role="status"></p></details>`;
    ul.appendChild(li);
  }
  fillWorkspacePicker();
}

async function loadWorkspaces() {
  try {
    state.workspaces = (await api("GET", "/api/workspaces")).workspaces;
  } catch (e) {
    state.workspaces = [];
  }
  renderWorkspaces();
}

function renderServices() {
  const box = $("#services");
  box.replaceChildren();
  for (const [key, label, note] of SERVICES) {
    const has = state.keys[key];
    const div = document.createElement("div");
    div.className = "provider";
    div.dataset.service = key;
    div.innerHTML = `<div class="provider-head"><strong>${label}</strong>`
      + `<span class="key-state${has ? " has-key" : ""}">${has ? "الرمز محفوظ على هذا الجهاز" : "غير متصل"}</span></div>`
      + `<p class="panel-note">${note}</p>`
      + `<label for="svc-${key}">رمز الوصول (token)</label>`
      + `<div class="row"><input id="svc-${key}" type="password" dir="ltr" autocomplete="off" spellcheck="false">`
      + `<button type="button" class="ghost" data-svc="save">احفظ</button>`
      + (has ? `<button type="button" class="ghost danger" data-svc="delete">احذف</button>` : "")
      + `</div><p class="status-line" role="status"></p>`;
    box.appendChild(div);
  }
}

function renderLessons(lessons) {
  const ul = $("#lessons");
  ul.replaceChildren();
  if (!lessons.length) {
    const li = document.createElement("li");
    li.textContent = "لا شيء بعد. أضف قاعدة، أو قيّم إجابة بعد انتهائها.";
    ul.appendChild(li);
    return;
  }
  for (const l of lessons) {
    const li = document.createElement("li");
    li.innerHTML = `<span class="kind ${esc(l.kind)}">${LESSON_KIND[l.kind] || l.kind}</span><p dir="auto">${esc(l.text)}</p>`
      + `<button type="button" class="link" data-lesson="${l.id}">احذف</button>`;
    ul.appendChild(li);
  }
}

function renderAgents() {
  const ul = $("#agents");
  ul.replaceChildren();
  for (const a of state.agents) {
    const li = document.createElement("li");
    li.className = a.available ? "is-live" : "";
    li.innerHTML = `<span>${esc(a.name)}</span><span>${a.available ? "يعمل الآن" : `في المرحلة ${a.phase}`}</span>`;
    ul.appendChild(li);
  }
}

async function openDrawer() {
  try {
    const [{ settings, keys }, { lessons }] = await Promise.all([api("GET", "/api/settings"), api("GET", "/api/lessons")]);
    state.settings = settings;
    state.keys = keys;
    await loadStatus(true);
    $("#ollama-url").value = settings.ollama_url;
    fillLocalModels(state.status.ollama.models);
    renderOllamaNote();
    renderProviders();
    renderServices();
    renderRouting();
    renderLessons(lessons);
    renderAgents();
    await loadWorkspaces();
    $("#ws-status").textContent = "";
    $("#settings-status").textContent = "";
    $("#about").textContent = `الإصدار ${state.status.version}. بياناتك محفوظة على هذا الجهاز في: ${state.status.data_dir}`;
    for (const hook of window.Yousef.drawerHooks) {
      try { await hook(); } catch (e) { console.error(e); }
    }
  } catch (e) {
    showHint(e.message, true);
    return;
  }
  els.drawer.hidden = false;
  els.scrim.hidden = false;
  els.btnSettings.setAttribute("aria-expanded", "true");
  $("#btn-close").focus();
}

function closeDrawer() {
  if (els.drawer.hidden) return;
  els.drawer.hidden = true;
  els.scrim.hidden = true;
  els.btnSettings.setAttribute("aria-expanded", "false");
  els.btnSettings.focus();
}

// ---------------------------------------------------------------- composer
function autosize() {
  els.input.style.height = "auto";
  els.input.style.height = `${Math.min(els.input.scrollHeight, 220)}px`;
}

// Shared with voice.js
window.Yousef = {
  api, state, submit, showHint, poll: () => schedulePoll(), taskHooks: [], drawerHooks: [],
};

// ---------------------------------------------------------------- boot
document.addEventListener("DOMContentLoaded", async () => {
  Object.assign(els, {
    tasks: $("#tasks"), empty: $("#empty"), input: $("#input"), send: $("#send"), hint: $("#composer-hint"),
    engineLocal: $("#engine-local"), engineCloud: $("#engine-cloud"), setupNote: $("#setup-note"),
    drawer: $("#drawer"), scrim: $("#scrim"), btnSettings: $("#btn-settings"),
  });

  if (window.matchMedia("(max-width: 640px)").matches) els.input.placeholder = "اكتب طلبك…";
  $("#composer").addEventListener("submit", (e) => { e.preventDefault(); submit(els.input.value); });
  els.input.addEventListener("input", autosize);
  els.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      submit(els.input.value);
    }
  });
  document.querySelectorAll(".example").forEach((b) => b.addEventListener("click", () => {
    els.input.value = b.textContent.trim();
    autosize();
    els.input.focus();
  }));
  els.tasks.addEventListener("click", onTaskClick);

  els.btnSettings.addEventListener("click", openDrawer);
  document.querySelectorAll("[data-open-settings]").forEach((b) => b.addEventListener("click", openDrawer));
  $("#btn-close").addEventListener("click", closeDrawer);
  els.scrim.addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });
  $("#providers").addEventListener("click", onProviderClick);
  $("#providers").addEventListener("change", onProviderChange);
  $("#btn-save-settings").addEventListener("click", saveSettings);
  $("#btn-refresh-local").addEventListener("click", async () => {
    try {
      const { models } = await api("GET", "/api/models/ollama");
      await loadStatus(true);
      fillLocalModels(models);
      renderOllamaNote();
    } catch (err) {
      $("#ollama-note").textContent = err.message;
    }
  });
  $("#workspace").addEventListener("change", (e) => store.set("workspace", e.target.value));
  $("#btn-add-ws").addEventListener("click", async () => {
    const input = $("#ws-path");
    const line = $("#ws-status");
    line.classList.remove("is-error");
    try {
      const data = await api("POST", "/api/workspaces", { path: input.value });
      state.workspaces = data.workspaces;
      renderWorkspaces();
      input.value = "";
      line.textContent = `أُضيف المجلد «${data.workspace.name}».`;
    } catch (err) {
      line.textContent = err.message;
      line.classList.add("is-error");
    }
  });
  $("#services").addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-svc]");
    if (!btn) return;
    const box = btn.closest("[data-service]");
    const key = box.dataset.service;
    const line = box.querySelector(".status-line");
    line.classList.remove("is-error");
    try {
      if (btn.dataset.svc === "save") {
        const input = box.querySelector("input");
        if (!input.value.trim()) throw new Error("الصق الرمز أولًا.");
        state.keys = (await api("PUT", `/api/keys/${key}`, { key: input.value })).keys;
      } else {
        if (!confirm("حذف هذا الرمز من الجهاز؟")) return;
        state.keys = (await api("DELETE", `/api/keys/${key}`)).keys;
      }
      renderServices();
    } catch (err) {
      line.textContent = err.message;
      line.classList.add("is-error");
    }
  });
  $("#ws-list").addEventListener("click", async (e) => {
    const save = e.target.closest("button[data-ws-save]");
    if (save) {
      const id = save.dataset.wsId;
      const line = save.parentElement.querySelector(".status-line");
      line.classList.remove("is-error");
      try {
        const data = await api("PUT", `/api/workspaces/${encodeURIComponent(save.dataset.wsSave)}`, {
          commands: $(`#${id}-cmd`).value, vercel_project: $(`#${id}-vercel`).value,
        });
        state.workspaces = data.workspaces;
        line.textContent = "حُفظ.";
      } catch (err) {
        line.textContent = err.message;
        line.classList.add("is-error");
      }
      return;
    }
    const btn = e.target.closest("button[data-ws]");
    if (!btn) return;
    try {
      state.workspaces = (await api("DELETE", `/api/workspaces/${encodeURIComponent(btn.dataset.ws)}`)).workspaces;
      renderWorkspaces();
    } catch (err) { showHint(err.message, true); }
  });
  $("#btn-add-rule").addEventListener("click", async () => {
    const box = $("#rule-input");
    if (!box.value.trim()) { box.focus(); return; }
    try {
      renderLessons((await api("POST", "/api/lessons", { text: box.value })).lessons);
      box.value = "";
    } catch (err) { showHint(err.message, true); }
  });
  $("#lessons").addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-lesson]");
    if (!btn) return;
    try { renderLessons((await api("DELETE", `/api/lessons/${btn.dataset.lesson}`)).lessons); }
    catch (err) { showHint(err.message, true); }
  });
  $("#btn-new").addEventListener("click", async () => {
    try {
      await api("POST", "/api/conversation/new");
      els.tasks.innerHTML = "";
      state.tasks.clear();
      els.empty.hidden = false;
      els.input.focus();
    } catch (err) { showHint(err.message, true); }
  });

  try {
    state.agents = (await api("GET", "/api/agents")).agents;
  } catch (e) { /* names fall back to keys */ }
  await loadStatus();
  await loadWorkspaces();
  try { await loadTasks(); } catch (e) { els.empty.hidden = false; showHint(e.message, true); }
  setInterval(() => loadStatus(), 30000);
  els.input.focus();
});
