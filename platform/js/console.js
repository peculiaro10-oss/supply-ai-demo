// =============================================================================
// CAULDRA PLATFORM OWNER CONTROL PANEL - console (served to signed-in owners only)
// Shares login.js's globals (API layer, token, sign-in screen helpers).
// Every /api/platform/* call is still independently re-verified server-side.
// OPS-ACCURACY-001: every number rendered here has one definition, served by
// the backend next to it (definitions / provenance), and a missing value reads
// as "Never", "Not recorded" or "Unavailable" rather than an ambiguous dash.
// =============================================================================

document.getElementById("sign-out-btn").addEventListener("click", async () => {
    try { await apiPost("/api/platform/auth/logout"); } catch (_) {}
    signOutLocally();
    showLogin();
    document.getElementById("login-form-mfa").classList.add("hidden");
    document.getElementById("login-form-password").classList.remove("hidden");
    document.getElementById("login-form-password").reset();
});

// ---------------------------------------------------------------- Nav / router
const VIEWS = ["overview", "businesses", "business-detail", "users", "user-detail", "subscriptions", "revenue", "ai-costs", "alerts", "system-health", "infrastructure"];
const NAV_TITLES = {
    overview: "Overview", businesses: "Businesses", "business-detail": "Business", users: "Users", "user-detail": "User",
    subscriptions: "Subscriptions", revenue: "Revenue", "ai-costs": "AI & Costs", alerts: "Alerts",
    "system-health": "System Health", infrastructure: "Infrastructure",
};
const NAV_PARENT = { "business-detail": "businesses", "user-detail": "users" };
let currentView = "overview";
let currentParam = null;

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
    currentParam = param || null;
    document.querySelectorAll("[data-view]").forEach(el => el.classList.toggle("active", el.getAttribute("data-view") === view));
    document.getElementById("page-title").textContent = NAV_TITLES[view] || view;
    setActiveNav(NAV_PARENT[view] || view);
    closeMobileNav();
    location.hash = view + (param ? `/${param}` : "");
    clearGlobalError();
    try {
        if (view === "overview") await loadOverview();
        else if (view === "businesses") await loadBusinesses();
        else if (view === "business-detail") await loadBusinessDetail(param);
        else if (view === "users") await loadUsers(param);
        else if (view === "user-detail") await loadUserDetail(param);
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
        await navigateTo(currentView, currentParam);
    } finally {
        btn.disabled = false;
        if (icon) icon.classList.remove("animate-spin");
    }
});

// ---------------------------------------------------------------- shared bits
const STATUS_LABELS = { trialing: "Trialing", active: "Active", past_due: "Past due", expired: "Expired", cancelled: "Cancelled", pending_payment_method: "Awaiting card" };
function statusPill(status) {
    if (!status) return `<span class="pill bg-cardHover text-textSec">No subscription</span>`;
    const cls = status === "active" ? "bg-success/15 text-success"
        : status === "trialing" ? "bg-primary/15 text-primary"
        : status === "past_due" || status === "pending_payment_method" ? "bg-warning/15 text-warning"
        : "bg-danger/15 text-danger";
    return `<span class="pill ${cls}">${escapeHtml(STATUS_LABELS[status] || status)}</span>`;
}
function planPill(label, id) {
    return `<span class="pill bg-primary/10 text-primary" title="plan id: ${escapeHtml(id || "")}">${escapeHtml(label || id || "—")}</span>`;
}
function healthChips(health) {
    if (!health || !health.length) return `<span class="text-[10px] muted-dash">Healthy</span>`;
    return health.map(h => {
        const cls = h.severity === "critical" ? "bg-danger/15 text-danger" : h.severity === "warning" ? "bg-warning/15 text-warning" : "bg-cardHover text-textSec";
        return `<span class="pill ${cls} mr-1 mb-0.5">${escapeHtml(h.label)}</span>`;
    }).join("");
}
function lastActive(iso) {
    return iso ? fmtDateTime(iso) : `<span class="muted-dash">Never</span>`;
}
// Compact tables: the date alone below md, date and time from md up.
function lastActiveCompact(iso) {
    return iso ? `<span class="hidden md:inline">${fmtDateTime(iso)}</span><span class="md:hidden" title="${escapeHtml(fmtDateTime(iso))}">${fmtDate(iso)}</span>` : `<span class="muted-dash">Never</span>`;
}
// One quiet line of text instead of pills where a column is narrow.
function healthLine(health) {
    if (!health || !health.length) return "";
    const worst = health.some(h => h.severity === "critical") ? "text-danger" : health.some(h => h.severity === "warning") ? "text-warning" : "text-textSec";
    return `<div class="text-[10px] leading-snug ${worst}">${health.map(h => escapeHtml(h.label)).join(" · ")}</div>`;
}
function joinedDate(iso) {
    return iso ? fmtDate(iso) : `<span class="muted-dash" title="Created before join dates were recorded">Not recorded</span>`;
}
function daysText(n) { return `${n} day${n === 1 ? "" : "s"}`; }
function billingText(b) {
    if (!b) return "—";
    const d = b.date ? fmtDate(b.date) : null;
    switch (b.kind) {
        case "trial_ends": return `Trial ends ${d}<div class="text-[10px] text-textSec">${b.days_left === 0 ? "ends today" : daysText(b.days_left) + " left"}</div>`;
        case "renews": return `Renews ${d || "—"}${b.pending_downgrade_plan ? `<div class="text-[10px] text-textSec">then ${escapeHtml(b.pending_downgrade_plan)}</div>` : ""}`;
        case "access_until": return `Access until ${d}<div class="text-[10px] text-textSec">cancelled, will not renew</div>`;
        case "grace_until": return `Payment overdue<div class="text-[10px] text-textSec">${d ? "grace until " + d : "grace period"}</div>`;
        case "expired": return d ? `Expired ${d}` : "Expired";
        case "ended": return d ? `Ended ${d}` : "Ended";
        case "awaiting_card": return `Awaiting card`;
        case "none": return `<span class="muted-dash">No subscription</span>`;
        default: return "—";
    }
}
function fmtUsd(n) {
    if (n === null || n === undefined) return "—";
    const v = Number(n);
    if (v !== 0 && Math.abs(v) < 0.01) return "$" + v.toPrecision(2);
    return fmtMoney(v, "USD");
}
function fmtPct(n) { return n === null || n === undefined ? "—" : `${n}%`; }
function setTitleFromDefinitions(defs, selector, attr) {
    document.querySelectorAll(selector).forEach(el => { const t = get(defs || {}, el.getAttribute(attr)); if (t) el.title = t; });
}
function pager(prefix, offset, count, total, size) {
    document.getElementById(`${prefix}-count`).textContent = `${total ? (offset + 1) : 0}–${offset + count} of ${fmtInt(total)}`;
    document.getElementById(`${prefix}-prev`).disabled = offset === 0;
    document.getElementById(`${prefix}-next`).disabled = offset + size >= total;
}
function bindOpenBusiness(root) {
    root.querySelectorAll("[data-open-business]").forEach(el => el.addEventListener("click", (e) => { e.stopPropagation(); navigateTo("business-detail", el.getAttribute("data-open-business")); }));
    root.querySelectorAll("[data-open-user]").forEach(el => el.addEventListener("click", (e) => { e.stopPropagation(); navigateTo("user-detail", el.getAttribute("data-open-user")); }));
}
const PAGE_SIZE = 25;

