// =============================================================================
// CAULDRA PLATFORM OWNER CONTROL PANEL - frontend
// Completely separate bundle from frontend/js/app.js (the customer app).
// Talks ONLY to /api/platform/* endpoints, which independently re-verify a
// platform_owner-scoped bearer token on every request (see backend/main.py's
// get_platform_owner()) - this file never assumes the hidden URL alone is
// what's protecting anything.
// =============================================================================

const API_URL = location.origin;
const TOKEN_KEY = "cauldra_platform_token";
const MFA_TOKEN_KEY = "cauldra_platform_mfa_token";
const EMAIL_KEY = "cauldra_platform_owner_email";

let accessToken = null;
try { accessToken = sessionStorage.getItem(TOKEN_KEY) || null; } catch (_) {}

function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}
function fmtMoney(n, currency) {
    if (n === null || n === undefined) return "—";
    const num = Number(n);
    const symbol = currency === "USD" ? "$" : "₦";
    return symbol + num.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function fmtInt(n) {
    if (n === null || n === undefined) return "—";
    return Number(n).toLocaleString();
}
function fmtDate(iso) {
    if (!iso) return "—";
    try {
        const d = new Date(iso);
        return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
    } catch (_) { return iso; }
}
function fmtDateTime(iso) {
    if (!iso) return "—";
    try {
        const d = new Date(iso);
        return d.toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
    } catch (_) { return iso; }
}
function get(obj, path, fallback) {
    const parts = path.split(".");
    let node = obj;
    for (const p of parts) { if (node == null) return fallback; node = node[p]; }
    return node === undefined ? fallback : node;
}

// ---------------------------------------------------------------- API layer
async function apiFetch(path, opts = {}) {
    const headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
    if (accessToken) headers["Authorization"] = `Bearer ${accessToken}`;
    const res = await fetch(`${API_URL}${path}`, Object.assign({}, opts, { headers }));
    if (res.status === 401 || res.status === 403) {
        // A revoked/expired/wrong-scope token - the backend is the actual
        // authority here; this just reacts to what it already decided.
        if (path.startsWith("/api/platform/") && !path.includes("/auth/")) {
            signOutLocally();
            showLogin();
            showLoginError("Your session has expired. Please sign in again.");
        }
    }
    let data = null;
    try { data = await res.json(); } catch (_) { data = null; }
    if (!res.ok) {
        const msg = (data && data.detail) ? (typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail)) : `Request failed (${res.status})`;
        throw new Error(msg);
    }
    return data;
}
const apiGet = (path) => apiFetch(path, { method: "GET", cache: "no-store" });
const apiPost = (path, body) => apiFetch(path, { method: "POST", body: body !== undefined ? JSON.stringify(body) : undefined });
const apiPut = (path, body) => apiFetch(path, { method: "PUT", body: JSON.stringify(body) });

// ---------------------------------------------------------------- Auth flow
function showLogin() {
    document.getElementById("login-screen").classList.remove("hidden");
    document.getElementById("app-shell").classList.add("hidden");
}
function showApp() {
    document.getElementById("login-screen").classList.add("hidden");
    document.getElementById("app-shell").classList.remove("hidden");
}
function showLoginError(msg) {
    const el = document.getElementById("login-status");
    el.textContent = msg;
    el.className = "mb-3 px-3 py-2 rounded-xl border text-[11px] border-danger/30 bg-danger/10 text-danger";
    el.classList.remove("hidden");
}
function clearLoginError() {
    document.getElementById("login-status").classList.add("hidden");
}
function signOutLocally() {
    accessToken = null;
    try { sessionStorage.removeItem(TOKEN_KEY); sessionStorage.removeItem(MFA_TOKEN_KEY); sessionStorage.removeItem(EMAIL_KEY); } catch (_) {}
}

document.getElementById("login-form-password").addEventListener("submit", async (e) => {
    e.preventDefault();
    clearLoginError();
    const btn = document.getElementById("login-submit-btn");
    btn.disabled = true;
    try {
        const email = document.getElementById("login-email").value.trim();
        const password = document.getElementById("login-password").value;
        const resp = await apiPost("/api/platform/auth/login", { email, password });
        if (resp && resp.mfa_required) {
            try { sessionStorage.setItem(MFA_TOKEN_KEY, resp.mfa_token); } catch (_) {}
            document.getElementById("login-form-password").classList.add("hidden");
            document.getElementById("login-form-mfa").classList.remove("hidden");
            document.getElementById("login-mfa-code").value = "";
            setTimeout(() => document.getElementById("login-mfa-code").focus(), 30);
        }
    } catch (err) {
        showLoginError(err.message || "Sign-in failed.");
    } finally {
        btn.disabled = false;
    }
});

