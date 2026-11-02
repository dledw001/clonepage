import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import ts from 'typescript';
import { compile } from 'svelte/compiler';

const source = readFileSync(new URL('../src/lib/infrastructure.ts', import.meta.url), 'utf8');
const javascript = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2023, module: ts.ModuleKind.ESNext } }).outputText;
const { buildPlot, ageLabel, isStale, readingAge, formatValue, fetchJson, ranges, averageDriveTemperature, collectionDiagnostics, sourceTime, convertTemperature, convertTemperatureSeries, nearestPlotPoint } = await import(`data:text/javascript;base64,${Buffer.from(javascript).toString('base64')}`);
const series = points => [{ metric: 'cpu.usage', label: 'CPU', color: '#abc', unit: '%', points }];
const point = (ts, value, min = value, max = value) => ({ ts, value, min, max });

const driveMetric = key => ({ key, label: key, unit: 'C' });
const driveReading = (metric, value, ts = 1900) => ({ metric, value, ts, unit: 'C', age_seconds: Math.max(0, 2000 - ts) });
const readingMap = (...readings) => new Map(readings.map(reading => [reading.metric, reading]));

test('history selector includes 30 and 90 days with the correct API durations', () => {
  assert.deepEqual(ranges.map(range => range.short), ['1h', '6h', '24h', '7d', '30d', '90d']);
  assert.equal(ranges.find(range => range.short === '30d').seconds, 30 * 86400);
  assert.equal(ranges.find(range => range.short === '90d').seconds, 90 * 86400);
});

test('HDD average includes all current HDD readings and excludes NVMe and CPU sensors', () => {
  const keys = ['smart.sata1.temp', 'smart.sata2.temp', 'smart.sata3.temp', 'smart.sata4.temp'];
  const metrics = [...keys.map(driveMetric), driveMetric('smart.nvme0.temp'), driveMetric('hwmon.k10temp.temp1')];
  const readings = readingMap(...keys.map((key, i) => driveReading(key, 30 + i * 2)), driveReading('smart.nvme0.temp', 50), driveReading('hwmon.k10temp.temp1', 70));
  assert.deepEqual(averageDriveTemperature(metrics, readings, 'hdd', 0), { value: 33, count: 4, total: 4, oldestAge: 100 });
});

test('a newly added second NVMe automatically contributes to its group average', () => {
  const metrics = [driveMetric('smart.nvme0.temp')];
  const readings = readingMap(driveReading('smart.nvme0.temp', 30));
  assert.equal(averageDriveTemperature(metrics, readings, 'nvme', 0).value, 30);
  metrics.push(driveMetric('smart.nvme1.temp'), driveMetric('smart.sata1.temp'));
  readings.set('smart.nvme1.temp', driveReading('smart.nvme1.temp', 36, 1800));
  readings.set('smart.sata1.temp', driveReading('smart.sata1.temp', 50));
  assert.deepEqual(averageDriveTemperature(metrics, readings, 'nvme', 0), { value: 33, count: 2, total: 2, oldestAge: 200 });
});

test('missing and stale drives are excluded while reporting partial coverage', () => {
  const metrics = ['smart.sata1.temp', 'smart.sata2.temp', 'smart.sata3.temp'].map(driveMetric);
  const readings = readingMap(driveReading('smart.sata1.temp', 32), driveReading('smart.sata2.temp', 90, 1000));
  assert.deepEqual(averageDriveTemperature(metrics, readings, 'hdd', 0), { value: 32, count: 1, total: 3, oldestAge: 100 });
});

test('zero temperatures are valid and non-finite values cannot contaminate an average', () => {
  const metrics = ['smart.nvme0.temp', 'smart.nvme1.temp', 'smart.nvme2.temp'].map(driveMetric);
  const readings = readingMap(driveReading('smart.nvme0.temp', 0), driveReading('smart.nvme1.temp', NaN), driveReading('smart.nvme2.temp', Infinity));
  assert.deepEqual(averageDriveTemperature(metrics, readings, 'nvme', 0), { value: 0, count: 1, total: 3, oldestAge: 100 });
});

test('groups with no current readings return unavailable instead of zero', () => {
  assert.deepEqual(averageDriveTemperature([], new Map(), 'nvme', 0), { value: null, count: 0, total: 0, oldestAge: null });
  assert.deepEqual(averageDriveTemperature([driveMetric('smart.nvme0.temp')], readingMap(driveReading('smart.nvme0.temp', 30, 1000)), 'nvme', 0), { value: null, count: 0, total: 1, oldestAge: null });
});

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

test('the leading partial bucket is preserved only for the matching requested lookback', () => {
  const data = [{ ...series([point(0, 10, 5, 99), point(10, 20, 15, 95), point(20, 25, 20, 30)])[0], since_seconds: 10 }];
  const plot = buildPlot(data, 15, 25, '%');
  assert.equal(plot.paths[0].points[0].ts, 10);
  assert.equal(plot.paths[0].points.length, 2);
  assert.ok(plot.paths[0].line.startsWith('M0.00,'));
  assert.equal(plot.maximum, 95);
  const wrongRange = buildPlot([{ ...data[0], since_seconds: 100 }], 15, 25, '%');
  assert.equal(wrongRange.paths[0].points[0].ts, 20);
});

