import {spawnLegacyPython} from '../../../scripts/legacy-python.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtemp, writeFile, readFile, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { prepareNaturalGrids, naturalBoundaryKind, naturalGridCandidate } from '../src/natural-grids';
function oracle() { const result = spawnLegacyPython( ['legacy/scripts/natural-grid-oracle.py'], { encoding: 'utf8', maxBuffer: 16e6 }); assert.equal(result.status, 0, result.stderr); return JSON.parse(result.stdout); }
test('natural boundaries preserve grade/route evidence, reject invented dividers and keep water opening vs internal lake policy', async () => {
    const fixtures = oracle();
    for (const row of fixtures.classifications)
        assert.equal(naturalBoundaryKind(row.tags, row.length === null ? undefined : row.length), row.expected, JSON.stringify(row));
    const input = fixtures.cases.find((row: any) => row.name === 'grades').input, before = JSON.stringify(input), doc = await prepareNaturalGrids(input);
    assert.equal(doc.road_cells.length, 3);
    assert.equal(doc.natural_audit.geometric_pass, true);
    assert.deepEqual(doc.natural_audit.retained_boundaries.map(row => row.osm_id), [1, 3]);
    assert.equal(JSON.stringify(input), before);
    assert.equal((await prepareNaturalGrids({ ...input, elements: [...input.elements].reverse() })).snapshot_id, doc.snapshot_id);
    const reference = { ...doc, road_cells: doc.road_cells.map((c, i) => ({ ...c, id: 'tokyo23-N-existing-' + i })) };
    const copied = await prepareNaturalGrids({ ...input, referenceSnapshot: reference });
    assert.deepEqual(copied.road_cells.map(c => c.id), reference.road_cells.map(c => c.id));
    await assert.rejects(prepareNaturalGrids({ ...input, regionCode: 'unknown' }), /地区不存在/);
    const water = await prepareNaturalGrids(fixtures.cases.find((row: any) => row.name === 'water').input);
    assert.equal(water.road_cells.length, 4);
    assert.ok(Math.abs(water.area_km2 - 1.8) < 1e-6);
    const candidate = await naturalGridCandidate(water);
    assert.equal(candidate.applied, false);
    assert.equal(candidate.audit.merchant_pass, false);
    assert.ok(candidate.features.every(f => f.properties.zone_id === null));
    assert.ok(candidate.features.every(f => f.properties.cell_ids.length === 1));
    await assert.rejects(prepareNaturalGrids(fixtures.cases.find((row: any) => row.name === 'only-minor').input), /缺少有标签/);
    await assert.rejects(prepareNaturalGrids(fixtures.cases.find((row: any) => row.name === 'invalid-divider').input), /真实来源标签/);
});
test('natural CLI defaults to offline preview and refuses overwrites or apply flags', async () => {
    const dir = await mkdtemp(join(tmpdir(), 'natural-grid-cli-')), input = join(dir, 'input.json'), output = join(dir, 'candidate.json'), fixture = oracle().cases.find((row: any) => row.name === 'water').input, run = (extra: string[]) => spawnSync(process.execPath, ['--import', 'tsx', 'scripts/prepare-natural-grids.mts', '--input', input, ...extra], { encoding: 'utf8' });
    try {
        await writeFile(input, JSON.stringify(fixture));
        const before = await readFile(input, 'utf8'), dry = run([]);
        assert.equal(dry.status, 0, dry.stderr);
        assert.equal(JSON.parse(dry.stdout).databaseTouched, false);
        assert.equal(JSON.parse(dry.stdout).merchantBindingReady, false);
        await assert.rejects(readFile(output), { code: 'ENOENT' });
        const saved = run(['--candidate', '--write', '--output', output]);
        assert.equal(saved.status, 0, saved.stderr);
        const frozen = await readFile(output, 'utf8');
        assert.equal(JSON.parse(frozen).status, 'candidate');
        assert.equal(JSON.parse(frozen).applied, false);
        assert.notEqual(run(['--candidate', '--write', '--output', output]).status, 0);
        assert.equal(await readFile(output, 'utf8'), frozen);
        assert.notEqual(run(['--write', '--output', input]).status, 0);
        assert.equal(await readFile(input, 'utf8'), before);
        assert.notEqual(run(['--apply']).status, 0);
    }
    finally {
        await rm(dir, { recursive: true, force: true });
    }
});
