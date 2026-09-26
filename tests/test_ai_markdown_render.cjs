// AI-001 / UX-013 — AI replies are rendered from Markdown into a fixed set of
// elements with every model character escaped first; hostile or HTML-like model
// text must never become markup. Generate Insights runs once at a time.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const appSource = fs.readFileSync(path.join(path.resolve(__dirname, '..'), 'frontend/js/app.js'), 'utf8');

function extract(name) {
  const start = appSource.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `${name} exists`);
  let depth = 0;
  for (let i = appSource.indexOf('{', start); i < appSource.length; i++) {
    if (appSource[i] === '{') depth++;
    else if (appSource[i] === '}' && --depth === 0) return appSource.slice(start, i + 1);
  }
  throw new Error(`could not extract ${name}`);
}

const ctx = {};
vm.createContext(ctx);
vm.runInContext(`${extract('renderAIMarkdown')}; this.render = renderAIMarkdown;`, ctx);
const render = ctx.render;

// --- The formatting Cauldra needs -------------------------------------------
const report = render('### Stock to check\n\nRice is **selling fast** and *needs* a reorder.\n\n- **Rice**: 3 left\n- Beans: 40 left\n\n1. Reorder rice\n2. Count beans\n\n---\nDone.');
assert.equal(report,
  '<p class="ai-md-heading">Stock to check</p>'
  + '<p>Rice is <strong>selling fast</strong> and <em>needs</em> a reorder.</p>'
  + '<ul><li><strong>Rice</strong>: 3 left</li><li>Beans: 40 left</li></ul>'
  + '<ol><li>Reorder rice</li><li>Count beans</li></ol>'
  + '<hr><p>Done.</p>');
assert.equal(render('Line one\nLine two'), '<p>Line one<br>Line two</p>', 'soft line breaks are kept');
assert.equal(render(''), '', 'empty reply renders nothing (the caller shows its own message)');
assert.equal(render(null), '');
assert.equal(render('2 * 3 * 4 units'), '<p>2 * 3 * 4 units</p>', 'arithmetic stars are not emphasis');
assert.equal(render('Price is ₦46,000'), '<p>Price is ₦46,000</p>');

// --- Hostile / HTML-like model text ------------------------------------------
const hostile = [
  '<script>alert(1)</script>',
  '<img src=x onerror=alert(1)>',
  '**<img src=x onerror=alert(1)>**',
  '- <a href="javascript:alert(1)">click</a>',
  '[click](javascript:alert(1))',
  '### <svg onload=alert(1)>',
  '`<iframe src=//evil>`',
  '"><script>alert(1)</script>',
  "' onmouseover='alert(1)",
  '<style>body{display:none}</style>',
  '&lt;script&gt;alert(1)&lt;/script&gt;',
];
const ALLOWED_TAG = /^<\/?(p|p class="ai-md-heading"|strong|em|ul|ol|li|hr|br)>$/;
for (const text of hostile) {
  const html = render(text);
  for (const tag of html.match(/<[^>]*>/g) || []) {
    assert.match(tag, ALLOWED_TAG, `only renderer-written tags appear for ${JSON.stringify(text)} (saw ${tag})`);
  }
  assert.ok(!/<(script|img|a|svg|iframe|style)\b/i.test(html), `no model tag survives: ${html}`);
  assert.ok(!/<[^>]*\son\w+\s*=/i.test(html), `no event attribute inside a tag: ${html}`);
  assert.ok(!/<[^>]*href/i.test(html), 'no link is ever produced');
}
assert.equal(render('<b>bold?</b>'), '<p>&lt;b&gt;bold?&lt;/b&gt;</p>', 'HTML is shown as text');
assert.equal(render('&lt;script&gt;'), '<p>&amp;lt;script&amp;gt;</p>', 'entities are not decoded into markup');

// --- The replies use the renderer (not a raw paragraph of escaped Markdown) ----
assert.ok(/appendAIReply\(chatMessages, "Cauldra Insights", data\.insight/.test(appSource), 'insights are rendered');
assert.ok(/appendAIReply\(chatMessages, "Cauldra Assistant", data\.reply/.test(appSource), 'chat replies are rendered');
assert.ok(!/escapeHtml\(data\.insight\)/.test(appSource) && !/escapeHtml\(data\.reply\)/.test(appSource),
  'the old one-paragraph output is gone');
assert.ok(/<div class="ai-md text-textMain text-xs">\$\{body\}<\/div>/.test(appSource),
  'block elements are placed in a div, never inside a <p>');

// --- UX-013: one run at a time, and the controls report it --------------------
const html = fs.readFileSync(path.join(path.resolve(__dirname, '..'), 'frontend/index.html'), 'utf8');
const triggers = html.match(/<button type="button"[^>]*data-ai-insights-trigger[^>]*>/g) || [];
assert.equal(triggers.length, 2, 'the AI Center card and the chat lightbulb are both real buttons');
{
  const events = [];
  const els = [{ disabled: false, attrs: {}, setAttribute(k, v) { this.attrs[k] = v; }, removeAttribute(k) { delete this.attrs[k]; } }];
  let release;
  const sandbox = {
    document: { querySelectorAll: () => els },
    runAIInsights: () => { events.push('run'); return new Promise(r => { release = r; }); },
  };
  vm.createContext(sandbox);
  vm.runInContext(`let aiInsightsInFlight = false; async ${extract('fetchAIInsights')}; this.go = fetchAIInsights;`, sandbox);
  const first = sandbox.go();
  assert.equal(els[0].disabled, true, 'buttons are disabled while insights load');
  assert.equal(els[0].attrs['aria-busy'], 'true');
  sandbox.go(); sandbox.go();  // Enter pressed twice more, or the lightbulb clicked
  assert.deepEqual(events, ['run'], 'only one request is made');
  release();
  first.then(() => {
    assert.equal(els[0].disabled, false, 'buttons are enabled again afterwards');
    assert.equal(els[0].attrs['aria-busy'], undefined);
    sandbox.go();
    assert.deepEqual(events, ['run', 'run'], 'a later, separate run is allowed');
    console.log('AI Markdown rendering and Generate Insights checks passed');
  });
}
