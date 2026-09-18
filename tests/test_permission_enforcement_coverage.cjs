'use strict';
// PERM-001 Phase A / T3 — static permission-enforcement coverage.
//
// PERM-001's root cause was structural: the PERMISSIONS registry declared
// codes that no endpoint ever checked, so a permission could look enforced
// in the UI (and in the registry) while the API happily served every caller.
// This test is the regression guard for that entire CLASS of defect, and it
// runs without a database or a server.
//
// The rule: EVERY non-reserved permission code in the registry must have at
// least one enforcement site — require_permission(...) / has_permission(...)
// in backend/main.py, an effective-permission lookup in
// backend/offline_access.py, or a replay rule mapping there.
//
// Deliberately broader than "Staff-denied codes only": `inventory.add_product`
// now defaults to Staff = true (product decision D13) while still being an
// enforceable permission, so a Staff-only rule would have stopped protecting
// exactly the code PERM-001 had to fix.
//
// Honest limitation: this proves a code is CHECKED SOMEWHERE, not that every
// route which exposes that data is gated (see finding X2 — `inventory.view`
// is referenced by GET /warehouses/operational, yet the product/inventory
// listings still do not check it; that remains a separate, tracked finding).
// The behavioural matrix in tests/test_perm001_staff_enforcement.py is what
// proves per-endpoint enforcement.

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const mainPy = fs.readFileSync(path.join(root, 'backend/main.py'), 'utf8');
const offlinePy = fs.readFileSync(path.join(root, 'backend/offline_access.py'), 'utf8');

// Codes that may legitimately have no enforcement site. Every entry needs a
// reason recorded here AND in PERM-001_REMEDIATION_PLAN.md. Never add a code
// to this list to make this test green — that is precisely the failure mode
// this file exists to catch.
const ALLOW_LIST = new Map([
    // (reserved codes are excluded structurally below; this list is for
    // anything that is NOT flagged reserved but is still knowingly unchecked)
]);

// ---------------------------------------------------------------- registry
const registryMatch = mainPy.match(/^PERMISSIONS\s*=\s*\{([\s\S]*?)^\}/m);
assert.ok(registryMatch, 'PERMISSIONS registry must be parseable from backend/main.py');
const registryBody = registryMatch[1];

const registry = new Map();
const entryRe = /^\s*"([a-z_]+\.[a-z_]+)":\s*\{([^\n]*?)\},\s*$/gm;
for (let m; (m = entryRe.exec(registryBody)); ) {
    registry.set(m[1], {
        spec: m[2],
        reserved: /"reserved":\s*True/.test(m[2]),
        staffDefault: /"staff":\s*True/.test(m[2]),
    });
}
assert.ok(registry.size >= 40, `expected the full registry, parsed only ${registry.size} codes`);

