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

// Parse provider-specific reset hint into a Date. Returns null on garbage.
//   10+ digit string   → Unix timestamp in seconds (e.g. "1784886200")
//   1-9 digit string   → relative seconds from now  (e.g. codex reset_after_seconds "3600")
//   ISO 8601 duration  → "PT88H45M" / "PT144H6M"   (Antigravity resetTime)
//   ISO 8601 datetime  → "2026-09-14T10:30:00Z"
function parseResetTime(rt) {
  if (!rt) return null;
  if (/^\d{10,}$/.test(rt)) {
    const d = new Date(parseInt(rt, 10) * 1000);
    return isNaN(d.getTime()) ? null : d;
  }
  if (/^\d{1,9}$/.test(rt)) {
    return new Date(Date.now() + parseInt(rt, 10) * 1000);
  }
  if (/^PT/i.test(rt)) {
    const m = /^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$/.exec(rt);
    if (m) {
      const ms =
        (parseInt(m[1] || "0", 10) * 3600 +
          parseInt(m[2] || "0", 10) * 60 +
          parseInt(m[3] || "0", 10)) *
        1000;
      return new Date(Date.now() + ms);
    }
    return null;
  }
  const d = new Date(rt);
  return isNaN(d.getTime()) ? null : d;
}

// Render "Xh Ym" / "soon" for a reset hint (Antigravity CLI style).
function fmtUntilReset(rt) {
  const d = parseResetTime(rt);
  if (!d) return "";
  const ms = d - Date.now();
  if (ms <= 0) return "soon";
  const h = Math.floor(ms / 3600000);
  const m = Math.floor((ms % 3600000) / 60000);
  return `${h}h ${m}m`;
}

// Format reset hint as an absolute UTC date (YYYY-MM-DD). Used for the Antigravity
// weekly bucket row — the API gives an ISO 8601 timestamp; we want the date only,
// not "Xh Ym" relative phrasing. UTC matches the API's reset timestamp semantics.
function fmtResetDate(rt) {
  const d = parseResetTime(rt);
  if (!d) return "";
  const y = d.getUTCFullYear();
  const m = String(d.getUTCMonth() + 1).padStart(2, "0");
  const day = String(d.getUTCDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
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

  // ok + groups (Antigravity-style multi-quota card)
  if (Array.isArray(r.groups) && r.groups.length) {
    return groupCardHTML(r, head, updated);
  }

  // ok + single flat quota (existing behavior)
  const level = levelFor(r.percent);
  let primary, sub;
  if (r.balance != null && r.unit !== "%") {
    primary = `${fmtNum(r.balance, r.unit)} <span class="unit">${r.unit || ""}</span>`;
    sub = r.total != null && r.used != null
      ? `已用 ${fmtNum(r.used, r.unit)} / ${fmtNum(r.total, r.unit)} ${r.unit || ""}`
      : "—";
  } else if (r.percent != null) {
    primary = `${Number(r.percent).toFixed(1)}<span class="unit">% 已用</span>`;
    const resetDate = fmtResetDate(r.reset_time);
    sub = resetDate ? `重置时间 ${resetDate}` : "—";
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

// Multi-group card (Antigravity CLI /usage style).
// r.groups: [{ label, models: [...], buckets?: [...], percent?, reset_time? }]
//   - New Antigravity 2.x shape: g.buckets has one entry per time window
//     (e.g. Weekly + 5h Session); each bucket gets its own bar.
//   - Legacy shape (no buckets): g.percent/g.reset_time drives a single bar.
// Top-level r.percent drives the card border color (max USED across groups).
function groupCardHTML(r, head, updated) {
  const cardLevel = levelFor(r.percent);
  const groupsHTML = r.groups.map((g) => {
    const modelsTxt = g.models && g.models.length
      ? `Models within this group: ${g.models.map(escapeHtml).join(", ")}`
      : "";
    let body = "";
    if (Array.isArray(g.buckets) && g.buckets.length) {
      body = g.buckets.map(bucketRowHTML).join("");
    } else if (g.percent != null) {
      body = bucketRowHTML({
        label: "",
        percent: g.percent,
        reset_time: g.reset_time,
        description: null,
      });
    }
    return `<div class="group">
      <div class="group-head">
        <span class="group-label">${escapeHtml(g.label.toUpperCase())} MODELS</span>
      </div>
      ${modelsTxt ? `<div class="group-models">${modelsTxt}</div>` : ""}
      ${body}
    </div>`;
  }).join("");
  return `<div class="card ok ${cardLevel}">${head}
    <div class="groups">${groupsHTML}</div>
    <div class="foot">${updated}</div></div>`;
}

// One bucket row: optional label + bar + sub-text (重置时间 YYYY-MM-DD).
// Percent is already shown on the bar (group-percent), so the sub-text just
// carries the reset date — same wording as the single-bar card path.
function bucketRowHTML(b) {
  const lvl = levelFor(b.percent);
  const resetDate = fmtResetDate(b.reset_time);
  const labelHTML = b.label ? `<div class="bucket-label">${escapeHtml(b.label)}</div>` : "";
  return `<div class="bucket">
    ${labelHTML}
    <div class="group-bar-row">
      <div class="bar"><div class="bar-fill ${lvl}" style="width:${Math.min(100, b.percent)}%"></div></div>
      <span class="group-percent">${Number(b.percent).toFixed(2)}%</span>
    </div>
    <div class="sub group-sub">${resetDate ? `重置时间 ${escapeHtml(resetDate)}` : "—"}</div>
  </div>`;
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
