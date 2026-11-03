export type Metric = { key: string; label: string; unit: string };
export type Reading = { metric: string; ts: number; value: number; unit: string; age_seconds: number };
export type Point = { ts: number; value: number | null; min: number | null; max: number | null; samples?: number | null };
export type Series = { metric: string; unit: string; points: Point[]; since_seconds?: number | null };
export type TemperatureUnit = 'C' | 'F';
export type Snapshot = {
  metrics: Metric[];
  readings: Reading[];
  health: {
    ok: boolean;
    smartctl_available: boolean | null;
    smart_devices: { label: string; device: string; present: boolean }[];
    last_collection_ts: number | null;
    last_smart_poll_ts: number | null;
    collection_age_seconds: number | null;
  } | null;
  errors: Record<string, string>;
};
export type ChartSeries = Series & { label: string; color: string };

export type CounterBaseline = Record<string, { ts: number; value: number; latestTs: number; latestValue: number }>;
export type Drive = {
  id: string; label: string; kind: 'HDD' | 'NVMe'; present: boolean | null;
  metrics: Metric[]; readings: Record<string, Reading>;
};
export type DriveStatus = {
  state: 'passed' | 'failed' | 'attention' | 'unknown'; label: string;
  smartPassed: boolean | null; reasons: string[];
};

export const driveFields = [
  { suffix: 'smart_passed', label: 'SMART Self-Assessment', unit: 'bool', history: false, counter: false },
  { suffix: 'power_on_hours', label: 'Power-On Hours', unit: 'hours', history: true, counter: false },
  { suffix: 'reallocated_sectors', label: 'Reallocated Sectors', unit: 'count', history: true, counter: true },
  { suffix: 'pending_sectors', label: 'Pending Sectors', unit: 'count', history: true, counter: false },
  { suffix: 'uncorrectable_sectors', label: 'Offline Uncorrectable Sectors', unit: 'count', history: true, counter: true },
  { suffix: 'reported_uncorrectable', label: 'Reported Uncorrectable Errors', unit: 'count', history: true, counter: true },
  { suffix: 'crc_errors', label: 'Interface CRC Errors', unit: 'count', history: true, counter: true },
  { suffix: 'endurance_used_percent', label: 'Endurance Used', unit: '%', history: true, counter: false },
  { suffix: 'available_spare_percent', label: 'Available Spare', unit: '%', history: true, counter: false },
  { suffix: 'critical_warning', label: 'Critical Warnings', unit: 'bits', history: false, counter: false },
  { suffix: 'media_errors', label: 'Media Errors', unit: 'count', history: true, counter: true },
  { suffix: 'unsafe_shutdowns', label: 'Unsafe Shutdowns', unit: 'count', history: true, counter: true },
  { suffix: 'error_log_entries', label: 'Error Log Entries', unit: 'count', history: true, counter: true },
  { suffix: 'data_written_tib', label: 'Host Data Written', unit: 'TiB', history: true, counter: false },
] as const;

export function metricReading(reading: Reading | undefined, unit: string): Reading | undefined {
  return reading && reading.unit === unit && Number.isFinite(reading.value) && reading.value >= 0 ? reading : undefined;
}

export function groupDrives(snapshot: Snapshot | null): Drive[] {
  const drives = new Map<string, Drive>();
  function ensure(id: string) {
    if (!drives.has(id)) drives.set(id, { id, label: id, kind: id.startsWith('nvme') ? 'NVMe' : 'HDD', present: null, metrics: [], readings: {} });
    return drives.get(id)!;
  }
  for (const metric of snapshot?.metrics ?? []) {
    const match = /^smart\.([^.]+)\.(.+)$/.exec(metric.key);
    if (!match) continue;
    const drive = ensure(match[1]);
    drive.metrics.push(metric);
    if (match[2] === 'temp') drive.label = metric.label;
  }
  for (const reading of snapshot?.readings ?? []) {
    const match = /^smart\.([^.]+)\.(.+)$/.exec(reading.metric);
    if (match) ensure(match[1]).readings[match[2]] = reading;
  }
  for (const device of snapshot?.health?.smart_devices ?? []) {
    const id = device.device.split('/').at(-1);
    if (!id) continue;
    const drive = ensure(id);
    drive.present = device.present;
    drive.label = device.label;
  }
  return [...drives.values()].sort((a, b) => Number(a.kind === 'NVMe') - Number(b.kind === 'NVMe') || a.id.localeCompare(b.id, undefined, { numeric: true }));
}

