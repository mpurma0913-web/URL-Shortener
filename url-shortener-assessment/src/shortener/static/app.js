"use strict";
const el = (id) => document.getElementById(id);
const lines = (id) => el(id).value.split("\n").map((s) => s.trim()).filter(Boolean);
let workflowVersion = null;
async function request(path, {method = "GET", body, reviewer = false, headers = {}} = {}) {
  const key = el(reviewer ? "reviewer" : "key").value;
  const response = await fetch(path, {
    method, credentials: "omit", redirect: "error",
    headers: {"Content-Type": "application/json", ...(key ? {"X-API-Key": key} : {}), ...headers},
    ...(body === undefined ? {} : {body: JSON.stringify(body)})
  });
  const data = response.status === 204 ? null : await response.json();
  if (!response.ok) throw new Error(`${response.status}: ${data.error?.message || "Request failed"} (request ${response.headers.get("x-request-id")})`);
  return data;
}
function show(id, data, error = false) {
  el(id).textContent = typeof data === "string" ? data : JSON.stringify(data, null, 2);
  el(id).className = error ? "error" : "";
}
function bind(id, event, output, fn) {
  el(id).addEventListener(event, async (e) => {
    e.preventDefault();
    try { await fn(); } catch (err) { show(output, err.message, true); }
  });
}
bind("create-form", "submit", "created", async () => {
  const body = {url: el("url").value};
  if (el("expiry").value.trim()) body.expires_at = el("expiry").value.trim();
  const headers = el("idem").value ? {"Idempotency-Key": el("idem").value} : {};
  const link = await request("/api/v1/links", {method: "POST", body, headers});
  el("code").value = link.code;
  el("created").replaceChildren();
  const p = document.createElement("p");
  p.textContent = "Short link: ";
  const a = document.createElement("a");
  // The API's absolute URL is configuration-driven. Use the origin serving
  // this console so the link still works when the app is reached through a
  // different hostname or port (for example localhost or a Docker mapping).
  const shortUrl = new URL(`/r/${encodeURIComponent(link.code)}`, window.location.origin).href;
  a.textContent = shortUrl; a.href = shortUrl; a.target = "_blank"; a.rel = "noopener noreferrer";
  p.appendChild(a); el("created").appendChild(p);
});
bind("stats-form", "submit", "stats", async () => show("stats", await request(`/api/v1/links/${encodeURIComponent(el("code").value)}/stats`)));
bind("revoke", "click", "stats", async () => {
  if (!window.confirm("Revoke this link? Existing redirects will stop working.")) return;
  await request(`/api/v1/links/${encodeURIComponent(el("code").value)}`, {method: "DELETE"});
  show("stats", "Link revoked; redirect now returns 410.");
});
bind("health", "click", "status", async () => show("status", await request("/health/ready")));
bind("workflow-form", "submit", "workflow", async () => {
  const run = await request("/api/v1/workflows", {method: "POST", body: {
    scenario: el("scenario").value, requirement: el("requirement").value,
    acceptance_criteria: lines("criteria"), ambiguities: lines("ambiguities")
  }});
  el("run-id").value = run.id; workflowVersion = run.version; show("workflow", run);
});
async function inspect() {
  const id = encodeURIComponent(el("run-id").value);
  const run = await request(`/api/v1/workflows/${id}`);
  workflowVersion = run.version;
  const audit = await request(`/api/v1/workflows/${id}/audit`);
  show("workflow", {run, audit});
}
bind("inspect-form", "submit", "workflow", inspect);
async function decide(approved) {
  if (workflowVersion === null) throw new Error("Inspect the workflow first to obtain its current version.");
  const body = {expected_version: workflowVersion, gate: el("gate").value,
    approve: approved, reason: el("reason").value, acceptance_criteria: lines("resolved-criteria"), evidence_refs: lines("impact-refs")};
  const run = await request(`/api/v1/workflows/${encodeURIComponent(el("run-id").value)}/decisions`, {method: "POST", body, reviewer: true});
  workflowVersion = run.version; show("workflow", run);
}
bind("approve-form", "submit", "workflow", async () => decide(true));
bind("reject", "click", "workflow", async () => decide(false));
el("clear").addEventListener("click", () => {
  el("key").value = ""; el("reviewer").value = "";
  for (const id of ["created", "stats", "workflow", "status"]) show(id, "");
  workflowVersion = null;
});
