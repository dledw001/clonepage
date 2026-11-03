import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import ts from 'typescript';
import { compile } from 'svelte/compiler';

const source = readFileSync(new URL('../src/lib/infrastructure.ts', import.meta.url), 'utf8');
const javascript = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2023, module: ts.ModuleKind.ESNext } }).outputText;
const { buildPlot, ageLabel, isStale, readingAge, formatValue, fetchJson, ranges, averageDriveTemperature, collectionDiagnostics, sourceTime, convertTemperature, convertTemperatureSeries, nearestPlotPoint,
  groupDrives, assessDrive, summarizeDrives, metricReading, driveReading: readSmartReading, driveHistoryMetrics, defaultDriveHistory, updateCounterBaseline, counterIncrease, decodeCriticalWarning, formatAxisValue,
} = await import(`data:text/javascript;base64,${Buffer.from(javascript).toString('base64')}`);
const series = points => [{ metric: 'cpu.usage', label: 'CPU', color: '#abc', unit: '%', points }];
const point = (ts, value, min = value, max = value) => ({ ts, value, min, max });

const driveMetric = key => ({ key, label: key, unit: 'C' });
const driveReading = (metric, value, ts = 1900) => ({ metric, value, ts, unit: 'C', age_seconds: Math.max(0, 2000 - ts) });
const readingMap = (...readings) => new Map(readings.map(reading => [reading.metric, reading]));

const smartFixture = (values = { smart_passed: 1, temp: 30, pending_sectors: 0 }, id = 'sata1') => {
  const units = { smart_passed: 'bool', temp: 'C', power_on_hours: 'hours', critical_warning: 'bits', endurance_used_percent: '%', available_spare_percent: '%', data_written_tib: 'TiB' };
  return {
    metrics: Object.keys(values).map(suffix => ({ key: `smart.${id}.${suffix}`, label: suffix === 'temp' ? 'Drive One' : suffix, unit: units[suffix] ?? 'count' })),
    readings: Object.entries(values).map(([suffix, value]) => ({ metric: `smart.${id}.${suffix}`, value, unit: units[suffix] ?? 'count', ts: 1000, age_seconds: 10 })),
    health: { ok: true, smartctl_available: true, smart_devices: [{ label: 'Drive One', device: `/host/dev/${id}`, present: true }] }, errors: {},
  };
};

test('drive grouping combines SMART fields, readings, and devices without counting fields as drives', () => {
  const fixture = smartFixture();
  fixture.metrics.push({ key: 'memory.used_percent', label: 'RAM', unit: '%' });
  fixture.health.smart_devices.push({ label: 'NVMe SSD 2', device: '/host/dev/nvme1', present: false });
  const drives = groupDrives(fixture);
  assert.equal(drives.length, 2);
  assert.equal(drives[0].label, 'Drive One');
  assert.equal(drives[0].readings.smart_passed.value, 1);
  assert.equal(drives[1].kind, 'NVMe');
  assert.equal(drives[1].present, false);
  assert.equal(assessDrive(drives[1]).state, 'unknown');
});

test('current SMART results distinguish passed, failed, and missing values including zero', () => {
  assert.equal(assessDrive(groupDrives(smartFixture())[0]).state, 'passed');
  const failed = assessDrive(groupDrives(smartFixture({ smart_passed: 0 }))[0]);
  assert.equal(failed.state, 'failed');
  assert.equal(failed.smartPassed, false);
  const missing = smartFixture();
  missing.readings = missing.readings.filter(reading => !reading.metric.endsWith('.smart_passed'));
  assert.equal(assessDrive(groupDrives(missing)[0]).state, 'unknown');
});