// =============================================================================
// OVERVIEW
// =============================================================================
let growthMonths = 6;
async function loadOverview() {
    const data = await apiGet(`/api/platform/overview?months=${growthMonths}`);
    document.querySelectorAll("[data-ov]").forEach(el => {
        const val = get(data, el.getAttribute("data-ov"));
        el.textContent = (typeof val === "number") ? fmtInt(val) : (val ?? "—");
    });
    document.querySelectorAll("[data-ov-money]").forEach(el => {
        const val = get(data, el.getAttribute("data-ov-money"));
        el.textContent = val === null || val === undefined ? "—" : fmtMoney(val, "NGN");
    });
    setTitleFromDefinitions(data, "[data-ov-title]", "data-ov-title");
    const split = Object.entries(data.businesses.paying_by_plan || {}).map(([k, v]) => `${v} ${k}`).join(" · ");
    document.getElementById("ov-plan-split").textContent = split || "no paid plans yet";
    const conv = data.trial_conversion;
    document.getElementById("ov-conversion").textContent = conv.rate_pct === null ? "—" : `${conv.rate_pct}%`;
    document.getElementById("ov-conversion-sub").textContent = conv.ended_trials ? `${conv.converted} of ${conv.ended_trials} ended trials paid` : "no trial has ended yet";
    document.getElementById("ov-ai-spend").textContent = fmtUsd(data.ai_spend.this_month_usd);
    document.getElementById("ov-ai-spend-sub").textContent = (data.ai_spend.this_month_ngn != null ? fmtMoney(data.ai_spend.this_month_ngn, "NGN") : "NGN unavailable (no rate)")
        + (data.ai_spend.unpriced_requests ? ` · ${data.ai_spend.unpriced_requests} unpriced request(s) not included` : "");

    document.querySelectorAll("[data-growth-months]").forEach(btn => {
        const on = Number(btn.getAttribute("data-growth-months")) === growthMonths;
        btn.className = `period-btn px-2.5 py-1 rounded-lg text-[10px] font-semibold border cursor-pointer ${on ? "bg-primary/15 text-primary border-primary/30" : "bg-cardBg text-textSec border-borderCol"}`;
    });
    const max = Math.max(...data.growth.map(g => g.new), 1);
    document.getElementById("ov-growth").innerHTML = data.growth.map(g => {
        const label = new Date(g.month + "-01T00:00:00Z").toLocaleDateString(undefined, { month: "short", year: "2-digit", timeZone: "UTC" });
        return `<div class="flex items-center gap-2 text-[11px]">
            <span class="w-14 shrink-0 text-textSec">${label}</span>
            <div class="bar-track flex-1"><div class="bar-fill bg-primary" style="width:${g.new ? Math.max(4, g.new / max * 100) : 0}%"></div></div>
            <span class="w-20 shrink-0 text-right text-textMain">+${fmtInt(g.new)} <span class="text-textSec">(${fmtInt(g.total)})</span></span>
        </div>`;
    }).join("");
    const items = data.attention.items;
    document.getElementById("ov-attention").innerHTML = items.length ? items.map(a => `
        <button type="button" data-nav-link-inline="alerts" class="w-full text-left rounded-xl border ${sevBorder(a.severity)} px-3 py-2 cursor-pointer">
            <div class="flex items-center gap-2">${sevPill(a.severity)}<span class="text-xs font-semibold text-textMain truncate">${escapeHtml(a.title)}</span></div>
            <div class="text-[10px] text-textSec mt-0.5">${escapeHtml(a.source)} · last seen ${fmtDateTime(a.last_seen)}</div>
        </button>`).join("") : `<p class="text-[11px] text-textSec">Nothing needs your attention right now.</p>`;
    document.querySelectorAll("[data-nav-link-inline]").forEach(b => b.addEventListener("click", () => navigateTo(b.getAttribute("data-nav-link-inline"))));
    document.getElementById("overview-generated-at").textContent = "Updated " + fmtDateTime(data.generated_at);
}
document.querySelectorAll("[data-growth-months]").forEach(btn => btn.addEventListener("click", () => {
    growthMonths = Number(btn.getAttribute("data-growth-months"));
    loadOverview().catch(err => showGlobalError(err.message));
}));

// =============================================================================
// BUSINESSES
// =============================================================================
let bizOffset = 0;
let bizSearchTimer = null;

async function loadBusinesses() {
    const params = new URLSearchParams({ limit: PAGE_SIZE, offset: bizOffset });
    const q = document.getElementById("biz-search").value.trim();
    if (q) params.set("q", q);
    [["plan", "biz-plan-filter"], ["status", "biz-status-filter"], ["health", "biz-health-filter"], ["activity", "biz-activity-filter"], ["sort", "biz-sort"]].forEach(([k, id]) => {
        const v = document.getElementById(id).value; if (v) params.set(k, v);
    });
    const data = await apiGet(`/api/platform/businesses?${params}`);
    const tbody = document.querySelector("#businesses-table tbody");
    tbody.innerHTML = data.items.map(b => `
        <tr class="cursor-pointer" data-open-business="${b.id}">
            <td><div class="font-semibold text-textMain">${escapeHtml(b.company_name)}</div><div class="text-[10px] text-textSec font-mono">${escapeHtml(b.business_code)}</div>
                <div class="lg:hidden mt-1">${healthLine(b.health)}</div></td>
            <td class="hidden lg:table-cell">${fmtDate(b.joined_at)}</td>
            <td>${planPill(b.plan_label, b.plan)}<div class="md:hidden mt-1">${statusPill(b.subscription_status)}</div></td>
            <td class="hidden md:table-cell">${statusPill(b.subscription_status)}</td>
            <td class="hidden lg:table-cell">${healthChips(b.health)}</td>
            <td title="${b.user_count_disabled ? b.user_count_disabled + " disabled account(s) not counted" : ""}">${fmtInt(b.user_count)}</td>
            <td class="text-[11px]">${lastActiveCompact(b.last_active_at)}</td>
            <td class="text-[11px]">${billingText(b.billing)}</td>
            <td class="hidden md:table-cell">${fmtMoney(b.paid_to_cauldra_naira, "NGN")}</td>
        </tr>`).join("");
    bindOpenBusiness(tbody);
    document.getElementById("businesses-empty").classList.toggle("hidden", data.items.length > 0);
    pager("businesses", bizOffset, data.items.length, data.total, PAGE_SIZE);
    const d = data.definitions || {};
    document.getElementById("businesses-definitions").textContent = `Users: ${d.users || ""} Last Active: ${d.last_active || ""} Paid to Cauldra: ${d.paid_to_cauldra || ""}`;
}
document.getElementById("biz-search").addEventListener("input", () => {
    clearTimeout(bizSearchTimer);
    bizSearchTimer = setTimeout(() => { bizOffset = 0; loadBusinesses().catch(err => showGlobalError(err.message)); }, 350);
});
["biz-plan-filter", "biz-status-filter", "biz-health-filter", "biz-activity-filter", "biz-sort"].forEach(id =>
    document.getElementById(id).addEventListener("change", () => { bizOffset = 0; loadBusinesses().catch(err => showGlobalError(err.message)); }));
document.getElementById("businesses-prev").addEventListener("click", () => { bizOffset = Math.max(0, bizOffset - PAGE_SIZE); loadBusinesses().catch(err => showGlobalError(err.message)); });
document.getElementById("businesses-next").addEventListener("click", () => { bizOffset += PAGE_SIZE; loadBusinesses().catch(err => showGlobalError(err.message)); });

// =============================================================================
// BUSINESS DETAIL
// =============================================================================
function field(label, value, extra = "") {
    return `<div ${extra}><div class="stat-label">${label}</div><div class="text-xs text-textMain mt-1 break-words">${value}</div></div>`;
}
function section(title, body, open = false) {
    return `<details class="stat-card" ${open ? "open" : ""}><summary class="stat-label cursor-pointer select-none">${title}</summary><div class="mt-3">${body}</div></details>`;
}
const AUDIT_LABELS = { BUSINESS_REGISTERED: "Business registered" };
function auditLabel(action) { return AUDIT_LABELS[action] || action.replace(/_/g, " ").toLowerCase().replace(/^./, c => c.toUpperCase()); }