document.getElementById("login-form-mfa").addEventListener("submit", async (e) => {
    e.preventDefault();
    clearLoginError();
    const btn = document.getElementById("mfa-submit-btn");
    btn.disabled = true;
    try {
        let mfaToken = null;
        try { mfaToken = sessionStorage.getItem(MFA_TOKEN_KEY); } catch (_) {}
        if (!mfaToken) { showLoginError("Your sign-in attempt has expired. Please start again."); backToPasswordStep(); return; }
        const code = document.getElementById("login-mfa-code").value.trim();
        const resp = await apiPost("/api/platform/auth/verify-mfa", { mfa_token: mfaToken, code });
        accessToken = resp.access_token;
        const ownerEmail = get(resp, "owner.email", "");
        try {
            sessionStorage.setItem(TOKEN_KEY, accessToken);
            sessionStorage.removeItem(MFA_TOKEN_KEY);
            sessionStorage.setItem(EMAIL_KEY, ownerEmail);
        } catch (_) {}
        document.getElementById("sidebar-owner-email").textContent = ownerEmail;
        showApp();
        navigateTo(location.hash.replace("#", "") || "overview");
    } catch (err) {
        showLoginError(err.message || "Verification failed.");
    } finally {
        btn.disabled = false;
    }
});

function backToPasswordStep() {
    document.getElementById("login-form-mfa").classList.add("hidden");
    document.getElementById("login-form-password").classList.remove("hidden");
    try { sessionStorage.removeItem(MFA_TOKEN_KEY); } catch (_) {}
}
document.getElementById("mfa-back-btn").addEventListener("click", backToPasswordStep);

document.getElementById("sign-out-btn").addEventListener("click", async () => {
    try { await apiPost("/api/platform/auth/logout"); } catch (_) {}
    signOutLocally();
    showLogin();
    document.getElementById("login-form-mfa").classList.add("hidden");
    document.getElementById("login-form-password").classList.remove("hidden");
    document.getElementById("login-form-password").reset();
});

// ---------------------------------------------------------------- Nav / router
const VIEWS = ["overview", "businesses", "business-detail", "users", "subscriptions", "revenue", "ai-costs", "alerts", "system-health", "infrastructure"];
const NAV_TITLES = {
    overview: "Overview", businesses: "Businesses", "business-detail": "Business", users: "Users",
    subscriptions: "Subscriptions", revenue: "Revenue", "ai-costs": "AI & Costs", alerts: "Alerts",
    "system-health": "System Health", infrastructure: "Infrastructure",
};
let currentView = "overview";

function setActiveNav(view) {
    document.querySelectorAll(".nav-link").forEach(btn => {
        const active = btn.getAttribute("data-nav-link") === view;
        btn.classList.toggle("bg-primary/15", active);
        btn.classList.toggle("text-primary", active);
        btn.classList.toggle("font-semibold", active);
        btn.classList.toggle("text-textSec", !active);
        btn.classList.toggle("border", active);
        btn.classList.toggle("border-primary/25", active);
    });
}

async function navigateTo(view, param) {
    if (!VIEWS.includes(view)) view = "overview";
    currentView = view;
    document.querySelectorAll("[data-view]").forEach(el => el.classList.toggle("active", el.getAttribute("data-view") === view));
    document.getElementById("page-title").textContent = NAV_TITLES[view] || view;
    setActiveNav(view === "business-detail" ? "businesses" : view);
    closeMobileNav();
    location.hash = view + (param ? `/${param}` : "");
    clearGlobalError();
    try {
        if (view === "overview") await loadOverview();
        else if (view === "businesses") await loadBusinesses();
        else if (view === "business-detail") await loadBusinessDetail(param);
        else if (view === "users") await loadUsers();
        else if (view === "subscriptions") await loadSubscriptions();
        else if (view === "revenue") await loadRevenue();
        else if (view === "ai-costs") await loadAiCosts();
        else if (view === "alerts") await loadAlerts();
        else if (view === "system-health") await loadSystemHealth();
        else if (view === "infrastructure") await loadInfrastructure();
    } catch (err) {
        showGlobalError(err.message || "Failed to load this page.");
    }
    if (view !== "alerts") refreshAlertBadge();
}

document.querySelectorAll("[data-nav-link]").forEach(btn => {
    btn.addEventListener("click", () => navigateTo(btn.getAttribute("data-nav-link")));
});

function showGlobalError(msg) {
    const el = document.getElementById("global-error");
    el.textContent = msg;
    el.classList.remove("hidden");
}
function clearGlobalError() {
    document.getElementById("global-error").classList.add("hidden");
}

// mobile nav drawer
function openMobileNav() {
    document.getElementById("sidebar").classList.remove("-translate-x-full");
    document.getElementById("mobile-nav-overlay").classList.remove("hidden");
}
function closeMobileNav() {
    if (window.innerWidth < 768) document.getElementById("sidebar").classList.add("-translate-x-full");
    document.getElementById("mobile-nav-overlay").classList.add("hidden");
}
document.getElementById("mobile-menu-btn").addEventListener("click", openMobileNav);
document.getElementById("sidebar-close-btn").addEventListener("click", closeMobileNav);
document.getElementById("mobile-nav-overlay").addEventListener("click", closeMobileNav);
document.getElementById("refresh-btn").addEventListener("click", async () => {
    const btn = document.getElementById("refresh-btn");
    const icon = btn.querySelector("i");
    try {
        btn.disabled = true;
        if (icon) icon.classList.add("animate-spin");
        const param = currentView === "business-detail" ? lastBusinessDetailId : null;
        await navigateTo(currentView, param);
    } finally {
        btn.disabled = false;
        if (icon) icon.classList.remove("animate-spin");
    }
});

