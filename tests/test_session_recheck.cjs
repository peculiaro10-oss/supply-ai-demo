// SESS-001 follow-up (final Android pass, F22): the 401 session re-check must
// never stay "in flight" after it returns early. Runs the real
// recheckSessionAfterUnauthorized from frontend/js/app.js in a vm: a 401 for an
// older token (or during sign-out) is ignored, and the NEXT genuine 401 still
// re-checks the session and ends it visibly.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const app = fs.readFileSync(path.join(__dirname, '..', 'frontend', 'js', 'app.js'), 'utf8');
const start = app.indexOf('let sessionRecheckInFlight = null;');
const end = app.indexOf('(function installSessionExpiryDetector', start);
if (start < 0 || end < 0) throw new Error('recheckSessionAfterUnauthorized not found');
const source = app.slice(start, end).replace('let sessionRecheckInFlight', 'var sessionRecheckInFlight');

let failures = 0;
const check = (name, ok, detail = '') => { console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${!ok && detail ? ' — ' + detail : ''}`); if (!ok) failures++; };

function world({ token = 'current', signingOut = false, refreshOk = false } = {}) {
    const calls = [];
    const ctx = {
        authToken: token, signOutInProgress: signingOut, businessDeletionInProgress: false,
        refreshSessionOrEnd: async () => { calls.push('refresh'); if (!refreshOk) { calls.push('ended'); ctx.authToken = ''; } return refreshOk; },
        showToast: msg => calls.push('toast:' + msg.slice(0, 20)),
    };
    vm.createContext(ctx);
    vm.runInContext(source + '\nthis.recheck = recheckSessionAfterUnauthorized; this.inFlight = () => sessionRecheckInFlight;', ctx);
    return { ctx, calls };
}

(async () => {
    let w = world();
    await w.ctx.recheck('older-token', false);
    await new Promise(r => setImmediate(r));
    check('A 401 for an older token clears the in-flight re-check', w.ctx.inFlight() === null);
    await w.ctx.recheck('current', false);
    check('The next genuine 401 still re-checks and ends the session', w.calls.join() === 'refresh,ended', w.calls.join());

    w = world({ signingOut: true });
    await w.ctx.recheck('current', false);
    await new Promise(r => setImmediate(r));
    check('A 401 during sign-out clears the in-flight re-check', w.ctx.inFlight() === null && !w.calls.length, w.calls.join());

    w = world({ refreshOk: true });
    const p1 = w.ctx.recheck('current', true), p2 = w.ctx.recheck('current', true);
    check('Concurrent 401s share one re-check', p1 === p2);
    await p1; await new Promise(r => setImmediate(r));
    check('A renewed session after a failed write says so once', w.calls.join() === 'refresh,toast:Your session was ren', w.calls.join());
    check('The re-check is cleared after it completes', w.ctx.inFlight() === null);

    console.log(failures ? `\n${failures} FAILED` : '\nAll session re-check checks passed');
    process.exit(failures ? 1 : 0);
})();