async function loadBusinessDetail(id) {
    const b = await apiGet(`/api/platform/businesses/${id}`);
    const owner = b.owner || {};
    const u = b.usage;
    const payRows = b.payments.map(p => `<tr>
        <td>${fmtDateTime(p.paid_at || p.created_at)}</td>
        <td>${escapeHtml(p.purpose)}${p.counts_as_revenue ? "" : ""}</td>
        <td class="hidden sm:table-cell">${escapeHtml(p.plan_label)} · ${escapeHtml(p.billing_interval || "")}</td>
        <td>${fmtMoney(p.amount_naira, "NGN")}</td>
        <td>${payStatusPill(p.status)}${p.refund_status ? `<div class="text-[10px] text-textSec">refund ${escapeHtml(p.refund_status)}</div>` : ""}</td>
        <td class="hidden md:table-cell font-mono text-[10px] text-textSec">${escapeHtml(p.reference)}</td></tr>`).join("");
    document.getElementById("business-detail-content").innerHTML = `
        <div class="stat-card mb-4">
            <div class="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <div class="text-lg font-extrabold text-textMain">${escapeHtml(b.company_name)}</div>
                    <div class="text-[11px] text-textSec font-mono">${escapeHtml(b.business_code)}</div>
                </div>
                <div class="flex flex-wrap gap-2">${statusPill(b.subscription_status)}${planPill(b.plan_label, b.plan)}</div>
            </div>
            <div class="mt-3">${healthChips(b.health)}</div>
            <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-4">
                ${field("Joined", fmtDate(b.joined_at))}
                ${field("Trial Started", b.trial_start_at ? fmtDate(b.trial_start_at) : '<span class="muted-dash">No trial started</span>')}
                ${field("Trial Ends", b.trial_end_at ? fmtDate(b.trial_end_at) : '<span class="muted-dash">—</span>')}
                ${field("Billing", billingText(b.billing))}
                ${field("Country", escapeHtml(b.country || "Not set"))}
                ${field("Currency", escapeHtml(b.currency || "Not set"))}
                ${field("Owner", owner.email ? `${escapeHtml(owner.name || "")}<div class="text-[11px] text-textSec">${escapeHtml(owner.email)} ${owner.email_verified ? "" : "· unverified"}</div>` : '<span class="muted-dash">No admin account</span>')}
                ${field("Business Phone", escapeHtml(b.phone || "Not set"))}
                ${field("Last Active", lastActive(b.last_active_at))}
            </div>
        </div>
        <div class="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
            <div class="stat-card" title="Successful subscription payments to Cauldra, lifetime (card-verification charge excluded)"><div class="stat-label">Paid to Cauldra</div><div class="stat-value">${fmtMoney(b.paid_to_cauldra_naira, "NGN")}</div><div class="text-[10px] text-textSec mt-1">${fmtInt(b.successful_payments)} payment(s)</div></div>
            <div class="stat-card"><div class="stat-label">Last Payment</div><div class="stat-value !text-sm">${b.last_payment ? fmtDate(b.last_payment.paid_at) : '<span class="muted-dash">None</span>'}</div><div class="text-[10px] text-textSec mt-1">${b.last_payment ? fmtMoney(b.last_payment.amount_naira, "NGN") + " · " + escapeHtml(b.last_payment.plan_label) : ""}</div></div>
            <div class="stat-card"><div class="stat-label">AI Usage</div><div class="stat-value">${fmtInt(u.ai_credits_30d)}</div><div class="text-[10px] text-textSec mt-1">credits in 30 days · ${fmtInt(u.ai_credits_lifetime)} lifetime</div></div>
            <div class="stat-card" title="Estimated provider cost of this business's AI requests, lifetime"><div class="stat-label">Cauldra AI Cost</div><div class="stat-value">${fmtUsd(u.ai_cost_usd_lifetime)}</div><div class="text-[10px] text-textSec mt-1">${u.ai_cost_ngn_lifetime != null ? fmtMoney(u.ai_cost_ngn_lifetime, "NGN") : ""}</div></div>
        </div>
        <div class="stat-card mb-4">
            <div class="stat-label mb-3">Subscription Lifecycle</div>
            <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
                ${field("Plan", `${escapeHtml(b.plan_label)} · ${escapeHtml(b.billing_interval || "")}`)}
                ${field("Status", statusPill(b.subscription_status))}
                ${field("Trial", b.trial_start_at ? `${fmtDate(b.trial_start_at)} → ${fmtDate(b.trial_end_at)}` : '<span class="muted-dash">No trial started</span>')}
                ${field("Current Period", b.current_period_start ? `${fmtDate(b.current_period_start)} → ${fmtDate(b.current_period_end)}` : '<span class="muted-dash">—</span>')}
                ${field("Next Renewal", b.next_billing_at ? fmtDate(b.next_billing_at) : '<span class="muted-dash">None scheduled</span>')}
                ${field("Cancellation", b.cancel_at_period_end ? "Cancels at period end" : b.cancelled_at ? "Cancelled " + fmtDate(b.cancelled_at) : "Not cancelled")}
                ${field("Scheduled Change", b.pending_downgrade ? `To ${escapeHtml(b.pending_downgrade.plan_label)} on ${fmtDate(b.pending_downgrade.effective_at)}` : "None")}
                ${field("Payment Health", b.alerts.filter(h => h.code.startsWith("payment") || h.code === "subscription_mismatch").map(h => escapeHtml(h.label)).join(", ") || "No payment issues")}
            </div>
        </div>
        <div class="stat-label mb-2">Users (${b.users.length})</div>
        <div class="table-scroll mb-4"><table class="data-table">
            <thead><tr><th>Name</th><th class="hidden md:table-cell">Username</th><th class="hidden sm:table-cell">Email</th><th>Role</th><th>Status</th><th class="hidden lg:table-cell">Joined</th><th>Last Active</th></tr></thead>
            <tbody>${b.users.map(x => `<tr class="cursor-pointer" data-open-user="${x.id}">
                <td>${escapeHtml([x.firstname, x.lastname].filter(Boolean).join(" ") || x.username)}</td>
                <td class="hidden md:table-cell font-mono">${escapeHtml(x.username)}</td>
                <td class="hidden sm:table-cell">${escapeHtml(x.email)}${x.email_verified ? "" : ' <span class="text-[10px] text-warning">unverified</span>'}</td>
                <td><span class="pill bg-cardHover text-textSec">${escapeHtml(x.role)}</span></td>
                <td>${x.disabled ? '<span class="pill bg-danger/15 text-danger">Disabled</span>' : '<span class="pill bg-success/15 text-success">Active</span>'}</td>
                <td class="hidden lg:table-cell">${joinedDate(x.created_at)}</td>
                <td class="text-[11px]">${lastActive(x.last_active_at)}</td>
            </tr>`).join("")}</tbody>
        </table></div>
        <div class="space-y-3">
            ${section(`Subscription &amp; Payments (${b.payments.length})`, b.payments.length ? `<div class="table-scroll"><table class="data-table"><thead><tr><th>Date</th><th>Purpose</th><th class="hidden sm:table-cell">Plan</th><th>Amount</th><th>Status</th><th class="hidden md:table-cell">Paystack Ref.</th></tr></thead><tbody>${payRows}</tbody></table></div>` : '<p class="text-[11px] text-textSec">No payment attempts recorded.</p>')}
            ${section("Usage &amp; Activity", `<div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
                ${field("Users active (30d)", `${fmtInt(u.users_active_30d)} of ${fmtInt(b.users.length)}`)}
                ${field("AI requests (30d)", `${fmtInt(u.ai_requests_30d)} ok · ${fmtInt(u.ai_failed_30d)} failed`)}
                ${field("Last AI use", u.last_ai_use_at ? fmtDateTime(u.last_ai_use_at) : '<span class="muted-dash">Never</span>')}
                ${field("Stored files (tracked)", `${formatBytes(u.storage_tracked_bytes)}${u.storage_plan_limit_gb != null ? " of " + u.storage_plan_limit_gb + " GB" : ""}`)}
            </div><p class="text-[10px] text-textSec mt-3">Support-level usage only. Sales, customers, inventory and expenses are not shown in Ops.</p>`)}
            ${section(`Alerts / Attention (${b.alerts.length})`, b.alerts.length ? healthChips(b.alerts) : '<p class="text-[11px] text-textSec">Nothing needs attention for this business.</p>')}
            ${section(`Audit History (${b.audit.length})`, b.audit.length ? `<div class="space-y-1.5">${b.audit.map(a => `<div class="text-[11px]"><span class="text-textSec">${fmtDateTime(a.created_at)}</span> · <span class="text-textMain font-semibold">${escapeHtml(auditLabel(a.action))}</span>${a.actor ? ` <span class="text-textSec">by ${escapeHtml(a.actor)}</span>` : ""}<div class="text-textSec">${escapeHtml(a.description)}</div></div>`).join("")}</div>` : '<p class="text-[11px] text-textSec">No subscription or account events recorded.</p>')}
        </div>`;
    bindOpenBusiness(document.getElementById("business-detail-content"));
}
function payStatusPill(status) {
    const map = { success: ["Successful", "bg-success/15 text-success"], failed: ["Failed", "bg-danger/15 text-danger"], initialized: ["Pending", "bg-cardHover text-textSec"], pending: ["Pending", "bg-cardHover text-textSec"] };
    const [label, cls] = map[status] || [(status || "").startsWith("flagged_") ? "Needs review" : status, (status || "").startsWith("flagged_") ? "bg-danger/15 text-danger" : "bg-cardHover text-textSec"];
    return `<span class="pill ${cls}">${escapeHtml(label)}</span>`;
}

// =============================================================================
// USERS
// =============================================================================
let usersOffset = 0;
let usersSearchTimer = null;
let usersBusinessId = null;

