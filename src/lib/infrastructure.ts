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
  return `${value.toFixed(1)}${unit === 'C' || unit === 'F' ? '°' : unit}`;
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
  const lower = unit === '%' ? 0 : Math.floor((minimum - padding) / 5) * 5;
  const upper = unit === '%' ? 100 : Math.ceil((maximum + padding) / 5) * 5;
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