export function driveReading(drive: Drive, suffix: string): Reading | undefined {
  const field = driveFields.find(field => field.suffix === suffix);
  const reading = drive.readings[suffix];
  if (suffix === 'temp') return reading?.unit === 'C' && Number.isFinite(reading.value) ? reading : undefined;
  if (!field) return undefined;
  const valid = metricReading(reading, field.unit);
  if (!valid || (['bool', 'bits', 'count', 'hours'].includes(field.unit) && !Number.isSafeInteger(valid.value))) return undefined;
  if (field.unit === 'bool' && valid.value !== 0 && valid.value !== 1) return undefined;
  if (field.unit === 'bits' && valid.value > 255) return undefined;
  return valid;
}

// These are page-session baselines, not persisted alerts. Cached samples and
// counter resets cannot create duplicate or negative increase notifications.
export function updateCounterBaseline(baseline: CounterBaseline, readings: Reading[]): CounterBaseline {
  const next = { ...baseline };
  for (const reading of readings) {
    const suffix = reading.metric.split('.').at(-1);
    if (!reading.metric.startsWith('smart.') || !driveFields.some(field => field.suffix === suffix && field.counter)
      || !metricReading(reading, 'count') || !Number.isSafeInteger(reading.value) || isStale(reading)) continue;
    const previous = next[reading.metric];
    if (!previous || (reading.ts > previous.latestTs && reading.value < previous.latestValue)) {
      next[reading.metric] = { ts: reading.ts, value: reading.value, latestTs: reading.ts, latestValue: reading.value };
    } else if (reading.ts > previous.latestTs) {
      next[reading.metric] = { ...previous, latestTs: reading.ts, latestValue: reading.value };
    }
  }
  return next;
}

export function counterIncrease(reading: Reading | undefined, baseline: CounterBaseline, elapsed = 0): number {
  if (!reading || isStale(reading, elapsed) || !metricReading(reading, 'count') || !Number.isSafeInteger(reading.value)) return 0;
  const previous = baseline[reading.metric];
  return previous && reading.ts > previous.ts && reading.ts >= previous.latestTs ? Math.max(0, reading.value - previous.value) : 0;
}

export function decodeCriticalWarning(value: number): string[] {
  if (!Number.isSafeInteger(value) || value < 0 || value > 255) return ['Unrecognized critical warning value'];
  const descriptions = ['Available spare below its threshold', 'Temperature outside the drive’s limits', 'Drive reliability degraded',
    'Drive is read-only', 'Volatile memory backup failed', 'Persistent memory is read-only or unreliable'];
  const warnings = descriptions.flatMap((description, bit) => value & (1 << bit) ? [description] : []);
  if (value & 0xc0) warnings.push(`Unrecognized warning bits (0x${(value & 0xc0).toString(16).toUpperCase()})`);
  return warnings;
}