test('a window containing only a partial bucket still displays its reading', () => {
  const data = [{ ...series([point(10, 20, 5, 95)])[0], since_seconds: 10 }];
  const plot = buildPlot(data, 15, 25, '%');
  assert.equal(plot.hasData, true);
  assert.equal(plot.paths[0].dots[0].x, 0);
  assert.equal(plot.maximum, 95);
});

test('the tooltip selects the partial bucket at its clipped plot position', () => {
  const points = [point(0, 20), point(100, 30)];
  assert.equal(nearestPlotPoint(points, 70, 70, 170).ts, 0);
});

test('recent sensor data is not shown when inspecting a time before that sensor has history', () => {
  assert.equal(nearestPlotPoint([point(9000, 30), point(9100, 31)], 100, 0, 10000), undefined);
});

test('tooltips do not bridge long collection gaps', () => {
  const points = [point(0, 20), point(10, 22), point(9900, 25), point(9910, 30)];
  assert.equal(nearestPlotPoint(points, 5000, 0, 10000), undefined);
});

test('temperature conversion preserves missing readings and known reference values', () => {
  assert.equal(convertTemperature(0, 'F'), 32);
  assert.equal(convertTemperature(100, 'F'), 212);
  assert.equal(convertTemperature(-40, 'F'), -40);
  assert.equal(convertTemperature(30, 'C'), 30);
  assert.equal(convertTemperature(null, 'F'), null);
  assert.equal(convertTemperature(NaN, 'F'), null);
  assert.equal(formatValue(32, 'F'), '32.0°');
});

test('history conversion includes averages and min/max without mutating cached Celsius data', () => {
  const original = { metric: 'cpu.temp', unit: 'C', since_seconds: 3600,
    points: [point(10, 10, 0, 20), point(20, null, null, null)] };
  const fahrenheit = convertTemperatureSeries(original, 'F');
  assert.equal(fahrenheit.unit, 'F');
  assert.deepEqual(fahrenheit.points[0], point(10, 50, 32, 68));
  assert.deepEqual(fahrenheit.points[1], point(20, null, null, null));
  assert.equal(fahrenheit.since_seconds, 3600);
  assert.deepEqual(convertTemperatureSeries(original, 'C'), original);
  assert.equal(original.points[0].value, 10);
  assert.equal(original.unit, 'C');
});

test('averages keep aging and drop a drive once its current reading becomes stale', () => {
  const metrics = ['smart.sata1.temp', 'smart.sata2.temp'].map(driveMetric);
  const readings = readingMap({ ...driveReading(metrics[0].key, 30), age_seconds: 650 }, driveReading(metrics[1].key, 40));
  assert.equal(averageDriveTemperature(metrics, readings, 'hdd', 0).value, 35);
  assert.deepEqual(averageDriveTemperature(metrics, readings, 'hdd', 11), { value: 40, count: 1, total: 2, oldestAge: 111 });
});

const diagnosticSnapshot = (changes = {}) => ({
  metrics: [driveMetric('smart.sata1.temp')],
  readings: [driveReading('smart.sata1.temp', 30)],
  health: { ok: true, smartctl_available: true, smart_devices: [], last_collection_ts: 1000,
    last_smart_poll_ts: 9999999999, collection_age_seconds: 10 },
  errors: {}, ...changes,
});

test('diagnostics distinguish collector status and missing SMART collection support', () => {
  const snapshot = diagnosticSnapshot();
  assert.deepEqual(collectionDiagnostics(snapshot), []);
  snapshot.health.ok = false;
  snapshot.health.smartctl_available = false;
  const messages = collectionDiagnostics(snapshot);
  assert.ok(messages.some(text => text.includes('collector is stopped')));
  assert.ok(messages.some(text => text.includes('smartctl is not installed')));
  assert.ok(!messages.some(text => text.includes('No current temperature')));
});

test('a recent SMART poll attempt does not hide stale or missing drive readings', () => {
  const snapshot = diagnosticSnapshot({ readings: [] });
  assert.ok(collectionDiagnostics(snapshot).some(text => text.includes('No current temperature readings for: smart.sata1.temp')));
  const missing = diagnosticSnapshot();
  missing.health.smart_devices = [{ label: 'HDD 1', device: '/host/dev/sata1', present: false }];
  assert.ok(collectionDiagnostics(missing).some(text => text.includes('Drive devices unavailable: HDD 1')));
  assert.ok(!collectionDiagnostics(missing).some(text => text.includes('unhealthy')));
});

test('collector collection age advances without using the browser wall clock', () => {
  const snapshot = diagnosticSnapshot();
  assert.ok(collectionDiagnostics(snapshot, 51).some(text => text.includes('last successful collection is delayed')));
  assert.equal(sourceTime(snapshot, 20), 2020);
  assert.equal(sourceTime(null), null);
});

test('freshness uses reported age plus elapsed time, independent of device clocks', () => {
  const cpu = { metric: 'cpu.usage', ts: 9999999999, value: 0, unit: '%', age_seconds: 10 };
  const drive = { ...cpu, metric: 'smart.sata1.temp', unit: 'C' };
  assert.equal(readingAge(cpu, 5), 15);
  assert.equal(isStale(cpu, 51), true);
  assert.equal(isStale(drive, 51), false);
  assert.equal(isStale(drive, 291), false);
  assert.equal(isStale(drive, 651), true);
  assert.equal(isStale(undefined, 0), true);
  assert.equal(readingAge(cpu, -10), 10);
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