async function loadUsers(businessParam) {
    if (businessParam !== undefined) usersBusinessId = businessParam || null;
    const params = new URLSearchParams({ limit: PAGE_SIZE, offset: usersOffset });
    const q = document.getElementById("users-search").value.trim();
    if (q) params.set("q", q);
    [["role", "users-role-filter"], ["status", "users-status-filter"], ["activity", "users-activity-filter"]].forEach(([k, id]) => {
        const v = document.getElementById(id).value; if (v) params.set(k, v);
    });
    if (usersBusinessId) params.set("business_id", usersBusinessId);
    const data = await apiGet(`/api/platform/users?${params}`);
    Object.entries(data.summary).forEach(([k, v]) => {
        document.querySelectorAll(`[data-users-summary="${k}"]`).forEach(el => el.textContent = fmtInt(v));
    });
    setTitleFromDefinitions(data.activity_definitions, "[data-users-title]", "data-users-title");
    const bf = document.getElementById("users-business-filter");
    bf.classList.toggle("hidden", !usersBusinessId);
    if (usersBusinessId) {
        bf.innerHTML = `Showing one business only · <button type="button" id="users-clear-business" class="text-primary font-semibold cursor-pointer">Show all users</button>`;
        document.getElementById("users-clear-business").addEventListener("click", () => { usersBusinessId = null; usersOffset = 0; navigateTo("users"); });
    }
    const tbody = document.querySelector("#users-table tbody");
    tbody.innerHTML = data.items.map(u => `
        <tr class="cursor-pointer" data-open-user="${u.id}">
            <td>${u.currently_active ? '<span class="w-2 h-2 rounded-full bg-success inline-block mr-1" title="Active in the last 5 minutes"></span>' : ""}${escapeHtml([u.firstname, u.lastname].filter(Boolean).join(" ") || u.username)}</td>
            <td class="hidden md:table-cell font-mono">${escapeHtml(u.username)}</td>
            <td class="hidden sm:table-cell truncate max-w-[180px]">${escapeHtml(u.email)}${u.email_verified ? "" : ' <span class="text-[10px] text-warning">unverified</span>'}</td>
            <td><a class="text-primary hover:underline cursor-pointer" data-open-business="${u.business_id}">${escapeHtml(u.business_name || "—")}</a><div class="text-[10px] text-textSec font-mono">${escapeHtml(u.business_code || "")}</div></td>
            <td><span class="pill bg-cardHover text-textSec">${escapeHtml(u.role)}</span></td>
            <td class="hidden md:table-cell">${u.disabled ? '<span class="pill bg-danger/15 text-danger">Disabled</span>' : '<span class="pill bg-success/15 text-success">Active</span>'}</td>
            <td class="hidden lg:table-cell">${joinedDate(u.created_at)}</td>
            <td class="text-[11px]">${lastActiveCompact(u.last_active_at)}</td>
        </tr>`).join("");
    bindOpenBusiness(tbody);
    document.getElementById("users-empty").classList.toggle("hidden", data.items.length > 0);
    pager("users", usersOffset, data.items.length, data.total, PAGE_SIZE);
    const d = data.activity_definitions || {};
    document.getElementById("users-definitions").textContent = `Last Active: ${d.last_active || ""} Returning: ${d.returning || ""}`
        + (data.summary.join_date_not_recorded ? ` ${data.summary.join_date_not_recorded} account(s) predate join-date recording and show "Not recorded".` : "");
}
document.getElementById("users-search").addEventListener("input", () => {
    clearTimeout(usersSearchTimer);
    usersSearchTimer = setTimeout(() => { usersOffset = 0; loadUsers().catch(err => showGlobalError(err.message)); }, 350);
});
["users-role-filter", "users-status-filter", "users-activity-filter"].forEach(id =>
    document.getElementById(id).addEventListener("change", () => { usersOffset = 0; loadUsers().catch(err => showGlobalError(err.message)); }));
document.getElementById("users-prev").addEventListener("click", () => { usersOffset = Math.max(0, usersOffset - PAGE_SIZE); loadUsers().catch(err => showGlobalError(err.message)); });
document.getElementById("users-next").addEventListener("click", () => { usersOffset += PAGE_SIZE; loadUsers().catch(err => showGlobalError(err.message)); });

async function loadUserDetail(id) {
    const u = await apiGet(`/api/platform/users/${id}`);
    document.getElementById("user-detail-content").innerHTML = `
        <div class="stat-card mb-4">
            <div class="flex flex-wrap items-start justify-between gap-3">
                <div><div class="text-lg font-extrabold text-textMain">${escapeHtml([u.firstname, u.lastname].filter(Boolean).join(" ") || u.username)}</div>
                     <div class="text-[11px] text-textSec font-mono">${escapeHtml(u.username)}</div></div>
                <div class="flex gap-2">${u.status === "disabled" ? '<span class="pill bg-danger/15 text-danger">Disabled</span>' : '<span class="pill bg-success/15 text-success">Active</span>'}</div>
            </div>
            <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-4">
                ${field("Email", escapeHtml(u.email))}
                ${field("Email Verified", u.email_verified ? "Yes · " + fmtDate(u.email_verified_at) : '<span class="text-warning">Not verified</span>')}
                ${field("Joined", joinedDate(u.created_at))}
                ${field("Last Active", lastActive(u.last_active_at))}
                ${field("Password", u.must_change_password ? "Must change at next sign-in" : "Set by the user")}
                ${field("Email Change", u.email_change_pending ? "Pending verification" : "None pending")}
            </div>
        </div>
        <div class="stat-card mb-4"><div class="stat-label mb-2">Business Membership</div>
            ${u.memberships.map(m => `<div class="flex flex-wrap items-center gap-2 text-xs"><a class="text-primary hover:underline cursor-pointer font-semibold" data-open-business="${m.business_id}">${escapeHtml(m.business_name)}</a><span class="font-mono text-[10px] text-textSec">${escapeHtml(m.business_code)}</span><span class="pill bg-cardHover text-textSec">${escapeHtml(m.role)}</span></div>`).join("") || '<p class="text-[11px] text-textSec">No business.</p>'}
        </div>
        <div class="space-y-3">
            ${section(`Account &amp; Security Events (${u.account_events.length})`, u.account_events.length ? u.account_events.map(e => `<div class="text-[11px] mb-1.5"><span class="text-textSec">${fmtDateTime(e.created_at)}</span> · <span class="font-semibold text-textMain">${escapeHtml(auditLabel(e.action))}</span>${e.actor ? ` <span class="text-textSec">by ${escapeHtml(e.actor)}</span>` : ""}<div class="text-textSec">${escapeHtml(e.description)}</div></div>`).join("") : '<p class="text-[11px] text-textSec">No account or security events recorded.</p>', true)}
            ${section(`Recent Sessions (${u.recent_sessions.length})`, u.recent_sessions.length ? u.recent_sessions.map(s => `<div class="text-[11px] mb-1">Signed in ${fmtDateTime(s.signed_in_at)} · last interaction ${s.last_activity_at ? fmtDateTime(s.last_activity_at) : "none"}${s.signed_out_at ? " · signed out " + fmtDateTime(s.signed_out_at) : ""}</div>`).join("") : '<p class="text-[11px] text-textSec">No app sessions recorded.</p>')}
        </div>`;
    bindOpenBusiness(document.getElementById("user-detail-content"));
}

// =============================================================================
// SUBSCRIPTIONS
// =============================================================================
let subsOffset = 0;
function renderDistribution(container, entries) {
    if (!entries.length) { container.innerHTML = `<p class="text-[11px] text-textSec">No data yet.</p>`; return; }
    const max = Math.max(...entries.map(e => e[1]), 1);
    container.innerHTML = entries.map(([label, count]) => `
        <div>
            <div class="flex items-center justify-between text-[11px] mb-1"><span class="text-textMain font-medium">${escapeHtml(label)}</span><span class="text-textSec">${fmtInt(count)}</span></div>
            <div class="bar-track"><div class="bar-fill bg-primary" style="width:${Math.max(4, (count / max) * 100)}%"></div></div>
        </div>`).join("");
}
async function loadSubscriptions() {
    const params = new URLSearchParams({ limit: PAGE_SIZE, offset: subsOffset });
    [["status", "subs-status-filter"], ["plan", "subs-plan-filter"], ["filter", "subs-special-filter"], ["sort", "subs-sort"]].forEach(([k, id]) => {
        const v = document.getElementById(id).value; if (v) params.set(k, v);
    });
    const data = await apiGet(`/api/platform/subscriptions?${params}`);
    renderDistribution(document.getElementById("subs-by-status"),
        Object.entries(data.by_status).sort((a, b) => b[1] - a[1]).map(([k, v]) => [STATUS_LABELS[k] || k, v]));
    renderDistribution(document.getElementById("subs-by-plan"),
        Object.entries(data.by_plan).sort((a, b) => b[1] - a[1]).map(([k, v]) => [(data.by_plan_labels || {})[k] || k, v]));
    const tb = document.getElementById("subs-table-body");
    tb.innerHTML = data.items.map(s => `
        <tr class="cursor-pointer" data-open-business="${s.id}">
            <td><div class="font-semibold text-textMain">${escapeHtml(s.company_name)}</div><div class="text-[10px] text-textSec font-mono">${escapeHtml(s.business_code)}</div></td>
            <td>${planPill(s.plan_label, s.plan)}<div class="text-[10px] text-textSec">${escapeHtml(s.billing_interval || "")}</div></td>
            <td>${statusPill(s.subscription_status)}</td>
            <td class="hidden md:table-cell text-[11px]">${s.trial_end_at ? fmtDate(s.trial_end_at) : '<span class="muted-dash">—</span>'}</td>
            <td class="hidden lg:table-cell text-[11px]">${s.current_period_start ? fmtDate(s.current_period_start) + " → " + fmtDate(s.current_period_end) : '<span class="muted-dash">—</span>'}</td>
            <td class="text-[11px]">${s.next_billing_at ? fmtDate(s.next_billing_at) : `<span class="muted-dash">${s.cancel_at_period_end ? "Will not renew" : "None"}</span>`}</td>
            <td class="hidden md:table-cell text-[11px]">${s.last_payment_at ? fmtDate(s.last_payment_at) : '<span class="muted-dash">None</span>'}</td>
            <td class="hidden sm:table-cell">${s.billing_health.length ? healthChips(s.billing_health) : '<span class="text-[10px] muted-dash">OK</span>'}</td>
        </tr>`).join("");
    bindOpenBusiness(tb);
    document.getElementById("subs-table-empty").classList.toggle("hidden", data.items.length > 0);
    pager("subs", subsOffset, data.items.length, data.table_total, PAGE_SIZE);
    const ch = document.getElementById("subs-changes");
    ch.innerHTML = data.recent_changes.length ? data.recent_changes.map(e => `
        <div class="flex flex-wrap items-center gap-2 text-[11px]"><span class="text-textSec w-32 shrink-0">${fmtDateTime(e.at)}</span>
            <span class="font-semibold text-textMain">${escapeHtml(e.event)}</span>
            <a class="text-primary hover:underline cursor-pointer" data-open-business="${e.business_id}">${escapeHtml(e.company_name || "—")}</a></div>`).join("")
        : `<p class="text-[11px] text-textSec">No subscription changes in the last 30 days.</p>`;
    bindOpenBusiness(ch);
}
["subs-status-filter", "subs-plan-filter", "subs-special-filter", "subs-sort"].forEach(id =>
    document.getElementById(id).addEventListener("change", () => { subsOffset = 0; loadSubscriptions().catch(err => showGlobalError(err.message)); }));
