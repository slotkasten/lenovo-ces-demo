/* Northwind Bank & Trust — booth demo front-end */
"use strict";

const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
const money = (n) => (n < 0 ? "-" : "") + "$" + Math.abs(Number(n)).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const CAT_ICON = {
  Groceries: "cart-outline", Dining: "silverware-fork-knife", Transport: "bus", Housing: "home-outline",
  Utilities: "flash-outline", Entertainment: "movie-outline", Income: "cash-plus", Health: "medical-bag",
  Shopping: "shopping-outline", Transfer: "bank-transfer", Interest: "percent-outline", Adjustment: "alert-outline",
};
const ACCT_ICON = { checking: "checkbook", savings: "piggy-bank-outline", money_market: "chart-line", business: "office-building-outline" };

const state = { session: null, status: null, role: "customer", guardrails: false };

/* ---------- API ---------- */
async function api(path, opts) {
  const res = await fetch(path, Object.assign({ headers: { "Content-Type": "application/json" } }, opts));
  if (!res.ok && res.status !== 403 && res.status !== 404) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) {}
    throw new Error(detail);
  }
  return res.json();
}

/* ---------- session + header ---------- */
function applySession(s) {
  state.session = s; state.role = s.role; state.guardrails = s.guardrails;
  const c = s.customer;
  $("#assistant-title").textContent = s.assistant_name;
  if (c) {
    $("#avatar").textContent = (c.first_name[0] + c.last_name[0]).toUpperCase();
    $("#who-name").textContent = c.first_name + " " + c.last_name;
    $("#who-sub").textContent = (c.segment || "retail") + " banking";
  } else {
    $("#avatar").textContent = "!"; $("#who-name").textContent = "Records unavailable"; $("#who-sub").textContent = "—";
  }
  const rt = $("#role-tag");
  rt.textContent = s.role; rt.classList.toggle("admin", s.role === "admin");
  renderSuggests(s.suggested_prompts || []);
  updateGuardChip();
}

function updateGuardChip() {
  const on = state.guardrails;
  $("#st-guard").textContent = on ? "On" : "Off";
  $("#dot-guard").className = "dot " + (on ? "ok" : "warn");
}

/* ---------- status bar ---------- */
async function loadStatus() {
  try {
    const st = await api("/api/status");
    state.status = st;
    const db = st.database.ok;
    $("#dot-db").className = "dot " + (db ? "ok" : "bad");
    $("#st-db").textContent = db ? "Connected" : "Error";
    const llm = st.llm;
    $("#dot-llm").className = "dot " + (llm.enabled ? (llm.available ? "ok" : "warn") : "");
    $("#st-llm").textContent = llm.enabled ? (llm.available ? ("LLM: " + (llm.model || "on")) : "LLM: offline") : "LLM: templates";
    state.guardrails = st.guardrails; updateGuardChip();
  } catch (e) { $("#st-db").textContent = "Error"; $("#dot-db").className = "dot bad"; }
}

/* ---------- accounts + activity ---------- */
async function loadAccounts() {
  const body = $("#accounts-body"), act = $("#activity-body");
  try {
    const data = await api("/api/me/accounts");
    if (data.state === "missing") {
      body.innerHTML = ""; body.appendChild(dataLostCard(data.missing_table));
      act.innerHTML = ""; act.appendChild(el("div", "empty", '<span class="mdi mdi-database-off-outline"></span> Transaction data is unavailable.'));
      $("#acct-sub").textContent = "";
      return;
    }
    body.innerHTML = "";
    let total = 0;
    data.accounts.forEach((a) => {
      total += Number(a.balance);
      const row = el("div", "acct");
      row.innerHTML =
        `<div class="acct-icon"><span class="mdi mdi-${ACCT_ICON[a.account_type] || "wallet-outline"}"></span></div>
         <div class="acct-main"><div class="nick">${esc(a.nickname)}</div><div class="num">${esc(a.masked_number)}</div></div>
         <div class="acct-bal">${money(a.balance)}<small>${esc(a.status)}</small></div>`;
      body.appendChild(row);
    });
    const tr = el("div", "total-row");
    tr.innerHTML = `<span class="label">Total relationship balance</span><span class="val">${money(total)}</span>`;
    body.appendChild(tr);
    $("#acct-sub").textContent = data.accounts.length + " account" + (data.accounts.length === 1 ? "" : "s");

    act.innerHTML = "";
    if (!data.transactions.length) { act.appendChild(el("div", "empty", "No recent transactions.")); }
    data.transactions.forEach((t) => {
      const neg = Number(t.amount) < 0;
      const row = el("div", "txn");
      const d = new Date(t.posted_at);
      row.innerHTML =
        `<div class="txn-cat"><span class="mdi mdi-${CAT_ICON[t.category] || "circle-small"}"></span></div>
         <div class="txn-desc">${esc(t.description)}<small>${esc(t.nickname)} · ${d.toLocaleDateString("en-US", { month: "short", day: "numeric" })}</small></div>
         <div class="txn-amt ${neg ? "neg" : "pos"}">${money(t.amount)}</div>`;
      act.appendChild(row);
    });
  } catch (e) {
    body.innerHTML = `<div class="empty"><span class="mdi mdi-alert-circle-outline"></span> ${esc(e.message)}</div>`;
  }
}