export function assessDrive(drive: Drive, elapsed = 0, baseline: CounterBaseline = {}, smartAvailable: boolean | null = null): DriveStatus {
  if (drive.present === false || smartAvailable === false) return {
    state: 'unknown', label: drive.present === false ? 'Device unavailable' : 'SMART unavailable', smartPassed: null,
    reasons: [drive.present === false ? 'The collector cannot find this device.' : 'The collector cannot read SMART data.'],
  };
  const smart = driveReading(drive, 'smart_passed');
  const smartPassed = smart && !isStale(smart, elapsed) ? smart.value === 1 : null;
  if (smartPassed === false) return { state: 'failed', label: 'SMART failed', smartPassed, reasons: ['The drive reports a failed SMART self-assessment.'] };
  const reasons: string[] = [];
  for (const field of driveFields) {
    const reading = driveReading(drive, field.suffix);
    if (!reading || isStale(reading, elapsed)) continue;
    if (field.suffix === 'critical_warning') reasons.push(...decodeCriticalWarning(reading.value));
    else if (['pending_sectors', 'reallocated_sectors', 'uncorrectable_sectors', 'reported_uncorrectable', 'media_errors'].includes(field.suffix) && reading.value > 0) {
      reasons.push(`${field.label}: ${reading.value.toLocaleString()} recorded.`);
    } else if (field.suffix === 'endurance_used_percent' && reading.value >= 100) reasons.push('Estimated rated endurance has been consumed.');
    const increase = counterIncrease(reading, baseline, elapsed);
    if (increase > 0) reasons.push(`${field.label}: +${increase.toLocaleString()} since this page was opened.`);
  }
  const expected = driveFields.filter(field => drive.metrics.some(metric => metric.key === `smart.${drive.id}.${field.suffix}`));
  const incomplete = expected.some(field => !driveReading(drive, field.suffix) || isStale(driveReading(drive, field.suffix), elapsed));
  const needsAttention = reasons.length > 0;
  if (smartPassed === null || incomplete) reasons.push('Some SMART readings are missing, invalid, or stale.');
  if (needsAttention) return { state: 'attention', label: 'Needs attention', smartPassed, reasons };
  if (smartPassed === null || incomplete) return { state: 'unknown', label: smart && isStale(smart, elapsed) ? 'SMART stale' : 'SMART unknown', smartPassed, reasons };
  return { state: 'passed', label: 'SMART passed', smartPassed, reasons: [] };
}

export function summarizeDrives(statuses: DriveStatus[]) {
  return { total: statuses.length, passed: statuses.filter(status => status.smartPassed === true).length,
    failed: statuses.filter(status => status.state === 'failed').length,
    attention: statuses.filter(status => status.state === 'attention').length,
    unknown: statuses.filter(status => status.state === 'unknown').length };
}

export function driveHistoryMetrics(drive: Drive): Metric[] {
  return driveFields.filter(field => field.history).flatMap(field => {
    const metric = drive.metrics.find(metric => metric.key === `smart.${drive.id}.${field.suffix}` && metric.unit === field.unit);
    return metric ? [{ ...metric, label: field.label }] : [];
  });
}

export function defaultDriveHistory(drive: Drive): string {
  const options = driveHistoryMetrics(drive);
  const preferred = drive.kind === 'NVMe' ? 'endurance_used_percent' : 'pending_sectors';
  return options.find(metric => metric.key.endsWith(`.${preferred}`))?.key ?? options[0]?.key ?? '';
}

export const ranges = [
  { label: '1 hour', short: '1h', seconds: 3600 },
  { label: '6 hours', short: '6h', seconds: 21600 },
  { label: '24 hours', short: '24h', seconds: 86400 },
  { label: '7 days', short: '7d', seconds: 604800 },
  { label: '30 days', short: '30d', seconds: 2592000 },
  { label: '90 days', short: '90d', seconds: 7776000 },
];
export const metricColors = Array.from({ length: 7 }, (_, i) => `var(--chart-${i})`);

export function readingAge(reading: Reading | undefined, elapsed = 0): number | null {
  return reading && Number.isFinite(reading.age_seconds) ? Math.max(0, reading.age_seconds) + Math.max(0, elapsed) : null;
}

export function isStale(reading: Reading | undefined, elapsed = 0): boolean {
  const age = readingAge(reading, elapsed);
  return age === null || age > (reading?.metric.startsWith('smart.') ? 660 : 60);
}

export function averageDriveTemperature(
  metrics: Metric[], readings: ReadonlyMap<string, Reading>, kind: 'hdd' | 'nvme', elapsed = 0,
) {
  const drives = metrics.filter(metric => metric.unit === 'C' && metric.key.startsWith('smart.')
    && metric.key.includes('nvme') === (kind === 'nvme'));
  const current = drives.map(metric => readings.get(metric.key))
    .filter((reading): reading is Reading => !!reading && reading.unit === 'C'
      && Number.isFinite(reading.value) && !isStale(reading, elapsed));
  return {
    value: current.length ? current.reduce((sum, reading) => sum + reading.value, 0) / current.length : null,
    count: current.length,
    total: drives.length,
    oldestAge: current.length ? Math.max(...current.map(reading => readingAge(reading, elapsed)!)) : null,
  };
}