// =============================================================================
// OVERVIEW
// =============================================================================
async function loadOverview() {
    const data = await apiGet("/api/platform/overview");
    document.querySelectorAll("[data-ov]").forEach(el => {
        const val = get(data, el.getAttribute("data-ov"));
        el.textContent = (typeof val === "number") ? fmtInt(val) : (val ?? "—");
    });
    document.querySelectorAll("[data-ov-money]").forEach(el => {
        const val = get(data, el.getAttribute("data-ov-money"));
        el.textContent = val === null || val === undefined ? "—" : fmtMoney(val, "NGN");
    });
    document.querySelectorAll("[data-ov-money-usd]").forEach(el => {
        const val = get(data, el.getAttribute("data-ov-money-usd"));
        el.textContent = val === null || val === undefined ? "—" : fmtMoney(val, "USD");
    });
    document.getElementById("overview-generated-at").textContent = "Updated " + fmtDateTime(data.generated_at);
}

// =============================================================================
// BUSINESSES
// =============================================================================
let bizOffset = 0;
const PAGE_SIZE = 25;
let bizSearchTimer = null;

async function loadBusinesses() {
    const q = document.getElementById("biz-search").value.trim();
    const plan = document.getElementById("biz-plan-filter").value;
    const params = new URLSearchParams({ limit: PAGE_SIZE, offset: bizOffset });
    if (q) params.set("q", q);
    if (plan) params.set("plan", plan);
    const data = await apiGet(`/api/platform/businesses?${params}`);
    const tbody = document.querySelector("#businesses-table tbody");
    tbody.innerHTML = data.items.map(b => `
        <tr class="cursor-pointer" data-open-business="${b.id}">
            <td><div class="font-semibold text-textMain">${escapeHtml(b.company_name)}</div><div class="text-[10px] text-textSec font-mono">${escapeHtml(b.business_code)}</div></td>
            <td>${fmtDate(b.joined_at)}</td>
            <td><span class="pill bg-primary/15 text-primary">${escapeHtml(b.plan)}</span></td>
            <td>${statusPill(b.subscription_status)}</td>
            <td>${fmtInt(b.user_count)}</td>
            <td>${fmtDateTime(b.last_active_at)}</td>
            <td>${fmtMoney(b.lifetime_revenue_naira, "NGN")}</td>
            <td>${fmtInt(b.ai_credits_consumed)}</td>
            <td>${b.ai_provider_cost_usd ? fmtMoney(b.ai_provider_cost_usd, "USD") : "—"}</td>
        </tr>`).join("");
    tbody.querySelectorAll("[data-open-business]").forEach(row => {
        row.addEventListener("click", () => navigateTo("business-detail", row.getAttribute("data-open-business")));
    });
    document.getElementById("businesses-empty").classList.toggle("hidden", data.items.length > 0);
    document.getElementById("businesses-count").textContent = `${data.total ? (bizOffset + 1) : 0}–${bizOffset + data.items.length} of ${fmtInt(data.total)}`;
    document.getElementById("businesses-prev").disabled = bizOffset === 0;
    document.getElementById("businesses-next").disabled = bizOffset + PAGE_SIZE >= data.total;
}
function statusPill(status) {
    if (!status) return `<span class="pill bg-cardHover text-textSec">—</span>`;
    const cls = status === "active" ? "bg-success/15 text-success" : status === "trialing" ? "bg-warning/15 text-warning" : "bg-danger/15 text-danger";
    return `<span class="pill ${cls}">${escapeHtml(status)}</span>`;
}
document.getElementById("biz-search").addEventListener("input", () => {
    clearTimeout(bizSearchTimer);
    bizSearchTimer = setTimeout(() => { bizOffset = 0; loadBusinesses().catch(err => showGlobalError(err.message)); }, 350);
});
document.getElementById("biz-plan-filter").addEventListener("change", () => { bizOffset = 0; loadBusinesses().catch(err => showGlobalError(err.message)); });
document.getElementById("businesses-prev").addEventListener("click", () => { bizOffset = Math.max(0, bizOffset - PAGE_SIZE); loadBusinesses().catch(err => showGlobalError(err.message)); });
document.getElementById("businesses-next").addEventListener("click", () => { bizOffset += PAGE_SIZE; loadBusinesses().catch(err => showGlobalError(err.message)); });