document.getElementById("subs-prev").addEventListener("click", () => { subsOffset = Math.max(0, subsOffset - PAGE_SIZE); loadSubscriptions().catch(err => showGlobalError(err.message)); });
document.getElementById("subs-next").addEventListener("click", () => { subsOffset += PAGE_SIZE; loadSubscriptions().catch(err => showGlobalError(err.message)); });

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
    document.querySelectorAll("[data-rev]").forEach(el => el.textContent = fmtInt(get(data, el.getAttribute("data-rev"))));
    document.querySelectorAll("[data-rev-money]").forEach(el => el.textContent = fmtMoney(get(data, el.getAttribute("data-rev-money")), "NGN"));
    document.getElementById("rev-mrr").textContent = fmtMoney(data.mrr.naira, "NGN");
    document.getElementById("rev-mrr-sub").textContent = `${fmtInt(data.mrr.subscriptions)} active subscription(s) at current plan prices`;
    document.getElementById("rev-mrr-card").title = data.mrr.definition;
    document.getElementById("rev-refunds-note").textContent = data.refunds.reason;

    const byPlanEl = document.getElementById("revenue-by-plan");
    if (!data.by_plan.length) {
        byPlanEl.innerHTML = `<p class="text-[11px] text-textSec">No revenue recorded for this period.</p>`;
    } else {
        byPlanEl.innerHTML = data.by_plan.map(p => `
            <div>
                <div class="flex items-center justify-between text-[11px] mb-1"><span class="text-textMain font-medium" title="plan id: ${escapeHtml(p.plan || "")}">${escapeHtml(p.plan_label || p.plan || "unknown")}</span><span class="text-textSec">${fmtMoney(p.revenue_naira, "NGN")} · ${fmtPct(p.share_pct)} · ${fmtInt(p.payments)} pmts</span></div>
                <div class="bar-track"><div class="bar-fill bg-primary" style="width:${Math.max(4, p.share_pct || 0)}%"></div></div>
            </div>`).join("");
    }
    const h = data.payment_health;
    const cell = (label, n, cls) => `<div class="rounded-lg border border-borderCol px-3 py-2"><div class="text-textSec">${label}</div><div class="text-base font-bold ${cls}">${fmtInt(n)}</div></div>`;
    document.getElementById("revenue-payment-health").innerHTML = cell("Successful", h.successful, "text-success") + cell("Pending", h.pending, "text-textMain")
        + cell("Failed", h.failed, h.failed ? "text-danger" : "text-textMain") + cell("Needs review", h.needs_review, h.needs_review ? "text-danger" : "text-textMain")
        + `<div class="col-span-2 text-[10px] text-textSec">Refunded: not tracked (see Refunds). Card-verification charges are excluded.</div>`;

    const bizBody = document.getElementById("revenue-by-business-body");
    bizBody.innerHTML = data.by_business.map(b => `
        <tr class="cursor-pointer" data-open-business="${b.business_id}">
            <td><div class="font-semibold text-textMain">${escapeHtml(b.company_name || "—")}</div><div class="text-[10px] text-textSec font-mono">${escapeHtml(b.business_code || "")}</div></td>
            <td class="hidden sm:table-cell">${escapeHtml(b.plan_label || "—")}</td>
            <td>${fmtMoney(b.revenue_naira, "NGN")}</td>
            <td>${fmtInt(b.payments)}</td>
            <td class="hidden md:table-cell"><span class="muted-dash">Not tracked</span></td>
            <td>${fmtDate(b.last_payment_at)}</td>
        </tr>`).join("");
    bindOpenBusiness(bizBody);
    document.getElementById("revenue-by-business-empty").classList.toggle("hidden", data.by_business.length > 0);
}

