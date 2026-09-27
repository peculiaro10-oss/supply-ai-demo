// Android Back and the mobile menu drawer (final Android pass, row 61 / AN-UI-7).
// Runs the real handleNativeBack from frontend/js/app.js against a stub DOM:
// an open drawer closes on Back, protected gates still win, and module modals
// and history behave as before.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const app = fs.readFileSync(path.join(__dirname, '..', 'frontend', 'js', 'app.js'), 'utf8');
const start = app.indexOf('function handleNativeBack(');
const end = app.indexOf('window.cauldraHandleNativeBack', start);
if (start < 0 || end < 0) throw new Error('handleNativeBack not found');
const source = app.slice(start, end);

let failures = 0;
const check = (name, ok, detail = '') => { console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${!ok && detail ? ' — ' + detail : ''}`); if (!ok) failures++; };

function world({ drawer = false, payment = false, dialog = null, modal = null } = {}) {
    const calls = [];
    const classes = new Set(drawer ? [] : ['hidden']);
    const overlay = { classList: { contains: c => classes.has(c) } };
    const elements = { 'mobile-nav-overlay': overlay, 'payment-overlay': payment ? { hidden: false } : { hidden: true } };
    const ctx = {
        document: {
            getElementById: id => elements[id] || null,
            querySelectorAll: sel => (sel === 'dialog[open]' && dialog ? [dialog] : []),
        },
        window: { history: { back: () => calls.push('history') } },
        Event: class { constructor(type, init) { this.type = type; Object.assign(this, init); } },
        BACK_PROTECTED_MODALS: new Set(['password-change-modal', 'business-id-success-modal']),
        topmostOpenModal: () => modal,
        modalCloseControl: m => (m && m.closable ? { click: () => calls.push('modal-close:' + m.id) } : null),
        closeMobileNav: () => { classes.add('hidden'); calls.push('closeMobileNav'); },
    };
    vm.createContext(ctx);
    vm.runInContext(source + '\nthis.handleNativeBack = handleNativeBack;', ctx);
    return { back: canGoBack => ctx.handleNativeBack(canGoBack), calls, drawerOpen: () => !classes.has('hidden') };
}

let w = world({ drawer: true });
check('Back closes an open menu drawer', w.back(false) === 'drawer' && !w.drawerOpen() && w.calls.join() === 'closeMobileNav', w.calls.join());
w = world({ drawer: true });
check('Back closes the drawer before leaving history', w.back(true) === 'drawer' && !w.calls.includes('history'), w.calls.join());
w = world({ drawer: true, modal: { id: 'products-modal', closable: true } });
check('With a module modal under the drawer, the drawer closes first', w.back(false) === 'drawer' && !w.calls.some(c => c.startsWith('modal-close')), w.calls.join());
w = world({ drawer: true, payment: true });
check('Payment hand-off still blocks Back (drawer untouched)', w.back(false) === 'blocked' && w.drawerOpen(), w.calls.join());
w = world({ drawer: true, dialog: { dataset: { mandatory: 'true' } } });
check('A mandatory dialog still blocks Back', w.back(false) === 'blocked' && w.drawerOpen(), w.calls.join());
w = world({ modal: { id: 'password-change-modal', closable: true } });
check('Protected modal (forced password change) still blocks Back', w.back(false) === 'blocked' && !w.calls.length, w.calls.join());
w = world({ modal: { id: 'products-modal', closable: true } });
check('Ordinary modal still closes on Back', w.back(false) === 'modal' && w.calls.join() === 'modal-close:products-modal', w.calls.join());
w = world({});
check('Nothing open: Back goes back in history when possible', w.back(true) === 'history' && w.calls.join() === 'history', w.calls.join());

console.log(failures ? `\n${failures} FAILED` : '\nAll Android Back drawer checks passed');
process.exit(failures ? 1 : 0);