let lastBusinessDetailId = null;
async function loadBusinessDetail(id) {
    lastBusinessDetailId = id;
    const b = await apiGet(`/api/platform/businesses/${id}`);
    document.getElementById("business-detail-content").innerHTML = `
        <div class="stat-card mb-4">
            <div class="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <div class="text-lg font-extrabold text-textMain">${escapeHtml(b.company_name)}</div>
                    <div class="text-[11px] text-textSec font-mono">${escapeHtml(b.business_code)}</div>
                </div>
                <div class="flex gap-2">${statusPill(b.subscription_status)}<span class="pill bg-primary/15 text-primary">${escapeHtml(b.plan)}</span></div>
            </div>
            <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-4">
                <div><div class="stat-label">Joined</div><div class="text-xs text-textMain mt-1">${fmtDate(b.joined_at)}</div></div>
                <div><div class="stat-label">Country</div><div class="text-xs text-textMain mt-1">${escapeHtml(b.country || "—")}</div></div>
                <div><div class="stat-label">Currency</div><div class="text-xs text-textMain mt-1">${escapeHtml(b.currency || "—")}</div></div>
                <div><div class="stat-label">Last Active</div><div class="text-xs text-textMain mt-1">${fmtDateTime(b.last_active_at)}</div></div>
                <div><div class="stat-label">Trial Ends</div><div class="text-xs text-textMain mt-1">${fmtDate(b.trial_end_at)}</div></div>
                <div><div class="stat-label">Period Ends</div><div class="text-xs text-textMain mt-1">${fmtDate(b.current_period_end)}</div></div>
                <div><div class="stat-label">Email</div><div class="text-xs text-textMain mt-1 truncate">${escapeHtml(b.email || "—")}</div></div>
                <div><div class="stat-label">Phone</div><div class="text-xs text-textMain mt-1">${escapeHtml(b.phone || "—")}</div></div>
            </div>
        </div>
        <div class="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
            <div class="stat-card"><div class="stat-label">Lifetime Revenue</div><div class="stat-value">${fmtMoney(b.lifetime_revenue_naira, "NGN")}</div></div>
            <div class="stat-card"><div class="stat-label">Last Payment</div><div class="stat-value !text-sm">${fmtDate(b.last_payment_at)}</div></div>
            <div class="stat-card"><div class="stat-label">AI Credits Used</div><div class="stat-value">${fmtInt(b.ai_credits_consumed)}</div></div>
            <div class="stat-card"><div class="stat-label">AI Provider Cost</div><div class="stat-value">${b.ai_provider_cost_usd ? fmtMoney(b.ai_provider_cost_usd, "USD") : "—"}</div></div>
        </div>
        <div class="stat-label mb-2">Users (${b.users.length})</div>
        <div class="table-scroll"><table class="data-table">
            <thead><tr><th>Name</th><th>Username</th><th>Email</th><th>Role</th><th>Status</th><th>Joined</th><th>Last Active</th></tr></thead>
            <tbody>${b.users.map(u => `<tr>
                <td>${escapeHtml([u.firstname, u.lastname].filter(Boolean).join(" ") || "—")}</td>
                <td class="font-mono">${escapeHtml(u.username)}</td>
                <td>${escapeHtml(u.email)}</td>
                <td><span class="pill bg-cardHover text-textSec">${escapeHtml(u.role)}</span></td>
                <td>${u.disabled ? '<span class="pill bg-danger/15 text-danger">disabled</span>' : '<span class="pill bg-success/15 text-success">active</span>'}</td>
                <td>${fmtDate(u.created_at)}</td>
                <td>${fmtDateTime(u.last_active_at)}</td>
            </tr>`).join("")}</tbody>
        </table></div>`;
}

// =============================================================================
// USERS
// =============================================================================
let usersOffset = 0;
let usersSearchTimer = null;

async function loadUsers() {
    const q = document.getElementById("users-search").value.trim();
    const role = document.getElementById("users-role-filter").value;
    const activeOnly = document.getElementById("users-active-only").checked;
    const params = new URLSearchParams({ limit: PAGE_SIZE, offset: usersOffset });
    if (q) params.set("q", q);
    if (role) params.set("role", role);
    if (activeOnly) params.set("active_only", "true");
    const data = await apiGet(`/api/platform/users?${params}`);
    Object.entries(data.summary).forEach(([k, v]) => {
        const el = document.querySelector(`[data-users-summary="${k}"]`);
        if (el) el.textContent = fmtInt(v);
    });
    const tbody = document.querySelector("#users-table tbody");
    tbody.innerHTML = data.items.map(u => `
        <tr>
            <td>${escapeHtml([u.firstname, u.lastname].filter(Boolean).join(" ") || "—")}</td>
            <td class="font-mono">${escapeHtml(u.username)}</td>
            <td class="truncate max-w-[180px]">${escapeHtml(u.email)}</td>
            <td><a class="text-primary hover:underline cursor-pointer" data-open-business="${u.business_id}">${escapeHtml(u.business_name || "—")}</a></td>
            <td><span class="pill bg-cardHover text-textSec">${escapeHtml(u.role)}</span></td>
            <td>${fmtDate(u.created_at)}</td>
            <td>${fmtDateTime(u.last_active_at)}</td>
            <td>${u.currently_active ? '<span class="w-2 h-2 rounded-full bg-success inline-block" title="Active now"></span>' : ""}</td>
        </tr>`).join("");
    tbody.querySelectorAll("[data-open-business]").forEach(a => a.addEventListener("click", () => navigateTo("business-detail", a.getAttribute("data-open-business"))));
    document.getElementById("users-empty").classList.toggle("hidden", data.items.length > 0);
    document.getElementById("users-count").textContent = `${data.total ? (usersOffset + 1) : 0}–${usersOffset + data.items.length} of ${fmtInt(data.total)}`;
    document.getElementById("users-prev").disabled = usersOffset === 0;
    document.getElementById("users-next").disabled = usersOffset + PAGE_SIZE >= data.total;
}
document.getElementById("users-search").addEventListener("input", () => {
    clearTimeout(usersSearchTimer);
    usersSearchTimer = setTimeout(() => { usersOffset = 0; loadUsers().catch(err => showGlobalError(err.message)); }, 350);
});
document.getElementById("users-role-filter").addEventListener("change", () => { usersOffset = 0; loadUsers().catch(err => showGlobalError(err.message)); });
document.getElementById("users-active-only").addEventListener("change", () => { usersOffset = 0; loadUsers().catch(err => showGlobalError(err.message)); });
document.getElementById("users-prev").addEventListener("click", () => { usersOffset = Math.max(0, usersOffset - PAGE_SIZE); loadUsers().catch(err => showGlobalError(err.message)); });
document.getElementById("users-next").addEventListener("click", () => { usersOffset += PAGE_SIZE; loadUsers().catch(err => showGlobalError(err.message)); });