// ------------------------------------------------------- enforcement sites
const sites = new Set();
const guardRe = /(?:require_permission|has_permission)\(\s*[A-Za-z_.[\]"']+\s*,\s*"([a-z_]+\.[a-z_]+)"/g;
for (let m; (m = guardRe.exec(mainPy)); ) sites.add(m[1]);

// offline_access.py reads the same effective map directly, and maps replay
// operation types onto permission codes.
const offlineLookupRe = /permissions\(user\)\.get\(\s*"([a-z_]+\.[a-z_]+)"/g;
for (let m; (m = offlineLookupRe.exec(offlinePy)); ) sites.add(m[1]);
const rulesMatch = offlinePy.match(/rules\s*=\s*\{([\s\S]*?)\}/);
assert.ok(rulesMatch, 'offline replay rule map must be parseable from backend/offline_access.py');
const ruleCodeRe = /"([a-z_]+\.[a-z_]+)"/g;
for (let m; (m = ruleCodeRe.exec(rulesMatch[1])); ) sites.add(m[1]);
const offlineGuardRe = /g\["(?:require_permission|has_permission)"\]\([^)]*?"([a-z_]+\.[a-z_]+)"/g;
for (let m; (m = offlineGuardRe.exec(offlinePy)); ) sites.add(m[1]);
assert.ok(
    /g\["require_permission"\]\(user,\s*rules\[op\.type\]\)/.test(offlinePy),
    'offline replay must enforce its permission rule map',
);

// -------------------------------------------------------------- the rule
const missing = [];
for (const [code, meta] of registry) {
    if (meta.reserved) continue;               // declared, endpoint not built yet
    if (ALLOW_LIST.has(code)) continue;        // knowingly unchecked, documented
    if (!sites.has(code)) missing.push(code);
}
assert.deepEqual(
    missing,
    [],
    `these permission codes are declared but never enforced anywhere (PERM-001 class of defect): ${missing.join(', ')}`,
);

// Stale allow-list entries must not linger: if a code has since been wired
// up (or removed from the registry), the exemption has to go too.
for (const code of ALLOW_LIST.keys()) {
    assert.ok(registry.has(code), `allow-listed code ${code} is no longer in the registry — remove the exemption`);
    assert.ok(!sites.has(code), `allow-listed code ${code} is now enforced — remove the exemption`);
}

// ------------------------------------- PERM-001 Phase A specific anchors
// These pin the exact decisions Phase A implemented, so a later refactor
// cannot quietly undo them.
const addProduct = registry.get('inventory.add_product');
assert.ok(addProduct, 'inventory.add_product must exist in the registry');
assert.ok(addProduct.staffDefault, 'D13: Staff must default to allowed for inventory.add_product');
assert.ok(
    /"staff_grantable":\s*True/.test(addProduct.spec) && /"manager_can_grant":\s*True/.test(addProduct.spec),
    'inventory.add_product must stay individually revocable by Admin and Manager (D13 requires revocation to be usable)',
);

const createProduct = mainPy.match(/def create_product\([\s\S]*?\n(?=@app\.)/);
assert.ok(createProduct, 'create_product must be parseable');
const createBody = createProduct[0];
assert.match(createBody, /require_permission\(user, "inventory\.add_product"\)/, 'B1: create_product must enforce inventory.add_product');
assert.ok(
    createBody.indexOf('require_permission(user, "inventory.add_product")') < createBody.indexOf('claim_idempotent_mutation('),
    'B1: the permission check must run BEFORE the idempotency claim',
);
assert.ok(
    createBody.indexOf('require_permission(user, "inventory.add_product")') < createBody.indexOf('check_plan_limit('),
    'B1: the permission check must run BEFORE the plan-limit check',
);
assert.doesNotMatch(createBody, /role\s*==\s*["']staff["']/, 'B1: never role-check Staff here — use the effective permission');

for (const [label, pattern] of [
    ['B2 GET /warehouses/ enforces warehouse.view', /def list_warehouses\([\s\S]*?require_permission\(user, "warehouse\.view"\)/],
    ['B3 operational warehouse endpoint exists', /@app\.get\("\/warehouses\/operational"\)/],
    ['B4 GET /suppliers/ enforces supplier.view', /def list_suppliers\([\s\S]*?require_permission\(user, "supplier\.view"\)/],
    ['B5 GET /sales/analytics enforces reports.sales', /def sales_analytics\([\s\S]*?require_permission\(user, "reports\.sales"\)/],
    ['B6 expense query scopes to the owner without expenses.view_all', /_build_expenses_query\([\s\S]*?has_permission\(user, "expenses\.view_all"\)[\s\S]*?Expense\.owner_id == user\.id/],
]) {
    assert.match(mainPy, pattern, label);
}

// B3's contract: identity only, and no Branch/location filtering in Phase A.
const projection = mainPy.match(/def operational_warehouse_projection\([\s\S]*?\n(?=@app\.)/);
assert.ok(projection, 'operational_warehouse_projection must be parseable');
// Strip the docstring: it legitimately NAMES the fields this projection must
// not return, so the forbidden-field check has to run against code only.
const projectionCode = projection[0].replace(/"""[\s\S]*?"""/, '');
assert.match(projectionCode, /"id": w\.id, "name": w\.name, "location_id": w\.location_id/, 'B3: exactly id/name/location_id');
for (const forbidden of ['sku_count', 'created_at', 'is_active":', 'location_name']) {
    assert.ok(!projectionCode.includes(forbidden), `B3: the operational projection must not expose ${forbidden}`);
}
assert.match(projectionCode, /Warehouse\.is_active == True/, 'B3: active warehouses only');
assert.match(projectionCode, /Warehouse\.business_id == business_id/, 'B3: business-scoped');

// O1/O2: the offline snapshot must not be a second, ungated read path.
assert.match(offlinePy, /suppliers_permitted = bool\(permissions\(user\)\.get\("supplier\.view"\)\)/, 'O1: snapshot must check supplier.view');
assert.match(offlinePy, /freshness\["\/suppliers\/"\] = "permission unavailable"/, 'O1: denied suppliers must be marked in freshness');
assert.match(offlinePy, /permissions\(user\)\.get\("warehouse\.view"\)[\s\S]*?operational_warehouse_projection/, 'O2: snapshot must fall back to the operational projection');
assert.match(offlinePy, /"business": g\["serialize_business"\]\(business, db\)/, 'PLAN-009: the business payload line must remain untouched');

// Frontend guards (F1–F3) — the UI must not fire requests it cannot make.
const appJs = fs.readFileSync(path.join(root, 'frontend/js/app.js'), 'utf8');
assert.match(appJs, /hasPermission\('warehouse\.view'\)/, 'F1: loadWarehouses must branch on warehouse.view');
assert.match(appJs, /\/warehouses\/operational/, 'F1: the operational endpoint must be used as the fallback');
assert.match(appJs, /const canReadSuppliers = hasPermission\('supplier\.view'\)/, 'F2: suppliers must only be fetched with supplier.view');
assert.match(appJs, /if\(!hasPermission\('reports\.sales'\)\)/, 'F3: the sales chart must be skipped without reports.sales');
assert.match(appJs, /'add product': 'inventory\.add_product'/, 'F4: Add Product must stay mapped to the effective permission');

console.log(`PASS permission enforcement coverage — ${registry.size} codes, ${sites.size} enforced, ${ALLOW_LIST.size} allow-listed`);
