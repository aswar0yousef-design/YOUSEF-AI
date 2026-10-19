"use strict";

// What the user is looking at in the editor, in the shape the YOUSEF server expects.
const path = require("path");

const MAX_CHARS = 20000;

function normalize(p, platform = process.platform) {
  let out = String(p || "").replace(/\\/g, "/").replace(/\/+$/, "");
  if (platform === "win32") out = out.toLowerCase();
  return out;
}

function samePath(a, b, platform) {
  return normalize(a, platform) === normalize(b, platform);
}

function isInside(child, root, platform) {
  const c = normalize(child, platform);
  const r = normalize(root, platform);
  return c === r || c.startsWith(r + "/");
}

function relativePath(root, file) {
  const rel = path.relative(root, file).split(path.sep).join("/");
  if (!rel || rel.startsWith("..") || path.isAbsolute(rel)) return path.basename(file);
  return rel;
}

// editor: {document: {uri: {fsPath}, fileName, languageId, isUntitled, lineCount, getText(range?)}, selection}
// folder: {uri: {fsPath}} or null
function buildContext(editor, folder, { includeFile = false, maxChars = MAX_CHARS } = {}) {
  if (!editor || !editor.document) return null;
  const doc = editor.document;
  const ctx = { language: doc.languageId || "" };
  if (doc.isUntitled) ctx.file = "untitled";
  else if (folder) ctx.file = relativePath(folder.uri.fsPath, doc.uri.fsPath);
  else ctx.file = path.basename(doc.fileName || doc.uri.fsPath);

  const sel = editor.selection;
  if (sel && !sel.isEmpty) {
    ctx.selection = doc.getText(sel).slice(0, maxChars);
    ctx.start_line = sel.start.line + 1;
    // a selection that ends at the start of a line does not include that line
    const endLine = sel.end.character === 0 && sel.end.line > sel.start.line ? sel.end.line : sel.end.line + 1;
    ctx.end_line = endLine;
  } else if (includeFile) {
    const text = doc.getText();
    ctx.selection = text.slice(0, maxChars);
    ctx.start_line = 1;
    ctx.end_line = doc.lineCount;
  }
  return ctx;
}

function describeContext(ctx) {
  if (!ctx || !ctx.file) return "";
  if (ctx.start_line && ctx.end_line && ctx.selection !== undefined) {
    // Arabic first, so the label reads right in a right-to-left panel
    return ctx.start_line === ctx.end_line
      ? `السطر ${ctx.start_line} من ${ctx.file}`
      : `الأسطر ${ctx.start_line}–${ctx.end_line} من ${ctx.file}`;
  }
  return ctx.file;
}

module.exports = { buildContext, describeContext, samePath, isInside, relativePath, normalize, MAX_CHARS };