// =============================================================================
// SUBSCRIPTIONS
// =============================================================================
function renderDistribution(container, dict, total) {
    const entries = Object.entries(dict || {}).sort((a, b) => b[1] - a[1]);
    if (!entries.length) { container.innerHTML = `<p class="text-[11px] text-textSec">No data yet.</p>`; return; }
    const max = Math.max(...entries.map(e => e[1]), 1);
    container.innerHTML = entries.map(([label, count]) => `
        <div>
            <div class="flex items-center justify-between text-[11px] mb-1"><span class="text-textMain font-medium capitalize">${escapeHtml(label)}</span><span class="text-textSec">${fmtInt(count)}</span></div>
            <div class="bar-track"><div class="bar-fill bg-primary" style="width:${Math.max(4, (count / max) * 100)}%"></div></div>
        </div>`).join("");
}
async function loadSubscriptions() {
    const data = await apiGet("/api/platform/subscriptions");
    renderDistribution(document.getElementById("subs-by-status"), data.by_status, data.total);
    renderDistribution(document.getElementById("subs-by-plan"), data.by_plan, data.total);
}

// =============================================================================
// REUSABLE DATE-RANGE COMPONENT
// =============================================================================
function buildPeriodBar(containerId, presets, onApply, storeKey) {
    const container = document.getElementById(containerId);
    let state = { period: presets[0].value, start: "", end: "" };
    try {
        const saved = JSON.parse(sessionStorage.getItem(storeKey) || "null");
        if (saved) state = saved;
    } catch (_) {}
    function render() {
        container.innerHTML = `
            <div class="flex flex-wrap items-center gap-1.5">
                ${presets.map(p => `<button type="button" data-period="${p.value}" class="period-btn px-3 py-1.5 rounded-lg text-[11px] font-semibold border cursor-pointer ${state.period === p.value ? "bg-primary/15 text-primary border-primary/30" : "bg-cardBg text-textSec border-borderCol"}">${p.label}</button>`).join("")}
            </div>
            <div id="${containerId}-custom" class="mt-2 flex flex-wrap items-end gap-2 ${state.period === "custom" ? "" : "hidden"}">
                <div><label class="block text-[10px] text-textSec mb-1">Start Date</label><input type="date" id="${containerId}-start" value="${state.start}" class="bg-cardBg border border-borderCol rounded-lg px-2.5 py-1.5 text-[11px] text-textMain focus:outline-none focus:border-primary"></div>
                <div><label class="block text-[10px] text-textSec mb-1">End Date</label><input type="date" id="${containerId}-end" value="${state.end}" class="bg-cardBg border border-borderCol rounded-lg px-2.5 py-1.5 text-[11px] text-textMain focus:outline-none focus:border-primary"></div>
                <button type="button" id="${containerId}-apply" class="bg-primary hover:bg-primaryHover text-white px-3 py-1.5 rounded-lg text-[11px] font-semibold cursor-pointer">Apply</button>
                <span id="${containerId}-error" class="hidden text-danger text-[10px]"></span>
            </div>`;
        container.querySelectorAll(".period-btn").forEach(btn => btn.addEventListener("click", () => {
            state.period = btn.getAttribute("data-period");
            try { sessionStorage.setItem(storeKey, JSON.stringify(state)); } catch (_) {}
            render();
            if (state.period !== "custom") onApply(state.period, null, null);
        }));
        if (state.period === "custom") {
            const applyBtn = document.getElementById(`${containerId}-apply`);
            applyBtn.addEventListener("click", () => {
                const s = document.getElementById(`${containerId}-start`).value;
                const e = document.getElementById(`${containerId}-end`).value;
                const errEl = document.getElementById(`${containerId}-error`);
                errEl.classList.add("hidden");
                if (!s || !e) { errEl.textContent = "Select both a start and end date."; errEl.classList.remove("hidden"); return; }
                if (s > e) { errEl.textContent = "End date cannot be earlier than start date."; errEl.classList.remove("hidden"); return; }
                state.start = s; state.end = e;
                try { sessionStorage.setItem(storeKey, JSON.stringify(state)); } catch (_) {}
                onApply("custom", s, e);
            });
        }
    }
    render();
    return { get: () => state, applyNow: () => { if (state.period === "custom" && state.start && state.end) return onApply("custom", state.start, state.end); return onApply(state.period, null, null); } };
}

