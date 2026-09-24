// =============================================================================
// CAULDRA PLATFORM OWNER CONTROL PANEL - sign-in (public part)
// Completely separate bundle from frontend/js/app.js (the customer app).
// OBS-7: this is the ONLY console code an unauthenticated visitor can fetch.
// The console markup and logic (console.html / console.js) are served by the
// backend only with a valid platform-owner session cookie, which the MFA step
// sets; loadConsole() below fetches them after sign-in.
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
    document.getElementById("app-shell")?.classList.add("hidden");
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
        await loadConsole();
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

// ---------------------------------------------------------------- Console loader
async function loadConsole() {
    const res = await fetch("console.html", { credentials: "same-origin", cache: "no-store" });
    if (!res.ok) {
        signOutLocally();
        showLogin();
        throw new Error("Please sign in again.");
    }
    document.getElementById("console-root").innerHTML = await res.text();
    await new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src = "console.js";
        script.onload = resolve;
        script.onerror = () => { signOutLocally(); showLogin(); reject(new Error("Please sign in again.")); };
        document.body.appendChild(script);
    });
}

(function bootSignIn() {
    if (accessToken) {
        loadConsole().catch(err => showLoginError(err.message || "Please sign in again."));
    } else {
        showLogin();
    }
})();