export function sourceTime(snapshot: Snapshot | null, elapsed = 0): number | null {
  const times = snapshot?.readings.flatMap(reading => {
    const age = readingAge(reading, elapsed);
    return age === null ? [] : [reading.ts + age];
  }) ?? [];
  return times.length ? Math.max(...times) : null;
}

export function collectionDiagnostics(snapshot: Snapshot | null, elapsed = 0): string[] {
  if (!snapshot) return [];
  const messages: string[] = [];
  const health = snapshot.health;
  if (health?.ok === false) messages.push('The collector is stopped. Readings are not being updated.');
  else if (health?.collection_age_seconds != null && health.collection_age_seconds + elapsed > 60) {
    messages.push('The collector is running, but its last successful collection is delayed.');
  }
  if (health?.smartctl_available === false) messages.push('Drive temperature collection is unavailable because smartctl is not installed.');
  const missing = health?.smart_devices.filter(device => !device.present).map(device => device.label) ?? [];
  if (missing.length) messages.push(`Drive devices unavailable: ${missing.join(', ')}. This reports device availability, not drive health.`);
  if (health?.smartctl_available !== false) {
    const readings = new Map(snapshot.readings.map(reading => [reading.metric, reading]));
    const stale = snapshot.metrics.filter(metric => metric.key.startsWith('smart.') && metric.unit === 'C' && isStale(readings.get(metric.key), elapsed));
    if (stale.length) messages.push(`No current temperature readings for: ${stale.map(metric => metric.label).join(', ')}.`);
  }
  return messages;
}

export function convertTemperature(value: number | null | undefined, unit: TemperatureUnit): number | null {
  if (value == null || !Number.isFinite(value)) return null;
  return unit === 'F' ? value * 9 / 5 + 32 : value;
}

export function convertTemperatureSeries(series: Series, unit: TemperatureUnit): Series {
  return {
    ...series, unit,
    points: series.points.map(point => ({ ...point,
      value: convertTemperature(point.value, unit),
      min: convertTemperature(point.min, unit),
      max: convertTemperature(point.max, unit),
    })),
  };
}

export function ageLabel(age: number | null): string {
  if (age === null) return 'No reading yet';
  if (age < 5) return 'Just updated';
  if (age < 60) return `${Math.floor(age)}s ago`;
  if (age < 3600) return `${Math.floor(age / 60)}m ago`;
  if (age < 86400) return `${Math.floor(age / 3600)}h ago`;
  return `${Math.floor(age / 86400)}d ago`;
}

export function formatValue(value: number | null | undefined, unit: string): string {
  if (value == null || !Number.isFinite(value)) return '—';
  if (unit === 'bool') return value === 1 ? 'Passed' : value === 0 ? 'Failed' : 'Unknown';
  if (unit === 'bits') return value === 0 ? 'None' : `0x${value.toString(16).toUpperCase().padStart(2, '0')}`;
  if (unit === 'count' || unit === 'hours') return `${value.toLocaleString(undefined, { maximumFractionDigits: 1 })}${unit === 'hours' ? ' h' : ''}`;
  if (unit === 'GiB' || unit === 'TiB') return `${value.toFixed(1)} ${unit}`;
  return `${value.toFixed(1)}${unit === 'C' || unit === 'F' ? '°' : unit}`;
}

export function formatAxisValue(value: number, unit: string): string {
  const number = value.toLocaleString(undefined, { maximumFractionDigits: 1 });
  if (unit === 'C' || unit === 'F') return `${number}°${unit}`;
  if (unit === '%') return `${number}%`;
  if (unit === 'count') return number;
  return `${number} ${unit === 'hours' ? 'h' : unit}`;
}

export async function fetchJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const timeout = AbortSignal.timeout(12000);
  const response = await fetch(path, {
    cache: 'no-store',
    signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
  });
  if (!response.ok) throw new Error('Could not load NAS readings.');
  return response.json();
}

export function nearestPlotPoint(points: Point[], cursor: number, start: number, end: number): Point | undefined {
  if (!points.length || cursor < Math.max(start, points[0].ts)) return undefined;
  const position = (point: Point) => Math.max(start, point.ts);
  const nearest = points.reduce((best, point) => Math.abs(position(point) - cursor) < Math.abs(position(best) - cursor) ? point : best);
  const intervals = points.slice(1).map((point, index) => point.ts - points[index].ts).filter(delta => delta > 0).sort((a, b) => a - b);
  const cadence = intervals[Math.floor(intervals.length / 2)] ?? (end - start) / 360;
  const proximity = Math.max(90, Math.min(cadence * 1.5, (end - start) / 100));
  return Math.abs(position(nearest) - cursor) <= proximity ? nearest : undefined;
}