// =============================================================================
// AI & COSTS
// =============================================================================
const AI_PRESETS = REVENUE_PRESETS;
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
    const s = data.summary;
    document.getElementById("ai-sum-requests").textContent = fmtInt(s.requests);
    document.getElementById("ai-sum-requests-sub").textContent = `${fmtInt(s.successful)} succeeded`;
    document.getElementById("ai-sum-credits").textContent = fmtInt(s.credits_consumed);
    document.getElementById("ai-sum-cost").textContent = fmtUsd(s.provider_cost_usd);
    document.getElementById("ai-sum-cost-sub").textContent = (s.provider_cost_ngn != null ? fmtMoney(s.provider_cost_ngn, "NGN") : "NGN unavailable")
        + (s.unpriced_requests ? ` · ${fmtInt(s.unpriced_requests)} unpriced request(s) not included` : "");
    document.getElementById("ai-sum-avg").textContent = s.average_cost_per_request_usd == null ? "—" : fmtUsd(s.average_cost_per_request_usd);
    document.getElementById("ai-sum-failed").textContent = fmtInt(s.failed);
    document.getElementById("ai-sum-failed-sub").textContent = s.requests ? `${(s.failed / s.requests * 100).toFixed(1)}% of requests` : "no requests";
    document.getElementById("ai-sum-businesses").textContent = fmtInt(s.businesses_using_ai);
    document.getElementById("ai-sum-pct").textContent = s.cost_pct_of_revenue == null ? "—" : `${s.cost_pct_of_revenue}%`;
    document.getElementById("ai-sum-pct-sub").textContent = s.cost_pct_of_revenue == null
        ? (s.revenue_naira ? "exchange rate unavailable" : "no subscription revenue in this period")
        : `${fmtMoney(s.provider_cost_ngn, "NGN")} of ${fmtMoney(s.revenue_naira, "NGN")}`;

    const cardsEl = document.getElementById("ai-provider-cards");
    const providers = Object.keys(data.providers);
    if (!providers.length) {
        cardsEl.innerHTML = `<p class="text-[11px] text-textSec sm:col-span-2">No AI requests recorded for this period.</p>`;
    } else {
        cardsEl.innerHTML = providers.map(p => {
            const v = data.providers[p];
            const pctKnown = v.budget_consumed_pct !== null && v.budget_consumed_pct !== undefined;
            const pct = pctKnown ? Math.min(100, v.budget_consumed_pct) : 0;
            const barColor = !pctKnown ? "bg-textSec" : pct >= 95 ? "bg-danger" : pct >= 75 ? "bg-warning" : "bg-success";
            return `<div class="stat-card">
                <div class="flex items-center justify-between mb-2"><span class="font-bold text-sm text-textMain capitalize">${escapeHtml(p)}</span><span class="text-[11px] text-textSec">${fmtInt(v.requests)} requests · ${fmtPct(v.failure_rate_pct)} failed</span></div>
                <div class="grid grid-cols-3 gap-2 text-[11px] mb-2">
                    <div><div class="text-textSec">Credits</div><div class="text-textMain font-semibold">${fmtInt(v.credits_consumed)}</div></div>
                    <div><div class="text-textSec">Provider cost</div><div class="text-textMain font-semibold">${v.priced_requests ? fmtUsd(v.provider_cost_usd) : "— (pricing not set)"}</div></div>
                    <div><div class="text-textSec">Avg / request</div><div class="text-textMain font-semibold">${v.average_cost_per_request_usd == null ? "—" : fmtUsd(v.average_cost_per_request_usd)}</div></div>
                </div>
                ${v.monthly_budget_ngn ? `
                <div class="text-[10px] text-textSec mb-1 flex justify-between"><span>This month vs internal budget</span><span>${pctKnown ? pct.toFixed(1) + "%" : "—"} of ${fmtMoney(v.monthly_budget_ngn, "NGN")}</span></div>
                <div class="bar-track"><div class="bar-fill ${barColor}" style="width:${Math.max(2, pct)}%"></div></div>
                <div class="text-[10px] text-textSec mt-1">Spent ${fmtMoney(v.month_to_date_cost_ngn, "NGN")} · projected month-end ${fmtMoney(v.projected_month_end_ngn, "NGN")}</div>
                ` : `<p class="text-[10px] text-textSec">No internal monthly budget set for ${escapeHtml(p)}.</p>`}
            </div>`;
        }).join("");
    }

    document.getElementById("ai-by-operation-body").innerHTML = data.by_operation.map(o => `
        <tr>
            <td>${escapeHtml(o.label)}</td>
            <td class="capitalize">${escapeHtml(o.provider || "—")}</td>
            <td>${fmtInt(o.requests)}</td>
            <td class="hidden sm:table-cell">${fmtInt(o.credits_consumed)}</td>
            <td>${o.requests - o.failed - o.unpriced_requests > 0 ? fmtUsd(o.provider_cost_usd) : '<span class="muted-dash">not priced</span>'}</td>
            <td class="hidden md:table-cell">${o.average_cost_per_request_usd == null ? "—" : fmtUsd(o.average_cost_per_request_usd)}</td>
            <td class="${o.failed ? "text-danger" : ""}">${fmtInt(o.failed)}</td>
        </tr>`).join("");
    document.getElementById("ai-by-operation-empty").classList.toggle("hidden", data.by_operation.length > 0);
    const bb = document.getElementById("ai-by-business-body");
    bb.innerHTML = data.by_business.map(b => `
        <tr class="cursor-pointer" data-open-business="${b.business_id}">
            <td><div class="font-semibold text-textMain">${escapeHtml(b.company_name || "—")}</div><div class="text-[10px] text-textSec font-mono">${escapeHtml(b.business_code || "")}</div></td>
            <td>${fmtInt(b.requests)}</td><td class="hidden sm:table-cell">${fmtInt(b.credits_consumed)}</td>
            <td>${fmtUsd(b.provider_cost_usd)}</td><td class="hidden md:table-cell text-[11px]">${fmtDateTime(b.last_ai_use_at)}</td>
        </tr>`).join("");
    bindOpenBusiness(bb);
    document.getElementById("ai-by-business-empty").classList.toggle("hidden", data.by_business.length > 0);
    const total = data.failures_by_reason.reduce((a, f) => a + f.count, 0);
    document.getElementById("ai-failures").innerHTML = total ? data.failures_by_reason.map(f => `
        <div><div class="flex items-center justify-between text-[11px] mb-1"><span class="text-textMain">${escapeHtml(f.label)}</span><span class="text-textSec">${fmtInt(f.count)}</span></div>
        <div class="bar-track"><div class="bar-fill bg-danger" style="width:${Math.max(4, f.count / total * 100)}%"></div></div></div>`).join("")
        : `<p class="text-[11px] text-textSec">No failed AI requests in this period.</p>`;
}

async function loadAiPricing() {
    const data = await apiGet("/api/platform/ai-pricing");
    const listEl = document.getElementById("ai-pricing-list");
    const rows = data.unconfigured_active_models.map(m => ({ ...m, input_price_per_1k_usd: null, output_price_per_1k_usd: null, updated_at: null, in_use: m.configured, missing: true }))
        .concat(data.configured);
    listEl.innerHTML = rows.map((r, i) => `
        <form data-pricing-row="${i}" class="grid grid-cols-2 sm:grid-cols-6 gap-2 items-center px-3 py-2 rounded-lg ${r.missing ? "bg-warning/10 border border-warning/25" : "bg-bgMain border border-borderCol"} text-[11px]">
            <div class="col-span-2"><span class="font-semibold text-textMain capitalize">${escapeHtml(r.provider)}</span> <span class="text-textSec">/ ${escapeHtml(r.model)}</span>
                <div class="text-[10px] ${r.missing ? "text-amber-300" : "text-textSec"}">${r.missing ? "In use — no price set" : (r.in_use ? "In use · " : "") + "updated " + fmtDate(r.updated_at)}</div></div>
            <input type="number" step="0.0001" min="0" name="in" value="${r.input_price_per_1k_usd ?? ""}" placeholder="$/1k input" class="bg-bgMain border border-borderCol rounded-lg px-2 py-1.5 text-textMain">
            <input type="number" step="0.0001" min="0" name="out" value="${r.output_price_per_1k_usd ?? ""}" placeholder="$/1k output" class="bg-bgMain border border-borderCol rounded-lg px-2 py-1.5 text-textMain">
            <button type="submit" class="col-span-2 bg-primary/15 hover:bg-primary/25 text-primary border border-primary/30 px-3 py-1.5 rounded-lg font-semibold cursor-pointer">Save</button>
        </form>`).join("") || `<p class="text-[11px] text-textSec">No models configured.</p>`;
    listEl.querySelectorAll("[data-pricing-row]").forEach(form => form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const r = rows[Number(form.getAttribute("data-pricing-row"))];
        const val = (n) => form.elements[n].value ? parseFloat(form.elements[n].value) : null;
        try {
            await apiPut("/api/platform/ai-pricing", { provider: r.provider, model: r.model, input_price_per_1k_usd: val("in"), output_price_per_1k_usd: val("out") });
            await loadAiPricing();
        } catch (err) { showGlobalError(err.message); }
    }));
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

function renderFxCard(fx) {
    if (!fx) return;
    document.getElementById("fx-rate").textContent = fx.effective_rate != null ? fmtMoney(fx.effective_rate, "NGN") : "—";
    const modePill = document.getElementById("fx-mode-pill");
    modePill.textContent = fx.manual_override_enabled ? "Manual" : "Automatic";
    modePill.className = "pill " + (fx.manual_override_enabled ? "bg-warning/15 text-amber-300" : "bg-success/15 text-success");
    const statusPill = document.getElementById("fx-status-pill");
    let statusText, statusCls;
    if (fx.manual_override_enabled) { statusText = "Manual"; statusCls = "bg-warning/15 text-amber-300"; }
    else if (fx.auto_rate == null) { statusText = "Unavailable"; statusCls = "bg-danger/15 text-danger"; }
    else if (fx.auto_stale) { statusText = "Stale"; statusCls = "bg-warning/15 text-amber-300"; }
    else { statusText = "Live"; statusCls = "bg-success/15 text-success"; }
    statusPill.textContent = statusText;
    statusPill.className = "pill " + statusCls;
    document.getElementById("fx-source").textContent = fx.manual_override_enabled ? "Manual override" : (fx.auto_source || "—");
    document.getElementById("fx-updated").textContent = fx.manual_override_enabled ? "Manual (not updated automatically)" : (fx.auto_fetched_at ? fmtDateTime(fx.auto_fetched_at) : "Never");
    document.getElementById("fx-override-banner").classList.toggle("hidden", !fx.manual_override_enabled);
    const errEl = document.getElementById("fx-error-banner");
    if (fx.auto_last_error) {
        errEl.textContent = "Last automatic refresh attempt failed: " + fx.auto_last_error;
        errEl.classList.remove("hidden");
    } else {
        errEl.classList.add("hidden");
    }
}
async function loadPlatformSettings() {
    const s = await apiGet("/api/platform/settings");
    document.getElementById("setting-usd-ngn").value = s.usd_to_ngn_rate ?? "";
    document.getElementById("setting-fx-override-enabled").checked = !!(s.fx && s.fx.manual_override_enabled);
    document.getElementById("setting-gemini-budget").value = s.gemini_monthly_budget_ngn ?? "";
    document.getElementById("setting-openai-budget").value = s.openai_monthly_budget_ngn ?? "";
    document.getElementById("setting-thresholds").value = (s.ai_alert_thresholds || []).join(", ");
    renderFxCard(s.fx);
}
document.getElementById("platform-settings-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
        const thresholdsRaw = document.getElementById("setting-thresholds").value.trim();
        const body = {
            usd_to_ngn_rate: document.getElementById("setting-usd-ngn").value ? parseFloat(document.getElementById("setting-usd-ngn").value) : null,
            fx_manual_override_enabled: document.getElementById("setting-fx-override-enabled").checked,
            gemini_monthly_budget_ngn: document.getElementById("setting-gemini-budget").value ? parseFloat(document.getElementById("setting-gemini-budget").value) : null,
            openai_monthly_budget_ngn: document.getElementById("setting-openai-budget").value ? parseFloat(document.getElementById("setting-openai-budget").value) : null,
        };
        if (thresholdsRaw) body.ai_alert_thresholds = thresholdsRaw.split(",").map(s => parseInt(s.trim(), 10)).filter(n => !isNaN(n));
        await apiPut("/api/platform/settings", body);
        await loadPlatformSettings();
        await aiPeriodCtl.applyNow();
    } catch (err) { showGlobalError(err.message); }
});
document.getElementById("fx-refresh-btn").addEventListener("click", async () => {
    const btn = document.getElementById("fx-refresh-btn");
    const icon = btn.querySelector("i");
    try {
        btn.disabled = true;
        if (icon) icon.classList.add("animate-spin");
        const resp = await apiPost("/api/platform/fx/refresh");
        renderFxCard(resp.fx);
        if (!resp.succeeded) {
            showGlobalError(resp.error || "Could not refresh the exchange rate right now. The previous rate is still in use.");
        }
    } catch (err) {
        showGlobalError(err.message);
    } finally {
        btn.disabled = false;
        if (icon) icon.classList.remove("animate-spin");
    }
});

