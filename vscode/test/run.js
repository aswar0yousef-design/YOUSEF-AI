"use strict";

// Drives the real extension.js against a mocked VS Code API and a running YOUSEF server.
// Usage: node vscode/test/run.js <serverUrl> <projectFolder>
// Prints one JSON line with the results; the Python test checks it.
const assert = require("assert");
const fs = require("fs");
const Module = require("module");
const path = require("path");

const mockPath = path.join(__dirname, "mock-vscode.js");
const originalResolve = Module._resolveFilename;
Module._resolveFilename = function (request, ...rest) {
  if (request === "vscode") return mockPath;
  return originalResolve.call(this, request, ...rest);
};

const vscode = require("vscode");
const { buildContext, describeContext, samePath, isInside } = require("../lib/context");
const extension = require("../extension");

const [serverUrl, project] = process.argv.slice(2);
const state = vscode.__state;
const out = {};

function fakeEditor(file, text, sel) {
  const lines = text.split("\n");
  const doc = {
    uri: vscode.Uri.file(file), fileName: file, languageId: "javascript", isUntitled: false, lineCount: lines.length,
    getText(range) {
      if (!range) return text;
      return lines.slice(range.start.line, range.end.line + 1).join("\n").slice(range.start.character);
    },
  };
  const selection = sel ? { isEmpty: false, start: sel.start, end: sel.end } : { isEmpty: true };
  return { document: doc, selection };
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function waitFor(fn, timeout = 20000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    const v = fn();
    if (v) return v;
    await sleep(100);
  }
  throw new Error("timed out");
}

(async () => {
  // ---- pure helpers
  const folder = { name: path.basename(project), uri: vscode.Uri.file(project) };
  const file = path.join(project, "src", "cart.js");
  const text = fs.readFileSync(file, "utf8");
  const ed = fakeEditor(file, text, { start: { line: 3, character: 0 }, end: { line: 3, character: 30 } });
  const ctx = buildContext(ed, folder);
  assert.strictEqual(ctx.file, "src/cart.js");
  assert.strictEqual(ctx.start_line, 4);
  assert.strictEqual(ctx.end_line, 4);
  assert.ok(ctx.selection.includes("sum += item.price"));
  assert.strictEqual(describeContext(ctx), "السطر 4 من src/cart.js");
  const whole = buildContext(fakeEditor(file, text, null), null, { includeFile: true });
  assert.strictEqual(whole.file, "cart.js");
  assert.strictEqual(whole.end_line, text.split("\n").length);
  assert.ok(samePath("C:\\Users\\Me\\Site\\", "c:/users/me/site", "win32"));
  assert.ok(!samePath("C:\\a", "c:/a", "linux"));
  assert.ok(isInside("/p/site/src", "/p/site", "linux"));
  assert.ok(!isInside("/p/site2", "/p/site", "linux"));
  out.helpers = "ok";

  // ---- activate with a webview
  state.config.serverUrl = serverUrl;
  state.folders = [folder];
  const store = {};
  const ext = {
    extensionUri: vscode.Uri.file(path.join(__dirname, "..")),
    subscriptions: [],
    workspaceState: { get: (k, d) => (k in store ? store[k] : d), update: (k, v) => { store[k] = v; return Promise.resolve(); } },
  };
  extension.activate(ext);
  const posted = [];
  let handler = null;
  const view = {
    webview: {
      options: null, html: "", cspSource: "vscode-resource:",
      asWebviewUri: (u) => `vscode-resource:${u.fsPath}`,
      postMessage: (m) => { posted.push(m); return Promise.resolve(true); },
      onDidReceiveMessage: (fn) => { handler = fn; return { dispose() {} }; },
    },
  };
  state.providers["yousef.chat"].resolveWebviewView(view);
  assert.ok(/script-src 'nonce-[^']+'/.test(view.webview.html), "CSP with nonce");
  assert.ok(view.webview.html.includes("chat.js"));
  out.commands = Object.keys(state.commands).sort();

  await handler({ type: "ready" });
  const statusMsg = await waitFor(() => posted.find((m) => m.type === "status" && m.online));
  out.status = { online: statusMsg.online, project: statusMsg.project };

  // ---- "fix this" on a selection: asks permission, then sends workspace + context
  vscode.window.activeTextEditor = ed;
  state.infoAnswers.push("اسمح");
  await state.commands["yousef.fix"]();
  out.permission_prompt = state.messages.find((m) => m.includes("اسمح ليوسف")) ? "shown" : "missing";
  const first = await waitFor(() => posted.find((m) => m.type === "task"));
  out.created = { source: first.task.source, workspace: first.task.workspace, file: (first.task.context || {}).file,
    start_line: (first.task.context || {}).start_line };
  out.focused = state.executed.some((e) => e[0] === "yousef.chat.focus");

  const waiting = await waitFor(() => posted.find((m) => m.type === "task" && m.task.status === "waiting"));
  const step = waiting.task.steps.find((s) => s.status === "awaiting_approval");
  const change = step.changes[0];
  out.waiting_notice = state.messages.some((m) => m.includes("يقترح يوسف تعديل"));

  // ---- open the diff in VS Code
  await handler({ type: "openDiff", changeId: change.id });
  const diff = state.executed.find((e) => e[0] === "vscode.diff");
  const left = await state.providers["yousef-change"].provideTextDocumentContent(diff[1]);
  const right = await state.providers["yousef-change"].provideTextDocumentContent(diff[2]);
  out.diff = { title: diff[3], left_has_qty: left.includes("qty"), right_has_qty: right.includes("qty") };

  // ---- approve from the panel
  await handler({ type: "decide", stepId: step.id, approve: true });
  const done = await waitFor(() => posted.find((m) => m.type === "task" && m.task.status === "done"));
  out.done = { final_has_applied: done.task.final.includes("طُبّقت"), change_status: done.task.steps[0].changes[0].status };

  // ---- second request reuses the permission (no new prompt) and restores after reload
  const prompts = state.messages.filter((m) => m.includes("اسمح ليوسف")).length;
  await handler({ type: "send", text: "ما الذي تغيّر؟", attach: false });
  out.second_prompted = state.messages.filter((m) => m.includes("اسمح ليوسف")).length > prompts;
  out.stored_ids = (store["yousef.taskIds"] || []).length;

  await handler({ type: "copy", text: "abc" });
  out.clipboard = state.clipboard;

  // ---- offline server
  state.config.serverUrl = "http://127.0.0.1:9";
  posted.length = 0;
  await handler({ type: "retry" });
  out.offline = posted.some((m) => m.type === "status" && m.online === false);

  console.log(JSON.stringify(out));
  process.exit(0);
})().catch((err) => {
  console.log(JSON.stringify({ error: String(err && err.stack || err), partial: out }));
  process.exit(1);
});