// =============================================================================
// REVENUE
// =============================================================================
const REVENUE_PRESETS = [
    { value: "month", label: "This Month" }, { value: "six_months", label: "6 Months" },
    { value: "year", label: "1 Year" }, { value: "all", label: "All Time" }, { value: "custom", label: "Custom" },
];
let revenuePeriodCtl = null;
async function loadRevenue() {
    if (!revenuePeriodCtl) {
        revenuePeriodCtl = buildPeriodBar("revenue-period-bar", REVENUE_PRESETS, fetchRevenue, "cauldra_platform_revenue_period");
    }
    await revenuePeriodCtl.applyNow();
}
async function fetchRevenue(period, start, end) {
    const params = new URLSearchParams({ period });
    if (start) params.set("start", start);
    if (end) params.set("end", end);
    const data = await apiGet(`/api/platform/revenue?${params}`);
    document.querySelectorAll("#view-revenue-wrap").length; // no-op guard
    document.querySelectorAll("[data-rev]").forEach(el => el.textContent = fmtInt(get(data, el.getAttribute("data-rev"))));
    document.querySelectorAll("[data-rev-money]").forEach(el => el.textContent = fmtMoney(get(data, el.getAttribute("data-rev-money")), "NGN"));

    const byPlanEl = document.getElementById("revenue-by-plan");
    if (!data.by_plan.length) {
        byPlanEl.innerHTML = `<p class="text-[11px] text-textSec">No revenue recorded for this period.</p>`;
    } else {
        const max = Math.max(...data.by_plan.map(p => p.revenue_naira), 1);
        byPlanEl.innerHTML = data.by_plan.map(p => `
            <div>
                <div class="flex items-center justify-between text-[11px] mb-1"><span class="text-textMain font-medium capitalize">${escapeHtml(p.plan || "unknown")}</span><span class="text-textSec">${fmtMoney(p.revenue_naira, "NGN")} · ${fmtInt(p.payments)} pmts</span></div>
                <div class="bar-track"><div class="bar-fill bg-primary" style="width:${Math.max(4, (p.revenue_naira / max) * 100)}%"></div></div>
            </div>`).join("");
    }

    const bizBody = document.getElementById("revenue-by-business-body");
    bizBody.innerHTML = data.by_business.map(b => `
        <tr class="cursor-pointer" data-open-business="${b.business_id}">
            <td><div class="font-semibold text-textMain">${escapeHtml(b.company_name || "—")}</div><div class="text-[10px] text-textSec font-mono">${escapeHtml(b.business_code || "")}</div></td>
            <td>${fmtMoney(b.revenue_naira, "NGN")}</td>
            <td>${fmtInt(b.payments)}</td>
            <td>${fmtDate(b.last_payment_at)}</td>
        </tr>`).join("");
    bizBody.querySelectorAll("[data-open-business]").forEach(row => row.addEventListener("click", () => navigateTo("business-detail", row.getAttribute("data-open-business"))));
    document.getElementById("revenue-by-business-empty").classList.toggle("hidden", data.by_business.length > 0);
}

// =============================================================================
// AI & COSTS
// =============================================================================
const AI_PRESETS = [
    { value: "month", label: "This Month" }, { value: "six_months", label: "6 Months" },
    { value: "year", label: "1 Year" }, { value: "all", label: "All Time" }, { value: "custom", label: "Custom" },
];
let aiPeriodCtl = null;
async function loadAiCosts() {
    if (!aiPeriodCtl) {
        aiPeriodCtl = buildPeriodBar("ai-period-bar", AI_PRESETS, fetchAiUsage, "cauldra_platform_ai_period");
    }
    await aiPeriodCtl.applyNow();
    await loadAiPricing();
    await loadPlatformSettings();
}
async function fetchAiUsage(period, start, end) {
    const params = new URLSearchParams({ period });
    if (start) params.set("start", start);
    if (end) params.set("end", end);
    const data = await apiGet(`/api/platform/ai-usage?${params}`);

    const cardsEl = document.getElementById("ai-provider-cards");
    const providers = Object.keys(data.providers);
    if (!providers.length) {
        cardsEl.innerHTML = `<p class="text-[11px] text-textSec sm:col-span-2">No AI usage recorded for this period.</p>`;
    } else {
        cardsEl.innerHTML = providers.map(p => {
            const v = data.providers[p];
            const pctKnown = v.budget_consumed_pct !== null && v.budget_consumed_pct !== undefined;
            const pct = pctKnown ? Math.min(100, v.budget_consumed_pct) : 0;
            const barColor = !pctKnown ? "bg-textSec" : pct >= 95 ? "bg-danger" : pct >= 75 ? "bg-warning" : "bg-success";
            return `<div class="stat-card">
                <div class="flex items-center justify-between mb-2"><span class="font-bold text-sm text-textMain capitalize">${escapeHtml(p)}</span><span class="text-[11px] text-textSec">${fmtInt(v.requests)} requests</span></div>
                <div class="grid grid-cols-2 gap-2 text-[11px] mb-2">
                    <div><div class="text-textSec">Cauldra Credits</div><div class="text-textMain font-semibold">${fmtInt(v.credits_consumed)}</div></div>
                    <div><div class="text-textSec">Provider Cost</div><div class="text-textMain font-semibold">${v.provider_cost_usd ? fmtMoney(v.provider_cost_usd, "USD") : "— (pricing not set)"}</div></div>
                </div>
                ${v.monthly_budget_ngn ? `
                <div class="text-[10px] text-textSec mb-1 flex justify-between"><span>Budget consumed</span><span>${pctKnown ? pct.toFixed(1) + "%" : "—"} of ${fmtMoney(v.monthly_budget_ngn, "NGN")}</span></div>
                <div class="bar-track"><div class="bar-fill ${barColor}" style="width:${Math.max(2, pct)}%"></div></div>
                <div class="text-[10px] text-textSec mt-1">${v.budget_remaining_ngn !== null && v.budget_remaining_ngn !== undefined ? "Remaining: " + fmtMoney(v.budget_remaining_ngn, "NGN") : ""}</div>
                ` : `<p class="text-[10px] text-textSec">No monthly budget configured for ${escapeHtml(p)}.</p>`}
            </div>`;
        }).join("");
    }

    const opBody = document.getElementById("ai-by-operation-body");
    opBody.innerHTML = data.by_operation.map(o => `
        <tr>
            <td class="capitalize">${escapeHtml((o.operation || "").replace(/_/g, " "))}</td>
            <td class="capitalize">${escapeHtml(o.provider || "—")}</td>
            <td>${fmtInt(o.requests)}</td>
            <td>${fmtInt(o.credits_consumed)}</td>
            <td>${o.provider_cost_usd ? fmtMoney(o.provider_cost_usd, "USD") : "—"}</td>
            <td>${o.average_cost_per_request_usd ? fmtMoney(o.average_cost_per_request_usd, "USD") : "—"}</td>
        </tr>`).join("");
    document.getElementById("ai-by-operation-empty").classList.toggle("hidden", data.by_operation.length > 0);
    document.getElementById("ai-failed-count").textContent = fmtInt(data.failed_requests);
}

