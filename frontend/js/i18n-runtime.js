/* Cauldra launch-language runtime (UI-POLISH / I18N stage).
 *
 * The keyed bundles in app.js (TRANSLATIONS + t()) remain the primary
 * mechanism. Much of the interface, however, is English copy written straight
 * into index.html, into app.js/offline.js/payments.js templates, or returned
 * by the server. This runtime translates that remaining copy for the launch
 * languages by exact source text, using the reviewed catalog in
 * js/i18n-catalog.js (generated from i18n/launch_catalog.json by
 * scripts/build-i18n-catalog.js).
 *
 * Rules:
 *  - Only whole text nodes / attribute values that exactly match a catalog
 *    entry (or a catalog pattern with {n} placeholders) change. Anything else
 *    — product names, amounts, codes, user-entered text — is left untouched.
 *  - Nothing is ever written back to data: only what is displayed changes.
 *  - Elements marked translate="no" or .notranslate, inputs' typed values,
 *    textareas, scripts and styles are never touched.
 *  - English is the source language: switching back restores the original
 *    text exactly.
 */
(function () {
    'use strict';
    var LAUNCH = ['en', 'fr', 'ar', 'es', 'pt'];
    var RTL = ['ar'];
    var STORAGE_KEY = 'cauldra_language';
    var CATALOG = window.CAULDRA_I18N_CATALOG || { langs: [], exact: [], patterns: [] };
    var ATTRS = ['placeholder', 'title', 'aria-label', 'alt'];
    var SKIP_TAGS = { SCRIPT: 1, STYLE: 1, TEXTAREA: 1, NOSCRIPT: 1, CODE: 1, PRE: 1, TEMPLATE: 1 };

    var lang = 'en';
    var exact = new Map();
    var patterns = [];
    var used = false;                 // becomes true once anything was translated
    var textState = new WeakMap();    // Text -> { src, out }
    var attrState = new WeakMap();    // Element -> { attr: { src, out } }
    var regionNames = null;

    function norm(s) { return String(s).replace(/\s+/g, ' ').trim(); }
    function escRe(s) { return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }

    function build(code) {
        exact = new Map(); patterns = []; regionNames = null;
        var i = CATALOG.langs.indexOf(code);
        if (i < 0) return;
        CATALOG.exact.forEach(function (row) { if (row[i + 1]) exact.set(row[0], row[i + 1]); });
        CATALOG.patterns.forEach(function (row) {
            var tr = row[i + 1];
            if (!tr) return;
            // {n} matches any text; {#n} matches a number only (counts, amounts).
            var parts = row[0].split(/\{(#?\d+)\}/), re = '^', order = [], anchor = '';
            for (var k = 0; k < parts.length; k++) {
                if (k % 2 === 0) { re += escRe(parts[k]); if (parts[k].trim().length > anchor.length) anchor = parts[k].trim(); }
                else if (parts[k].charAt(0) === '#') { re += '([-+]?\\d[\\d.,\\s\\u00a0]*)'; order.push(Number(parts[k].slice(1))); }
                else { re += '(.*?)'; order.push(Number(parts[k])); }
            }
            patterns.push({ re: new RegExp(re + '$'), order: order, tr: tr, anchor: anchor });
        });
        try { regionNames = new Intl.DisplayNames([code], { type: 'region' }); } catch (_) { regionNames = null; }
    }

    // Country names come from the browser's own locale data (by ISO code), so
    // all ~240 names are correct without a hand-maintained list.
    function countryName(key) {
        var iso = window.CAULDRA_COUNTRY_ISO && window.CAULDRA_COUNTRY_ISO[key];
        if (!iso || !regionNames) return null;
        try { var n = regionNames.of(iso); return n && n !== iso ? n : null; } catch (_) { return null; }
    }

    function translate(src) {
        if (lang === 'en') return null;
        var key = norm(src);
        if (!key || !/[A-Za-z]/.test(key)) return null;
        var out = exact.get(key);
        if (out == null) out = countryName(key);
        if (out == null) {
            for (var p = 0; p < patterns.length; p++) {
                var pat = patterns[p];
                if (pat.anchor && key.indexOf(pat.anchor) < 0) continue;
                var m = pat.re.exec(key);
                if (!m) continue;
                var vals = {};
                // A captured value that is itself catalog copy (e.g. "month", "Monthly") is
                // translated too; anything else (amounts, names, codes) is kept as is.
                pat.order.forEach(function (n, j) { var v = m[j + 1]; var t = v && exact.get(norm(v)); vals[n] = t ? v.replace(norm(v), t) : v; });
                out = pat.tr.replace(/\{(\d+)\}/g, function (_, n) { return vals[n] != null ? vals[n] : ''; });
                break;
            }
        }
        if (out == null) return null;
        var lead = src.match(/^\s*/)[0], trail = src.match(/\s*$/)[0];
        return lead + out + trail;
    }

    function skipped(el) {
        for (var e = el; e && e.nodeType === 1; e = e.parentElement) {
            if (SKIP_TAGS[e.tagName]) return true;
            if (e.getAttribute('translate') === 'no' || (e.classList && e.classList.contains('notranslate')) || e.isContentEditable) return true;
        }
        return false;
    }

    function processText(node) {
        var st = textState.get(node), cur = node.nodeValue;
        var src = (st && cur === st.out) ? st.src : cur;
        var out = (lang === 'en' || !node.parentElement || skipped(node.parentElement)) ? null : translate(src);
        if (out == null) {
            if (st) { textState.delete(node); if (cur === st.out && cur !== st.src) node.nodeValue = st.src; }
            return;
        }
        used = true;
        textState.set(node, { src: src, out: out });
        if (cur !== out) node.nodeValue = out;
    }

    function processAttr(el, name) {
        if (!el.hasAttribute(name) && !(attrState.get(el) || {})[name]) return;
        var all = attrState.get(el), st = all && all[name], cur = el.getAttribute(name);
        if (cur == null) { if (st) delete all[name]; return; }
        var src = (st && cur === st.out) ? st.src : cur;
        var out = (lang === 'en' || skipped(el)) ? null : translate(src);
        if (out == null) {
            if (st) { delete all[name]; if (cur === st.out && cur !== st.src) el.setAttribute(name, st.src); }
            return;
        }
        used = true;
        if (!all) { all = {}; attrState.set(el, all); }
        all[name] = { src: src, out: out };
        if (cur !== out) el.setAttribute(name, out);
    }

    function processElement(el) {
        for (var a = 0; a < ATTRS.length; a++) if (el.hasAttribute(ATTRS[a]) || (attrState.get(el) || {})[ATTRS[a]]) processAttr(el, ATTRS[a]);
        if (el.tagName === 'INPUT' && /^(button|submit|reset)$/i.test(el.type) && el.hasAttribute('value')) processAttr(el, 'value');
    }

    function walk(root) {
        if (!root) return;
        if (root.nodeType === 3) { processText(root); return; }
        if (root.nodeType !== 1 && root.nodeType !== 9 && root.nodeType !== 11) return;
        if (root.nodeType === 1) { if (SKIP_TAGS[root.tagName]) return; processElement(root); }
        var tw = document.createTreeWalker(root, 5 /* SHOW_ELEMENT | SHOW_TEXT */, {
            acceptNode: function (n) { return (n.nodeType === 1 && SKIP_TAGS[n.tagName]) ? 2 /* REJECT */ : 1; }
        });
        var n;
        while ((n = tw.nextNode())) { if (n.nodeType === 3) processText(n); else processElement(n); }
    }

    var observer = new MutationObserver(function (muts) {
        if (lang === 'en' && !used) return;
        for (var i = 0; i < muts.length; i++) {
            var m = muts[i];
            if (m.type === 'characterData') processText(m.target);
            else if (m.type === 'childList') { for (var j = 0; j < m.addedNodes.length; j++) walk(m.addedNodes[j]); }
            else if (m.type === 'attributes') {
                // "value" is only ever a label on button-type inputs; a text field's value is data.
                if (m.attributeName === 'value' && !(m.target.tagName === 'INPUT' && /^(button|submit|reset)$/i.test(m.target.type))) continue;
                processAttr(m.target, m.attributeName);
            }
        }
    });

    function applyDocumentLanguage() {
        var root = document.documentElement;
        root.lang = lang;
        root.dir = RTL.indexOf(lang) >= 0 ? 'rtl' : 'ltr';
        root.classList.toggle('cauldra-rtl', RTL.indexOf(lang) >= 0);
    }

    function setLanguage(code) {
        var next = LAUNCH.indexOf(code) >= 0 ? code : 'en';
        if (next === lang && document.readyState !== 'loading') { applyDocumentLanguage(); return lang; }
        lang = next;
        build(lang);
        applyDocumentLanguage();
        if (lang !== 'en' || used) walk(document.documentElement);
        return lang;
    }

    function initialLanguage() {
        try {
            var stored = String(localStorage.getItem(STORAGE_KEY) || '').toLowerCase();
            if (LAUNCH.indexOf(stored) >= 0) return stored;
        } catch (_) {}
        var nav = String(navigator.language || '').slice(0, 2).toLowerCase();
        return LAUNCH.indexOf(nav) >= 0 ? nav : 'en';
    }

    lang = initialLanguage();
    build(lang);
    applyDocumentLanguage();
    observer.observe(document.documentElement, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS.concat(['value']) });
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () { if (lang !== 'en') walk(document.documentElement); });
    } else if (lang !== 'en') {
        walk(document.documentElement);
    }

    window.CauldraI18n = Object.freeze({
        LAUNCH_LANGUAGES: LAUNCH.slice(),
        isLaunchLanguage: function (code) { return LAUNCH.indexOf(code) >= 0; },
        setLanguage: setLanguage,
        currentLanguage: function () { return lang; },
        translate: function (text) { var t = translate(String(text || '')); return t == null ? String(text || '') : t; },
        refresh: function (root) { if (lang !== 'en' || used) walk(root || document.documentElement); },
    });
})();
