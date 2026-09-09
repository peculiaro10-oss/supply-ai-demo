(function () {
    "use strict";

    const DB_NAME = "cauldra_offline";
    const DB_VERSION = 2;
    const CLIENT_SCHEMA_VERSION = 2;
    const PBKDF2_ITERATIONS = 310000;
    const LOCK_AFTER_FAILURES = 5;
    const BACKGROUND_RELOCK_MS = 5 * 60 * 1000;
    const ACCESS_STATES = Object.freeze({
        ONLINE: "ONLINE", DEGRADED: "DEGRADED", OFFLINE_LOCKED: "OFFLINE_LOCKED",
        OFFLINE_UNLOCKING: "OFFLINE_UNLOCKING", OFFLINE_UNLOCKED: "OFFLINE_UNLOCKED", SYNCING: "SYNCING",
        OFFLINE_GRANT_EXPIRED: "OFFLINE_GRANT_EXPIRED", OFFLINE_ACCESS_NOT_PROVISIONED: "OFFLINE_ACCESS_NOT_PROVISIONED",
        OFFLINE_ACCESS_REVOKED: "OFFLINE_ACCESS_REVOKED",
    });
    const textEncoder = new TextEncoder();
    const textDecoder = new TextDecoder();
    let dbPromise = null;
    let active = null;
    let accessState = ACCESS_STATES.OFFLINE_ACCESS_NOT_PROVISIONED;
    let backgroundedAt = 0;

    function setAccessState(state) {
        accessState = state;
        document.documentElement.dataset.offlineAccessState = state;
        window.dispatchEvent(new CustomEvent("cauldra-offline-state", { detail: { state } }));
    }

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

    function principalFor(user, business) {
        const userId = user && user.id;
        const businessId = (business && (business.id || business.business_id)) || (user && user.business_id);
        return userId && businessId ? `${businessId}:${userId}` : null;
    }

    async function installationDeviceId(principal) {
        const key = "installation_device_id";
        const stored = await transaction("meta", "readonly", ({ meta }) => requestToPromise(meta.get(key)));
        if (stored?.value) return stored.value;
        // A v2 principal-scoped identity proves which device id belongs to
        // this exact business/user and can be adopted without guessing.
        const legacy = principal ? await getIdentity(principal) : null;
        const value = legacy?.device_id || crypto.randomUUID();
        await transaction("meta", "readwrite", ({ meta }) => requestToPromise(meta.put({ key, value })));
        return value;
    }

    async function scopeFor(user, business) {
        const principal = principalFor(user, business);
        if (!principal) return null;
        const legacy = await getIdentity(principal);
        const deviceId = legacy?.device_id || await installationDeviceId(principal);
        return `${principal}:${deviceId}`;
    }

    function nativeBinding(record) {
        return String(record.scope).endsWith(`:${record.device_id}`) ? record.scope : `${record.scope}:${record.device_id}`;
    }

    function keyAad(record) {
        return record.binding_version === 2 ? `cauldra-key:${nativeBinding(record)}` : `cauldra-key:${record.scope}`;
    }

    function grantAad(record) {
        return record.binding_version === 2 ? `cauldra-grant:${nativeBinding(record)}` : `cauldra-grant:${record.scope}`;
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
        return (rows || []).map(({ scope, user_id, business_id, device_id, display_name, business_name, expires_at, last_server_verified_at, biometric_enabled, biometric_prompt_dismissed, revoked_locally_at }) => ({
            scope, user_id, business_id, device_id, display_name, business_name, expires_at, last_server_verified_at,
            biometric_enabled: !!biometric_enabled, biometric_prompt_dismissed: !!biometric_prompt_dismissed, revoked_locally_at: revoked_locally_at || 0,
        }));
    }

    async function putIdentity(identity) {
        return transaction("offline_identities", "readwrite", ({ offline_identities: store }) => requestToPromise(store.put(identity)));
    }

    async function migratePrincipalIdentity(principal, exactScope) {
        if (!principal || !exactScope || principal === exactScope) return getIdentity(exactScope);
        const legacy = await getIdentity(principal);
        if (!legacy || !legacy.device_key || exactScope !== `${principal}:${legacy.device_id}`) return null;
        const cacheRows = await transaction("secure_cache", "readonly", ({ secure_cache }) => requestToPromise(secure_cache.index("by_scope").getAll(principal)));
        const outboxRows = await transaction("secure_outbox", "readonly", ({ secure_outbox }) => requestToPromise(secure_outbox.index("by_scope").getAll(principal)));
        const migratedCaches = [];
        for (const row of cacheRows || []) {
            const value = await openWithKey(legacy.device_key, row.sealed, `cauldra-cache:${principal}:${row.kind}`);
            migratedCaches.push({ ...row, key: `${exactScope}:${row.kind}`, scope: exactScope,
                sealed: await sealWithKey(legacy.device_key, value, `cauldra-cache:${exactScope}:${row.kind}`) });
        }
        const migratedOutbox = [];
        for (const row of outboxRows || []) {
            const value = await openWithKey(legacy.device_key, row.sealed, `cauldra-outbox:${principal}:${row.op_id}`);
            migratedOutbox.push({ ...row, scope: exactScope,
                sealed: await sealWithKey(legacy.device_key, value, `cauldra-outbox:${exactScope}:${row.op_id}`) });
        }
        const migrated = { ...legacy, scope: exactScope };
        await transaction(["offline_identities", "secure_cache", "secure_outbox"], "readwrite", async ({ offline_identities, secure_cache, secure_outbox }) => {
            await requestToPromise(offline_identities.put(migrated));
            for (const row of migratedCaches) await requestToPromise(secure_cache.put(row));
            for (const row of migratedOutbox) await requestToPromise(secure_outbox.put(row));
            await requestToPromise(offline_identities.delete(principal));
            for (const row of cacheRows || []) await requestToPromise(secure_cache.delete(row.key));
        });
        return migrated;
    }

    async function identityForSession(user, business) {
        const principal = principalFor(user, business);
        const scope = await scopeFor(user, business);
        if (!scope) return { scope: null, record: null };
        let record = await getIdentity(scope);
        if (!record) record = await migratePrincipalIdentity(principal, scope);
        return { scope, record };
    }

    function optInKey(scope) { return `offline_opt_in:${scope}`; }

    async function getOptInState(scope) {
        if (!scope) return "never_prompted";
        const row = await transaction("meta", "readonly", ({ meta }) => requestToPromise(meta.get(optInKey(scope))));
        if (row?.value === "declined" || row?.value === "enabled") return row.value;
        return "never_prompted";
    }

    async function setOptInState(scope, value) {
        if (!scope || !["declined", "enabled"].includes(value)) return;
        await transaction("meta", "readwrite", ({ meta }) => requestToPromise(meta.put({ key: optInKey(scope), value, updated_at: Date.now() })));
    }

    async function sessionStatus(user, business) {
        const { scope, record } = await identityForSession(user, business);
        if (record) await setOptInState(scope, "enabled");
        return { scope, identity: record ? (await listIdentities()).find((item) => item.scope === scope) || null : null,
            opt_in_state: record ? "enabled" : await getOptInState(scope) };
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
        if (Number(grant.auth_version || 0) !== Number(record.auth_version || 0) || grant.role !== record.role) {
            throw new Error("Offline authorization no longer matches this user.");
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
            protectedGrant = await openWithKey(dataKey, record.sealed_grant, grantAad(record));
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
        if (record.revoked_locally_at) {
            setAccessState(ACCESS_STATES.OFFLINE_ACCESS_REVOKED);
            throw new Error("Offline Access was disabled on this device. Connect to the internet to enable it again.");
        }
        if (Number(record.expires_at || 0) <= Date.now()) {
            setAccessState(ACCESS_STATES.OFFLINE_GRANT_EXPIRED);
            throw new Error("Offline Access has expired. Connect to the internet to refresh it.");
        }
        if (record.locked_until && record.locked_until > Date.now()) {
            const seconds = Math.ceil((record.locked_until - Date.now()) / 1000);
            throw new Error(`Too many incorrect attempts. Try again in ${seconds} seconds.`);
        }
        setAccessState(ACCESS_STATES.OFFLINE_UNLOCKING);
        const pinKey = await derivePinKey(pin, b64ToBytes(record.pin_salt));
        let wrappedDataKey;
        try {
            wrappedDataKey = await openWithKey(pinKey, record.wrapped_data_key, keyAad(record));
        } catch (_) {
            const outcome = await recordPinFailure(record);
            setAccessState(ACCESS_STATES.OFFLINE_LOCKED);
            if (outcome.delay) throw new Error(`Too many incorrect attempts. Offline access is locked for ${Math.ceil(outcome.delay / 1000)} seconds.`);
            throw new Error("That offline PIN is incorrect.");
        }
        const rawDataKey = b64ToBytes(wrappedDataKey.key);
        const dataKey = await importDataKey(rawDataKey);
        let grant;
        try { grant = await openVerifiedGrant(record, dataKey); }
        catch (error) {
            setAccessState(/expired/i.test(error.message) ? ACCESS_STATES.OFFLINE_GRANT_EXPIRED : ACCESS_STATES.OFFLINE_LOCKED);
            throw error;
        }
        try {
            const snapshotRow = await transaction("secure_cache", "readonly", ({ secure_cache: store }) => requestToPromise(store.get(`${scope}:snapshot`)));
            if (!snapshotRow) throw new Error("No synchronized workspace is available on this device.");
            const snapshot = await applyWorkingOverlays(scope, dataKey, await openWithKey(dataKey, snapshotRow.sealed, `cauldra-cache:${scope}:snapshot`));
            active = { scope, record: { ...record, failed_attempts: 0, locked_until: 0 }, grant, dataKey, rawDataKey, snapshot, offline: !!(options && options.offline) };
            await putIdentity(active.record);
            await migrateLegacy(scope);
            setAccessState(active.offline ? ACCESS_STATES.OFFLINE_UNLOCKED : ACCESS_STATES.ONLINE);
            announce("Offline workspace unlocked.");
            return { grant, snapshot, scope };
        } catch (_) {
            setAccessState(ACCESS_STATES.OFFLINE_LOCKED);
            throw new Error("Offline data failed its integrity check. Connect to the internet to refresh this workspace.");
        }
    }

    async function resumeOnline({ user, business }) {
        const { scope, record } = await identityForSession(user, business);
        if (!scope) return null;
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
            closeUnlockUi();
            setAccessState(ACCESS_STATES.ONLINE);
            return snapshot;
        } catch (_) {
            return null;
        }
    }

    async function provision({ apiUrl, token, user, business, pin }) {
        if (!crypto || !crypto.subtle) throw new Error("Secure offline access is not supported on this device.");
        if (!/^\d{6,12}$/.test(pin)) throw new Error("Use a 6–12 digit offline PIN.");
        const scope = await scopeFor(user, business);
        if (!scope) throw new Error("A verified business session is required.");
        if (await getIdentity(scope)) throw new Error("Offline Access already exists on this device. Refresh it instead of creating a second device key.");
        const deviceId = scope.split(":").slice(2).join(":");
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
        const bindingRecord = { scope, device_id: deviceId, binding_version: 2 };
        const wrappedDataKey = await sealWithKey(pinKey, { key: bytesToB64(rawDataKey) }, keyAad(bindingRecord));
        const sealedGrant = await sealWithKey(dataKey, {
            grant_payload: provisionData.payload,
            signature: provisionData.signature,
            public_key: provisionData.public_key,
        }, grantAad(bindingRecord));
        const record = {
            schema_version: CLIENT_SCHEMA_VERSION, binding_version: 2, scope, user_id: user.id, business_id: Number(business.id || business.business_id),
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
        await setOptInState(scope, "enabled");
        await migrateLegacy(scope);
        setAccessState(ACCESS_STATES.ONLINE);
        announce("Offline access enabled. This device can open Cauldra without internet.");
        return snapshot;
    }

    async function refreshAccess({ apiUrl, token, user, business }) {
        if (!token) throw new Error("Internet connection required for this action.");
        const { scope, record } = await identityForSession(user, business);
        if (!record?.device_key) throw new Error("Set an Offline PIN before refreshing Offline Access.");
        const headers = { "Authorization": `Bearer ${token}`, "Content-Type": "application/json", "Accept": "application/json" };
        const provisionResponse = await timedFetch(`${apiUrl}/offline/provision`, {
            method: "POST", credentials: "include", headers, body: JSON.stringify({ device_id: record.device_id }),
        }, 8000);
        const provisionData = await provisionResponse.json().catch(() => ({}));
        if (!provisionResponse.ok) throw new Error(messageFromResponse(provisionData, "Offline Access could not be refreshed."));
        const snapshotResponse = await timedFetch(`${apiUrl}/offline/snapshot`, { credentials: "include", headers }, 15000);
        const snapshot = await snapshotResponse.json().catch(() => ({}));
        if (!snapshotResponse.ok) throw new Error(messageFromResponse(snapshot, "The offline workspace could not be refreshed."));
        const grant = JSON.parse(textDecoder.decode(b64ToBytes(provisionData.payload)));
        record.sealed_grant = await sealWithKey(record.device_key, {
            grant_payload: provisionData.payload, signature: provisionData.signature, public_key: provisionData.public_key,
        }, grantAad(record));
        record.role = user.role;
        record.auth_version = Number(user.auth_version || 1);
        record.expires_at = Number(grant.expires_at) * 1000;
        record.last_server_verified_at = Number(grant.last_server_verified) * 1000;
        record.revoked_locally_at = 0;
        record.failed_attempts = 0;
        record.locked_until = 0;
        await openVerifiedGrant(record, record.device_key);
        const sealedSnapshot = await sealWithKey(record.device_key, snapshot, `cauldra-cache:${scope}:snapshot`);
        await transaction(["offline_identities", "secure_cache"], "readwrite", async ({ offline_identities, secure_cache }) => {
            await requestToPromise(offline_identities.put(record));
            await requestToPromise(secure_cache.put({ key: `${scope}:snapshot`, scope, kind: "snapshot", schema_version: CLIENT_SCHEMA_VERSION,
                updated_at: Date.now(), sealed: sealedSnapshot }));
        });
        active = { scope, record, grant, dataKey: record.device_key, rawDataKey: null, snapshot, offline: false };
        closeUnlockUi();
        setAccessState(ACCESS_STATES.ONLINE);
        announce("Offline Access refreshed.");
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
        const parts = scope.split(":");
        const rows = await transaction("outbox", "readonly", ({ outbox: store }) => requestToPromise(store.getAll()));
        for (const row of rows || []) {
            // A business-only row (or one without the exact device id) is
            // ambiguous and remains quarantined in the legacy store.
            if (Number(row.business_id) !== Number(parts[0]) || Number(row.user_id) !== Number(parts[1]) || row.device_id !== active.record.device_id) continue;
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
        setAccessState(ACCESS_STATES.OFFLINE_ACCESS_REVOKED);
        announce("Offline access was locked because your account or permissions changed.");
    }

    function nativePlugin() {
        const plugin = window.Capacitor?.Plugins?.CauldraBiometric;
        return plugin && typeof plugin.getStatus === "function" ? plugin : null;
    }

    async function biometricStatus(scope) {
        const record = scope ? await getIdentity(scope) : active?.record;
        const plugin = nativePlugin();
        if (!record || !plugin) return { available: false, enabled: false, reason: "platform_unavailable" };
        try {
            const result = await plugin.getStatus({ scope: nativeBinding(record) });
            return { available: !!result?.available, enabled: !!(record.biometric_enabled && result?.enabled), reason: result?.reason || "" };
        } catch (_) {
            return { available: false, enabled: false, reason: "platform_unavailable" };
        }
    }

    async function rawKeyFromPin(record, pin, countFailure = true) {
        const pinKey = await derivePinKey(pin, b64ToBytes(record.pin_salt));
        try {
            const opened = await openWithKey(pinKey, record.wrapped_data_key, keyAad(record));
            return b64ToBytes(opened.key);
        } catch (_) {
            if (countFailure) await recordPinFailure(record);
            throw new Error("That offline PIN is incorrect.");
        }
    }

    async function enableBiometrics(pin = "") {
        if (!active) throw new Error("Unlock Offline Access first.");
        const plugin = nativePlugin();
        if (!plugin || typeof plugin.enable !== "function") throw new Error("Biometric unlock is unavailable on this device.");
        let rawDataKey = active.rawDataKey;
        if (!rawDataKey) {
            if (!pin) throw new Error("Enter your Offline PIN to enable biometrics.");
            rawDataKey = await rawKeyFromPin(active.record, pin);
        }
        const result = await plugin.enable({ scope: nativeBinding(active.record), secret: bytesToB64(rawDataKey) });
        if (result?.status !== "success") {
            if (result?.status === "cancelled") throw new Error("Biometric setup was cancelled. Your Offline PIN is unchanged.");
            throw new Error("Biometric unlock is unavailable. Your Offline PIN is still available.");
        }
        active.rawDataKey = rawDataKey;
        active.record.biometric_enabled = true;
        active.record.biometric_prompt_dismissed = false;
        await putIdentity(active.record);
        announce("Biometric unlock enabled. Your Offline PIN remains available.");
        return true;
    }

    async function disableBiometrics(scope = active?.scope) {
        const record = scope ? await getIdentity(scope) : null;
        if (!record) return false;
        const plugin = nativePlugin();
        if (plugin && typeof plugin.disable === "function") {
            try { await plugin.disable({ scope: nativeBinding(record) }); } catch (_) {}
        }
        record.biometric_enabled = false;
        record.biometric_prompt_dismissed = true;
        await putIdentity(record);
        if (active?.scope === scope) active.record = record;
        announce("Biometric unlock disabled. Your Offline PIN is unchanged.");
        return true;
    }

    async function dismissBiometricOffer() {
        if (!active) return;
        active.record.biometric_prompt_dismissed = true;
        await putIdentity(active.record);
    }

    async function unlockWithBiometric(scope) {
        const record = await getIdentity(scope);
        if (!record || !record.biometric_enabled) return { status: "unavailable" };
        if (record.revoked_locally_at) return { status: "revoked" };
        if (Number(record.expires_at || 0) <= Date.now()) return { status: "expired" };
        const plugin = nativePlugin();
        if (!plugin || typeof plugin.unlock !== "function") return { status: "unavailable" };
        setAccessState(ACCESS_STATES.OFFLINE_UNLOCKING);
        let result;
        try { result = await plugin.unlock({ scope: nativeBinding(record) }); }
        catch (_) { result = { status: "unavailable" }; }
        if (result?.status !== "success" || !result.secret || result.scope !== nativeBinding(record)) {
            if (result?.status === "invalidated") {
                record.biometric_enabled = false;
                await putIdentity(record);
            }
            setAccessState(ACCESS_STATES.OFFLINE_LOCKED);
            return { status: result?.status || "unavailable" };
        }
        try {
            const rawDataKey = b64ToBytes(result.secret);
            const dataKey = await importDataKey(rawDataKey);
            const grant = await openVerifiedGrant(record, dataKey);
            const snapshotRow = await transaction("secure_cache", "readonly", ({ secure_cache: store }) => requestToPromise(store.get(`${scope}:snapshot`)));
            if (!snapshotRow) throw new Error("No synchronized workspace is available on this device.");
            const snapshot = await applyWorkingOverlays(scope, dataKey, await openWithKey(dataKey, snapshotRow.sealed, `cauldra-cache:${scope}:snapshot`));
            active = { scope, record, grant, dataKey, rawDataKey, snapshot, offline: true };
            await migrateLegacy(scope);
            setAccessState(ACCESS_STATES.OFFLINE_UNLOCKED);
            announce("Offline workspace unlocked with device biometrics.");
            return { status: "success", grant, snapshot, scope };
        } catch (_) {
            setAccessState(ACCESS_STATES.OFFLINE_LOCKED);
            return { status: "invalidated" };
        }
    }

    let removeInFlight = false;
    async function deleteCurrentData(scope) {
        if (removeInFlight) return false;
        removeInFlight = true;
        const button = document.getElementById("offline-remove-confirm");
        const status = document.getElementById("offline-remove-status");
        if (button) { button.disabled = true; button.textContent = "Removing…"; }
        if (status) status.textContent = "";
        try {
            const recordBeingRemoved = active.record;
            const db = await openDb();
            await new Promise((resolve, reject) => {
                const tx = db.transaction(["offline_identities", "secure_cache", "secure_outbox"], "readwrite");
                tx.objectStore("offline_identities").delete(scope);
                for (const storeName of ["secure_cache", "secure_outbox"]) {
                    const cursorRequest = tx.objectStore(storeName).index("by_scope").openCursor(IDBKeyRange.only(scope));
                    cursorRequest.onsuccess = () => { const cursor = cursorRequest.result; if (cursor) { cursor.delete(); cursor.continue(); } };
                }
                tx.oncomplete = resolve;
                tx.onerror = () => reject(tx.error);
                tx.onabort = () => reject(tx.error || new Error("Offline data removal was cancelled."));
            });
            // Best-effort native cleanup after the local transaction commits;
            // a storage failure therefore leaves the entire workspace intact.
            const plugin = nativePlugin();
            if (plugin && recordBeingRemoved) {
                try { await plugin.disable({ scope: nativeBinding(recordBeingRemoved) }); } catch (_) {}
            }
            active = null;
            await setOptInState(scope, "declined");
            setAccessState(ACCESS_STATES.OFFLINE_ACCESS_NOT_PROVISIONED);
            announce("Offline data removed from this device.");
            return true;
        } catch (error) {
            if (status) status.textContent = error.message || "Offline data could not be removed.";
            throw error;
        } finally {
            removeInFlight = false;
            if (button) { button.disabled = false; button.textContent = button.dataset.label || "Remove Offline Data"; }
        }
    }

    async function removeCurrentData(expectedScope) {
        if (!active) throw new Error("Unlock this offline workspace first.");
        const scope = active.scope;
        if (expectedScope && expectedScope !== scope) throw new Error("This offline workspace does not match the signed-in account on this device.");
        const rows = await listOutbox();
        const pending = rows.filter((row) => row.status !== "synced");
        const dialog = document.getElementById("offline-remove-dialog");
        const body = document.getElementById("offline-remove-copy");
        const cancel = document.getElementById("offline-remove-cancel");
        const confirm = document.getElementById("offline-remove-confirm");
        body.textContent = pending.length
            ? `This account has ${pending.length} unsynced change${pending.length === 1 ? "" : "s"} on this device. Removing its local workspace will permanently discard ${pending.length === 1 ? "that change" : "those changes"} before ${pending.length === 1 ? "it reaches" : "they reach"} the server. Your existing online business data will not be deleted.`
            : "This will remove the encrypted offline workspace and cached data for this Cauldra account on this device. Your online account and business data will not be deleted.";
        cancel.textContent = pending.length ? "Keep Data" : "Cancel";
        confirm.textContent = pending.length ? "Remove Anyway" : "Remove Offline Data";
        confirm.dataset.label = confirm.textContent;
        dialog.dataset.scope = scope;
        if (!dialog.open) dialog.showModal();
        setTimeout(() => cancel.focus(), 0);
        return false;
    }

    async function changePin(oldPin, newPin) {
        if (!active) throw new Error("Unlock this offline workspace first.");
        if (!/^\d{6,12}$/.test(newPin)) throw new Error("Use a 6–12 digit offline PIN.");
        const rawDataKey = await rawKeyFromPin(active.record, oldPin);
        const salt = crypto.getRandomValues(new Uint8Array(16));
        const pinKey = await derivePinKey(newPin, salt);
        active.record.pin_salt = bytesToB64(salt);
        active.record.wrapped_data_key = await sealWithKey(pinKey, { key: bytesToB64(rawDataKey) }, keyAad(active.record));
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
        await disableBiometrics(active.scope);
        active.record.expires_at = 0;
        active.record.revoked_locally_at = Date.now();
        await putIdentity(active.record);
        active = null;
        setAccessState(ACCESS_STATES.OFFLINE_ACCESS_REVOKED);
        announce("Offline access disabled. Preserved local changes remain quarantined on this device.");
    }

    async function storageStatus() {
        if (!navigator.storage || !navigator.storage.estimate) return null;
        const estimate = await navigator.storage.estimate();
        const remaining = Math.max(0, Number(estimate.quota || 0) - Number(estimate.usage || 0));
        return { usage: Number(estimate.usage || 0), quota: Number(estimate.quota || 0), remaining, low: estimate.quota > 0 && remaining / estimate.quota < 0.1 };
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

    let optInContext = null;

    async function offerOptIn(context) {
        if (!context?.token || !navigator.onLine) return false;
        const state = await sessionStatus(context.user, context.business);
        if (!state.scope || state.identity || state.opt_in_state !== "never_prompted") return false;
        optInContext = { ...context, scope: state.scope };
        const dialog = document.getElementById("offline-opt-in-dialog");
        if (!dialog.open) dialog.showModal();
        setTimeout(() => document.getElementById("offline-opt-in-not-now")?.focus(), 0);
        return true;
    }

    function installUi() {
        if (document.getElementById("offline-unlock-dialog")) return;
        document.body.insertAdjacentHTML("beforeend", `
            <div id="offline-live-region" class="sr-only" role="status" aria-live="polite"></div>
            <dialog id="offline-opt-in-dialog" class="offline-dialog offline-opt-in-dialog" aria-labelledby="offline-opt-in-title">
                <h2 id="offline-opt-in-title">Keep Cauldra available offline?</h2>
                <p>Use Cauldra on this trusted device when your internet connection is unavailable.</p>
                <div class="offline-dialog-actions"><button type="button" id="offline-opt-in-enable" class="offline-primary">Enable Offline Access</button><button type="button" id="offline-opt-in-not-now">Not Now</button></div>
            </dialog>
            <dialog id="offline-unlock-dialog" class="offline-dialog offline-unlock-dialog" aria-labelledby="offline-unlock-title" data-mandatory="true">
                <div class="offline-brand" aria-label="Cauldra"><span aria-hidden="true">C</span> Cauldra</div>
                <div class="offline-state-label"><span aria-hidden="true">●</span> Offline Access</div>
                <h2 id="offline-unlock-title">Open your offline workspace</h2>
                <p id="offline-unlock-copy">Choose a previously verified workspace and enter its offline PIN.</p>
                <form id="offline-unlock-form">
                    <div id="offline-workspace-fields"><label for="offline-identity">Workspace</label><select id="offline-identity"></select>
                    <label for="offline-pin">Offline PIN</label><input id="offline-pin" type="password" inputmode="numeric" pattern="[0-9]{6,12}" minlength="6" maxlength="12" autocomplete="off" required></div>
                    <p id="offline-unlock-status" role="status"></p>
                    <div class="offline-dialog-actions"><button type="button" id="offline-biometric-unlock" hidden>Use fingerprint or face</button><button type="submit" id="offline-pin-unlock" class="offline-primary">Unlock with PIN</button><button type="button" id="offline-retry-online">Retry internet</button></div>
                </form>
            </dialog>
            <dialog id="offline-setup-dialog" class="offline-dialog" aria-labelledby="offline-setup-title">
                <form method="dialog"><button class="offline-dialog-close" value="cancel" aria-label="Close">×</button></form>
                <h2 id="offline-setup-title">Enable offline access</h2>
                <p>Open Cauldra without internet and save supported work on this trusted device. Changes sync after reconnecting.</p>
                <form id="offline-setup-form">
                    <label for="offline-setup-pin">Create a 6–12 digit offline PIN</label><input id="offline-setup-pin" type="password" inputmode="numeric" pattern="[0-9]{6,12}" minlength="6" maxlength="12" autocomplete="new-password" required>
                    <label for="offline-setup-confirm">Confirm offline PIN</label><input id="offline-setup-confirm" type="password" inputmode="numeric" pattern="[0-9]{6,12}" minlength="6" maxlength="12" autocomplete="new-password" required>
                    <p id="offline-setup-status" role="status"></p><button type="submit" class="offline-primary">Enable on this device</button>
                </form>
            </dialog>
            <dialog id="offline-change-pin-dialog" class="offline-dialog" aria-labelledby="offline-change-pin-title">
                <form method="dialog"><button class="offline-dialog-close" value="cancel" aria-label="Close">×</button></form>
                <h2 id="offline-change-pin-title">Change Offline PIN</h2>
                <form id="offline-change-pin-form">
                    <label for="offline-current-pin">Current Offline PIN</label><input id="offline-current-pin" type="password" inputmode="numeric" pattern="[0-9]{6,12}" autocomplete="off" required>
                    <label for="offline-new-pin">New 6–12 digit Offline PIN</label><input id="offline-new-pin" type="password" inputmode="numeric" pattern="[0-9]{6,12}" minlength="6" maxlength="12" autocomplete="new-password" required>
                    <label for="offline-new-pin-confirm">Confirm new Offline PIN</label><input id="offline-new-pin-confirm" type="password" inputmode="numeric" pattern="[0-9]{6,12}" minlength="6" maxlength="12" autocomplete="new-password" required>
                    <p id="offline-change-pin-status" role="status"></p><button type="submit" class="offline-primary">Change Offline PIN</button>
                </form>
            </dialog>
            <dialog id="offline-biometric-dialog" class="offline-dialog" aria-labelledby="offline-biometric-title">
                <form method="dialog"><button class="offline-dialog-close" value="cancel" aria-label="Close">×</button></form>
                <h2 id="offline-biometric-title">Use fingerprint or face unlock?</h2>
                <p>Android will show its secure system prompt. Cauldra never receives fingerprint or face data. Your Offline PIN will always remain available as fallback.</p>
                <form id="offline-biometric-form">
                    <div id="offline-biometric-pin-wrap"><label for="offline-biometric-pin">Confirm your Offline PIN</label><input id="offline-biometric-pin" type="password" inputmode="numeric" pattern="[0-9]{6,12}" autocomplete="off"></div>
                    <p id="offline-biometric-status" role="status"></p>
                    <div class="offline-dialog-actions"><button type="submit" class="offline-primary">Enable Biometrics</button><button type="button" id="offline-biometric-not-now">Not Now</button></div>
                </form>
            </dialog>
            <dialog id="offline-sync-dialog" class="offline-dialog offline-sync-dialog" aria-labelledby="offline-sync-title">
                <form method="dialog"><button class="offline-dialog-close" value="cancel" aria-label="Close">×</button></form>
                <h2 id="offline-sync-title">Sync details</h2><p id="offline-sync-summary" role="status"></p>
                <div id="offline-conflict-list"></div><button type="button" id="offline-sync-retry" class="offline-primary">Retry sync</button>
            </dialog>
            <dialog id="offline-remove-dialog" class="offline-dialog offline-remove-dialog" aria-labelledby="offline-remove-title">
                <h2 id="offline-remove-title">Remove offline data?</h2>
                <p id="offline-remove-copy"></p><p id="offline-remove-status" role="status"></p>
                <div class="offline-dialog-actions"><button type="button" id="offline-remove-cancel">Cancel</button><button type="button" id="offline-remove-confirm" class="offline-danger">Remove Offline Data</button></div>
            </dialog>`);
        document.getElementById("offline-opt-in-not-now").addEventListener("click", async () => {
            if (optInContext?.scope) await setOptInState(optInContext.scope, "declined");
            optInContext = null; document.getElementById("offline-opt-in-dialog").close();
        });
        document.getElementById("offline-opt-in-enable").addEventListener("click", () => {
            const context = optInContext; optInContext = null;
            document.getElementById("offline-opt-in-dialog").close();
            if (context) openSetup(context);
        });
        document.getElementById("offline-opt-in-dialog").addEventListener("cancel", async (event) => {
            event.preventDefault();
            if (optInContext?.scope) await setOptInState(optInContext.scope, "declined");
            optInContext = null; event.currentTarget.close();
        });
        document.getElementById("offline-remove-cancel").addEventListener("click", () => document.getElementById("offline-remove-dialog").close());
        document.getElementById("offline-remove-dialog").addEventListener("cancel", (event) => { if (!removeInFlight) event.currentTarget.close(); else event.preventDefault(); });
        document.getElementById("offline-remove-confirm").addEventListener("click", async () => {
            const dialog = document.getElementById("offline-remove-dialog");
            if (removeInFlight || !active || dialog.dataset.scope !== active.scope) return;
            try {
                if (await deleteCurrentData(dialog.dataset.scope)) {
                    dialog.close();
                    window.dispatchEvent(new Event("cauldra-offline-data-removed"));
                }
            } catch (_) { /* status remains visible and data is preserved */ }
        });
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
        document.getElementById("offline-unlock-dialog").addEventListener("cancel", (event) => {
            if (event.currentTarget.dataset.mandatory === "true") event.preventDefault();
        });
        document.getElementById("offline-identity").addEventListener("change", () => configureBiometricButton());
        document.getElementById("offline-biometric-unlock").addEventListener("click", () => attemptBiometricFromDialog());
        document.getElementById("offline-retry-online").addEventListener("click", () => window.dispatchEvent(new Event("cauldra-retry-online")));
        document.getElementById("offline-sync-retry").addEventListener("click", () => window.dispatchEvent(new Event("cauldra-manual-sync")));
        document.getElementById("offline-change-pin-form").addEventListener("submit", async (event) => {
            event.preventDefault();
            const status = document.getElementById("offline-change-pin-status");
            const oldPin = document.getElementById("offline-current-pin").value;
            const newPin = document.getElementById("offline-new-pin").value;
            const confirmPin = document.getElementById("offline-new-pin-confirm").value;
            if (newPin !== confirmPin) { status.textContent = "PINs do not match."; return; }
            status.textContent = "Changing PIN…";
            try {
                await changePin(oldPin, newPin);
                event.currentTarget.reset(); document.getElementById("offline-change-pin-dialog").close(); status.textContent = "";
                window.dispatchEvent(new Event("cauldra-offline-settings-changed"));
            } catch (error) { status.textContent = error.message; }
        });
        document.getElementById("offline-biometric-form").addEventListener("submit", async (event) => {
            event.preventDefault();
            const status = document.getElementById("offline-biometric-status");
            status.textContent = "Waiting for Android’s secure biometric prompt…";
            try {
                await enableBiometrics(document.getElementById("offline-biometric-pin").value);
                event.currentTarget.reset(); document.getElementById("offline-biometric-dialog").close(); status.textContent = "";
                window.dispatchEvent(new Event("cauldra-offline-settings-changed"));
            } catch (error) { status.textContent = error.message; }
        });
        document.getElementById("offline-biometric-not-now").addEventListener("click", async () => {
            await dismissBiometricOffer(); document.getElementById("offline-biometric-dialog").close();
            window.dispatchEvent(new Event("cauldra-offline-settings-changed"));
        });
    }

    function closeUnlockUi() {
        const dialog = document.getElementById("offline-unlock-dialog");
        if (dialog?.open) dialog.close();
        const pin = document.getElementById("offline-pin");
        if (pin) pin.value = "";
    }

    async function configureBiometricButton() {
        const select = document.getElementById("offline-identity");
        const button = document.getElementById("offline-biometric-unlock");
        const identity = (await listIdentities()).find((item) => item.scope === select?.value);
        const status = identity ? await biometricStatus(identity.scope) : { enabled: false };
        button.hidden = !status.enabled;
        return status.enabled;
    }

    function biometricFallbackMessage(status) {
        if (status === "cancelled") return "Biometric unlock was cancelled. Use your Offline PIN.";
        if (status === "lockout") return "Biometric unlock is temporarily unavailable. Use your Offline PIN.";
        if (status === "invalidated") return "Biometric unlock needs to be enabled again. Use your Offline PIN.";
        if (status === "expired") return "Offline Access has expired. Connect to the internet to refresh it.";
        if (status === "revoked") return "Offline Access was disabled on this device. Connect to the internet to enable it again.";
        return "Biometric unlock is unavailable. Use your Offline PIN.";
    }

    async function attemptBiometricFromDialog() {
        const scope = document.getElementById("offline-identity").value;
        const status = document.getElementById("offline-unlock-status");
        status.textContent = "Waiting for Android’s secure biometric prompt…";
        const result = await unlockWithBiometric(scope);
        if (result.status === "success") {
            closeUnlockUi(); status.textContent = "";
            window.dispatchEvent(new CustomEvent("cauldra-offline-unlocked", { detail: result }));
            return true;
        }
        status.textContent = biometricFallbackMessage(result.status);
        document.getElementById("offline-pin").focus();
        return false;
    }

    async function requestColdStart() {
        installUi();
        const identities = await listIdentities();
        const dialog = document.getElementById("offline-unlock-dialog");
        const select = document.getElementById("offline-identity");
        const copy = document.getElementById("offline-unlock-copy");
        const workspaceFields = document.getElementById("offline-workspace-fields");
        const pinButton = document.getElementById("offline-pin-unlock");
        const biometricButton = document.getElementById("offline-biometric-unlock");
        select.innerHTML = "";
        if (!identities.length) {
            copy.textContent = "Internet connection is required for first sign-in on this device.";
            workspaceFields.hidden = true; pinButton.hidden = true; biometricButton.hidden = true;
            document.getElementById("offline-pin").disabled = true;
            setAccessState(ACCESS_STATES.OFFLINE_ACCESS_NOT_PROVISIONED);
            if (!dialog.open) dialog.showModal();
            return false;
        }
        const valid = identities.filter((identity) => !identity.revoked_locally_at && Number(identity.expires_at || 0) > Date.now());
        if (!valid.length) {
            const revoked = identities.some((identity) => identity.revoked_locally_at);
            copy.textContent = revoked
                ? "Offline Access was disabled on this device. Connect to the internet to enable it again."
                : "Offline Access has expired. Connect to the internet to refresh it.";
            workspaceFields.hidden = true; pinButton.hidden = true; biometricButton.hidden = true;
            document.getElementById("offline-pin").disabled = true;
            setAccessState(revoked ? ACCESS_STATES.OFFLINE_ACCESS_REVOKED : ACCESS_STATES.OFFLINE_GRANT_EXPIRED);
            if (!dialog.open) dialog.showModal();
            return false;
        }
        copy.textContent = "Choose a previously verified workspace and enter its offline PIN.";
        workspaceFields.hidden = false; pinButton.hidden = false;
        document.getElementById("offline-pin").disabled = false;
        for (const identity of valid) {
            const option = document.createElement("option"); option.value = identity.scope;
            option.textContent = `${identity.display_name} — ${identity.business_name}`; select.appendChild(option);
        }
        setAccessState(ACCESS_STATES.OFFLINE_LOCKED);
        if (!dialog.open) dialog.showModal();
        const biometricEnabled = await configureBiometricButton();
        if (biometricEnabled) setTimeout(() => attemptBiometricFromDialog(), 100);
        else setTimeout(() => document.getElementById("offline-pin").focus(), 50);
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
                const nativeStatus = await biometricStatus(active?.scope);
                if (nativeStatus.available && !active.record.biometric_prompt_dismissed) await openBiometricSetup(true);
            } catch (error) { status.textContent = error.message; }
        };
        dialog.showModal();
        setTimeout(() => document.getElementById("offline-setup-pin").focus(), 50);
    }

    function openChangePin() {
        installUi();
        if (!active) throw new Error("Unlock Offline Access first.");
        const form = document.getElementById("offline-change-pin-form");
        form.reset(); document.getElementById("offline-change-pin-status").textContent = "";
        document.getElementById("offline-change-pin-dialog").showModal();
        setTimeout(() => document.getElementById("offline-current-pin").focus(), 50);
    }

    async function openBiometricSetup(isInitialOffer = false) {
        installUi();
        if (!active) throw new Error("Unlock Offline Access first.");
        const status = await biometricStatus(active.scope);
        if (!status.available) throw new Error("Biometric unlock is unavailable on this device.");
        const dialog = document.getElementById("offline-biometric-dialog");
        const pinWrap = document.getElementById("offline-biometric-pin-wrap");
        const pin = document.getElementById("offline-biometric-pin");
        pinWrap.hidden = !!active.rawDataKey;
        pin.required = !active.rawDataKey;
        document.getElementById("offline-biometric-not-now").textContent = isInitialOffer ? "Not Now" : "Cancel";
        document.getElementById("offline-biometric-status").textContent = "";
        if (!dialog.open) dialog.showModal();
        setTimeout(() => (pin.required ? pin : dialog.querySelector('button[type="submit"]')).focus(), 50);
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

    function lockWorkspace() {
        const wasOffline = !!active?.offline;
        active = null;
        if (wasOffline) setAccessState(ACCESS_STATES.OFFLINE_LOCKED);
    }

    function handleAppActivity(isActive) {
        if (!isActive) { backgroundedAt = Date.now(); return; }
        if (active?.offline && backgroundedAt && Date.now() - backgroundedAt >= BACKGROUND_RELOCK_MS) {
            lockWorkspace();
            requestColdStart().catch(() => {});
            window.dispatchEvent(new Event("cauldra-offline-locked"));
        }
        backgroundedAt = 0;
    }

    function installLifecycle() {
        document.addEventListener("visibilitychange", () => handleAppActivity(!document.hidden));
        const appPlugin = window.Capacitor?.Plugins?.App;
        if (appPlugin && typeof appPlugin.addListener === "function") {
            appPlugin.addListener("appStateChange", ({ isActive }) => handleAppActivity(!!isActive)).catch(() => {});
        }
    }

    document.addEventListener("DOMContentLoaded", () => { installUi(); installLifecycle(); });

    window.CauldraOffline = Object.freeze({
        DB_VERSION, CLIENT_SCHEMA_VERSION, ACCESS_STATES, openDb, listIdentities, requestColdStart, unlock, unlockWithBiometric,
        resumeOnline, provision, refreshAccess, refreshSnapshot, cacheWrite, cacheRead, enqueue, listOutbox, updateOutbox, removeOutbox,
        openSetup, openChangePin, openBiometricSetup, openSyncDetails, biometricStatus, enableBiometrics, disableBiometrics,
        removeCurrentData, changePin, disable, storageStatus, quarantineActive, closeUnlockUi, offerOptIn, sessionStatus, lock: lockWorkspace,
        setState(state) { if (Object.values(ACCESS_STATES).includes(state)) setAccessState(state); }, currentState() { return accessState; },
        isUnlocked() { return !!active; }, isOffline() { return !!(active && active.offline); },
        currentScope() { return active && active.scope; }, currentDeviceId() { return active && active.record.device_id; },
        currentSnapshot() { return active && active.snapshot; }, currentGrant() { return active && active.grant; },
    });
})();
