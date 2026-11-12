import test from 'node:test';
import assert from 'node:assert/strict';
import { baselineSql, summarize } from './refactor-baseline.mjs';

test('baseline uses one consistent read-only transaction and never queries raw rows', () => {
  const sql = baselineSql();
  assert.match(sql, /^BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;/);
  assert.match(sql, /ROLLBACK;$/);
  assert.doesNotMatch(sql, /\b(?:FROM|JOIN)\s+raw_records\b/i);
  assert.doesNotMatch(sql, /\b(?:INSERT|UPDATE|DELETE|ALTER|CREATE|DROP|TRUNCATE)\b/i);
  assert.match(sql, /statement_timeout/);
});

test('legacy discrepancies remain visible without rewriting or accepting them as zero', () => {
  const snapshot = { read_only: 'on', columns: [{ table_name: 'users' }], constraints: [], indexes: [],
    migration: ['legacy'], invariants: { points: 0, debt: 2 }, task_counts: { stores: 50 } };
  const result = summarize(snapshot);
  assert.deepEqual(result.deviations, [{ name: 'debt', count: 2 }]);
  assert.equal(result.tables, 1);
  assert.equal(result.schemaSha256, summarize(structuredClone(snapshot)).schemaSha256);
  assert.notEqual(result.schemaSha256, summarize({ ...snapshot, indexes: [{ indexname: 'changed' }] }).schemaSha256);
  assert.throws(() => summarize({ ...snapshot, read_only: 'off' }), /Read-only/);
});