async function loadAiPricing() {
    const data = await apiGet("/api/platform/ai-pricing");
    const listEl = document.getElementById("ai-pricing-list");
    const rows = [];
    data.configured.forEach(r => rows.push(`
        <div class="flex flex-wrap items-center justify-between gap-2 px-3 py-2 rounded-lg bg-bgMain border border-borderCol text-[11px]">
            <span class="font-semibold text-textMain capitalize">${escapeHtml(r.provider)} <span class="text-textSec font-normal">/ ${escapeHtml(r.model)}</span></span>
            <span class="text-textSec">in: ${r.input_price_per_1k_usd != null ? "$" + r.input_price_per_1k_usd : "—"}/1k &nbsp;&middot;&nbsp; out: ${r.output_price_per_1k_usd != null ? "$" + r.output_price_per_1k_usd : "—"}/1k</span>
        </div>`));
    data.unconfigured_active_models.forEach(m => rows.push(`
        <div class="flex flex-wrap items-center justify-between gap-2 px-3 py-2 rounded-lg bg-warning/10 border border-warning/25 text-[11px]">
            <span class="font-semibold text-textMain capitalize">${escapeHtml(m.provider)} <span class="text-textSec font-normal">/ ${escapeHtml(m.model)}</span></span>
            <span class="text-amber-300">Currently in use — pricing not set</span>
        </div>`));
    listEl.innerHTML = rows.join("") || `<p class="text-[11px] text-textSec">No pricing configured yet.</p>`;
}
document.getElementById("ai-pricing-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
        await apiPut("/api/platform/ai-pricing", {
            provider: document.getElementById("pricing-provider").value.trim(),
            model: document.getElementById("pricing-model").value.trim(),
            input_price_per_1k_usd: document.getElementById("pricing-input-rate").value ? parseFloat(document.getElementById("pricing-input-rate").value) : null,
            output_price_per_1k_usd: document.getElementById("pricing-output-rate").value ? parseFloat(document.getElementById("pricing-output-rate").value) : null,
        });
        document.getElementById("ai-pricing-form").reset();
        await loadAiPricing();
    } catch (err) { showGlobalError(err.message); }
});

async function loadPlatformSettings() {
    const s = await apiGet("/api/platform/settings");
    document.getElementById("setting-usd-ngn").value = s.usd_to_ngn_rate ?? "";
    document.getElementById("setting-gemini-budget").value = s.gemini_monthly_budget_ngn ?? "";
    document.getElementById("setting-openai-budget").value = s.openai_monthly_budget_ngn ?? "";
    document.getElementById("setting-thresholds").value = (s.ai_alert_thresholds || []).join(", ");
}
document.getElementById("platform-settings-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
        const thresholdsRaw = document.getElementById("setting-thresholds").value.trim();
        const body = {
            usd_to_ngn_rate: document.getElementById("setting-usd-ngn").value ? parseFloat(document.getElementById("setting-usd-ngn").value) : null,
            gemini_monthly_budget_ngn: document.getElementById("setting-gemini-budget").value ? parseFloat(document.getElementById("setting-gemini-budget").value) : null,
            openai_monthly_budget_ngn: document.getElementById("setting-openai-budget").value ? parseFloat(document.getElementById("setting-openai-budget").value) : null,
        };
        if (thresholdsRaw) body.ai_alert_thresholds = thresholdsRaw.split(",").map(s => parseInt(s.trim(), 10)).filter(n => !isNaN(n));
        await apiPut("/api/platform/settings", body);
        await loadPlatformSettings();
        await aiPeriodCtl.applyNow();
    } catch (err) { showGlobalError(err.message); }
});