// =============================================================================
// ALERTS - one grouped, plain-English inbox (Cauldra records, payments, email,
// AI providers, storage, background jobs, and Sentry when it is connected)
// =============================================================================
let alertsFilter = "unresolved";
function sevPill(sev) {
    const cls = sev === "critical" ? "bg-danger/20 text-danger" : sev === "high" ? "bg-warning/20 text-amber-300" : sev === "medium" ? "bg-primary/15 text-primary" : "bg-cardHover text-textSec";
    return `<span class="pill ${cls}">${escapeHtml(sev)}</span>`;
}
function sevBorder(sev) {
    return sev === "critical" ? "border-danger/40 bg-danger/10" : sev === "high" ? "border-warning/40 bg-warning/10" : "border-borderCol bg-cardBg";
}
async function loadAlerts() {
    document.querySelectorAll(".alert-filter-btn").forEach(btn => {
        const active = btn.getAttribute("data-alert-filter") === alertsFilter;
        btn.classList.toggle("bg-primary/15", active); btn.classList.toggle("text-primary", active); btn.classList.toggle("border-primary/30", active);
        btn.classList.toggle("bg-cardBg", !active); btn.classList.toggle("text-textSec", !active);
    });
    const params = new URLSearchParams({ limit: 200 });
    if (alertsFilter !== "all") params.set("state", alertsFilter);
    const data = await apiGet(`/api/platform/alerts?${params}`);
    Object.entries(data.counts || {}).forEach(([k, v]) => document.querySelectorAll(`[data-alert-count="${k}"]`).forEach(el => el.textContent = `(${v})`));
    document.getElementById("alerts-sources").textContent = "Sources: Cauldra records, payments, email, AI providers, storage, background jobs"
        + (data.sources.sentry === "connected" ? ", Sentry." + (data.sources.sentry_last_error ? ` Last Sentry sync failed (${data.sources.sentry_last_error}).` : "") : ". Sentry is not connected (needs SENTRY_API_TOKEN, SENTRY_ORG_SLUG, SENTRY_PROJECT_SLUG).");
    const listEl = document.getElementById("alerts-list");
    listEl.innerHTML = data.items.map(a => `
        <div class="rounded-xl border ${sevBorder(a.severity)} p-3">
            <div class="flex flex-col sm:flex-row sm:items-start justify-between gap-2">
                <div class="min-w-0">
                    <div class="flex flex-wrap items-center gap-2 mb-1">
                        ${sevPill(a.severity)}
                        <span class="font-semibold text-textMain text-xs">${escapeHtml(a.title)}</span>
                        <span class="pill bg-cardHover text-textSec">${escapeHtml(a.state)}</span>
                    </div>
                    <p class="text-[11px] text-textMain leading-relaxed">${escapeHtml(a.message)}</p>
                    ${a.impact ? `<p class="text-[11px] text-textSec leading-relaxed"><span class="font-semibold">May affect:</span> ${escapeHtml(a.impact)}</p>` : ""}
                    ${a.why_it_matters ? `<p class="text-[11px] text-textSec leading-relaxed"><span class="font-semibold">Why it matters:</span> ${escapeHtml(a.why_it_matters)}</p>` : ""}
                    <p class="text-[10px] text-textSec mt-1">${escapeHtml(a.source)} · first seen ${fmtDateTime(a.first_seen)} · last seen ${fmtDateTime(a.last_seen)} · ${fmtInt(a.occurrences)} occurrence(s)${a.affected_businesses != null ? ` · ${fmtInt(a.affected_businesses)} business(es)` : ""}${a.resolved_at ? " · resolved " + fmtDateTime(a.resolved_at) : ""}</p>
                    ${a.technical_detail || a.external_url ? `<details class="mt-1"><summary class="text-[10px] text-primary cursor-pointer">Technical detail</summary><pre class="text-[10px] text-textSec whitespace-pre-wrap mt-1">${escapeHtml(a.technical_detail || "")}</pre>${a.external_url ? `<a href="${escapeHtml(a.external_url)}" target="_blank" rel="noopener noreferrer" class="text-[10px] text-primary">Open in Sentry</a>` : ""}</details>` : ""}
                </div>
                <div class="flex sm:flex-col gap-1.5 shrink-0">
                    ${a.state !== "watching" && a.state !== "resolved" ? `<button type="button" data-alert-state="watching" data-alert-id="${a.id}" class="bg-primary/15 hover:bg-primary/25 text-primary border border-primary/30 px-2.5 py-1 rounded-lg text-[10px] font-semibold cursor-pointer">Watch</button>` : ""}
                    ${a.state !== "resolved" && !a.self_clearing ? `<button type="button" data-alert-state="resolved" data-alert-id="${a.id}" class="bg-success/15 hover:bg-success/25 text-success border border-success/30 px-2.5 py-1 rounded-lg text-[10px] font-semibold cursor-pointer">Resolve</button>` : ""}
                    ${a.state !== "resolved" && a.self_clearing ? `<span class="text-[10px] text-textSec max-w-[120px]">Clears by itself once fixed</span>` : ""}
                    ${a.state === "resolved" && !a.self_clearing ? `<button type="button" data-alert-state="unresolved" data-alert-id="${a.id}" class="bg-cardHover text-textSec border border-borderCol px-2.5 py-1 rounded-lg text-[10px] font-semibold cursor-pointer">Reopen</button>` : ""}
                </div>
            </div>
        </div>`).join("");
    listEl.querySelectorAll("[data-alert-state]").forEach(btn => btn.addEventListener("click", async () => {
        try {
            await apiPost(`/api/platform/alerts/${btn.getAttribute("data-alert-id")}/state`, { state: btn.getAttribute("data-alert-state") });
            await loadAlerts();
        } catch (err) { showGlobalError(err.message); }
    }));
    document.getElementById("alerts-empty").classList.toggle("hidden", data.items.length > 0);
    refreshAlertBadge();
}
document.querySelectorAll(".alert-filter-btn").forEach(btn => btn.addEventListener("click", () => { alertsFilter = btn.getAttribute("data-alert-filter"); loadAlerts().catch(err => showGlobalError(err.message)); }));

async function refreshAlertBadge() {
    try {
        const data = await apiGet(`/api/platform/alerts?state=unresolved&limit=200`);
        const badge = document.getElementById("alerts-nav-badge");
        const n = data.items.length;
        badge.textContent = n > 99 ? "99+" : String(n);
        badge.classList.toggle("hidden", n === 0);
    } catch (_) { /* non-critical */ }
}

