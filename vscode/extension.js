"use strict";

// YOUSEF AI for VS Code: a chat panel and editor commands backed by the local YOUSEF server.
// All thinking happens in the server; this extension only gathers editor context,
// shows progress, opens diffs in VS Code and sends the user's approvals.
const crypto = require("crypto");
const vscode = require("vscode");
const { YousefClient } = require("./lib/client");
const { buildContext, describeContext, isInside, samePath } = require("./lib/context");

const ACTIVE = new Set(["planning", "running", "waiting"]);
const POLL_MS = 1000;
const STATE_KEY = "yousef.taskIds";
const CHANGE_SCHEME = "yousef-change";

function config() {
  return vscode.workspace.getConfiguration("yousef");
}

function serverUrl() {
  return config().get("serverUrl", "http://127.0.0.1:8765");
}

function folderFor(editor) {
  if (editor && editor.document && !editor.document.isUntitled) {
    const folder = vscode.workspace.getWorkspaceFolder(editor.document.uri);
    if (folder) return folder;
  }
  const folders = vscode.workspace.workspaceFolders || [];
  return folders.length ? folders[0] : null;
}

class ChangeContentProvider {
  constructor(client) {
    this.client = client;
    this.cache = new Map();
  }

  async get(id) {
    if (!this.cache.has(id)) this.cache.set(id, await this.client.change(id));
    return this.cache.get(id);
  }

  async provideTextDocumentContent(uri) {
    const query = new URLSearchParams(uri.query);
    const change = await this.get(query.get("id"));
    return query.get("side") === "old" ? change.old_text || "" : change.new_text;
  }
}

class ChatView {
  constructor(ext, client, changes) {
    this.ext = ext;
    this.client = client;
    this.changes = changes;
    this.view = null;
    this.tasks = new Map();
    this.order = ext.workspaceState.get(STATE_KEY, []);
    this.pollTimer = null;
    this.withoutProject = new Set();
    this.online = null;
  }

  // ---- webview ---------------------------------------------------------------
  resolveWebviewView(view) {
    this.view = view;
    const media = vscode.Uri.joinPath(this.ext.extensionUri, "media");
    view.webview.options = { enableScripts: true, localResourceRoots: [media] };
    view.webview.html = this.html(view.webview, media);
    view.webview.onDidReceiveMessage((msg) => this.onMessage(msg));
  }

  html(webview, media) {
    const nonce = crypto.randomBytes(16).toString("base64");
    const css = webview.asWebviewUri(vscode.Uri.joinPath(media, "chat.css"));
    const js = webview.asWebviewUri(vscode.Uri.joinPath(media, "chat.js"));
    return `<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${webview.cspSource}; img-src ${webview.cspSource} data:; script-src 'nonce-${nonce}';">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="${css}">
<title>YOUSEF AI</title>
</head>
<body>
<header id="status" class="status" role="status"></header>
<main id="feed" class="feed" aria-live="polite"></main>
<form id="composer" class="composer">
  <label class="sr" for="input">اطلب من يوسف</label>
  <textarea id="input" rows="3" placeholder="اسأل عن الكود، اطلب إصلاحًا، أو ميزة جديدة…"></textarea>
  <div class="composer-row">
    <label class="attach"><input type="checkbox" id="attach" checked> أرفق <span id="ctx-label">المحرر الحالي</span></label>
    <button type="submit" id="send">أرسل</button>
  </div>
</form>
<script nonce="${nonce}" src="${js}"></script>
</body>
</html>`;
  }

  post(message) {
    if (this.view) this.view.webview.postMessage(message);
  }

  async onMessage(msg) {
    try {
      switch (msg.type) {
        case "ready":
          await this.refreshStatus();
          this.pushContext();
          await this.restoreTasks();
          break;
        case "send":
          await this.send(msg.text, { attach: msg.attach });
          break;
        case "decide":
          await this.client.decide(msg.stepId, msg.approve, msg.scope || "all", msg.exclude || []);
          this.schedulePoll(200);
          break;
        case "cancel":
          await this.client.cancel(msg.taskId);
          this.schedulePoll(200);
          break;
        case "openDiff":
          await this.openDiff(msg.changeId);
          break;
        case "copy":
          await vscode.env.clipboard.writeText(msg.text || "");
          vscode.window.showInformationMessage("نُسخت الإجابة.");
          break;
        case "openDashboard":
          await vscode.env.openExternal(vscode.Uri.parse(serverUrl()));
          break;
        case "openMedia":
          // a picture an agent made, opened from Yousef's library in the browser
          if (/^img-[0-9a-f]{16}\.(?:jpg|png)$/.test(msg.name || "")) {
            await vscode.env.openExternal(vscode.Uri.parse(`${serverUrl()}/api/media/${msg.name}`));
          }
          break;
        case "retry":
          await this.refreshStatus();
          break;
        case "newConversation":
          await this.newConversation();
          break;
        default:
          break;
      }
    } catch (err) {
      this.showError(err);
    }
  }

