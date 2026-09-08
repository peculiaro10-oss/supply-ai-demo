const fs = require('fs');
const http = require('http');
const path = require('path');
const port = Number(process.argv[2] || 9333);
const origin = 'http://127.0.0.1:8769';
const frontend = path.resolve(__dirname, '..', 'frontend');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const mime = { '.html': 'text/html; charset=utf-8', '.js': 'application/javascript', '.css': 'text/css', '.json': 'application/json', '.png': 'image/png', '.woff2': 'font/woff2' };

const server = http.createServer((request, response) => {
  const pathname = new URL(request.url, origin).pathname;
  if (pathname === '/health') { response.writeHead(200, { 'content-type': 'application/json' }); return response.end('{"status":"ok"}'); }
  const relative = pathname === '/' ? 'index.html' : pathname.slice(1);
  const target = path.resolve(frontend, relative);
  if (!target.startsWith(frontend + path.sep) || !fs.existsSync(target) || !fs.statSync(target).isFile()) { response.writeHead(404); return response.end('not found'); }
  response.writeHead(200, { 'content-type': mime[path.extname(target)] || 'application/octet-stream', 'cache-control': 'no-store' });
  fs.createReadStream(target).pipe(response);
});

async function connect(url) {
  const socket = new WebSocket(url);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  let id = 1;
  const pending = new Map();
  socket.onmessage = event => {
    const message = JSON.parse(event.data);
    if (!message.id || !pending.has(message.id)) return;
    const handlers = pending.get(message.id);
    pending.delete(message.id);
    message.error ? handlers.reject(new Error(message.error.message)) : handlers.resolve(message.result);
  };
  return {
    call(method, params = {}) {
      const callId = id++;
      socket.send(JSON.stringify({ id: callId, method, params }));
      return new Promise((resolve, reject) => pending.set(callId, { resolve, reject }));
    },
    close() { socket.close(); },
  };
}

(async () => {
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(8769, '127.0.0.1', resolve); });
  const created = await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' });
  const page = await created.json();
  const cdp = await connect(page.webSocketDebuggerUrl);
  await cdp.call('Page.enable');
  await cdp.call('Runtime.enable');
  await cdp.call('Storage.clearDataForOrigin', { origin, storageTypes: 'all' });
  await cdp.call('Page.navigate', { url: `${origin}/` });
  await pause(2500);
  await cdp.call('Runtime.evaluate', {
    expression: `(async()=>{await navigator.serviceWorker.register('/sw.js');await Promise.race([navigator.serviceWorker.ready,new Promise((_,reject)=>setTimeout(()=>reject(new Error('service worker ready timeout')),15000))]);return true})()`,
    awaitPromise: true,
  });
  await cdp.call('Page.reload', { ignoreCache: false });
  await pause(2500);
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const control = await cdp.call('Runtime.evaluate', { expression: '!!navigator.serviceWorker.controller', returnByValue: true });
    if (control.result.value) break;
    await cdp.call('Page.reload', { ignoreCache: false });
    await pause(2000);
  }
  const cached = await cdp.call('Runtime.evaluate', {
    expression: `(async()=>{const cache=await caches.open('cauldra-shell-v8-offline-first');const urls=['/','/js/app.js','/js/offline.js','/css/offline.css'];const found={};for(const url of urls)found[url]=!!(await cache.match(url));found.controlled=!!navigator.serviceWorker.controller;return found})()`,
    awaitPromise: true,
    returnByValue: true,
  });
  await new Promise(resolve => server.close(resolve));
  await cdp.call('Page.navigate', { url: `${origin}/` });
  for (let attempt = 0; attempt < 60; attempt += 1) {
    await pause(250);
    const opened = await cdp.call('Runtime.evaluate', { expression: `!!document.getElementById('offline-unlock-dialog')?.open`, returnByValue: true });
    if (opened.result.value) break;
  }
  const state = await cdp.call('Runtime.evaluate', {
    expression: `({title:document.title,offlineModule:!!window.CauldraOffline,unlockOpen:!!document.getElementById('offline-unlock-dialog')?.open,firstTime:/first time/i.test(document.getElementById('offline-unlock-dialog')?.textContent||''),bodyWidth:document.documentElement.scrollWidth,viewportWidth:innerWidth})`,
    returnByValue: true,
  });
  const result = { cached: cached.result.value, coldOffline: state.result.value };
  result.passed = Object.values(result.cached).every(Boolean) && result.coldOffline.offlineModule && result.coldOffline.unlockOpen && result.coldOffline.firstTime && result.coldOffline.bodyWidth <= result.coldOffline.viewportWidth + 1;
  console.log(JSON.stringify(result, null, 2));
  await cdp.call('Page.close');
  cdp.close();
  if (!result.passed) process.exitCode = 1;
})().catch(error => { server.close(); console.error(error); process.exitCode = 1; });
