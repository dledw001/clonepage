import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import ts from 'typescript';
import { compile } from 'svelte/compiler';

const source = readFileSync(new URL('../src/lib/infrastructure.ts', import.meta.url), 'utf8');
const javascript = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2023, module: ts.ModuleKind.ESNext } }).outputText;
const { buildPlot, ageLabel, isStale, readingAge, formatValue, fetchJson } = await import(`data:text/javascript;base64,${Buffer.from(javascript).toString('base64')}`);
const series = points => [{ metric: 'cpu.usage', label: 'CPU', color: '#abc', unit: '%', points }];
const point = (ts, value, min = value, max = value) => ({ ts, value, min, max });

test('infrastructure, chart, and navigation compile for client and server', () => {
  for (const filename of ['Infrastructure.svelte', 'MetricChart.svelte', '../App.svelte']) {
    const path = filename.startsWith('../') ? '../src/App.svelte' : `../src/components/${filename}`;
    const component = readFileSync(new URL(path, import.meta.url), 'utf8');
    for (const generate of ['client', 'server']) assert.doesNotThrow(() => compile(component, { filename, generate }));
  }
});

test('chart preserves bucket maxima instead of using only averages', () => {
  const plot = buildPlot(series([point(10, 20, 5, 95), point(20, 30, 10, 90)]), 0, 30, '%');
  assert.equal(plot.maximum, 95);
  assert.equal(plot.minimum, 5);
  assert.ok(plot.paths[0].band.endsWith('Z'));
  assert.equal(plot.lower, 0);
  assert.equal(plot.upper, 100);
});

test('zero readings are valid and single readings are rendered as dots', () => {
  const plot = buildPlot(series([point(10, 0)]), 0, 20, '%');
  assert.equal(plot.hasData, true);
  assert.equal(plot.paths[0].dots.length, 1);
  assert.equal(formatValue(0, '%'), '0.0%');
});

test('missing values split the line instead of becoming zeroes', () => {
  const plot = buildPlot(series([point(10, 20), point(20, null), point(30, 25)]), 0, 40, '%');
  assert.equal(plot.paths[0].line.match(/M/g).length, 2);
  assert.equal(plot.paths[0].dots.length, 2);
});

test('long collection gaps split chart paths', () => {
  const plot = buildPlot(series([point(10, 20), point(20, 22), point(30, 25), point(900, 30)]), 0, 1000, '%');
  assert.equal(plot.paths[0].line.match(/M/g).length, 2);
});

test('range filtering does not show readings from another time window', () => {
  const plot = buildPlot(series([point(10, 20), point(100, 25)]), 200, 300, '%');
  assert.equal(plot.hasData, false);
  assert.equal(plot.paths[0].line, '');
});

test('unsorted history is plotted chronologically and temperatures use their own scale', () => {
  const plot = buildPlot(series([point(20, 31), point(10, 30)]), 0, 30, 'C');
  assert.equal(plot.paths[0].points[0].ts, 10);
  assert.ok(plot.lower < 30);
  assert.ok(plot.upper > 31);
  assert.ok(plot.upper < 100);
});

test('freshness uses collection timestamps and allows the slower drive polling interval', () => {
  const cpu = { metric: 'cpu.usage', ts: 1000, value: 0, unit: '%' };
  const drive = { ...cpu, metric: 'smart.sata1.temp', unit: 'C' };
  assert.equal(isStale(cpu, 1061), true);
  assert.equal(isStale(drive, 1061), false);
  assert.equal(isStale(drive, 1301), false);
  assert.equal(isStale(drive, 1661), true);
  assert.equal(isStale(undefined, 1000), true);
  assert.equal(readingAge(cpu, 990), 0);
  assert.equal(ageLabel(null), 'No reading yet');
  assert.equal(formatValue(undefined, 'C'), '—');
});

test('failed requests reject instead of being treated as metric payloads', async t => {
  t.mock.method(globalThis, 'fetch', async () => ({ ok: false, status: 502 }));
  await assert.rejects(fetchJson('/api/infrastructure/snapshot'), /Could not load/);
});

test('fetches use the same-origin proxy, disable browser caching, and honor cancellation', async t => {
  const controller = new AbortController();
  t.mock.method(globalThis, 'fetch', async (path, options) => {
    assert.equal(path, '/api/infrastructure/snapshot');
    assert.equal(options.cache, 'no-store');
    controller.abort();
    assert.equal(options.signal.aborted, true);
    return { ok: true, json: async () => ({ metrics: [] }) };
  });
  assert.deepEqual(await fetchJson('/api/infrastructure/snapshot', controller.signal), { metrics: [] });
});
