const fs = require('fs');
const path = require('path');

const port = Number(process.argv[2] || 9333);
const outputDir = process.argv[3] || process.cwd();
const testOrigin = process.argv[4] || 'http://127.0.0.1:8768';
const viewports = [
  ['mobile', 375, 812, true],
  ['ipad-portrait', 768, 1024, true],
  ['ipad-landscape', 1024, 768, true],
  ['desktop', 1366, 768, false],
];

const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

async function connect(url) {
  const socket = new WebSocket(url);
  await new Promise((resolve, reject) => {
    socket.onopen = resolve;
    socket.onerror = reject;
  });
  let nextId = 1;
  const pending = new Map();
  socket.onmessage = event => {
    const message = JSON.parse(event.data);
    if (!message.id || !pending.has(message.id)) return;
    const { resolve, reject } = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) reject(new Error(message.error.message));
    else resolve(message.result);
  };
  return {
    call(method, params = {}) {
      const id = nextId++;
      socket.send(JSON.stringify({ id, method, params }));
      return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
    },
    close() { socket.close(); },
  };
}

async function newPage() {
  const response = await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, { method: 'PUT' });
  if (!response.ok) throw new Error(`Unable to create browser page: ${response.status}`);
  return response.json();
}

(async () => {
  fs.mkdirSync(outputDir, { recursive: true });
  const summaries = [];
  for (const [name, width, height, mobile] of viewports) {
    const page = await newPage();
    const cdp = await connect(page.webSocketDebuggerUrl);
    await cdp.call('Page.enable');
    await cdp.call('Runtime.enable');
    await cdp.call('Storage.clearDataForOrigin', { origin: testOrigin, storageTypes: 'all' });
    await cdp.call('Emulation.setDeviceMetricsOverride', {
      width, height, deviceScaleFactor: 1, mobile, screenWidth: width, screenHeight: height,
    });
    await cdp.call('Page.navigate', { url: `${testOrigin}/offline-browser-harness.html` });
    let state;
    for (let attempt = 0; attempt < 160; attempt += 1) {
      await pause(250);
      const result = await cdp.call('Runtime.evaluate', {
        expression: `(() => ({ready: document.body?.dataset.browserChecks || '', results: [...document.querySelectorAll('#results li')].map(node => node.textContent)}))()`,
        returnByValue: true,
      });
      state = result.result.value;
      if (state.ready) break;
    }
    const measured = await cdp.call('Runtime.evaluate', {
      expression: `(() => { const dialog=document.getElementById('offline-unlock-dialog'); const rect=dialog?.getBoundingClientRect(); const controls=dialog?[...dialog.querySelectorAll('button:not([hidden]),input:not([hidden]),select:not([hidden])')].filter(node=>node.getClientRects().length):[]; return {ready:document.body.dataset.browserChecks,innerWidth,innerHeight,scrollWidth:document.documentElement.scrollWidth,scrollHeight:document.documentElement.scrollHeight,dialog:rect?{left:rect.left,top:rect.top,right:rect.right,bottom:rect.bottom,width:rect.width,height:rect.height,open:dialog.open}:null,minControlHeight:controls.length?Math.min(...controls.map(node=>node.getBoundingClientRect().height)):0,results:[...document.querySelectorAll('#results li')].map(node=>node.textContent)} })()`,
      returnByValue: true,
    });
    const value = measured.result.value;
    const storageCheck = await cdp.call('Runtime.evaluate', {
      expression: `(async()=>{const db=await CauldraOffline.openDb();const stores=[...db.objectStoreNames];const rows={};for(const name of stores){rows[name]=await new Promise((resolve,reject)=>{const tx=db.transaction(name,'readonly');const request=tx.objectStore(name).getAll();request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error)})}const serialized=JSON.stringify({indexedDb:rows,localStorage:{...localStorage},sessionStorage:{...sessionStorage}});return {pinPlaintext:serialized.includes('123456'),bearerToken:serialized.includes('Bearer test')||serialized.includes('\\\"token\\\":\\\"test\\\"'),productPlaintext:serialized.includes('Rice')}})()`,
      awaitPromise: true,
      returnByValue: true,
    });
    value.plaintextLeaks = storageCheck.result.value;
    const png = await cdp.call('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
    fs.writeFileSync(path.join(outputDir, `${name}-${width}x${height}-cdp.png`), Buffer.from(png.data, 'base64'));
    const passed = value.ready === 'complete' && value.results.every(item => item.startsWith('PASS')) && value.scrollWidth <= width + 1 && value.dialog && value.dialog.left >= -1 && value.dialog.right <= width + 1 && value.dialog.top >= -1 && value.dialog.bottom <= height + 1 && value.minControlHeight >= 43 && Object.values(value.plaintextLeaks).every(leaked => !leaked);
    summaries.push({ name, width, height, passed, ...value });
    await cdp.call('Page.close');
    cdp.close();
  }
  console.log(JSON.stringify(summaries, null, 2));
  if (summaries.some(item => !item.passed)) process.exitCode = 1;
})().catch(error => { console.error(error); process.exitCode = 1; });