// =============================================================================
// ALERTS
// =============================================================================
let alertsFilter = "unresolved";
async function loadAlerts() {
    document.querySelectorAll(".alert-filter-btn").forEach(btn => {
        const active = btn.getAttribute("data-alert-filter") === alertsFilter;
        btn.classList.toggle("bg-primary/15", active); btn.classList.toggle("text-primary", active); btn.classList.toggle("border-primary/30", active);
        btn.classList.toggle("bg-cardBg", !active); btn.classList.toggle("text-textSec", !active);
    });
    const resolvedParam = alertsFilter === "unresolved" ? "false" : null;
    const params = new URLSearchParams();
    if (resolvedParam) params.set("resolved", resolvedParam);
    const data = await apiGet(`/api/platform/alerts?${params}`);
    const listEl = document.getElementById("alerts-list");
    listEl.innerHTML = data.items.map(a => {
        const sevColor = a.severity === "critical" ? "border-danger/40 bg-danger/10" : a.severity === "important" ? "border-warning/40 bg-warning/10" : "border-borderCol bg-cardBg";
        return `<div class="rounded-xl border ${sevColor} p-3">
            <div class="flex items-start justify-between gap-2">
                <div class="min-w-0">
                    <div class="flex items-center gap-2 mb-0.5">
                        <span class="pill ${a.severity === "critical" ? "bg-danger/20 text-danger" : a.severity === "important" ? "bg-warning/20 text-amber-300" : "bg-cardHover text-textSec"}">${escapeHtml(a.severity)}</span>
                        <span class="font-semibold text-textMain text-xs">${escapeHtml(a.title)}</span>
                    </div>
                    <p class="text-[11px] text-textSec leading-relaxed">${escapeHtml(a.message)}</p>
                    <p class="text-[10px] text-textSec mt-1">${fmtDateTime(a.created_at)}${a.acknowledged_at ? " · acknowledged " + fmtDateTime(a.acknowledged_at) : ""}</p>
                </div>
                ${!a.acknowledged_at ? `<button type="button" data-ack="${a.id}" class="shrink-0 bg-primary/15 hover:bg-primary/25 text-primary border border-primary/30 px-2.5 py-1 rounded-lg text-[10px] font-semibold cursor-pointer">Acknowledge</button>` : ""}
            </div>
        </div>`;
    }).join("");
    listEl.querySelectorAll("[data-ack]").forEach(btn => btn.addEventListener("click", async () => {
        try { await apiPost(`/api/platform/alerts/${btn.getAttribute("data-ack")}/acknowledge`); await loadAlerts(); } catch (err) { showGlobalError(err.message); }
    }));
    document.getElementById("alerts-empty").classList.toggle("hidden", data.items.length > 0);
    refreshAlertBadge();
}
document.querySelectorAll(".alert-filter-btn").forEach(btn => btn.addEventListener("click", () => { alertsFilter = btn.getAttribute("data-alert-filter"); loadAlerts().catch(err => showGlobalError(err.message)); }));

async function refreshAlertBadge() {
    try {
        const data = await apiGet(`/api/platform/alerts?resolved=false&limit=200`);
        const badge = document.getElementById("alerts-nav-badge");
        const n = data.items.length;
        badge.textContent = n > 99 ? "99+" : String(n);
        badge.classList.toggle("hidden", n === 0);
    } catch (_) { /* non-critical */ }
}

// =============================================================================
// SYSTEM HEALTH / INFRASTRUCTURE
// =============================================================================
async function loadSystemHealth() {
    const h = await apiGet("/api/platform/system-health");
    document.getElementById("health-db-status").textContent = h.database.status === "ok" ? "OK" : "Degraded";
    document.getElementById("health-db-status").className = "stat-value " + (h.database.status === "ok" ? "text-success" : "text-danger");
    document.getElementById("health-ai-failed").textContent = fmtInt(h.ai.failed_requests_24h);
    document.getElementById("health-payments-failed").textContent = fmtInt(h.payments.failed_24h);
    document.getElementById("health-webhooks").textContent = fmtInt(h.webhooks.received_24h);
    document.getElementById("health-critical-alerts").textContent = fmtInt(h.alerts.unresolved_critical);
    document.getElementById("health-checked-at").textContent = "Checked " + fmtDateTime(h.checked_at);
}
async function loadInfrastructure() {
    const i = await apiGet("/api/platform/infrastructure");
    document.getElementById("infra-note").textContent = i.note;
    document.getElementById("infra-storage").textContent = formatBytes(i.storage.total_bytes_used);
    document.getElementById("infra-files").textContent = fmtInt(i.storage.total_files);
    document.getElementById("infra-db-host").textContent = i.database.host || "—";
    document.getElementById("infra-ai-providers").innerHTML = Object.entries(i.ai_providers_configured).map(([name, on]) =>
        `<span class="pill ${on ? "bg-success/15 text-success" : "bg-cardHover text-textSec"} capitalize">${escapeHtml(name)}: ${on ? "configured" : "not configured"}</span>`
    ).join("");
}
function formatBytes(n) {
    if (!n) return "0 B";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let i = 0, v = n;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return v.toFixed(v < 10 && i > 0 ? 1 : 0) + " " + units[i];
}

// =============================================================================
// BOOT
// =============================================================================
window.addEventListener("hashchange", () => {
    const [view, param] = location.hash.replace("#", "").split("/");
    if (view && view !== currentView) navigateTo(view, param);
});

(function boot() {
    if (accessToken) {
        let storedEmail = "";
        try { storedEmail = sessionStorage.getItem(EMAIL_KEY) || ""; } catch (_) {}
        document.getElementById("sidebar-owner-email").textContent = storedEmail;
        showApp();
        const [view, param] = (location.hash.replace("#", "") || "overview").split("/");
        navigateTo(view, param);
    } else {
        showLogin();
    }
})();