// Bucket means form the line; min/max form the envelope so averaging does not
// erase short spikes. Missing values and collection gaps split the paths.
export function buildPlot(series: ChartSeries[], start: number, end: number, unit: string) {
  const width = 900;
  const height = 180;
  const prepared = series.map(item => {
    const sorted = [...item.points].sort((a, b) => a.ts - b.ts);
    const points = sorted.filter(p => p.ts >= start && p.ts <= end);
    // The proxy identifies the lookback used for this response. Its leading
    // partial bucket contains in-window samples even if its start is earlier.
    // Keep the actual timestamp for tooltips; clip only its plotted position.
    const leading = sorted.filter(p => p.ts < start).at(-1);
    if (leading && item.since_seconds != null && Math.abs(item.since_seconds - (end - start)) < 1) points.unshift(leading);
    return { ...item, gaps: points.filter(p => p.value === null).map(p => p.ts),
      points: points.filter(p => p.value !== null && Number.isFinite(p.value)) };
  });
  const values = prepared.flatMap(s => s.points.flatMap(p => [p.min ?? p.value!, p.max ?? p.value!]));
  const minimum = values.length ? Math.min(...values) : 0;
  const maximum = values.length ? Math.max(...values) : 100;
  const padding = Math.max(2, (maximum - minimum) * 0.15);
  const temperature = unit === 'C' || unit === 'F';
  const step = temperature ? 5 : Math.max(unit === 'count' || unit === 'hours' ? 1 : 0.1, 10 ** Math.floor(Math.log10(Math.max(1, maximum - minimum))) / 2);
  const numericPadding = temperature ? padding : Math.max(step, (maximum - minimum) * .15);
  const lower = unit === '%' ? 0 : Math.max(temperature ? -Infinity : 0, Math.floor((minimum - numericPadding) / step) * step);
  const upper = unit === '%' ? Math.max(100, Math.ceil(maximum / 10) * 10) : Math.ceil((maximum + numericPadding) / step) * step;
  const x = (ts: number) => Math.max(0, Math.min(width, (ts - start) / Math.max(1, end - start) * width));
  const y = (value: number) => height - (value - lower) / Math.max(1, upper - lower) * height;
  const paths = prepared.map(item => {
    const deltas = item.points.slice(1).map((p, i) => p.ts - item.points[i].ts).filter(d => d > 0).sort((a, b) => a - b);
    const gap = Math.max(90, (deltas[Math.floor(deltas.length / 2)] ?? 30) * 3);
    const segments: Point[][] = [];
    for (const point of item.points) {
      const previous = segments.at(-1)?.at(-1);
      const explicitGap = previous && item.gaps.some(ts => ts > previous.ts && ts < point.ts);
      if (!previous || point.ts - previous.ts > gap || explicitGap) segments.push([]);
      segments.at(-1)!.push(point);
    }
    const line = segments.map(segment => segment.map((p, i) => `${i ? 'L' : 'M'}${x(p.ts).toFixed(2)},${y(p.value!).toFixed(2)}`).join(' ')).join(' ');
    const band = segments.filter(s => s.length > 1).map(segment => {
      const high = segment.map((p, i) => `${i ? 'L' : 'M'}${x(p.ts).toFixed(2)},${y(p.max ?? p.value!).toFixed(2)}`).join(' ');
      const low = [...segment].reverse().map(p => `L${x(p.ts).toFixed(2)},${y(p.min ?? p.value!).toFixed(2)}`).join(' ');
      return `${high} ${low} Z`;
    }).join(' ');
    return { ...item, line, band, dots: segments.filter(s => s.length === 1).map(s => ({ x: x(s[0].ts), y: y(s[0].value!) })) };
  });
  return { paths, lower, upper, ticks: [upper, (lower + upper) / 2, lower], hasData: values.length > 0, minimum, maximum };
}
