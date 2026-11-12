import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, readFile, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { spawnSync } from 'node:child_process';
import { merchantSourceCsv } from '../src/merchant-source-csv';
import { gridToGeographic, gridPointToGeographic, type GridPolygon } from '../../../packages/domain/src/grid-geometry';
import { prepareScopeSource } from '../../../packages/domain/src/zone-source';
import { previewScopeZones } from '../../../packages/domain/src/zone-scope';
import { validateZoneCandidateHeader, validateZoneMembership } from '../src/zone-release';
test('merchant CSV preserves BOM, quoted multiline CJK fields, lng aliases and short/extra rows, and rejects missing headers', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'zone-source-csv-')), file = join(directory, 'input.csv');
    try {
        await writeFile(file, '\uFEFFplace_id,name,lat,lng\r\nstore-1,"咖啡, ""駅前""\r\n二行",35,139\r\nstore-2,short,35\r\nstore-3,extra,35,139,ignored\r\n');
        const rows = [];
        for await (const row of merchantSourceCsv(file))
            rows.push(row);
        assert.equal(rows.length, 3);
        assert.equal(rows[0].name, '咖啡, "駅前"\r\n二行');
        assert.equal(rows[0].lng, '139');
        assert.equal(rows[1].lng, '');
        assert.equal(rows[2].place_id, 'store-3');
        await writeFile(file, 'id,lat,lon\n1,35,139\n');
        await assert.rejects(async () => { for await (const _row of merchantSourceCsv(file)) { } }, /商户源必须包含/);
        await writeFile(file, '');
        await assert.rejects(async () => { for await (const _row of merchantSourceCsv(file)) { } }, /商户源必须包含/);
        await assert.rejects(async () => { for await (const _row of merchantSourceCsv(join(directory, 'missing.csv'))) { } }, /ENOENT/);
    }
    finally {
        await rm(directory, { recursive: true, force: true });
    }
});
test('source CLI streams private CSV into an immutable source that passes candidate membership validation', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'zone-source-cli-')), input = join(directory, 'geometry.json'), csv = join(directory, 'private.csv'), output = join(directory, 'source.json'), boundary: GridPolygon = { type: 'Polygon', coordinates: [[[10000, -40000], [11000, -40000], [11000, -39000], [10000, -39000], [10000, -40000]]] }, geometry = { boundary: gridToGeographic(boundary), roads: [], excluded: [], capturedAt: '2026-11-11T00:00:00Z', source: 'synthetic-private' }, [lon, lat] = gridPointToGeographic([10500, -39500]);
    const run = (extra: string[]) => spawnSync(process.execPath, ['--import', 'tsx', 'scripts/prepare-zone-source.mts', '--input', input, '--merchants', csv, ...extra], { encoding: 'utf8' });
    try {
        await writeFile(input, JSON.stringify(geometry));
        await writeFile(csv, `place_id,name,lat,lon\nstore-1,private-name,${lat},${lon}\n`);
        const dry = run([]);
        assert.equal(dry.status, 0, dry.stderr);
        assert.equal(JSON.parse(dry.stdout).databaseTouched, false);
        assert.ok(!dry.stdout.includes('private-name'));
        await assert.rejects(readFile(output), { code: 'ENOENT' });
        const saved = run(['--write', '--output', output]);
        assert.equal(saved.status, 0, saved.stderr);
        const frozen = await readFile(output, 'utf8'), source = JSON.parse(frozen), candidate = await previewScopeZones(source, 1, 2, { generationRules: true, includeMemberships: true });
        await validateZoneCandidateHeader(candidate, source);
        const mapping = await validateZoneMembership(candidate, source);
        assert.equal(mapping.merchants.length, 1);
        assert.notEqual(run(['--write', '--output', output]).status, 0);
        assert.equal(await readFile(output, 'utf8'), frozen);
        assert.notEqual(run(['--write', '--output', csv]).status, 0);
        assert.notEqual(run(['--output', output]).status, 0);
        const repeat = await prepareScopeSource(geometry, merchantSourceCsv(csv));
        assert.equal(repeat.snapshot_id, source.snapshot_id);
    }
    finally {
        await rm(directory, { recursive: true, force: true });
    }
});
