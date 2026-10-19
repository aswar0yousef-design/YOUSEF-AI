"use strict";

// The small part of the VS Code API the extension uses, recorded for tests.
const path = require("path");

const state = {
  messages: [], infoAnswers: [], inputAnswers: [], commands: {}, executed: [], providers: {},
  opened: [], clipboard: null, folders: [], config: {},
};

function uri(fsPath) {
  return { scheme: "file", fsPath, path: fsPath, query: "", toString() { return `file://${fsPath}`; } };
}

const ask = async (msg, ...items) => {
  state.messages.push(msg);
  return state.infoAnswers.length ? state.infoAnswers.shift() : undefined;
};

module.exports = {
  __state: state,
  Uri: {
    file: uri,
    parse: (s) => ({ scheme: String(s).split(":")[0], path: String(s), query: "", toString() { return String(s); } }),
    joinPath: (base, ...parts) => uri(path.join(base.fsPath, ...parts)),
    from: (c) => ({ ...c, toString() { return `${c.scheme}:${c.path}?${c.query}`; } }),
  },
  StatusBarAlignment: { Left: 1, Right: 2 },
  window: {
    activeTextEditor: null,
    createStatusBarItem: () => ({ show() {}, dispose() {}, text: "", tooltip: "", command: "" }),
    registerWebviewViewProvider: (id, provider) => { state.providers[id] = provider; return { dispose() {} }; },
    showInformationMessage: ask,
    showWarningMessage: ask,
    showErrorMessage: ask,
    showInputBox: async (opts) => (state.inputAnswers.length ? state.inputAnswers.shift() : opts.value),
    onDidChangeActiveTextEditor: () => ({ dispose() {} }),
    onDidChangeTextEditorSelection: () => ({ dispose() {} }),
  },
  workspace: {
    get workspaceFolders() { return state.folders; },
    getWorkspaceFolder: (u) => state.folders.find((f) => u.fsPath.startsWith(f.uri.fsPath)),
    getConfiguration: () => ({ get: (key, fallback) => (key in state.config ? state.config[key] : fallback) }),
    registerTextDocumentContentProvider: (scheme, provider) => { state.providers[scheme] = provider; return { dispose() {} }; },
  },
  commands: {
    registerCommand: (id, fn) => { state.commands[id] = fn; return { dispose() {} }; },
    executeCommand: async (id, ...args) => {
      state.executed.push([id, ...args]);
      if (state.commands[id]) return state.commands[id](...args);
      return undefined;
    },
  },
  env: {
    openExternal: async (u) => { state.opened.push(String(u)); return true; },
    clipboard: { writeText: async (t) => { state.clipboard = t; } },
  },
};