  showError(err) {
    if (err && err.offline) this.setOnline(false);
    this.post({ type: "error", message: (err && err.message) || String(err) });
  }

  setOnline(online, extra = {}) {
    this.online = online;
    this.post({ type: "status", online, ...extra });
    if (this.onStatus) this.onStatus(online);
  }

  async refreshStatus() {
    try {
      const st = await this.client.status();
      const folder = folderFor(vscode.window.activeTextEditor);
      let project = null;
      if (folder) project = { name: folder.name, allowed: await this.isAllowed(folder.uri.fsPath) };
      const model = st.ollama && st.ollama.reachable ? st.ollama.active_model : "";
      this.setOnline(true, { version: st.version, model, project });
    } catch (err) {
      if (err && err.offline) this.setOnline(false);
      else this.showError(err);
    }
  }

  pushContext() {
    const editor = vscode.window.activeTextEditor;
    const ctx = editor ? buildContext(editor, folderFor(editor)) : null;
    this.post({ type: "context", label: describeContext(ctx) });
  }

  // ---- projects ----------------------------------------------------------------
  async isAllowed(folderPath) {
    const list = await this.client.workspaces();
    return list.some((w) => samePath(w.path, folderPath) || isInside(folderPath, w.path));
  }

  async allowFolder(folder, { ask = true } = {}) {
    if (ask) {
      const choice = await vscode.window.showInformationMessage(
        `اسمح ليوسف بقراءة مجلد «${folder.name}» واقتراح تعديلات عليه؟ لن يُكتب أي ملف إلا بعد موافقتك، وملفات الأسرار مثل .env لا تُقرأ.`,
        { modal: true }, "اسمح", "بدون المشروع",
      );
      if (choice !== "اسمح") return choice === "بدون المشروع" ? "without" : "cancel";
    }
    await this.client.addWorkspace(folder.uri.fsPath);
    await this.refreshStatus();
    return "allowed";
  }

  // ---- tasks --------------------------------------------------------------------
  async send(text, { attach = true, includeFile = false } = {}) {
    const request = String(text || "").trim();
    if (!request) return;
    const editor = vscode.window.activeTextEditor;
    const folder = folderFor(editor);
    let workspace = null;
    if (folder) {
      const key = folder.uri.fsPath;
      let allowed = await this.isAllowed(key);
      if (!allowed && !this.withoutProject.has(key)) {
        const answer = await this.allowFolder(folder);
        if (answer === "cancel") return;
        if (answer === "without") this.withoutProject.add(key);
        allowed = answer === "allowed";
      }
      if (allowed) workspace = key;
    }
    // without project access the agent cannot open the file, so send its text
    const context = attach && editor ? buildContext(editor, folder, { includeFile: includeFile || !workspace }) : null;
    const task = await this.client.createTask({ request, source: "vscode", workspace, context });
    this.addTask(task);
    await vscode.commands.executeCommand("yousef.chat.focus");
  }

  addTask(task) {
    this.tasks.set(task.id, task);
    if (!this.order.includes(task.id)) {
      this.order.push(task.id);
      this.order = this.order.slice(-30);
      this.ext.workspaceState.update(STATE_KEY, this.order);
    }
    this.post({ type: "task", task });
    if (ACTIVE.has(task.status)) this.schedulePoll();
  }

  async restoreTasks() {
    const tasks = [];
    for (const id of this.order) {
      try {
        tasks.push(await this.client.task(id));
      } catch (err) {
        if (err && err.offline) return;
      }
    }
    tasks.forEach((t) => this.tasks.set(t.id, t));
    this.order = tasks.map((t) => t.id);
    this.post({ type: "tasks", tasks });
    if (tasks.some((t) => ACTIVE.has(t.status))) this.schedulePoll();
  }

  schedulePoll(delay = POLL_MS) {
    clearTimeout(this.pollTimer);
    this.pollTimer = setTimeout(() => this.poll(), delay);
  }

  async poll() {
    this.pollTimer = null;
    const active = [...this.tasks.values()].filter((t) => ACTIVE.has(t.status));
    for (const t of active) {
      try {
        const task = await this.client.task(t.id);
        const before = this.tasks.get(t.id);
        this.tasks.set(task.id, task);
        this.post({ type: "task", task });
        if (before && before.status !== "waiting" && task.status === "waiting") this.notifyWaiting(task);
      } catch (err) {
        this.showError(err);
        return;
      }
    }
    if ([...this.tasks.values()].some((t) => ACTIVE.has(t.status))) this.schedulePoll();
  }