test('advertised readings that are stale, missing, or invalid cannot produce an all-clear', () => {
  const fixture = smartFixture();
  fixture.readings.find(reading => reading.metric.endsWith('.pending_sectors')).age_seconds = 700;
  assert.equal(assessDrive(groupDrives(fixture)[0]).state, 'unknown');
  fixture.readings = fixture.readings.filter(reading => !reading.metric.endsWith('.pending_sectors'));
  assert.equal(assessDrive(groupDrives(fixture)[0]).state, 'unknown');
  fixture.readings.push({ metric: 'smart.sata1.pending_sectors', value: -1, unit: 'count', ts: 1000, age_seconds: 1 });
  assert.equal(assessDrive(groupDrives(fixture)[0]).state, 'unknown');
  const old = groupDrives(smartFixture())[0];
  assert.equal(assessDrive(old, 651).label, 'SMART stale');
  assert.equal(assessDrive(old, 651).smartPassed, null);
});

test('missing devices and unavailable SMART tools override retained passing results', () => {
  const drive = groupDrives(smartFixture())[0];
  assert.equal(assessDrive({ ...drive, present: false }).smartPassed, null);
  assert.equal(assessDrive(drive, 0, {}, false).label, 'SMART unavailable');
});

test('drive warnings remain visible even if another SMART reading is missing', () => {
  const fixture = smartFixture({ smart_passed: 1, pending_sectors: 2, reallocated_sectors: 0 });
  fixture.readings = fixture.readings.filter(reading => !reading.metric.endsWith('.reallocated_sectors'));
  const status = assessDrive(groupDrives(fixture)[0]);
  assert.equal(status.state, 'attention');
  assert.ok(status.reasons.some(reason => reason.includes('Pending Sectors: 2')));
  assert.ok(status.reasons.some(reason => reason.includes('missing')));
});

test('NVMe critical bits are decoded individually and unknown bits are preserved', () => {
  assert.deepEqual(decodeCriticalWarning(0), []);
  assert.equal(decodeCriticalWarning(9).length, 2);
  assert.ok(decodeCriticalWarning(9).includes('Drive is read-only'));
  assert.match(decodeCriticalWarning(128)[0], /0x80/);
  assert.match(decodeCriticalWarning(1.2)[0], /Unrecognized/);
  assert.equal(assessDrive(groupDrives(smartFixture({ smart_passed: 1, critical_warning: 8 }, 'nvme0'))[0]).state, 'attention');
});

test('endurance is wear consumed and values above 100 remain visible', () => {
  const drive = groupDrives(smartFixture({ smart_passed: 1, endurance_used_percent: 105, available_spare_percent: 100 }, 'nvme0'))[0];
  assert.equal(assessDrive(drive).state, 'attention');
  assert.equal(formatValue(readSmartReading(drive, 'endurance_used_percent').value, '%'), '105.0%');
  const plot = buildPlot(series([point(10, 105, 103, 106)]), 0, 20, '%');
  assert.ok(plot.upper >= 106);
});

test('old CRC, shutdown, and log totals do not create permanent alerts', () => {
  const drive = groupDrives(smartFixture({ smart_passed: 1, crc_errors: 4, unsafe_shutdowns: 9, error_log_entries: 3 }))[0];
  assert.equal(assessDrive(drive).state, 'passed');
  assert.equal(formatValue(drive.readings.unsafe_shutdowns.value, 'count'), '9');
});

