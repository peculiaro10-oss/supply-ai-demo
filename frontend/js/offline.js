(function () {
    "use strict";

    const DB_NAME = "cauldra_offline";
    const DB_VERSION = 2;
    const CLIENT_SCHEMA_VERSION = 2;
    const PBKDF2_ITERATIONS = 310000;
    const LOCK_AFTER_FAILURES = 5;
    const textEncoder = new TextEncoder();
    const textDecoder = new TextDecoder();
    let dbPromise = null;
    let active = null;

    function bytesToB64(bytes) {
        let value = "";
        const array = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
        for (let i = 0; i < array.length; i += 1) value += String.fromCharCode(array[i]);
        return btoa(value).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    }

    function b64ToBytes(value) {
        const normalized = String(value).replace(/-/g, "+").replace(/_/g, "/");
        const raw = atob(normalized + "=".repeat((4 - normalized.length % 4) % 4));
        return Uint8Array.from(raw, (character) => character.charCodeAt(0));
    }

    function scopeFor(user, business) {
        const userId = user && user.id;
        const businessId = (business && (business.id || business.business_id)) || (user && user.business_id);
        return userId && businessId ? `${businessId}:${userId}` : null;
    }

    function requestToPromise(request) {
        return new Promise((resolve, reject) => {
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error || new Error("Offline database request failed."));
        });
    }

    function openDb() {
        if (dbPromise) return dbPromise;
        dbPromise = new Promise((resolve, reject) => {
            if (!("indexedDB" in window)) return reject(new Error("Offline storage is unavailable."));
            const request = indexedDB.open(DB_NAME, DB_VERSION);
            request.onupgradeneeded = () => {
                const db = request.result;
                if (!db.objectStoreNames.contains("outbox")) {
                    const legacy = db.createObjectStore("outbox", { keyPath: "op_id" });
                    legacy.createIndex("by_business", "business_id");
                }
                if (!db.objectStoreNames.contains("products_cache")) {
                    db.createObjectStore("products_cache", { keyPath: "id" }).createIndex("by_business", "business_id");
                }
                if (!db.objectStoreNames.contains("suppliers_cache")) {
                    db.createObjectStore("suppliers_cache", { keyPath: "id" }).createIndex("by_business", "business_id");
                }
                if (!db.objectStoreNames.contains("meta")) db.createObjectStore("meta", { keyPath: "key" });
                if (!db.objectStoreNames.contains("offline_identities")) db.createObjectStore("offline_identities", { keyPath: "scope" });
                if (!db.objectStoreNames.contains("secure_cache")) {
                    const cache = db.createObjectStore("secure_cache", { keyPath: "key" });
                    cache.createIndex("by_scope", "scope");
                }
                if (!db.objectStoreNames.contains("secure_outbox")) {
                    const outbox = db.createObjectStore("secure_outbox", { keyPath: "op_id" });
                    outbox.createIndex("by_scope", "scope");
                    outbox.createIndex("by_scope_status", ["scope", "status"]);
                }
            };
            request.onsuccess = () => {
                request.result.onversionchange = () => request.result.close();
                resolve(request.result);
            };
            request.onerror = () => reject(request.error || new Error("Offline storage could not open."));
            request.onblocked = () => reject(new Error("Close other Cauldra tabs to upgrade offline storage."));
        });
        return dbPromise;
    }

    async function transaction(storeNames, mode, callback) {
        const db = await openDb();
        return new Promise((resolve, reject) => {
            const transactionValue = db.transaction(storeNames, mode);
            const stores = Object.fromEntries([].concat(storeNames).map((name) => [name, transactionValue.objectStore(name)]));
            let result;
            Promise.resolve(callback(stores, transactionValue)).then((value) => { result = value; }).catch((error) => {
                try { transactionValue.abort(); } catch (_) {}
                reject(error);
            });
            transactionValue.oncomplete = () => resolve(result);
            transactionValue.onerror = () => reject(transactionValue.error || new Error("Offline storage transaction failed."));
            transactionValue.onabort = () => reject(transactionValue.error || new Error("Offline storage transaction was cancelled."));
        });
    }

    async function getIdentity(scope) {
        return transaction("offline_identities", "readonly", ({ offline_identities: store }) => requestToPromise(store.get(scope)));
    }

    async function listIdentities() {
        const rows = await transaction("offline_identities", "readonly", ({ offline_identities: store }) => requestToPromise(store.getAll()));
        return (rows || []).map(({ scope, user_id, business_id, display_name, business_name, expires_at, last_server_verified_at }) => ({
            scope, user_id, business_id, display_name, business_name, expires_at, last_server_verified_at,
        }));
    }

    async function putIdentity(identity) {
        return transaction("offline_identities", "readwrite", ({ offline_identities: store }) => requestToPromise(store.put(identity)));
    }

    async function derivePinKey(pin, salt) {
        const material = await crypto.subtle.importKey("raw", textEncoder.encode(pin), "PBKDF2", false, ["deriveKey"]);
        return crypto.subtle.deriveKey({ name: "PBKDF2", salt, iterations: PBKDF2_ITERATIONS, hash: "SHA-256" }, material,
            { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
    }

    async function importDataKey(raw) {
        return crypto.subtle.importKey("raw", raw, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
    }

    async function sealWithKey(key, value, aad) {
        const iv = crypto.getRandomValues(new Uint8Array(12));
        const plain = textEncoder.encode(JSON.stringify(value));
        const cipher = await crypto.subtle.encrypt({ name: "AES-GCM", iv, additionalData: textEncoder.encode(aad) }, key, plain);
        return { v: CLIENT_SCHEMA_VERSION, iv: bytesToB64(iv), data: bytesToB64(cipher) };
    }

    async function openWithKey(key, envelope, aad) {
        if (!envelope || envelope.v !== CLIENT_SCHEMA_VERSION) throw new Error("Offline data uses an unsupported format.");
        const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: b64ToBytes(envelope.iv), additionalData: textEncoder.encode(aad) }, key, b64ToBytes(envelope.data));
        return JSON.parse(textDecoder.decode(plain));
    }

    async function verifyGrant(record) {
        const publicKey = await crypto.subtle.importKey("jwk", record.public_key, { name: "ECDSA", namedCurve: "P-256" }, false, ["verify"]);
        const valid = await crypto.subtle.verify({ name: "ECDSA", hash: "SHA-256" }, publicKey, b64ToBytes(record.signature), textEncoder.encode(record.grant_payload));
        if (!valid) throw new Error("Offline authorization failed its integrity check.");
        const grant = JSON.parse(textDecoder.decode(b64ToBytes(record.grant_payload)));
        if (grant.schema_version !== CLIENT_SCHEMA_VERSION || grant.user_id !== record.user_id || grant.business_id !== record.business_id || grant.device_id !== record.device_id) {
            throw new Error("Offline authorization does not match this workspace.");
        }
        if (Number(grant.expires_at) * 1000 <= Date.now()) throw new Error("Offline access has expired. Connect to the internet to sign in again.");
        return grant;
    }

    async function openVerifiedGrant(record, dataKey) {
        if (!record.sealed_grant) {
            throw new Error("Offline authorization needs an online security refresh before it can be used.");
        }
        let protectedGrant;
        try {
            protectedGrant = await openWithKey(dataKey, record.sealed_grant, `cauldra-grant:${record.scope}`);
        } catch (_) {
            throw new Error("Offline authorization failed its integrity check.");
        }
        return verifyGrant({ ...record, ...protectedGrant });
    }

    async function applyWorkingOverlays(scope, dataKey, snapshot) {
        const merged = { ...snapshot };
        for (const [kind, field] of [["products", "products"], ["suppliers", "suppliers"], ["warehouse_stocks", "stocks"], ["sales", "sales"], ["expenses", "expenses"], ["purchase_orders", "purchase_orders"]]) {
            const row = await transaction("secure_cache", "readonly", ({ secure_cache: store }) => requestToPromise(store.get(`${scope}:${kind}`)));
            if (row) merged[field] = await openWithKey(dataKey, row.sealed, `cauldra-cache:${scope}:${kind}`);
        }
        return merged;
    }

    function lockDelay(failures) {
        if (failures < LOCK_AFTER_FAILURES) return 0;
        return Math.min(15 * 60 * 1000, 30000 * Math.pow(2, failures - LOCK_AFTER_FAILURES));
    }

    async function recordPinFailure(record) {
        const failures = Number(record.failed_attempts || 0) + 1;
        const delay = lockDelay(failures);
        await putIdentity({ ...record, failed_attempts: failures, locked_until: delay ? Date.now() + delay : 0 });
        return { failures, delay };
    }

    async function unlock(scope, pin, options) {
        const record = await getIdentity(scope);
        if (!record) throw new Error("Connect to the internet to sign in on this device for the first time.");
        if (record.locked_until && record.locked_until > Date.now()) {
            const seconds = Math.ceil((record.locked_until - Date.now()) / 1000);
            throw new Error(`Too many incorrect attempts. Try again in ${seconds} seconds.`);
        }
        const pinKey = await derivePinKey(pin, b64ToBytes(record.pin_salt));
        let rawDataKey;
        try {
            rawDataKey = await openWithKey(pinKey, record.wrapped_data_key, `cauldra-key:${scope}`);
        } catch (_) {
            const outcome = await recordPinFailure(record);
            if (outcome.delay) throw new Error(`Too many incorrect attempts. Offline access is locked for ${Math.ceil(outcome.delay / 1000)} seconds.`);
            throw new Error("That offline PIN is incorrect.");
        }
        const dataKey = await importDataKey(b64ToBytes(rawDataKey.key));
        const grant = await openVerifiedGrant(record, dataKey);
        try {
            const snapshotRow = await transaction("secure_cache", "readonly", ({ secure_cache: store }) => requestToPromise(store.get(`${scope}:snapshot`)));
            if (!snapshotRow) throw new Error("No synchronized workspace is available on this device.");
            const snapshot = await applyWorkingOverlays(scope, dataKey, await openWithKey(dataKey, snapshotRow.sealed, `cauldra-cache:${scope}:snapshot`));
            active = { scope, record: { ...record, failed_attempts: 0, locked_until: 0 }, grant, dataKey, rawDataKey, snapshot, offline: !!(options && options.offline) };
            await putIdentity(active.record);
            await migrateLegacy(scope);
            announce("Offline workspace unlocked.");
            return { grant, snapshot, scope };
        } catch (_) {
            throw new Error("Offline data failed its integrity check. Connect to the internet to refresh this workspace.");
        }
    }

    async function resumeOnline({ user, business }) {
        const scope = scopeFor(user, business);
        if (!scope) return null;
        const record = await getIdentity(scope);
        if (!record || !record.device_key) return null;
        try {
            const grant = await openVerifiedGrant(record, record.device_key);
            if (grant.auth_version !== Number(user.auth_version || 1) || grant.role !== user.role) {
                active = { scope, record, grant, dataKey: record.device_key, rawDataKey: null, snapshot: null, offline: false };
                await quarantineActive("Your account role or authorization changed while this device was offline.");
                return null;
            }
            const snapshotRow = await transaction("secure_cache", "readonly", ({ secure_cache: store }) => requestToPromise(store.get(`${scope}:snapshot`)));
            const snapshot = snapshotRow ? await applyWorkingOverlays(scope, record.device_key, await openWithKey(record.device_key, snapshotRow.sealed, `cauldra-cache:${scope}:snapshot`)) : null;
            active = { scope, record, grant, dataKey: record.device_key, rawDataKey: null, snapshot, offline: false };
            await migrateLegacy(scope);
            return snapshot;
        } catch (_) {
            return null;
        }
    }

    async function provision({ apiUrl, token, user, business, pin }) {
        if (!crypto || !crypto.subtle) throw new Error("Secure offline access is not supported on this device.");
        if (!/^\d{6,12}$/.test(pin)) throw new Error("Use a 6–12 digit offline PIN.");
        const scope = scopeFor(user, business);
        if (!scope) throw new Error("A verified business session is required.");
        const deviceId = crypto.randomUUID();
        const headers = { "Authorization": `Bearer ${token}`, "Content-Type": "application/json", "Accept": "application/json" };
        const provisionResponse = await timedFetch(`${apiUrl}/offline/provision`, { method: "POST", credentials: "include", headers, body: JSON.stringify({ device_id: deviceId }) }, 8000);
        const provisionData = await provisionResponse.json().catch(() => ({}));
        if (!provisionResponse.ok) throw new Error(messageFromResponse(provisionData, "Offline access could not be enabled."));
        const snapshotResponse = await timedFetch(`${apiUrl}/offline/snapshot`, { credentials: "include", headers }, 15000);
        const snapshot = await snapshotResponse.json().catch(() => ({}));
        if (!snapshotResponse.ok) throw new Error(messageFromResponse(snapshot, "The offline workspace could not be synchronized."));

        const grant = JSON.parse(textDecoder.decode(b64ToBytes(provisionData.payload)));
        const rawDataKey = crypto.getRandomValues(new Uint8Array(32));
        const dataKey = await importDataKey(rawDataKey);
        const salt = crypto.getRandomValues(new Uint8Array(16));
        const pinKey = await derivePinKey(pin, salt);
        const wrappedDataKey = await sealWithKey(pinKey, { key: bytesToB64(rawDataKey) }, `cauldra-key:${scope}`);
        const sealedGrant = await sealWithKey(dataKey, {
            grant_payload: provisionData.payload,
            signature: provisionData.signature,
            public_key: provisionData.public_key,
        }, `cauldra-grant:${scope}`);
        const record = {
            schema_version: CLIENT_SCHEMA_VERSION, scope, user_id: user.id, business_id: Number(business.id || business.business_id),
            device_id: deviceId, display_name: [user.firstname, user.lastname].filter(Boolean).join(" ") || user.username,
            business_name: business.company_name || "Business", role: user.role, auth_version: Number(user.auth_version || 1),
            expires_at: Number(grant.expires_at) * 1000, last_server_verified_at: Number(grant.last_server_verified) * 1000,
            sealed_grant: sealedGrant, pin_salt: bytesToB64(salt), wrapped_data_key: wrappedDataKey, device_key: dataKey,
            failed_attempts: 0, locked_until: 0,
        };
        await openVerifiedGrant(record, dataKey);
        const sealedSnapshot = await sealWithKey(dataKey, snapshot, `cauldra-cache:${scope}:snapshot`);
        await transaction(["offline_identities", "secure_cache"], "readwrite", async ({ offline_identities, secure_cache }) => {
            await requestToPromise(offline_identities.put(record));
            await requestToPromise(secure_cache.put({ key: `${scope}:snapshot`, scope, kind: "snapshot", schema_version: CLIENT_SCHEMA_VERSION,
                updated_at: Date.now(), sealed: sealedSnapshot }));
        });
        active = { scope, record, grant, dataKey, rawDataKey, snapshot, offline: false };
        await migrateLegacy(scope);
        announce("Offline access enabled. This device can open Cauldra without internet.");
        return snapshot;
    }

    async function refreshSnapshot({ apiUrl, token }) {
        if (!active || !token) return null;
        const headers = { "Authorization": `Bearer ${token}`, "Accept": "application/json" };
        const response = await timedFetch(`${apiUrl}/offline/snapshot`, { credentials: "include", headers }, 15000);
        const snapshot = await response.json().catch(() => ({}));
        if (!response.ok) {
            if ([401, 403].includes(response.status)) await quarantineActive(messageFromResponse(snapshot, "Authorization changed."));
            throw new Error(messageFromResponse(snapshot, "Offline data could not be refreshed."));
        }
        active.snapshot = snapshot;
        active.record.last_server_verified_at = Date.now();
        await putIdentity(active.record);
        await cacheWrite("snapshot", snapshot);
        return snapshot;
    }

    async function cacheWrite(kind, value) {
        if (!active) throw new Error("Offline access is locked.");
        const key = `${active.scope}:${kind}`;
        const sealed = await sealWithKey(active.dataKey, value, `cauldra-cache:${active.scope}:${kind}`);
        try {
            await transaction("secure_cache", "readwrite", ({ secure_cache: store }) => requestToPromise(store.put({ key, scope: active.scope, kind, schema_version: CLIENT_SCHEMA_VERSION, updated_at: Date.now(), sealed })));
        } catch (error) {
            if (error && (error.name === "QuotaExceededError" || /quota/i.test(error.message))) throw new Error("Offline storage is full. Connect to sync or remove older offline data.");
            throw error;
        }
    }

    async function cacheRead(kind) {
        if (!active) return null;
        const row = await transaction("secure_cache", "readonly", ({ secure_cache: store }) => requestToPromise(store.get(`${active.scope}:${kind}`)));
        if (!row) return null;
        return openWithKey(active.dataKey, row.sealed, `cauldra-cache:${active.scope}:${kind}`);
    }

    async function enqueue(op, cacheUpdates = []) {
        if (!active) throw new Error("Enable and unlock Offline Access before saving changes without internet.");
        const normalized = {
            ...op, schema_version: CLIENT_SCHEMA_VERSION, device_id: active.record.device_id,
            business_id: active.record.business_id, user_id: active.record.user_id,
            auth_version: active.record.auth_version, captured_at: op.captured_at || new Date().toISOString(),
            created_at: Number(op.created_at || Date.now()), status: "pending", attempts: Number(op.attempts || 0),
            depends_on_op_ids: Array.isArray(op.depends_on_op_ids) ? op.depends_on_op_ids : [],
        };
        const sealed = await sealWithKey(active.dataKey, normalized, `cauldra-outbox:${active.scope}:${normalized.op_id}`);
        const envelope = { op_id: normalized.op_id, scope: active.scope, status: normalized.status, created_at: normalized.created_at,
            next_retry_at: normalized.next_retry_at || 0, depends_on_op_ids: normalized.depends_on_op_ids, type: normalized.type, sealed };
        const sealedCaches = [];
        for (const update of cacheUpdates) {
            const key = `${active.scope}:${update.kind}`;
            sealedCaches.push({ key, scope: active.scope, kind: update.kind, schema_version: CLIENT_SCHEMA_VERSION, updated_at: Date.now(),
                sealed: await sealWithKey(active.dataKey, update.value, `cauldra-cache:${active.scope}:${update.kind}`) });
        }
        try {
            await transaction(cacheUpdates.length ? ["secure_outbox", "secure_cache"] : "secure_outbox", "readwrite", async (stores) => {
                await requestToPromise(stores.secure_outbox.add(envelope));
                for (const cacheRow of sealedCaches) await requestToPromise(stores.secure_cache.put(cacheRow));
            });
        } catch (error) {
            if (error && (error.name === "QuotaExceededError" || /quota/i.test(error.message))) throw new Error("Offline storage is full. This change was not saved.");
            throw error;
        }
        return normalized;
    }

    async function listOutbox() {
        if (!active) return [];
        const rows = await transaction("secure_outbox", "readonly", ({ secure_outbox: store }) => requestToPromise(store.index("by_scope").getAll(active.scope)));
        const result = [];
        for (const row of rows || []) {
            try { result.push(await openWithKey(active.dataKey, row.sealed, `cauldra-outbox:${active.scope}:${row.op_id}`)); }
            catch (_) { result.push({ op_id: row.op_id, type: row.type, status: "conflict", created_at: row.created_at, conflict_code: "CACHE_TAMPERED", last_error: "This local change failed its integrity check and was quarantined." }); }
        }
        return result;
    }

    async function updateOutbox(opId, patch) {
        if (!active) return;
        const row = await transaction("secure_outbox", "readonly", ({ secure_outbox: store }) => requestToPromise(store.get(opId)));
        if (!row || row.scope !== active.scope) return;
        const current = await openWithKey(active.dataKey, row.sealed, `cauldra-outbox:${active.scope}:${opId}`);
        const updated = { ...current, ...patch };
        row.status = updated.status;
        row.next_retry_at = updated.next_retry_at || 0;
        row.depends_on_op_ids = updated.depends_on_op_ids || [];
        row.sealed = await sealWithKey(active.dataKey, updated, `cauldra-outbox:${active.scope}:${opId}`);
        await transaction("secure_outbox", "readwrite", ({ secure_outbox: store }) => requestToPromise(store.put(row)));
    }

    async function removeOutbox(opId) {
        if (!active) return;
        const row = await transaction("secure_outbox", "readonly", ({ secure_outbox: store }) => requestToPromise(store.get(opId)));
        if (row && row.scope === active.scope) await transaction("secure_outbox", "readwrite", ({ secure_outbox: store }) => requestToPromise(store.delete(opId)));
    }

    async function migrateLegacy(scope) {
        if (!active || active.scope !== scope) return;
        const parts = scope.split(":").map(Number);
        const rows = await transaction("outbox", "readonly", ({ outbox: store }) => requestToPromise(store.getAll()));
        for (const row of rows || []) {
            if (Number(row.business_id) !== parts[0] || Number(row.user_id) !== parts[1]) continue;
            try {
                await enqueue({ ...row, status: row.status === "syncing" ? "pending" : row.status });
                await transaction("outbox", "readwrite", ({ outbox: store }) => requestToPromise(store.delete(row.op_id)));
            } catch (error) {
                if (error && error.name === "ConstraintError") await transaction("outbox", "readwrite", ({ outbox: store }) => requestToPromise(store.delete(row.op_id)));
            }
        }
    }

    async function quarantineActive(reason) {
        if (!active) return;
        const rows = await listOutbox();
        for (const row of rows) await updateOutbox(row.op_id, { status: "conflict", conflict_code: "AUTH_EXPIRED", last_error: reason });
        active.record.revoked_locally_at = Date.now();
        await putIdentity(active.record);
        active = null;
        announce("Offline access was locked because your account or permissions changed.");
    }

    async function removeCurrentData() {
        if (!active) throw new Error("Unlock this offline workspace first.");
        const scope = active.scope;
        const rows = await listOutbox();
        if (rows.some((row) => !["synced"].includes(row.status))) {
            const confirmed = window.confirm(`${rows.length} local change${rows.length === 1 ? " has" : "s have"} not synced. Remove it permanently from this device?`);
            if (!confirmed) return false;
        }
        const db = await openDb();
        await new Promise((resolve, reject) => {
            const tx = db.transaction(["offline_identities", "secure_cache", "secure_outbox"], "readwrite");
            tx.objectStore("offline_identities").delete(scope);
            for (const storeName of ["secure_cache", "secure_outbox"]) {
                const index = tx.objectStore(storeName).index("by_scope");
                const cursorRequest = index.openCursor(IDBKeyRange.only(scope));
                cursorRequest.onsuccess = () => { const cursor = cursorRequest.result; if (cursor) { cursor.delete(); cursor.continue(); } };
            }
            tx.oncomplete = resolve;
            tx.onerror = () => reject(tx.error);
        });
        active = null;
        announce("Offline data removed from this device.");
        return true;
    }

    async function changePin(oldPin, newPin) {
        if (!active) throw new Error("Unlock this offline workspace first.");
        if (!/^\d{6,12}$/.test(newPin)) throw new Error("Use a 6–12 digit offline PIN.");
        let rawDataKey = active.rawDataKey;
        if (!rawDataKey) {
            const oldKey = await derivePinKey(oldPin, b64ToBytes(active.record.pin_salt));
            const opened = await openWithKey(oldKey, active.record.wrapped_data_key, `cauldra-key:${active.scope}`);
            rawDataKey = b64ToBytes(opened.key);
        }
        const salt = crypto.getRandomValues(new Uint8Array(16));
        const pinKey = await derivePinKey(newPin, salt);
        active.record.pin_salt = bytesToB64(salt);
        active.record.wrapped_data_key = await sealWithKey(pinKey, { key: bytesToB64(rawDataKey) }, `cauldra-key:${active.scope}`);
        active.record.failed_attempts = 0;
        active.record.locked_until = 0;
        active.rawDataKey = rawDataKey;
        await putIdentity(active.record);
        announce("Offline PIN changed.");
    }

    async function disable({ apiUrl, token }) {
        if (!active) throw new Error("Unlock this offline workspace first.");
        if (token) {
            const response = await timedFetch(`${apiUrl}/offline/devices/${encodeURIComponent(active.record.device_id)}`, {
                method: "DELETE", credentials: "include", headers: { "Authorization": `Bearer ${token}` },
            }, 8000);
            if (!response.ok) throw new Error("Offline access could not be disabled on the server.");
        }
        active.record.expires_at = 0;
        active.record.revoked_locally_at = Date.now();
        await putIdentity(active.record);
        active = null;
        announce("Offline access disabled. Preserved local changes remain quarantined on this device.");
    }

    async function storageStatus() {
        if (!navigator.storage || !navigator.storage.estimate) return null;
        const estimate = await navigator.storage.estimate();
        const remaining = Math.max(0, Number(estimate.quota || 0) - Number(estimate.usage || 0));
        return { usage: Number(estimate.usage || 0), quota: Number(estimate.quota || 0), remaining, low: estimate.quota > 0 && remaining / estimate.quota < 0.1 };
    }

    async function tryNativeBiometric(scope) {
        const plugin = window.Capacitor?.Plugins?.CauldraBiometric;
        if (!plugin || typeof plugin.unlockOfflineKey !== "function") return { available: false };
        // The native plugin contract returns the already-protected PIN/key
        // material from Keychain/Keystore; this web layer never labels a
        // checkbox as biometric and never receives a biometric template.
        const result = await plugin.unlockOfflineKey({ scope });
        return { available: true, unlocked: !!result?.unlocked };
    }

    function timedFetch(url, options, timeoutMs) {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), timeoutMs);
        return fetch(url, { ...options, signal: controller.signal }).finally(() => clearTimeout(timer));
    }

    function messageFromResponse(data, fallback) {
        const detail = data && data.detail;
        return (detail && typeof detail === "object" && detail.message) || (typeof detail === "string" && detail) || (data && data.message) || fallback;
    }

    function announce(message) {
        const region = document.getElementById("offline-live-region");
        if (region) region.textContent = message;
    }

    function installUi() {
        if (document.getElementById("offline-unlock-dialog")) return;
        document.body.insertAdjacentHTML("beforeend", `
            <div id="offline-live-region" class="sr-only" role="status" aria-live="polite"></div>
            <dialog id="offline-unlock-dialog" class="offline-dialog" aria-labelledby="offline-unlock-title">
                <form method="dialog"><button class="offline-dialog-close" value="cancel" aria-label="Close">×</button></form>
                <h2 id="offline-unlock-title">Open Cauldra offline</h2>
                <p id="offline-unlock-copy">Choose a previously verified workspace and enter its offline PIN.</p>
                <form id="offline-unlock-form">
                    <label for="offline-identity">Workspace</label><select id="offline-identity"></select>
                    <label for="offline-pin">Offline PIN</label><input id="offline-pin" type="password" inputmode="numeric" pattern="[0-9]{6,12}" minlength="6" maxlength="12" autocomplete="off" required>
                    <p id="offline-unlock-status" role="status"></p>
                    <button type="submit">Unlock offline</button><button type="button" id="offline-retry-online">Retry internet</button>
                </form>
            </dialog>
            <dialog id="offline-setup-dialog" class="offline-dialog" aria-labelledby="offline-setup-title">
                <form method="dialog"><button class="offline-dialog-close" value="cancel" aria-label="Close">×</button></form>
                <h2 id="offline-setup-title">Enable offline access</h2>
                <p>Open Cauldra without internet and save supported work on this trusted device. Changes sync after reconnecting.</p>
                <form id="offline-setup-form">
                    <label for="offline-setup-pin">Create a 6–12 digit offline PIN</label><input id="offline-setup-pin" type="password" inputmode="numeric" pattern="[0-9]{6,12}" minlength="6" maxlength="12" autocomplete="new-password" required>
                    <label for="offline-setup-confirm">Confirm offline PIN</label><input id="offline-setup-confirm" type="password" inputmode="numeric" pattern="[0-9]{6,12}" minlength="6" maxlength="12" autocomplete="new-password" required>
                    <p id="offline-setup-status" role="status"></p><button type="submit">Enable on this device</button>
                </form>
            </dialog>
            <dialog id="offline-sync-dialog" class="offline-dialog offline-sync-dialog" aria-labelledby="offline-sync-title">
                <form method="dialog"><button class="offline-dialog-close" value="cancel" aria-label="Close">×</button></form>
                <h2 id="offline-sync-title">Sync details</h2><p id="offline-sync-summary" role="status"></p>
                <div id="offline-conflict-list"></div><button type="button" id="offline-sync-retry">Retry sync</button>
            </dialog>`);
        document.getElementById("offline-unlock-form").addEventListener("submit", async (event) => {
            event.preventDefault();
            const status = document.getElementById("offline-unlock-status");
            status.textContent = "Checking…";
            try {
                const result = await unlock(document.getElementById("offline-identity").value, document.getElementById("offline-pin").value, { offline: true });
                document.getElementById("offline-pin").value = "";
                document.getElementById("offline-unlock-dialog").close();
                status.textContent = "";
                window.dispatchEvent(new CustomEvent("cauldra-offline-unlocked", { detail: result }));
            } catch (error) { status.textContent = error.message; }
        });
        document.getElementById("offline-retry-online").addEventListener("click", () => window.dispatchEvent(new Event("cauldra-retry-online")));
        document.getElementById("offline-sync-retry").addEventListener("click", () => window.dispatchEvent(new Event("cauldra-manual-sync")));
    }

    async function requestColdStart() {
        installUi();
        const identities = await listIdentities();
        const dialog = document.getElementById("offline-unlock-dialog");
        const select = document.getElementById("offline-identity");
        const copy = document.getElementById("offline-unlock-copy");
        select.innerHTML = "";
        if (!identities.length) {
            copy.textContent = "Connect to the internet to sign in on this device for the first time.";
            document.getElementById("offline-pin").disabled = true;
            dialog.showModal();
            return false;
        }
        copy.textContent = "Choose a previously verified workspace and enter its offline PIN.";
        document.getElementById("offline-pin").disabled = false;
        for (const identity of identities) {
            const option = document.createElement("option"); option.value = identity.scope;
            option.textContent = `${identity.display_name} — ${identity.business_name}`; select.appendChild(option);
        }
        dialog.showModal();
        setTimeout(() => document.getElementById("offline-pin").focus(), 50);
        return true;
    }

    async function openSetup(context) {
        installUi();
        const dialog = document.getElementById("offline-setup-dialog");
        const form = document.getElementById("offline-setup-form");
        form.onsubmit = async (event) => {
            event.preventDefault();
            const status = document.getElementById("offline-setup-status");
            const pin = document.getElementById("offline-setup-pin").value;
            const confirmPin = document.getElementById("offline-setup-confirm").value;
            if (pin !== confirmPin) { status.textContent = "PINs do not match."; return; }
            status.textContent = "Securing this device and synchronizing data…";
            try {
                await provision({ ...context, pin });
                form.reset(); dialog.close(); status.textContent = "";
                window.dispatchEvent(new Event("cauldra-offline-settings-changed"));
            } catch (error) { status.textContent = error.message; }
        };
        dialog.showModal();
        setTimeout(() => document.getElementById("offline-setup-pin").focus(), 50);
    }

    async function openSyncDetails() {
        installUi();
        const rows = await listOutbox();
        const pending = rows.filter((row) => ["pending", "syncing", "failed_retryable", "blocked_dependency"].includes(row.status));
        const conflicts = rows.filter((row) => row.status === "conflict");
        document.getElementById("offline-sync-summary").textContent = `${pending.length} waiting to sync · ${conflicts.length} need attention`;
        const list = document.getElementById("offline-conflict-list");
        list.innerHTML = conflicts.length ? conflicts.map((row) => `<article><h3>Sync conflict · ${escapeText(row.type || "change")}</h3><p>${escapeText(row.last_error || conflictGuidance(row.conflict_code))}</p><small>${escapeText(new Date(row.created_at).toLocaleString())} · ${escapeText(row.op_id)}</small></article>`).join("") : "<p>No conflicts need attention.</p>";
        document.getElementById("offline-sync-dialog").showModal();
    }

    function conflictGuidance(code) {
        const guidance = { STOCK_CHANGED: "Stock or price changed on the server. Review the original sale.", RESOURCE_DELETED: "The original record no longer exists.",
            PERMISSION_CHANGED: "Your permissions changed while this device was offline.", LOCATION_CHANGED: "The original location or warehouse changed.",
            BUSINESS_DAY_CLOSED: "The original Business Day closed before this change synchronized.", AUTH_EXPIRED: "Sign in online as the original user to review this change.",
            VALIDATION_ERROR: "Review the saved values before retrying." };
        return guidance[code] || "This change needs review before it can synchronize.";
    }

    function escapeText(value) {
        return String(value == null ? "" : value).replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[character]));
    }

    document.addEventListener("DOMContentLoaded", installUi);

    window.CauldraOffline = Object.freeze({
        DB_VERSION, CLIENT_SCHEMA_VERSION, openDb, listIdentities, requestColdStart, unlock, resumeOnline, provision, refreshSnapshot,
        cacheWrite, cacheRead, enqueue, listOutbox, updateOutbox, removeOutbox, openSetup, openSyncDetails,
        removeCurrentData, changePin, disable, storageStatus, tryNativeBiometric, quarantineActive, lock() { active = null; },
        isUnlocked() { return !!active; }, isOffline() { return !!(active && active.offline); },
        currentScope() { return active && active.scope; }, currentDeviceId() { return active && active.record.device_id; },
        currentSnapshot() { return active && active.snapshot; }, currentGrant() { return active && active.grant; },
    });
})();