  notifyWaiting(task) {
    const step = (task.steps || []).find((s) => s.status === "awaiting_approval");
    if (!step) return;
    const files = (step.changes || []).filter((c) => c.status === "pending");
    const text = files.length
      ? `يقترح يوسف تعديل ${files.length} ملف. راجع الفروقات في لوحة يوسف قبل التطبيق.`
      : "خطوة من مهمة يوسف تنتظر موافقتك.";
    vscode.window.showInformationMessage(text, "افتح لوحة يوسف").then((choice) => {
      if (choice) vscode.commands.executeCommand("yousef.chat.focus");
    });
  }

  async openDiff(changeId) {
    const change = await this.changes.get(String(changeId));
    const query = (side) => `id=${encodeURIComponent(changeId)}&side=${side}`;
    const right = vscode.Uri.from({ scheme: CHANGE_SCHEME, path: `/${change.path}`, query: query("new") });
    const left = vscode.Uri.from({ scheme: CHANGE_SCHEME, path: `/${change.path}`, query: query("old") });
    const title = `${change.path} ↔ اقتراح يوسف`;
    await vscode.commands.executeCommand("vscode.diff", left, right, title);
  }

  async newConversation() {
    await this.client.newConversation();
    this.tasks.clear();
    this.order = [];
    this.ext.workspaceState.update(STATE_KEY, []);
    this.post({ type: "tasks", tasks: [] });
  }
}

function activate(ext) {
  const client = new YousefClient(serverUrl);
  const changes = new ChangeContentProvider(client);
  const chat = new ChatView(ext, client, changes);

  const status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  status.command = "yousef.focus";
  status.text = "$(comment-discussion) يوسف";
  status.tooltip = "افتح يوسف";
  status.show();
  chat.onStatus = (online) => {
    status.text = online ? "$(comment-discussion) يوسف" : "$(debug-disconnect) يوسف";
    status.tooltip = online ? "افتح يوسف" : "يوسف غير متصل؛ شغّل start.bat";
  };

  const ask = async (preset, { includeFile = false, prompt = false, attach = true } = {}) => {
    let text = preset;
    if (prompt) {
      text = await vscode.window.showInputBox({
        prompt: "ماذا تريد من يوسف بخصوص هذا الكود؟",
        value: preset,
        ignoreFocusOut: true,
      });
      if (!text) return;
    }
    try {
      await chat.send(text, { attach, includeFile });
    } catch (err) {
      chat.showError(err);
      vscode.window.showErrorMessage(err.message || String(err));
    }
  };

  ext.subscriptions.push(
    status,
    vscode.window.registerWebviewViewProvider("yousef.chat", chat, { webviewOptions: { retainContextWhenHidden: true } }),
    vscode.workspace.registerTextDocumentContentProvider(CHANGE_SCHEME, changes),
    vscode.commands.registerCommand("yousef.focus", () => vscode.commands.executeCommand("yousef.chat.focus")),
    vscode.commands.registerCommand("yousef.ask", () => ask("اشرح هذا الكود", { prompt: true })),
    vscode.commands.registerCommand("yousef.explain", () => ask("اشرح هذا الكود: ماذا يفعل، وما نقاط ضعفه؟", { includeFile: true })),
    vscode.commands.registerCommand("yousef.fix", () => ask("ابحث عن الأخطاء في هذا الكود وأصلحها، واشرح السبب باختصار.", { includeFile: true })),
    vscode.commands.registerCommand("yousef.audit", () => ask(
      "افحص هذا المشروع فحصًا تقنيًا: الثغرات الأمنية، والمفاتيح أو كلمات المرور المكتوبة في الكود، والحزم ذات الثغرات المعروفة، والأخطاء الواضحة. "
      + "أكّد كل مشكلة بقراءة الكود، واقترح الإصلاحات، ورتّب التقرير حسب الخطورة.", { attach: false })),
    vscode.commands.registerCommand("yousef.openDashboard", () => vscode.env.openExternal(vscode.Uri.parse(serverUrl()))),
    vscode.commands.registerCommand("yousef.newConversation", () => chat.newConversation().catch((e) => chat.showError(e))),
    vscode.commands.registerCommand("yousef.allowWorkspace", async () => {
      const folder = folderFor(vscode.window.activeTextEditor);
      if (!folder) {
        vscode.window.showWarningMessage("افتح مجلد مشروع في VS Code أولًا.");
        return;
      }
      try {
        const result = await chat.allowFolder(folder);
        if (result === "allowed") vscode.window.showInformationMessage(`يستطيع يوسف الآن العمل على «${folder.name}».`);
      } catch (err) {
        vscode.window.showErrorMessage(err.message || String(err));
      }
    }),
    vscode.window.onDidChangeActiveTextEditor(() => chat.pushContext()),
    vscode.window.onDidChangeTextEditorSelection(() => chat.pushContext()),
  );

  chat.refreshStatus();
  const timer = setInterval(() => chat.refreshStatus(), 15000);
  ext.subscriptions.push({ dispose: () => clearInterval(timer) });
  return { chat, client };
}

function deactivate() {}

module.exports = { activate, deactivate };
