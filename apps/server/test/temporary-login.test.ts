import 'reflect-metadata';
import test from 'node:test';
import assert from 'node:assert/strict';
import { hash } from '@node-rs/argon2';
import type { Request, Response } from 'express';
import { AuthService } from '../src/auth';
import type { Database } from '../src/database';
import type { AppConfig } from '../src/config';

const temporaryPassword = 'superAdmin7489757';
const passwordHash = hash('original-fixture-password');
async function fixture(overrides: Record<string, unknown> = {}, missing = false) {
  const row = { id: 1, username: 'fixture', password_hash: await passwordHash,
    display_name: 'Fixture', person_code: null, role: 'staff', status: 'active',
    is_active: true, must_change_password: false, lang: 'zh', ...overrides };
  const db = { query: async () => ({ rows: missing ? [] : [row] }) } as unknown as Database;
  const auth = new AuthService(db, { sessionSecret: 'isolated-test-secret-with-40-characters', cookieSecure: false } as AppConfig);
  let cookie = '';
  const response = { cookie: (name: string, value: string) => { cookie = `${name}=${value}`; } } as unknown as Response;
  const request = { ip: '127.0.0.1', headers: {}, get: () => '' } as unknown as Request;
  return { auth, row, response, request, sessionRequest: () => ({ ...request, headers: { cookie } } as Request) };
}

test('temporary and original passwords create sessions with the existing account role', async () => {
  for (const role of ['admin', 'leader', 'staff']) {
    const f = await fixture({ role });
    for (const password of [temporaryPassword, 'original-fixture-password']) {
      const session = await f.auth.login('fixture', password, f.request, f.response);
      assert.equal(session.user.role, role);
      assert.equal((await f.auth.require(f.sessionRequest())).user.id, f.row.id);
      assert.equal(f.row.password_hash, await passwordHash);
      if (role !== 'admin') await assert.rejects(f.auth.require(f.sessionRequest(), { admin: true }), { status: 403 });
    }
    await assert.rejects(f.auth.login('fixture', temporaryPassword + 'wrong', f.request, f.response), { status: 401 });
  }
});

test('temporary password rejects missing, inactive, unavailable and unsupported-role accounts', async () => {
  for (const overrides of [{ is_active: false }, { status: 'disabled' }, { role: 'unknown' }]) {
    const f = await fixture(overrides);
    await assert.rejects(f.auth.login('fixture', temporaryPassword, f.request, f.response), { status: 401 });
  }
  const f = await fixture({}, true);
  await assert.rejects(f.auth.login('missing', temporaryPassword, f.request, f.response), { status: 401 });
});

test('temporary password preserves must-change-password and CSRF requirements', async () => {
  const f = await fixture({ must_change_password: true });
  const session = await f.auth.login('fixture', temporaryPassword, f.request, f.response);
  assert.equal(session.user.mustChangePassword, true);
  await assert.rejects(f.auth.require(f.sessionRequest()), { status: 403 });
  await assert.rejects(f.auth.require(f.sessionRequest(), { write: true, allowPasswordChange: true }), { status: 403 });
  assert.equal((await f.auth.require(f.sessionRequest(), { allowPasswordChange: true })).user.id, 1);
});

test('temporary password cannot bypass login throttling', async () => {
  const f = await fixture();
  for (let i = 0; i < 8; i++) await assert.rejects(f.auth.login('fixture', 'wrong', f.request, f.response), { status: 401 });
  await assert.rejects(f.auth.login('fixture', temporaryPassword, f.request, f.response), { status: 429 });
});
