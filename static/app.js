"use strict";
/* Vanilla JS SPA: login → fetch /api/quota → render card grid. No build step.
   The session cookie is HttpOnly (set by the server); we store nothing sensitive
   client-side — on reload we just re-GET /api/quota and 401 redirects to login. */

const $ = (id) => document.getElementById(id);
const loginView = $("login-view");
const dashView = $("dash-view");
const grid = $("grid");
const banner = $("banner");
const lastUpdated = $("last-updated");

const STATUS_TEXT = { ok: "正常", error: "错误", unsupported: "暂不支持" };
const REFRESH_MS = 5 * 60 * 1000;

const show = (el) => el.classList.remove("hidden");
const hide = (el) => el.classList.add("hidden");

function showLogin() {
  hide(dashView);
  show(loginView);
  $("password").focus();
}
function showDash() {
  hide(loginView);
  show(dashView);
}

function escapeHtml(s) {
  return String(s).replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

function levelFor(percent) {
  if (percent == null) return "neutral";
  if (percent >= 90) return "danger";
  if (percent >= 70) return "warn";
  return "good";
}

function fmtNum(v, unit) {
  if (v == null) return "—";
  if (unit === "CNY" || unit === "USD") return Number(v).toFixed(2);
  return String(v);
}

function cardHTML(r) {
  const updated = new Date(r.updated_at).toLocaleString("zh-CN");
  const head = `<div class="head"><span class="label">${escapeHtml(r.label)}</span>` +
    `<span class="head-right">` +
    `<span class="badge b-${r.status}">${STATUS_TEXT[r.status] || r.status}</span>` +
    `<button class="card-refresh" data-channel="${escapeHtml(r.id)}" title="刷新此渠道" aria-label="刷新 ${escapeHtml(r.label)}">↻</button>` +
    `</span></div>`;

  if (r.status === "unsupported") {
    return `<div class="card muted">${head}
      <div class="metric">—</div>
      <div class="sub">该渠道无官方额度查询接口</div>
      <div class="foot">${updated}</div></div>`;
  }
  if (r.status === "error") {
    return `<div class="card error">${head}
      <div class="metric">—</div>
      <div class="sub err-text">${escapeHtml(r.error || "未知错误")}</div>
      <div class="foot">${updated}</div></div>`;
  }

  // ok
  const level = levelFor(r.percent);
  let primary, sub;
  if (r.balance != null && r.unit !== "%") {
    primary = `${fmtNum(r.balance, r.unit)} <span class="unit">${r.unit || ""}</span>`;
    sub = r.total != null && r.used != null
      ? `已用 ${fmtNum(r.used, r.unit)} / ${fmtNum(r.total, r.unit)} ${r.unit || ""}`
      : "—";
  } else if (r.percent != null) {
    primary = `${Number(r.percent).toFixed(1)}<span class="unit">% 已用</span>`;
    sub = r.reset_time ? `下次重置 ${new Date(r.reset_time).toLocaleString("zh-CN")}` : "—";
  } else {
    primary = "—";
    sub = "—";
  }
  const bar = r.percent != null
    ? `<div class="bar"><div class="bar-fill ${level}" style="width:${Math.min(100, r.percent)}%"></div></div>`
    : "";
  return `<div class="card ok ${level}">${head}
    <div class="metric">${primary}</div>
    ${bar}
    <div class="sub">${sub}</div>
    <div class="foot">${updated}</div></div>`;
}

function render(results) {
  if (!results.length) {
    grid.innerHTML = `<div class="empty">暂无已启用渠道</div>`;
    return;
  }
  grid.innerHTML = results.map(cardHTML).join("");
  lastUpdated.textContent = "更新于 " + new Date().toLocaleString("zh-CN");
}

async function fetchQuota(refresh = false) {
  try {
    hide(banner);
    grid.innerHTML = `<div class="empty">加载中…</div>`;
    const resp = await fetch(refresh ? "/api/quota?refresh=1" : "/api/quota", {
      credentials: "same-origin",
    });
    if (resp.status === 401) {
      showLogin();
      return;
    }
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    render(await resp.json());
  } catch (e) {
    banner.textContent = "获取额度失败：" + e.message;
    show(banner);
    grid.innerHTML = "";
  }
}

async function loginSubmit(e) {
  e.preventDefault();
  const btn = $("login-btn");
  const err = $("login-error");
  btn.disabled = true;
  hide(err);
  try {
    const resp = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ password: $("password").value }),
    });
    if (resp.ok) {
      $("password").value = "";
      showDash();
      await fetchQuota(true);
    } else if (resp.status === 429) {
      err.textContent = "尝试过多，稍后再试";
      show(err);
    } else {
      err.textContent = "密码错误";
      show(err);
    }
  } catch (e) {
    err.textContent = "网络错误：" + e.message;
    show(err);
  } finally {
    btn.disabled = false;
  }
}

async function refreshCard(channelId, btn) {
  btn.disabled = true;
  btn.classList.add("loading");
  const original = btn.textContent;
  btn.textContent = "…";
  try {
    const resp = await fetch(`/api/quota/${encodeURIComponent(channelId)}`, {
      credentials: "same-origin",
    });
    if (resp.status === 401) {
      showLogin();
      return;
    }
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const result = await resp.json();
    const card = btn.closest(".card");
    if (!card) return;
    const wrapper = document.createElement("div");
    wrapper.innerHTML = cardHTML(result);
    const newCard = wrapper.firstElementChild;
    if (newCard) card.replaceWith(newCard);
  } catch (e) {
    btn.textContent = "×";
    setTimeout(() => { btn.textContent = original; }, 1500);
    console.error("refresh failed:", e);
  } finally {
    btn.disabled = false;
    btn.classList.remove("loading");
    if (btn.textContent === "…") btn.textContent = original;
  }
}

// wire up
$("login-form").addEventListener("submit", loginSubmit);
grid.addEventListener("click", (e) => {
  const btn = e.target.closest(".card-refresh");
  if (btn && !btn.disabled) refreshCard(btn.dataset.channel, btn);
});
$("logout-btn").addEventListener("click", async () => {
  await fetch("/api/logout", { method: "POST", credentials: "same-origin" });
  showLogin();
});

// boot: probe auth
(async () => {
  try {
    const resp = await fetch("/api/quota", { credentials: "same-origin" });
    if (resp.ok) {
      showDash();
      render(await resp.json());
      return;
    }
  } catch {
    /* fall through to login */
  }
  showLogin();
})();

// auto-refresh every 5 minutes while viewing the dashboard
setInterval(() => {
  if (!dashView.classList.contains("hidden")) fetchQuota(false);
}, REFRESH_MS);
