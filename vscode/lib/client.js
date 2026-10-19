"use strict";

// Talks to the local YOUSEF AI server (start.bat). Node's http module only, no dependencies.
const http = require("http");
const https = require("https");

class OfflineError extends Error {
  constructor(url) {
    super(`يوسف غير متصل على ${url}. شغّل start.bat ثم حاول مجددًا.`);
    this.name = "OfflineError";
    this.offline = true;
  }
}

class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

class YousefClient {
  constructor(getBaseUrl, timeoutMs = 15000) {
    this.getBaseUrl = typeof getBaseUrl === "function" ? getBaseUrl : () => getBaseUrl;
    this.timeoutMs = timeoutMs;
  }

  request(method, path, body) {
    const base = String(this.getBaseUrl() || "http://127.0.0.1:8765").replace(/\/+$/, "");
    const url = new URL(base + path);
    const lib = url.protocol === "https:" ? https : http;
    const data = body === undefined ? null : Buffer.from(JSON.stringify(body), "utf8");
    const headers = { "X-Yousef-Client": "1", Accept: "application/json" };
    if (data) {
      headers["Content-Type"] = "application/json";
      headers["Content-Length"] = String(data.length);
    }
    return new Promise((resolve, reject) => {
      const req = lib.request(url, { method, headers, timeout: this.timeoutMs }, (res) => {
        const chunks = [];
        res.on("data", (c) => chunks.push(c));
        res.on("end", () => {
          let payload = {};
          try { payload = JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}"); } catch (e) { payload = {}; }
          if (res.statusCode >= 200 && res.statusCode < 300) resolve(payload);
          else reject(new ApiError(res.statusCode, payload.error || `HTTP ${res.statusCode}`));
        });
      });
      req.on("timeout", () => req.destroy(new Error("timeout")));
      req.on("error", (err) => {
        if (["ECONNREFUSED", "ECONNRESET", "EHOSTUNREACH", "ENOTFOUND"].includes(err.code) || err.message === "timeout") {
          reject(new OfflineError(base));
        } else {
          reject(err);
        }
      });
      if (data) req.write(data);
      req.end();
    });
  }

  status() { return this.request("GET", "/api/status"); }
  workspaces() { return this.request("GET", "/api/workspaces").then((r) => r.workspaces); }
  addWorkspace(path) { return this.request("POST", "/api/workspaces", { path }); }
  createTask(body) { return this.request("POST", "/api/tasks", body).then((r) => r.task); }
  task(id) { return this.request("GET", `/api/tasks/${id}`).then((r) => r.task); }
  cancel(id) { return this.request("POST", `/api/tasks/${id}/cancel`, {}); }
  decide(stepId, approve, scope = "all", exclude = []) {
    return this.request("POST", `/api/steps/${stepId}/decision`, { approve, scope, exclude });
  }
  change(id) { return this.request("GET", `/api/changes/${id}`).then((r) => r.change); }
  newConversation() { return this.request("POST", "/api/conversation/new", {}); }
}

module.exports = { YousefClient, OfflineError, ApiError };