function dataLostCard(table) {
  const d = el("div", "sec-banner critical");
  d.innerHTML = `<span class="mdi mdi-database-alert-outline"></span>
    <span><b>Banking data unavailable</b>The table <code>${esc(table)}</code> no longer exists in the database.
    Restore the application from a Veeam Kasten recovery point to bring balances and history back.</span>`;
  return d;
}

/* ---------- personas ---------- */
async function loadPersonas() {
  const sel = $("#persona");
  try {
    const data = await api("/api/customers");
    sel.innerHTML = "";
    if (data.state === "missing") { sel.innerHTML = '<option>— records unavailable —</option>'; sel.disabled = true; return; }
    sel.disabled = false;
    data.customers.forEach((c) => {
      const o = el("option"); o.value = c.id;
      o.textContent = `${c.first_name} ${c.last_name} · ${c.segment}`;
      sel.appendChild(o);
    });
    if (state.session && state.session.customer) sel.value = state.session.customer.id;
  } catch (e) { sel.innerHTML = '<option>— error —</option>'; }
}

/* ---------- chat ---------- */
function renderSuggests(list) {
  const box = $("#suggests"); box.innerHTML = "";
  list.forEach((p) => {
    const s = el("span", "s", esc(p));
    s.addEventListener("click", () => { $("#chat-input").value = p; sendMessage(p); });
    box.appendChild(s);
  });
}

function addMessage(sender, text, opts = {}) {
  const chat = $("#chat");
  const m = el("div", "msg " + (sender === "user" ? "user" : "bot"));
  const badge = el("div", "mbadge");
  badge.innerHTML = `<span class="mdi mdi-${sender === "user" ? "account" : "robot-happy-outline"}"></span>`;
  const bubble = el("div", "bubble " + (opts.kind && opts.kind !== "text" ? opts.kind : ""));
  bubble.textContent = text;
  if (opts.sources && opts.sources.length) {
    const sb = el("div", "sources");
    opts.sources.forEach((s) => sb.appendChild(el("span", "chip",
      `<span class="mdi mdi-file-document-outline"></span> ${esc(s.title)}`)));
    bubble.appendChild(sb);
  }
  if (opts.security_event) bubble.appendChild(securityBanner(opts.security_event));
  m.appendChild(badge); m.appendChild(bubble);
  chat.appendChild(m); chat.scrollTop = chat.scrollHeight;
  return m;
}

function securityBanner(ev) {
  const blocked = ev.outcome === "refused";
  const b = el("div", "sec-banner " + (blocked ? "blocked" : "critical"));
  const icon = blocked ? "shield-check" : "shield-alert";
  b.innerHTML = `<span class="mdi mdi-${icon}"></span>
    <span><b>${esc(ev.label)} ${blocked ? "— blocked" : "— executed"}</b>${esc(ev.detail || "")}</span>`;
  return b;
}

let pending = false;
async function sendMessage(text) {
  if (pending) return;
  text = (text || $("#chat-input").value).trim();
  if (!text) return;
  pending = true;
  addMessage("user", text);
  $("#chat-input").value = "";
  const thinking = addMessage("bot", "…", {});
  thinking.querySelector(".bubble").innerHTML = '<span class="mdi mdi-loading mdi-spin"></span> Thinking…';
  try {
    const r = await api("/api/chat", { method: "POST", body: JSON.stringify({ message: text }) });
    thinking.remove();
    addMessage("bot", r.reply, { kind: r.kind, sources: r.sources, security_event: r.security_event });
    await afterChat(r);
  } catch (e) {
    thinking.remove();
    addMessage("bot", "Sorry, something went wrong: " + e.message, { kind: "error" });
  } finally { pending = false; }
}

async function afterChat(r) {
  // Reflect side effects of the demo scenarios.
  if (r.security_event && r.security_event.outcome === "executed") {
    showStrip("danger", r.security_event.label + " — " + (r.security_event.detail || ""));
  }
  if (r.role_changed && r.role === "admin") { state.role = "admin"; await refreshSession(); openAdmin(); }
  if (["destructive", "tampering", "exfiltration"].includes(r.kind)) { await loadAccounts(); }
  await loadAudit();
}

