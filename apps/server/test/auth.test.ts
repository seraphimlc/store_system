import 'reflect-metadata';
import test from 'node:test';
import assert from 'node:assert/strict';
import request from '../node_modules/supertest';
import { hash, verify } from '@node-rs/argon2';
import { createApp } from '../src/main';
import { Database } from '../src/database';
import { loadConfig } from '../src/config';

const databaseUrl = process.env.NEXT_TEST_DATABASE_URL;
test('verifies unchanged Python argon2 hashes', async () => {
  assert.ok(await verify('$argon2id$v=19$m=65536,t=3,p=4$dkkr5BdxK4t1vwS9vNP/xg$EVwlA1HAiFWBhqX2na1USbIwS7zOrzygniCAhkBnomw', 'python-fixture-password'));
});

test('configuration rejects weak secrets and insecure production cookies', () => {
  assert.throws(() => loadConfig({ NEXT_DATABASE_URL: 'postgresql://local/test', NEXT_SESSION_SECRET: 'short' }), /32/);
  assert.throws(() => loadConfig({ NEXT_DATABASE_URL: 'postgresql://local/test', NEXT_SESSION_SECRET: 'x'.repeat(40), NODE_ENV: 'production' }), /secure/);
});

test('local baseline HTTP exception requires an explicit flag and local database host',()=>{
 const env={NEXT_DATABASE_URL:'postgresql://tester:fixture@host.docker.internal:5433/store_settle',NEXT_SESSION_SECRET:'x'.repeat(40),NODE_ENV:'production',NEXT_LOCAL_HTTP:'true'};
 assert.throws(()=>loadConfig(env),/secure/);
 assert.equal(loadConfig({...env,NEXT_LOCAL_BASELINE:'true'}).cookieSecure,false);
 assert.throws(()=>loadConfig({...env,NEXT_LOCAL_BASELINE:'true',NEXT_DATABASE_URL:'postgresql://tester:fixture@production.example/store_settle'}),/secure/);
});

test('original PostgreSQL schema: auth, must-change, CSRF, role boundary and session invalidation', { skip: !databaseUrl }, async () => {
  assert.match(new URL(databaseUrl!).pathname, /_refactor_p1$/, 'Integration tests require explicitly isolated database');
  const app = await createApp({ databaseUrl: databaseUrl!, sessionSecret: 'test-only-isolated-secret-with-40-characters', cookieSecure: false, port: 0, host: '127.0.0.1' });
  const db = app.get(Database);
  const password = 'fixture-password-2026';
  const passwordHash = await hash(password);
  const client = request.agent(app.getHttpServer());
  try {
    // This database contains schema and synthetic fixtures only; no baseline employee credentials are changed.
    await db.query(`DELETE FROM users WHERE username LIKE 'refactor-p1-%'`);
    await db.query(`INSERT INTO users(username,password_hash,display_name,role,is_active,status,must_change_password,person_code,lang,created_at) VALUES
      ('refactor-p1-staff',$1,'验收员工','staff',true,'active',true,'P1-STAFF','ja',now()),
      ('refactor-p1-admin',$1,'验收管理员','admin',true,'active',false,NULL,'zh',now()),
      ('refactor-p1-disabled',$1,'停用账号','staff',false,'disabled',false,'P1-OFF','zh',now())`, [passwordHash]);
    assert.equal((await client.get('/api/auth/session')).status, 401);
    assert.equal((await client.post('/api/auth/login').send({ username: 'refactor-p1-disabled', password })).status, 401);
    assert.equal((await client.post('/api/auth/login').send({ username: 'refactor-p1-staff', password: 'wrong' })).status, 401);
    const login = await client.post('/api/auth/login').send({ username: 'refactor-p1-staff', password });
    assert.equal(login.status, 201); assert.equal(login.body.user.language, 'ja');
    assert.equal(login.body.user.mustChangePassword, true); assert.ok(!('password_hash' in login.body.user));
    assert.match(String(login.headers['set-cookie']), /HttpOnly/);
    const originalCookie = login.headers['set-cookie'];
    assert.equal((await client.get('/api/work/batches')).body.code, 'PASSWORD_CHANGE_REQUIRED');
    assert.equal((await client.post('/api/auth/password').send({ currentPassword: password, newPassword: 'next06' })).status, 403);
    const changed = await client.post('/api/auth/password').set('X-CSRF-Token', login.body.csrfToken).send({ currentPassword: password, newPassword: 'next06' });
    assert.equal(changed.status, 201); assert.equal(changed.body.user.mustChangePassword, false);
    const stored = (await db.query<{ password_hash: string }>(`SELECT password_hash FROM users WHERE username='refactor-p1-staff'`)).rows[0];
    assert.ok(await verify(stored.password_hash, 'next06'));
    assert.equal((await request(app.getHttpServer()).get('/api/auth/session').set('Cookie', originalCookie)).status, 401);
    assert.equal((await client.get('/api/dashboard')).status, 403);
    assert.equal((await client.get('/api/work/batches')).status, 200);
    assert.equal((await client.post('/api/auth/logout').send({})).status, 403);
    assert.equal((await client.post('/api/auth/logout').set('X-CSRF-Token', changed.body.csrfToken).send({})).status, 201);
    assert.equal((await client.get('/api/auth/session')).status, 401);
    const admin = await client.post('/api/auth/login').send({ username: 'refactor-p1-admin', password });
    assert.equal(admin.status, 201);
    assert.equal((await client.get('/api/dashboard')).status, 200);
    assert.equal((await client.get('/api/work/batches')).status, 200);
    const unknown = await client.get('/api/not-real'); assert.equal(unknown.status, 404); assert.equal(unknown.body.code, 'NOT_FOUND');
    assert.ok(unknown.headers['x-request-id']);
    await db.query(`UPDATE users SET status='disabled' WHERE username='refactor-p1-admin'`);
    assert.equal((await client.get('/api/dashboard')).status, 401);
  } finally {
    await db.query(`DELETE FROM users WHERE username LIKE 'refactor-p1-%'`);
    await app.close();
  }
});
