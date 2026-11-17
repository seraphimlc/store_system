import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFile, writeFile, mkdtemp, mkdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { staffPwaPaths, stageStaffPwa, verifyStaffPwa, staffPwaGatewayLocations } from './staff-pwa-release.mjs';
import { gatewayConfig } from './release-lib.mjs';
import { activateUpdate } from '../apps/staff/src/pwa/client.ts';
import { staffPwa } from '../apps/staff/pwa-build.ts';

async function worker(file = 'apps/staff/public/sw.js') {
  const listeners = new Map(), stores = new Map(), deleted = [], network = [];
  let fail = false, status = 200, skip = 0, unregistered = 0;
  const caches = {
    keys: async () => [...stores.keys()],
    delete: async key => { deleted.push(key); return stores.delete(key); },
    open: async key => {
      if (!stores.has(key)) stores.set(key, new Map());
      return { put: async (path, response) => stores.get(key).set(path, response), match: async path => stores.get(key).get(path)?.clone() };
    },
  };
  const self = { location: new URL('https://staff.test/sw.js'), addEventListener: (name, fn) => listeners.set(name, fn), skipWaiting: async () => { skip++; }, registration: { unregister: async () => { unregistered++; } } };
  const fetch = async (request, options) => {
    network.push([typeof request === 'string' ? request : request.url, options]);
    if (fail) throw Error('offline');
    return new Response('<main data-staff-offline>offline</main>', { status, headers: { 'Content-Type': 'text/html' } });
  };
  vm.runInNewContext(await readFile(file, 'utf8'), { self, caches, fetch, URL, Response });
  return {
    stores, deleted, network, get skip() { return skip; }, get unregistered() { return unregistered; },
    offline(value) { fail = value; }, status(value) { status = value; },
    async event(name, data = {}) { let result; listeners.get(name)?.({ ...data, waitUntil: p => { result = p; } }); await result; },
    fetch(path, { method = 'GET', mode = 'navigate' } = {}) {
      let result;
      listeners.get('fetch')({ request: { url: new URL(path, self.location.origin).href, method, mode }, respondWith: p => { result = p; } });
      return result;
    },
  };
}
test('Worker only stores the anonymous offline page and never auto-activates or claims clients', async () => {
  const w = await worker(); await w.event('install');
  assert.equal(w.skip, 0); assert.equal(w.stores.size, 1);
  assert.deepEqual([...w.stores.values()][0].keys().toArray(), ['/pwa/offline.html']);
  assert.equal(w.network[0][1].credentials, 'omit');
  w.stores.set('staff-pwa-offline-old', new Map()); w.stores.set('another-app-cache', new Map());
  await w.event('activate');
  assert.deepEqual(w.deleted, ['staff-pwa-offline-old']); assert.ok(w.stores.has('another-app-cache'));
  await w.event('message', { data: { type: 'STAFF_PWA_ACTIVATE' }, source: { type: 'window', url: 'https://other.test/' } });
  assert.equal(w.skip, 0);
  await w.event('message', { data: { type: 'STAFF_PWA_ACTIVATE' }, source: { type: 'window', url: 'https://staff.test/my/password' } });
  assert.equal(w.skip, 1);
});
test('Worker only falls back on employee navigation network failures, not HTTP errors', async () => {
  const w = await worker(); await w.event('install'); w.offline(true);
  for (const path of ['/', '/login', '/my/password', '/my/attendance', '/my/team-attendance', '/messages', '/my/report/feedback', '/my/work/batches/42/regions/3', '/my/work/batch-grids/8']) {
    assert.match(await (await w.fetch(path)).text(), /data-staff-offline/);
  }
  for (const path of ['/admin', '/admin/users', '/api/auth/session', '/api/reports', '/oauth/authorize', '/mcp', '/.well-known/openid-configuration', '/uploads/file', '/unknown', '/my/work/not-a-page', '/pwa/sw-disable.js', 'https://cdn.test/my/report']) assert.equal(w.fetch(path), undefined, path);
  assert.equal(w.fetch('/my/report', { method: 'POST' }), undefined);
  assert.equal(w.fetch('/my/report', { mode: 'cors' }), undefined);
  w.offline(false); w.status(503); assert.equal((await w.fetch('/my/report')).status, 503);
  w.status(401); assert.equal((await w.fetch('/my/confirm')).status, 401);
  assert.equal([...w.stores.values()][0].size, 1);
});
test('a broken offline endpoint rejects Worker installation', async () => {
  const w = await worker(); w.status(404); await assert.rejects(w.event('install'), /Offline page unavailable/); assert.equal(w.stores.size, 0);
});
test('disable Worker clears only its own caches, unregisters, never navigates', async () => {
  const w = await worker('apps/staff/public/pwa/sw-disable.js');
  w.stores.set('staff-pwa-offline-old', new Map()); w.stores.set('other', new Map());
  await w.event('install'); await w.event('activate');
  assert.equal(w.skip, 1); assert.equal(w.unregistered, 1); assert.deepEqual(w.deleted, ['staff-pwa-offline-old']);
});
test('update activation requires a waiting Worker and has no global reload side effects', async () => {
  class Worker extends EventTarget {
    state = 'installed'; messages = [];
    postMessage(message) { this.messages.push(message); queueMicrotask(() => { this.state = 'activated'; this.dispatchEvent(new Event('statechange')); }); }
  }
  const w = new Worker(); assert.equal(w.messages.length, 0);
  await activateUpdate(w); assert.deepEqual(w.messages, [{ type: 'STAFF_PWA_ACTIVATE' }]);
  await assert.rejects(activateUpdate(w), /no longer waiting/);
});
test('Manifest and HTML injection stay same-origin even with a CDN base', async () => {
  const manifest = JSON.parse(await readFile('apps/staff/public/manifest.webmanifest', 'utf8'));
  assert.equal(manifest.id, '/'); assert.equal(manifest.start_url, '/'); assert.equal(manifest.display, 'standalone');
  assert.ok(manifest.icons.some(icon => icon.sizes === '512x512' && icon.purpose.includes('maskable')));
  for (const icon of manifest.icons) assert.ok(icon.src.startsWith('/pwa/'));
  const tags = staffPwa().transformIndexHtml.handler();
  assert.equal(tags.find(tag => tag.attrs.rel === 'manifest').attrs.href, '/manifest.webmanifest');
  assert.equal(tags.find(tag => tag.attrs.rel === 'apple-touch-icon').attrs.href, '/pwa/apple-touch-icon.png');
  const locations = staffPwaGatewayLocations();
  for (const path of ['/sw.js', '/manifest.webmanifest', '/pwa/']) assert.ok(gatewayConfig('pwa-test').includes(path));
  assert.match(locations, /try_files \$uri =404/); assert.match(locations, /application\/manifest\+json/); assert.ok(!locations.includes('immutable'));
});
test('Gateway staging, staff reuse and checksum verification include the complete PWA set', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'staff-pwa-test-'));
  try {
    const source = join(dir, 'source'), target = join(dir, 'target');
    await mkdir(source); await mkdir(target);
    assert.deepEqual(await stageStaffPwa(source, target, { optional: true }), []);
    for (const path of staffPwaPaths) { await mkdir(dirname(join(source, path)), { recursive: true }); await writeFile(join(source, path), 'synthetic:' + path); }
    const files = await stageStaffPwa(source, target);
    await verifyStaffPwa(target, files);
    assert.equal(files.length, staffPwaPaths.length);
    await stageStaffPwa(target, join(dir, 'reused'), { expected: files });
    await assert.rejects(verifyStaffPwa(target, files.slice(1)), /Incomplete/);
    await writeFile(join(target, 'sw.js'), 'corrupt');
    await assert.rejects(verifyStaffPwa(target, files), /checksum/);
    await assert.rejects(stageStaffPwa(target, join(dir, 'bad'), { expected: files }), /checksum/);
  } finally { await rm(dir, { recursive: true, force: true }); }
});

test('waiting updates remain available in account settings without automatic toast interruptions', async () => {
  const source = await readFile('apps/staff/src/pwa/PwaProvider.tsx', 'utf8');
  const provider = source.slice(source.indexOf('export function PwaProvider'), source.indexOf('export function PwaSettings'));
  assert.doesNotMatch(provider, /Toast\.show/);
  assert.match(provider, /patch\(\{ waiting:/);
  assert.match(source, /activateUpdate\(state\.waiting\)/);
});