// =============================================================================
// SYSTEM HEALTH - checks run on load; a result older than fresh_seconds turns
// STALE on screen instead of staying green.
// =============================================================================
const HEALTH_STYLE = {
    healthy: ["Healthy", "text-success", "bg-success/15 text-success"],
    needs_attention: ["Needs attention", "text-warning", "bg-warning/15 text-warning"],
    down: ["Down", "text-danger", "bg-danger/15 text-danger"],
    unknown: ["Unknown", "text-textSec", "bg-cardHover text-textSec"],
    not_configured: ["Not configured", "text-textSec", "bg-cardHover text-textSec"],
    stale: ["Stale", "text-textSec", "bg-cardHover text-textSec"],
};
let lastHealth = null;
let healthTimer = null;
function healthIsStale(h) {
    return !h || (Date.now() - new Date(h.checked_at).getTime()) > (h.fresh_seconds || 300) * 1000;
}
function renderHealth() {
    const h = lastHealth;
    if (!h) return;
    const stale = healthIsStale(h);
    const order = ["app", "database", "storage", "payments", "email", "ai", "background", "errors"];
    document.getElementById("health-cards").innerHTML = order.filter(k => h.checks[k]).map(k => {
        const c = h.checks[k];
        const [label, textCls, pillCls] = HEALTH_STYLE[stale ? "stale" : c.status] || HEALTH_STYLE.unknown;
        return `<div class="stat-card">
            <div class="flex items-center justify-between gap-2"><div class="stat-label">${escapeHtml(c.label)}</div><span class="pill ${pillCls}">${label}</span></div>
            <div class="text-sm font-bold ${textCls} mt-2">${stale ? "Result is older than " + Math.round((h.fresh_seconds || 300) / 60) + " min — refresh" : escapeHtml(c.summary || "")}</div>
            ${c.detail ? `<div class="text-[10px] text-textSec mt-1 leading-relaxed">${escapeHtml(c.detail)}</div>` : ""}
            <div class="text-[10px] text-textSec mt-2">Checked ${fmtDateTime(c.checked_at)}${c.signal_at ? " · signal " + fmtDateTime(c.signal_at) : ""} · <span class="prov-tag">${escapeHtml(c.provenance || "")}</span></div>
        </div>`;
    }).join("");
    const ct = h.counters;
    const counter = (label, v, note) => `<div class="stat-card" ${note ? `title="${escapeHtml(note)}"` : ""}><div class="stat-label">${label}</div><div class="stat-value ${v === null ? "!text-sm text-textSec" : ""}">${v === null ? "Unavailable" : fmtInt(v)}</div></div>`;
    document.getElementById("health-counters").innerHTML = counter("Failed AI Requests (24H)", ct.failed_ai_requests_24h) + counter("Failed Payments (24H)", ct.failed_payments_24h)
        + counter("Webhooks Received (24H)", ct.webhooks_received_24h) + counter("Failed Webhooks (24H)", ct.failed_webhooks_24h, (h.counter_notes || {}).failed_webhooks_24h)
        + counter("Open Critical Alerts", ct.open_critical_alerts);
    document.getElementById("health-checked-at").textContent = (stale ? "STALE — " : "") + "Checked " + fmtDateTime(h.checked_at) + ". Results older than " + Math.round((h.fresh_seconds || 300) / 60) + " minutes are shown as stale.";
}
async function loadSystemHealth() {
    lastHealth = await apiGet("/api/platform/system-health");
    renderHealth();
    if (!healthTimer) healthTimer = setInterval(() => { if (currentView === "system-health") renderHealth(); }, 30000);
}

// =============================================================================
// INFRASTRUCTURE
// =============================================================================
function prov(label) { return `<span class="prov-tag">${escapeHtml(label)}</span>`; }
function kv(label, value) { return `<div class="flex justify-between gap-3 text-[11px] py-0.5"><span class="text-textSec">${label}</span><span class="text-textMain text-right">${value}</span></div>`; }
async function loadInfrastructure() {
    const i = await apiGet("/api/platform/infrastructure");
    const P = i.provenance_labels;
    const st = i.storage, inv = st.provider_inventory;
    let invHtml;
    if (!inv) invHtml = `<p class="text-[11px] text-textSec">Provider inventory not checked yet.</p>`;
    else if (inv.status !== "ok") invHtml = `<p class="text-[11px] text-textSec">${escapeHtml(inv.reason || "Unavailable")} · ${fmtDateTime(inv.checked_at)}</p>`;
    else invHtml = kv("Objects in provider", fmtInt(inv.provider_objects) + (inv.complete ? "" : " (partial)"))
        + kv("Bytes in provider", formatBytes(inv.provider_bytes))
        + kv("Records with no object", inv.records_missing_object ? `<span class="text-danger">${fmtInt(inv.records_missing_object)}</span>` : "0")
        + kv("Objects with no record", inv.objects_without_record == null ? "Unknown (partial listing)" : inv.objects_without_record ? `<span class="text-warning">${fmtInt(inv.objects_without_record)}</span>` : "0")
        + kv("Checked", fmtDateTime(inv.checked_at));
    document.getElementById("infra-storage").innerHTML = `
        <div class="flex items-center justify-between mb-2"><div class="stat-label">Storage · ${escapeHtml(st.provider)}</div>
            <button type="button" id="infra-storage-check" class="text-[10px] font-semibold text-primary cursor-pointer"><i class="fa-solid fa-rotate text-[10px]"></i> Check provider now</button></div>
        <div class="text-[10px] text-textSec mb-1">Cauldra-tracked storage ${prov(P.calculated)}</div>
        ${kv("Tracked bytes", formatBytes(st.tracked.bytes))}${kv("Tracked objects", fmtInt(st.tracked.objects))}
        <div class="text-[10px] text-textSec mt-3 mb-1">Provider inventory ${prov(P.live)} <span class="text-[10px]">(read-only; nothing is deleted)</span></div>
        ${invHtml}`;
    document.getElementById("infra-storage-check").addEventListener("click", async (e) => {
        e.target.disabled = true;
        try { await apiPost("/api/platform/infrastructure/storage-check"); await loadInfrastructure(); } catch (err) { showGlobalError(err.message); } finally { e.target.disabled = false; }
    });
    const d = i.database;
    document.getElementById("infra-database").innerHTML = `<div class="stat-label mb-2">Database ${prov(d.provenance)}</div>
        ${kv("Provider", escapeHtml(d.provider))}${kv("Region", escapeHtml(d.region || "Not shown by the host name"))}
        ${kv("Connection", d.connection === "connected" ? '<span class="text-success">Connected</span>' : `<span class="text-danger">${escapeHtml(d.error || "Unreachable")}</span>`)}
        ${kv("Migration", escapeHtml(d.migration.state || "unknown"))}${kv("PostgreSQL", escapeHtml(d.postgres_version || "—"))}
        ${kv("Database size", d.size_bytes != null ? formatBytes(d.size_bytes) : "—")}${kv("Last verified", fmtDateTime(d.checked_at))}
        <p class="text-[10px] text-textSec mt-2 leading-relaxed">${escapeHtml(d.migration.summary || "")}</p>`;
    document.getElementById("infra-ai").innerHTML = `<div class="stat-label mb-2">AI Providers ${prov(P.configuration)}</div>`
        + i.ai_providers.map(a => kv(escapeHtml(a.provider), `${escapeHtml(a.model)} · ${a.configured ? "configured" : '<span class="text-textSec">not configured</span>'}`)).join("")
        + `<p class="text-[10px] text-textSec mt-2">Configured is not the same as healthy — see System Health.</p>`;
    const em = i.email;
    document.getElementById("infra-email").innerHTML = `<div class="stat-label mb-2">Email ${prov(P.configuration)}</div>${kv("Provider", em.provider)}${kv("Configured", em.configured ? "Yes" : '<span class="text-danger">No</span>')}${kv("Sender domain", escapeHtml(em.sender_domain || "—"))}`;
    const pay = i.payments;
    document.getElementById("infra-payments").innerHTML = `<div class="stat-label mb-2">Payments ${prov(P.configuration)}</div>${kv("Provider", pay.provider)}${kv("Configured", pay.configured ? "Yes" : '<span class="text-danger">No</span>')}${kv("Mode", escapeHtml(pay.mode || "unknown"))}${kv("Plan codes", `${pay.plan_codes_configured} of ${pay.plan_codes_expected}`)}`;
    const ho = i.hosting;
    document.getElementById("infra-hosting").innerHTML = `<div class="stat-label mb-2">Hosting ${prov(P.configuration)}</div>${kv("Environment", escapeHtml(ho.environment))}${kv("Platform", escapeHtml(ho.platform || "—"))}${kv("Service", escapeHtml(ho.service || "—"))}${kv("Deployment", escapeHtml(ho.deployment_id || "—"))}${kv("Release", escapeHtml(ho.release || "—"))}${kv("Running since", fmtDateTime(ho.running_since))}`;
    const bk = i.backups;
    document.getElementById("infra-backups").innerHTML = `<div class="stat-label mb-2">Backups ${prov(bk.provenance)}</div>
        ${kv("Last successful backup", '<span class="text-textSec">Unavailable</span>')}${kv("Off-platform backup variables", bk.off_platform_script_configured ? "Set" : "Not set")}
        <p class="text-[10px] text-textSec mt-2 leading-relaxed">${escapeHtml(bk.summary)} ${escapeHtml(bk.provider_backups)}</p>`;
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

// Loaded by login.js only after sign-in (the server refuses this file without
// a platform-owner session), so a token is always present here.
(function boot() {
    let storedEmail = "";
    try { storedEmail = sessionStorage.getItem(EMAIL_KEY) || ""; } catch (_) {}
    document.getElementById("sidebar-owner-email").textContent = storedEmail;
    showApp();
    const [view, param] = (location.hash.replace("#", "") || "overview").split("/");
    navigateTo(view, param);
})();