/* ---------- strip banner ---------- */
function showStrip(kind, text) {
  const s = $("#strip");
  s.className = "banner-strip show " + kind;
  $("#strip-icon").className = "mdi mdi-" + (kind === "danger" ? "alert-octagon" : "shield-check");
  $("#strip-text").textContent = text;
}
$("#strip-dismiss").addEventListener("click", () => $("#strip").className = "banner-strip");

/* ---------- audit ---------- */
async function loadAudit() {
  const body = $("#audit-body");
  try {
    const data = await api("/api/audit");
    if (!data.events.length) {
      body.innerHTML = '<div class="empty"><span class="mdi mdi-check-circle-outline"></span> No security events recorded.</div>';
      return;
    }
    body.innerHTML = "";
    data.events.forEach((ev) => {
      const item = el("div", "audit-item");
      const d = new Date(ev.created_at);
      item.innerHTML = `<div class="sev ${esc(ev.severity)}"></div>
        <div><div>${esc(ev.event.replace(/_/g, " "))}</div>
        <div class="meta">${esc(ev.detail || "")} · ${d.toLocaleString()}</div></div>`;
      body.appendChild(item);
    });
  } catch (e) {}
}

/* ---------- admin console ---------- */
async function openAdmin() {
  const modal = $("#admin-modal"), body = $("#admin-body");
  modal.classList.add("show");
  body.innerHTML = '<div class="empty"><span class="mdi mdi-loading mdi-spin"></span> Loading unmasked customer records…</div>';
  try {
    const data = await api("/api/admin/customers");
    if (data.state === "missing") { body.innerHTML = ""; body.appendChild(dataLostCard(data.missing_table)); return; }
    const rows = data.customers.map((c) => `<tr>
      <td>${esc(c.first_name)} ${esc(c.last_name)}</td>
      <td><code class="ssn">${esc(c.ssn)}</code></td>
      <td>${esc(c.email)}</td><td>${esc(c.phone)}</td>
      <td>${esc(c.city)}, ${esc(c.state)}</td>
      <td>${c.account_count}</td><td>${money(c.total_balance)}</td></tr>`).join("");
    body.innerHTML = `<div class="sec-banner critical"><span class="mdi mdi-shield-alert"></span>
      <span><b>Elevated access granted via prompt injection</b>Every record below is shown unmasked, including
      full Social Security numbers. A customer assistant should never expose this. Restore and re-enforce the
      masking policy from a Veeam Kasten recovery point.</span></div>
      <div class="tablewrap" style="margin-top:14px"><table class="data"><thead><tr>
      <th>Name</th><th>SSN</th><th>Email</th><th>Phone</th><th>Location</th><th>Accts</th><th>Total balance</th>
      </tr></thead><tbody>${rows}</tbody></table></div>`;
  } catch (e) {
    body.innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}
$("#admin-close").addEventListener("click", (e) => { e.preventDefault(); $("#admin-modal").classList.remove("show"); });
$("#admin-modal").addEventListener("click", (e) => { if (e.target.id === "admin-modal") e.target.classList.remove("show"); });

/* ---------- session helpers ---------- */
async function refreshSession() { applySession(await api("/api/session")); }

async function loadHistory() {
  try {
    const data = await api("/api/history");
    $("#chat").innerHTML = "";
    if (!data.messages.length) {
      addMessage("bot", `Welcome to ${state.session.bank_name}. I'm ${state.session.assistant_name}, your virtual banking assistant. I can help with balances, transactions, fees and account services. How can I help you today?`, {});
      return;
    }
    data.messages.forEach((m) => addMessage(m.sender === "user" ? "user" : "bot", m.content, { kind: m.kind }));
  } catch (e) {}
}

/* ---------- wire up ---------- */
$("#composer").addEventListener("submit", (e) => { e.preventDefault(); sendMessage(); });
$("#persona").addEventListener("change", async (e) => {
  applySession(await api("/api/session/customer", { method: "POST", body: JSON.stringify({ customer_id: Number(e.target.value) }) }));
  $("#strip").className = "banner-strip";
  await Promise.all([loadAccounts(), loadHistory(), loadAudit(), loadStatus()]);
});
$("#btn-reset").addEventListener("click", async () => {
  applySession(await api("/api/session/reset", { method: "POST" }));
  $("#strip").className = "banner-strip"; $("#admin-modal").classList.remove("show");
  await Promise.all([loadPersonas(), loadAccounts(), loadHistory(), loadAudit(), loadStatus()]);
});
$("#refresh-audit").addEventListener("click", (e) => { e.preventDefault(); loadAudit(); });

/* ---------- boot ---------- */
(async function init() {
  await refreshSession();
  await Promise.all([loadStatus(), loadPersonas(), loadAccounts(), loadHistory(), loadAudit()]);
})();
