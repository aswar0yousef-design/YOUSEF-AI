// Webview for the YOUSEF AI panel. It only renders; the extension does all requests.
(function () {
  "use strict";
  const vscode = acquireVsCodeApi();
  const $ = (sel) => document.querySelector(sel);

  const AGENTS = {
    general: "المساعد العام", coder: "المبرمج", writer: "صانع المحتوى", analyst: "المحلل", store_manager: "مدير المتاجر",
    tech_manager: "المدير التقني", social_publisher: "ناشر السوشيال", ads_manager: "مدير الإعلانات", designer: "صانع الصور",
    trading_analyst: "محلل التداول", device_control: "التحكم بالجهاز",
  };
  const TOOL_LABEL = {
    list_files: "عرض مجلد", read_file: "قراءة", search: "بحث", replace: "تعديل مقترح", write_file: "ملف مقترح",
    git_status: "حالة git", git_diff: "فروقات git", run: "تشغيل أمر", vercel_status: "حالة Vercel",
    git_commit: "commit مقترح", git_push: "push مقترح", open_pr: "Pull Request مقترح",
    list_stores: "عرض المتاجر", store_info: "معلومات المتجر", list_products: "عرض المنتجات", get_product: "قراءة منتج",
    list_orders: "عرض الطلبات", sales_summary: "ملخص المبيعات", propose_product: "منتج مقترح", propose_update: "تعديل مقترح",
    list_discounts: "عرض العروض", propose_discount: "عرض مقترح", propose_end_discount: "إيقاف عرض مقترح",
    propose_bundle: "باقة مقترحة", site_check: "فحص الموقع", store_health: "صحة المتجر", scan_code: "فحص الكود",
    check_dependencies: "فحص الحزم", list_accounts: "عرض الحسابات", recent_posts: "آخر المنشورات",
    list_scheduled: "المنشورات المجدولة", list_media: "مكتبة الصور", propose_post: "منشور مقترح",
    propose_cancel: "إلغاء منشور مقترح", list_ad_accounts: "الحسابات الإعلانية", campaigns: "الحملات",
    adsets: "المجموعات الإعلانية", daily: "النتائج اليومية", propose_pause: "إيقاف مقترح", propose_resume: "تشغيل مقترح",
    propose_budget: "ميزانية مقترحة", generate_image: "صورة جديدة",
    price: "السعر", analyze: "تحليل الشارت", compare: "مقارنة", portfolio: "المحفظة التجريبية", list_alerts: "التنبيهات",
    propose_trade: "صفقة تجريبية مقترحة", propose_alert: "تنبيه مقترح", propose_cancel_alert: "إلغاء تنبيه مقترح",
  };
  const PLATFORM_AR = { facebook: "فيسبوك", instagram: "إنستغرام" };
  const AUDIT_TOOLS = new Set(["site_check", "store_health", "scan_code", "check_dependencies"]);
  const PROPOSAL_KIND = {
    store_create_product: "منتج جديد", store_update_product: "تعديل", store_create_discount: "عرض جديد",
    store_end_discount: "إيقاف عرض", store_create_bundle: "باقة جديدة", social_post: "منشور",
    social_cancel: "إلغاء منشور مجدول", ads_pause: "إيقاف إعلان", ads_resume: "تشغيل إعلان", ads_budget: "ميزانية إعلان",
    trade_buy: "شراء تجريبي", trade_sell: "بيع تجريبي", trade_alert: "تنبيه سعر", trade_cancel_alert: "إلغاء تنبيه",
  };
  const TASK_STATE = {
    planning: "يخطّط…", running: "يعمل…", waiting: "ينتظر موافقتك", failed: "تعثّرت المهمة",
    cancelled: "أُلغيت", interrupted: "توقّفت عند إغلاق يوسف",
  };
  const STEP_STATE = {
    pending: "في الانتظار", running: "يعمل الآن", awaiting_approval: "ينتظر موافقتك", done: "تم", failed: "تعثّر", skipped: "تُخطّيت",
  };
  const CHANGE_STATUS = { pending: "بانتظار موافقتك", applied: "طُبّق", rejected: "مرفوض", conflict: "تعارض", error: "خطأ" };
  const ACTION_STATUS = {
    pending: "بانتظار موافقتك", done: "تم", rejected: "مرفوض", failed: "تعذّر", skipped: "لم يُنفَّذ", conflict: "تعارض",
  };
  const FIELD_LABEL = {
    title: "العنوان", description: "الوصف", price: "السعر", compare_at_price: "السعر قبل الخصم",
    sale_price: "سعر التخفيض", sku: "SKU", status: "الحالة", tags: "الوسوم", vendor: "المورّد", product_type: "النوع",
    seo_title: "عنوان SEO", seo_description: "وصف SEO", sale_from: "بداية التخفيض", sale_to: "نهاية التخفيض",
  };
  const PRODUCT_STATUS = { draft: "مسودة", active: "منشور", archived: "مؤرشف" };
  const unticked = new Set();
  const ACTION_NAME = { git_commit: "commit", git_push: "push", open_pr: "Pull Request" };
  const PROVIDER = { ollama: "محلي", anthropic: "Claude", openai: "OpenAI", gemini: "Gemini" };
  const ACTIVE = new Set(["planning", "running", "waiting"]);

  const tasks = new Map();
  let order = [];
  let status = { online: null };

  // ---------------------------------------------------------- text rendering
  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function inline(s) {
    return esc(s).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  }
  function mdText(text) {
    let html = "", para = [], list = null;
    const flushPara = () => { if (para.length) { html += `<p dir="auto">${para.map(inline).join("<br>")}</p>`; para = []; } };
    const flushList = () => {
      if (list) { html += `<${list.tag} dir="auto">${list.items.map((i) => `<li>${inline(i)}</li>`).join("")}</${list.tag}>`; list = null; }
    };
    for (const raw of text.split("\n")) {
      const t = raw.trim();
      let m;
      if (!t) { flushPara(); flushList(); continue; }
      if ((m = t.match(/^#{1,6}\s+(.*)$/))) { flushPara(); flushList(); html += `<h4 dir="auto">${inline(m[1])}</h4>`; continue; }
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
    flushPara();
    flushList();
    return html;
  }
  function md(src) {
    return String(src ?? "").split("```").map((part, i) => {
      if (i % 2 === 0) return mdText(part);
      const nl = part.indexOf("\n");
      const code = nl > -1 && /^[\w+#.-]*$/.test(part.slice(0, nl).trim()) ? part.slice(nl + 1) : part;
      return `<pre dir="ltr"><code>${esc(code.replace(/\n$/, ""))}</code></pre>`;
    }).join("");
  }
  function countFiles(n) {
    if (n === 1) return "ملف واحد";
    if (n === 2) return "ملفين";
    return n <= 10 ? `${n} ملفات` : `${n} ملفًا`;
  }

  // ---------------------------------------------------------- rendering
  function renderStatus() {
    const el = $("#status");
    if (status.online === false) {
      el.className = "status offline";
      el.innerHTML = `<p>يوسف غير متصل. شغّل <code>start.bat</code> من مجلد يوسف، ثم حاول مجددًا.</p>`
        + `<div class="row"><button type="button" data-act="retry">حاول مجددًا</button></div>`;
      return;
    }
    if (status.online !== true) { el.className = "status"; el.textContent = "يتصل بيوسف…"; return; }
    const chips = [`<span class="chip ok">متصل</span>`];
    if (status.model) chips.push(`<span class="chip"><bdi dir="ltr">${esc(status.model)}</bdi></span>`);
    if (status.project) {
      chips.push(status.project.allowed
        ? `<span class="chip">المشروع: ${esc(status.project.name)}</span>`
        : `<span class="chip warn">المشروع «${esc(status.project.name)}» غير مسموح بعد</span>`);
    }
    el.className = "status";
    el.innerHTML = `<div class="chips">${chips.join("")}</div>`
      + `<div class="row"><button type="button" class="link" data-act="dashboard">لوحة يوسف</button>`
      + `<button type="button" class="link" data-act="new">محادثة جديدة</button></div>`;
  }

  function changesHTML(step) {
    const changes = step.changes || [];
    if (!changes.length) return "";
    return `<ul class="changes">${changes.map((c) => `<li>`
      + `<bdi dir="ltr" class="path">${esc(c.path)}</bdi>`
      + `${c.new_file ? `<span class="new">جديد</span>` : ""}`
      + `<bdi dir="ltr" class="counts"><span class="plus">+${c.added}</span> <span class="minus">−${c.removed}</span></bdi>`
      + `<span class="cstatus is-${esc(c.status)}">${CHANGE_STATUS[c.status] || esc(c.status)}</span>`
      + `<button type="button" class="link" data-act="diff" data-change="${c.id}">اعرض الفرق</button>`
      + `${c.note ? `<span class="note">${esc(c.note)}</span>` : ""}</li>`).join("")}</ul>`;
  }

  function value(field, v) {
    if (v === null || v === undefined || v === "" || (Array.isArray(v) && !v.length)) return "—";
    if (field === "status") return esc(PRODUCT_STATUS[v] || v);
    if (field === "tags") return esc(v.join("، "));
    if (field === "description") {
      const d = document.createElement("div");
      d.innerHTML = String(v);
      const text = (d.textContent || "").replace(/\s+/g, " ").trim();
      return esc(text.length > 120 ? `${text.slice(0, 120)}…` : text);
    }
    return `<bdi>${esc(v)}</bdi>`;
  }

  function proposalsHTML(step) {
    const items = (step.actions || []).filter(external);
    if (!items.length) return "";
    const waiting = step.status === "awaiting_approval";
    return `<div class="proposals" data-proposals="${step.id}">${items.map((a) => {
      const p = a.payload || {};
      const creating = a.kind === "store_create_product";
      if (a.kind === "social_post") return postHTML(a, waiting);
      const rows = Array.isArray(p.rows)  // offers and bundles: label / value rows
        ? p.rows.map((r) => `<tr><th dir="auto">${esc(r.label)}</th><td class="new" dir="${/[\u0600-\u06FF]/.test(String(r.value)) ? "rtl" : "auto"}">${esc(r.value)}</td></tr>`).join("")
        : Object.keys(p.after || {}).map((f) => `<tr><th>${FIELD_LABEL[f] || esc(f)}</th>`
          + (creating ? `<td class="new">${value(f, p.after[f])}</td>` : `<td class="old">${value(f, (p.before || {})[f])}</td><td class="new">${value(f, p.after[f])}</td>`)
          + "</tr>").join("");
      const pick = waiting && a.status === "pending"
        ? `<input type="checkbox" class="pick" data-action="${a.id}" aria-label="ضمن الموافقة"${unticked.has(a.id) ? "" : " checked"}>` : "";
      const title = creating ? (p.after || {}).title : p.title;
      return `<div class="proposal is-${esc(a.status)}"><label class="phead">${pick}<span class="pkind">${PROPOSAL_KIND[a.kind] || esc(a.kind)}</span>`
        + `<strong dir="auto">${esc(title || "")}</strong></label>`
        + `<div class="pmeta"><span class="muted">${esc(p.store || "")}</span><span class="cstatus is-${esc(a.status)}">${ACTION_STATUS[a.status] || esc(a.status)}</span></div>`
        + `<table class="ba"><tbody>${rows}</tbody></table>`
        + (p.warnings || []).map((w) => `<p class="pwarn" dir="auto">تنبيه: ${esc(w)}</p>`).join("")
        + (a.result ? `<p class="note" dir="auto">${esc(a.result)}</p>` : "") + "</div>";
    }).join("")}</div>`;
  }

  // a post as text: the panel cannot load the pictures, so it says how many
  function postHTML(a, waiting) {
    const p = a.payload || {};
    const pick = waiting && a.status === "pending"
      ? `<input type="checkbox" class="pick" data-action="${a.id}" aria-label="ضمن الموافقة"${unticked.has(a.id) ? "" : " checked"}>` : "";
    const both = (p.platforms || []).length > 1 && p.text_instagram && p.text_instagram !== p.text;
    const text = (t) => `<div class="ptext" dir="auto">${esc(t)}</div>`;
    const body = both ? `<p class="muted">فيسبوك</p>${text(p.text)}<p class="muted">إنستغرام</p>${text(p.text_instagram)}`
      : text((p.platforms || [])[0] === "instagram" ? (p.text_instagram || p.text) : p.text);
    const when = p.when ? new Date(p.when).toLocaleString("ar-u-nu-latn", { dateStyle: "medium", timeStyle: "short" }) : "فور الموافقة";
    const imgs = (p.images || []).length;
    return `<div class="proposal is-${esc(a.status)}"><label class="phead">${pick}<span class="pkind">${p.when ? "منشور مجدول" : "منشور"}</span>`
      + `<strong dir="auto">${esc(p.account || "")}</strong></label>`
      + `<div class="pmeta"><span class="muted">${(p.platforms || []).map((x) => PLATFORM_AR[x] || x).join(" + ")} · <bdi>${esc(when)}</bdi>`
      + `${imgs ? ` · ${imgs === 1 ? "صورة واحدة" : imgs === 2 ? "صورتان" : `${imgs} صور`}` : ""}</span>`
      + `<span class="cstatus is-${esc(a.status)}">${ACTION_STATUS[a.status] || esc(a.status)}</span></div>${body}`
      + (p.warnings || []).map((w) => `<p class="pwarn" dir="auto">تنبيه: ${esc(w)}</p>`).join("")
      + (a.result ? `<p class="note" dir="auto">${esc(a.result)}</p>` : "") + "</div>";
  }

  function external(a) {
    return ["store_", "social_", "ads_", "trade_", "device_"].some((p) => a.kind.startsWith(p));
  }

  function actionsHTML(step) {
    const actions = (step.actions || []).filter((a) => !external(a));
    if (!actions.length) return "";
    return `<ul class="changes actions">${actions.map((a) => {
      const p = a.payload || {};
      let label = ACTION_NAME[a.kind] || a.kind;
      if (a.kind === "git_commit") label = `commit: <bdi>${esc(p.message || "")}</bdi>${p.branch ? `، على فرع <bdi dir="ltr">${esc(p.branch)}</bdi>` : ""}`;
      if (a.kind === "open_pr") label = `Pull Request: <bdi>${esc(p.title || "")}</bdi>`;
      return `<li><span>${label}</span><span class="cstatus is-${esc(a.status)}">${ACTION_STATUS[a.status] || esc(a.status)}</span>`
        + `${a.result ? `<span class="note" dir="auto">${esc(a.result)}</span>` : ""}</li>`;
    }).join("")}</ul>`;
  }

  function stepHTML(step, task, single) {
    const tag = step.provider ? `<span class="tag"><bdi dir="ltr">${esc(PROVIDER[step.provider] || step.provider)} ${esc(step.model || "")}</bdi></span>` : "";
    let h = `<li class="step is-${esc(step.status)}"><div class="step-head"><strong>${esc(AGENTS[step.agent] || step.agent)}</strong>`
      + `${tag}<span class="muted">${STEP_STATE[step.status] || ""}</span></div>`;
    if (!single && step.instruction.trim() !== task.request.trim()) h += `<p class="instr" dir="auto">${esc(step.instruction)}</p>`;
    const trace = step.trace || [];
    if (trace.length) {
      h += `<details class="trace"><summary>${trace.length} ${trace.length > 10 ? "أداة" : "أدوات"} استُخدمت</summary><ul>`
        + trace.map((t) => `<li class="${t.ok ? "" : "bad"}">${esc(TOOL_LABEL[t.tool] || t.tool)} <bdi dir="ltr">${esc(t.target || "")}</bdi>`
          + `${t.note ? ` <span class="muted" dir="auto">${esc(t.note)}</span>` : ""}</li>`).join("")
        + `</ul></details>`;
      const made = trace.filter((t) => t.ok && t.tool === "generate_image" && /^media:img-[0-9a-f]{16}\.(?:jpg|png)$/.test(t.note || ""));
      if (made.length) {
        h += `<p class="row">${made.map((t, i) => `<button type="button" class="link" data-act="media" data-media="${esc(t.note.slice(6))}">`
          + `افتح الصورة ${made.length > 1 ? i + 1 : ""}</button>`).join("")}</p>`;
      }
      const audits = trace.filter((t) => t.ok && AUDIT_TOOLS.has(t.tool) && t.note && t.note !== "مكرر");
      if (audits.length) {
        h += `<ul class="audits">${audits.map((t) => `<li class="${/حرج|عالٍ/.test(t.note) ? "hot" : ""}"><b>${esc(TOOL_LABEL[t.tool])}</b>`
          + `${t.target ? ` <bdi dir="auto">${esc(t.target)}</bdi>` : ""}: <span dir="auto">${esc(t.note)}</span></li>`).join("")}</ul>`;
      }
    }
    const pending = (step.changes || []).filter((c) => c.status === "pending").length;
    const pendingAll = (step.actions || []).filter((a) => a.status === "pending");
    const pendingStore = pendingAll.filter(external);
    const pendingActions = pendingAll.filter((a) => !external(a));
    if ((pending || pendingAll.length) && step.output) h += `<div class="md explain">${md(step.output)}</div>`;
    h += changesHTML(step) + actionsHTML(step) + proposalsHTML(step);
    if (step.status === "awaiting_approval") {
      // one gate for everything the step proposes: files, then git, then store changes
      const parts = [];
      if (pending) parts.push(`تعديل ${countFiles(pending)}`);
      if (pendingActions.length) parts.push(pendingActions.map((a) => ACTION_NAME[a.kind] || a.kind).join(" ثم "));
      const posts = pendingStore.filter((a) => a.kind.startsWith("social_")).length;
      const adsN = pendingStore.filter((a) => a.kind.startsWith("ads_")).length;
      const tradeN = pendingStore.filter((a) => a.kind.startsWith("trade_")).length;
      const otherN = pendingStore.filter((a) => a.kind.startsWith("device_")).length;
      const storeN = pendingStore.length - posts - adsN - otherN - tradeN;
      if (tradeN) parts.push(`${parts.length ? "و" : ""}${tradeN === 1 ? "إجراءً تجريبيًا واحدًا" : `${tradeN} إجراءات تجريبية`} (أموال افتراضية)`);
      if (adsN) parts.push(`${parts.length ? "و" : ""}${adsN === 1 ? "تغييرًا واحدًا" : `${adsN} تغييرات`} في الإعلانات`);
      if (otherN) parts.push(`${parts.length ? "و" : ""}${otherN === 1 ? "إجراءً واحدًا" : `${otherN} إجراءات`}`);
      if (storeN) {
        const count = storeN === 1 ? "تغييرًا واحدًا" : storeN === 2 ? "تغييرين" : storeN <= 10 ? `${storeN} تغييرات` : `${storeN} تغييرًا`;
        parts.push(`${parts.length ? "و" : ""}${count} على المتجر`);
      }
      if (posts) parts.push(`${parts.length ? "و" : ""}${posts === 1 ? "منشورًا واحدًا" : posts === 2 ? "منشورين" : `${posts} منشورات`}`);
      const pick = pendingStore.length ? " أزل علامة ما لا تريده، ثم قرّر." : " راجعها، ثم قرّر.";
      let approve = pendingActions.length ? "طبّق الكل" : "طبّق التعديلات";
      if (pendingStore.length) approve = "طبّق المحدد";
      h += parts.length
        ? `<div class="gate"><p>يقترح ${esc(AGENTS[step.agent] || "يوسف")}: ${parts.map((x, i) => (i && !x.startsWith("و") ? "، ثم " : i ? "، " : "") + x).join("")}.${pick} لن يُنفَّذ شيء قبل موافقتك.</p>`
          + `<div class="row"><button type="button" data-act="approve" data-step="${step.id}">${approve}</button>`
          + (pending && (pendingActions.length || pendingStore.length) ? `<button type="button" class="secondary" data-act="approve-files" data-step="${step.id}">الملفات فقط</button>` : "")
          + `<button type="button" class="secondary" data-act="reject" data-step="${step.id}">ارفض</button></div></div>`
        : `<div class="gate"><p>هذه الخطوة فيها إجراء خارج المحادثة، لذلك تنتظر موافقتك.</p>`
          + `<div class="row"><button type="button" data-act="approve" data-step="${step.id}">وافق ونفّذ</button>`
          + `<button type="button" class="secondary" data-act="reject" data-step="${step.id}">ارفض</button></div></div>`;
    }
    return h + "</li>";
  }

  function taskHTML(t) {
    const steps = t.steps || [];
    let h = `<h3 dir="auto">${esc(t.request)}</h3>`;
    const ctx = t.context || {};
    if (ctx.file) {
      const lines = ctx.start_line ? ` (${ctx.start_line === ctx.end_line ? `السطر ${ctx.start_line}` : `الأسطر ${ctx.start_line}–${ctx.end_line}`})` : "";
      h += `<p class="meta"><bdi dir="ltr">${esc(ctx.file)}</bdi>${lines}</p>`;
    }
    if (TASK_STATE[t.status]) h += `<p class="state is-${esc(t.status)}">${TASK_STATE[t.status]}</p>`;
    if (steps.length) h += `<ol class="steps">${steps.map((s) => stepHTML(s, t, steps.length === 1)).join("")}</ol>`;
    if (t.error && t.status !== "done") h += `<div class="error" dir="auto">${esc(t.error)}</div>`;
    if (t.status === "done" && t.final) h += `<div class="md answer">${md(t.final)}</div>`;
    const foot = [];
    if (ACTIVE.has(t.status)) foot.push(`<button type="button" class="link" data-act="cancel" data-task="${t.id}">ألغِ المهمة</button>`);
    if (t.status === "done") foot.push(`<button type="button" class="link" data-act="copy" data-task="${t.id}">انسخ الإجابة</button>`);
    if (["failed", "cancelled", "interrupted"].includes(t.status)) foot.push(`<button type="button" class="link" data-act="again" data-task="${t.id}">أعد الطلب</button>`);
    if (foot.length) h += `<div class="foot">${foot.join("")}</div>`;
    return h;
  }

  function renderFeed() {
    const feed = $("#feed");
    if (!order.length) {
      feed.innerHTML = `<div class="empty"><p>حدّد كودًا في المحرر ثم اسأل يوسف عنه، أو اطلب ميزة أو إصلاحًا للمشروع كله.</p>`
        + `<p class="muted">من قائمة الزر الأيمن في المحرر: «يوسف: اشرح هذا» و«يوسف: أصلح هذا».</p></div>`;
      return;
    }
    const open = new Set([...feed.querySelectorAll("details[open]")].map((d) => d.dataset.key));
    feed.innerHTML = order.map((id) => tasks.get(id)).filter(Boolean)
      .map((t) => `<article class="task" id="t${t.id}">${taskHTML(t)}</article>`).join("");
    feed.querySelectorAll("details.trace").forEach((d, i) => {
      d.dataset.key = `trace${i}`;
      if (open.has(d.dataset.key)) d.open = true;
    });
  }

  function upsert(task) {
    const stick = window.innerHeight + window.scrollY >= document.body.scrollHeight - 80;
    tasks.set(task.id, task);
    if (!order.includes(task.id)) order.push(task.id);
    renderFeed();
    if (stick) window.scrollTo({ top: document.body.scrollHeight });
  }

  // ---------------------------------------------------------- events
  window.addEventListener("message", (event) => {
    const msg = event.data || {};
    if (msg.type === "status") { status = msg; renderStatus(); }
    else if (msg.type === "task") upsert(msg.task);
    else if (msg.type === "tasks") {
      tasks.clear();
      order = [];
      (msg.tasks || []).forEach((t) => { tasks.set(t.id, t); order.push(t.id); });
      renderFeed();
      window.scrollTo({ top: document.body.scrollHeight });
    } else if (msg.type === "context") {
      $("#ctx-label").textContent = msg.label || "المحرر الحالي";
    } else if (msg.type === "error") {
      const box = $("#feed").appendChild(document.createElement("div"));
      box.className = "error toast";
      box.textContent = msg.message;
      setTimeout(() => box.remove(), 8000);
      box.scrollIntoView({ block: "end" });
    }
  });

  document.addEventListener("change", (e) => {
    const box = e.target.closest("input.pick");
    if (!box) return;
    if (box.checked) unticked.delete(Number(box.dataset.action)); else unticked.add(Number(box.dataset.action));
  });

  document.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-act]");
    if (!btn) return;
    const act = btn.dataset.act;
    const task = tasks.get(Number(btn.dataset.task));
    if (act === "approve" || act === "reject" || act === "approve-files") {
      const boxes = [...document.querySelectorAll(`[data-proposals="${btn.dataset.step}"] input.pick`)];
      const exclude = boxes.filter((b) => !b.checked).map((b) => Number(b.dataset.action));
      if (act === "approve" && boxes.length && exclude.length === boxes.length) return;
      btn.disabled = true;
      vscode.postMessage({
        type: "decide", stepId: Number(btn.dataset.step), approve: act !== "reject",
        scope: act === "approve-files" ? "files" : "all", exclude,
      });
    } else if (act === "diff") vscode.postMessage({ type: "openDiff", changeId: Number(btn.dataset.change) });
    else if (act === "cancel") vscode.postMessage({ type: "cancel", taskId: Number(btn.dataset.task) });
    else if (act === "copy" && task) vscode.postMessage({ type: "copy", text: task.final });
    else if (act === "again" && task) vscode.postMessage({ type: "send", text: task.request, attach: false });
    else if (act === "retry") vscode.postMessage({ type: "retry" });
    else if (act === "dashboard") vscode.postMessage({ type: "openDashboard" });
    else if (act === "media") vscode.postMessage({ type: "openMedia", name: btn.dataset.media });
    else if (act === "new") vscode.postMessage({ type: "newConversation" });
  });

  const input = $("#input");
  const send = () => {
    const text = input.value.trim();
    if (!text) { input.focus(); return; }
    vscode.postMessage({ type: "send", text, attach: $("#attach").checked });
    input.value = "";
  };
  $("#composer").addEventListener("submit", (e) => { e.preventDefault(); send(); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
  });

  renderStatus();
  renderFeed();
  vscode.postMessage({ type: "ready" });
})();