test('counter increases use fresh newer samples, survive cached polls, and reset safely', () => {
  const fixture = smartFixture({ smart_passed: 1, unsafe_shutdowns: 9 }, 'nvme0');
  const initial = updateCounterBaseline({}, fixture.readings);
  const reading = fixture.readings.find(reading => reading.unit === 'count');
  assert.equal(counterIncrease(reading, initial), 0);
  assert.equal(counterIncrease({ ...reading, value: 11 }, initial), 0);
  const newer = { ...reading, ts: 1300, value: 11 };
  const baseline = updateCounterBaseline(initial, [newer]);
  assert.equal(counterIncrease(newer, baseline), 2);
  assert.equal(counterIncrease({ ...newer, age_seconds: 700 }, baseline), 0);
  assert.equal(counterIncrease(newer, baseline, 651), 0);
  const partialReset = { ...newer, ts: 1600, value: 10 };
  const partialBaseline = updateCounterBaseline(baseline, [partialReset]);
  assert.equal(counterIncrease(partialReset, partialBaseline), 0);
  assert.equal(counterIncrease({ ...partialReset, ts: 1900, value: 11 }, partialBaseline), 1);
  assert.equal(counterIncrease(newer, partialBaseline), 0);
  assert.equal(assessDrive({ ...groupDrives(fixture)[0], readings: { smart_passed: fixture.readings[0], unsafe_shutdowns: newer } }, 0, baseline).state, 'attention');
  const reset = { ...newer, ts: 1600, value: 0 };
  const resetBaseline = updateCounterBaseline(baseline, [reset]);
  assert.equal(counterIncrease(reset, resetBaseline), 0);
  assert.equal(counterIncrease({ ...reset, ts: 1900, value: 1 }, resetBaseline), 1);
  assert.equal(initial[reading.metric].value, 9);
});

test('stale counters do not establish baselines and missing SMART results cannot inflate totals', () => {
  const reading = { metric: 'smart.nvme0.unsafe_shutdowns', value: 9, unit: 'count', ts: 1000, age_seconds: 700 };
  assert.deepEqual(updateCounterBaseline({}, [reading]), {});
  const passed = assessDrive(groupDrives(smartFixture())[0]);
  const unknown = assessDrive(groupDrives(smartFixture({ temp: 30 }))[0]);
  const failed = assessDrive(groupDrives(smartFixture({ smart_passed: 0 }))[0]);
  assert.deepEqual(summarizeDrives([passed, unknown, failed]), { total: 3, passed: 1, failed: 1, attention: 0, unknown: 1 });
  assert.equal(summarizeDrives([]).total, 0);
});

test('resource readings preserve zero and reject missing, invalid, or mismatched units', () => {
  const reading = { metric: 'swap.used_gib', value: 0, unit: 'GiB', ts: 1000, age_seconds: 10 };
  assert.equal(metricReading(reading, 'GiB').value, 0);
  assert.equal(metricReading(undefined, 'GiB'), undefined);
  assert.equal(metricReading({ ...reading, value: NaN }, 'GiB'), undefined);
  assert.equal(metricReading({ ...reading, value: -1 }, 'GiB'), undefined);
  assert.equal(metricReading(reading, '%'), undefined);
  assert.equal(isStale(reading, 51), true);
});

test('drive history excludes booleans and bitmasks and selects a meaningful default', () => {
  const drive = groupDrives(smartFixture({ smart_passed: 1, critical_warning: 0, endurance_used_percent: 32, power_on_hours: 23000 }, 'nvme0'))[0];
  const options = driveHistoryMetrics(drive);
  assert.equal(options.length, 2);
  assert.ok(options.every(metric => !['bool', 'bits'].includes(metric.unit)));
  assert.equal(defaultDriveHistory(drive), 'smart.nvme0.endurance_used_percent');
});

test('drive charts and axis labels use the actual units and nonnegative count scales', () => {
  assert.equal(formatAxisValue(2.5, 'TiB'), '2.5 TiB');
  assert.equal(formatAxisValue(0, 'count'), '0');
  assert.equal(formatAxisValue(23, 'hours'), '23 h');
  assert.equal(formatAxisValue(0, 'F'), '0°F');
  assert.equal(formatValue(47.137, 'TiB'), '47.1 TiB');
  const countPlot = buildPlot(series([point(10, 0)]), 0, 20, 'count');
  assert.equal(countPlot.lower, 0);
  assert.ok(countPlot.upper > 0);
  const tibPlot = buildPlot(series([point(10, 47.1), point(20, 47.2)]), 0, 30, 'TiB');
  assert.ok(tibPlot.lower <= 47.1 && tibPlot.upper >= 47.2);
});

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
  for (const filename of ['Infrastructure.svelte', 'MetricChart.svelte', 'DriveCard.svelte', '../App.svelte']) {
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
