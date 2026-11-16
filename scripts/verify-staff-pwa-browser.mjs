/** Built Worker lifecycle needs a production build, not a running dev service.
 * Disposable loopback static server; synthetic session only, no database or external APIs. */
import { chromium } from '@playwright/test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { resolve, extname } from 'node:path';
const dist = resolve('apps/staff/dist');
const html = await readFile(resolve(dist, 'index.html'), 'utf8');
const worker = await readFile(resolve(dist, 'sw.js'), 'utf8');
assert.ok(!worker.includes('__STAFF_PWA_VERSION__'), 'Build staff before browser acceptance');
assert.match(html, /href="\/manifest.webmanifest"/);
assert.match(html, /href="\/pwa\/apple-touch-icon.png"/);
const checks = [], errors = [];
let diagnosticPage;
let revision = 1, disable = false;
const mime = path => ({ '.js': 'application/javascript', '.css': 'text/css', '.png': 'image/png', '.woff2': 'font/woff2', '.html': 'text/html', '.webmanifest': 'application/manifest+json' })[extname(path)] || 'application/octet-stream';
const server = createServer(async (req, res) => {
  const path = new URL(req.url, 'http://localhost').pathname;
  res.setHeader('Cache-Control', 'no-cache');
  try {
    if (path.startsWith('/api/')) {
      res.setHeader('Content-Type', 'application/json');
      const body = path === '/api/auth/session' ? { user: { id: 1, username: 'pwa-test', displayName: 'PWA 验收', role: 'staff', personCode: null, mustChangePassword: false, languagePreference: 'zh' }, csrfToken: 'synthetic-only' } : { unread: 0 };
      res.end(JSON.stringify(body)); return;
    }
    if (path === '/sw.js') {
      res.setHeader('Content-Type', 'application/javascript');
      res.end(disable ? await readFile(resolve(dist, 'pwa/sw-disable.js')) : revision === 1 ? worker : worker.replace(/const VERSION = '[^']+';/, `const VERSION = 'browser-test-${revision}';`)); return;
    }
    if (path === '/admin/' || path === '/oauth/authorize' || path === '/my/report') { res.statusCode = path === '/my/report' ? 503 : 401; res.end('network-only:' + path); return; }
    const file = path === '/manifest.webmanifest' || path.startsWith('/pwa/') || path.startsWith('/assets/');
    res.setHeader('Content-Type', file ? mime(path) : 'text/html');
    res.end(file ? await readFile(resolve(dist, '.' + path)) : html);
  } catch { res.statusCode = 404; res.end('not found'); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const base = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({ headless: true, channel: 'chrome' });
try {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, reducedMotion: 'reduce' });
  // Real browser installation UI is manual acceptance; use a deterministic event below.
  await context.addInitScript(() => window.addEventListener('beforeinstallprompt', event => { if (event.isTrusted) { event.preventDefault(); event.stopImmediatePropagation(); } }, true));
  // A CDN build is intentional: these mocked public bytes must never enter PWA caches.
  await context.route('https://pwa-assets.invalid/staff/**', async route => {
    const path = new URL(route.request().url()).pathname.slice('/staff/'.length);
    await route.fulfill({ body: await readFile(resolve(dist, path)), contentType: mime(path), headers: { 'access-control-allow-origin': '*' } });
  });
  const page = await context.newPage(); diagnosticPage = page; page.on('pageerror', e => errors.push(e.message));
  await page.goto(base + '/my/password');
  await page.getByRole('button', { name: '退出登录', exact: true }).waitFor();
  await page.evaluate(async () => { const reg = await navigator.serviceWorker.ready; if (reg.active?.state !== 'activated') await new Promise(resolve => reg.active.addEventListener('statechange', resolve, { once: true })); });
  await page.reload();
  await page.waitForFunction(() => Boolean(navigator.serviceWorker.controller));
  assert.equal((await page.request.get(base + '/manifest.webmanifest')).headers()['content-type'], 'application/manifest+json');
  const info = await page.evaluate(async () => ({ scope: (await navigator.serviceWorker.getRegistration()).scope, keys: await caches.keys(), urls: await Promise.all((await caches.keys()).map(async key => (await (await caches.open(key)).keys()).map(req => req.url))) }));
  assert.equal(info.scope, base + '/'); assert.equal(info.keys.length, 1); assert.deepEqual(info.urls.flat(), [base + '/pwa/offline.html']);
  checks.push('same-origin registration and offline-only cache with CDN build');
  await page.evaluate(() => {
    const event = new Event('beforeinstallprompt', { cancelable: true });
    event.prompt = async () => { window.__installPrompted = true; };
    event.userChoice = new Promise(resolve => { window.__finishInstall = resolve; });
    window.dispatchEvent(event);
  });
  await page.getByText('添加到桌面', { exact: true }).click();
  await page.waitForFunction(() => window.__installPrompted);
  await page.evaluate(() => window.__finishInstall({ outcome: 'dismissed' }));
  await page.getByText('添加到桌面', { exact: true }).click();
  await page.locator('.adm-dialog').getByText('知道了', { exact: true }).click();
  await page.getByRole('button', { name: '菜单', exact: true }).click();
  await page.locator('#staff-menu').getByRole('button', { name: '中文 / 日本語' }).click();
  await page.locator('#staff-menu').getByRole('button', { name: '閉じる', exact: true }).click();
  await page.getByText('ホーム画面に追加', { exact: true }).click();
  await page.locator('.adm-dialog').getByText('閉じる', { exact: true }).click();
  await page.getByRole('button', { name: 'メニュー', exact: true }).click();
  await page.locator('#staff-menu').getByRole('button', { name: '中文 / 日本語' }).click();
  await page.locator('#staff-menu').getByRole('button', { name: '关闭', exact: true }).click();
  checks.push('native install event, dismissed prompt fallback, Chinese/Japanese guidance');
  await context.setOffline(true);
  await page.goto(base + '/my/work/batches/17');
  assert.equal(await page.locator('[data-staff-offline]').count(), 1);
  assert.match(await page.locator('body').innerText(), /不会自动提交/);
  assert.equal(await page.evaluate(async () => { try { await fetch('/api/auth/session'); return 'unexpected'; } catch { return 'network-failed'; } }), 'network-failed');
  await assert.rejects(page.goto(base + '/admin/'));
  await context.setOffline(false);
  await page.goto(base + '/my/report'); assert.match(await page.locator('body').innerText(), /network-only/);
  await page.goto(base + '/my/password');
  checks.push('offline deep link, API bypass, admin exclusion and HTTP error passthrough');
  const other = await context.newPage(); await other.goto(base + '/my/password');
  await other.getByRole('button', { name: '退出登录', exact: true }).waitFor();
  await other.evaluate(() => { window.__pwaSentinel = 'unsaved-other-window'; });
  revision = 2;
  await page.evaluate(async () => { await (await navigator.serviceWorker.getRegistration()).update(); });
  await page.getByText('更新应用', { exact: true }).waitFor();
  await page.getByText('更新应用', { exact: true }).click();
  await page.getByText('稍后', { exact: true }).click();
  assert.equal(await page.evaluate(async () => (await caches.keys()).some(key => key.endsWith('browser-test-2'))), true); // Waiting cache exists, active page was not refreshed.
  assert.equal(await other.evaluate(() => window.__pwaSentinel), 'unsaved-other-window');
  await page.getByText('更新应用', { exact: true }).click();
  await Promise.all([page.waitForEvent('load'), page.getByText('已保存，更新', { exact: true }).click()]);
  assert.equal(await other.evaluate(() => window.__pwaSentinel), 'unsaved-other-window');
  assert.deepEqual(await page.evaluate(() => caches.keys()), ['staff-pwa-offline-browser-test-2']);
  checks.push('cancel update, explicitly accepted update and no other-window reload');
  disable = true;
  await page.evaluate(async () => { await (await navigator.serviceWorker.getRegistration()).update(); });
  await page.waitForFunction(async () => !(await navigator.serviceWorker.getRegistration()) && !(await caches.keys()).some(key => key.startsWith('staff-pwa-offline-')));
  assert.equal(await other.evaluate(() => window.__pwaSentinel), 'unsaved-other-window');
  checks.push('disable artifact unregisters and clears only employee caches without reloading');
  const standalone = await browser.newContext();
  await standalone.addInitScript(() => { Object.defineProperty(navigator, 'standalone', { value: true }); });
  await standalone.route('https://pwa-assets.invalid/staff/**', async route => { const path = new URL(route.request().url()).pathname.slice('/staff/'.length); await route.fulfill({ body: await readFile(resolve(dist, path)), contentType: mime(path), headers: { 'access-control-allow-origin': '*' } }); });
  const installed = await standalone.newPage(); await installed.goto(base + '/my/password');
  await installed.getByRole('button', { name: '退出登录', exact: true }).waitFor();
  assert.equal(await installed.getByText('添加到桌面', { exact: true }).count(), 0);
  checks.push('installed-mode install entry hidden');
  await standalone.close(); await context.close(); assert.deepEqual(errors, []);
  console.log(JSON.stringify({ ok: true, checks, realDeviceInstallation: 'manual verification required' }, null, 2));
} catch (error) { console.error(JSON.stringify({ failed: error.message, page: diagnosticPage?.url(), text: await diagnosticPage?.locator('body').innerText().catch(() => '').then(text => text.slice(-1400)) })); throw error; } finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
