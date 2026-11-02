export type Metric = { key: string; label: string; unit: string };
export type Reading = { metric: string; ts: number; value: number; unit: string };
export type Point = { ts: number; value: number | null; min: number | null; max: number | null; samples?: number | null };
export type Series = { metric: string; unit: string; points: Point[] };
export type Snapshot = {
  metrics: Metric[];
  readings: Reading[];
  health: {
    ok: boolean;
    smartctl_available: boolean | null;
    smart_devices: { label: string; device: string; present: boolean }[];
    last_collection_ts: number | null;
    last_smart_poll_ts: number | null;
  } | null;
  errors: Record<string, string>;
};
export type ChartSeries = Series & { label: string; color: string };

export const ranges = [
  { label: '1 hour', short: '1h', seconds: 3600 },
  { label: '6 hours', short: '6h', seconds: 21600 },
  { label: '24 hours', short: '24h', seconds: 86400 },
  { label: '7 days', short: '7d', seconds: 604800 },
];
export const metricColors = Array.from({ length: 7 }, (_, i) => `var(--chart-${i})`);

export function readingAge(reading: Reading | undefined, now: number): number | null {
  return reading ? Math.max(0, now - reading.ts) : null;
}

export function isStale(reading: Reading | undefined, now: number): boolean {
  const age = readingAge(reading, now);
  return age === null || age > (reading?.metric.startsWith('smart.') ? 660 : 60);
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
  return `${value.toFixed(1)}${unit === 'C' ? '°' : unit}`;
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

// Bucket means form the line; min/max form the envelope so averaging does not
// erase short spikes. Missing values and collection gaps split the paths.
export function buildPlot(series: ChartSeries[], start: number, end: number, unit: string) {
  const width = 900;
  const height = 180;
  const prepared = series.map(item => ({
    ...item,
    gaps: item.points.filter(p => p.value === null).map(p => p.ts),
    points: item.points.filter(p => p.ts >= start && p.ts <= end && p.value !== null && Number.isFinite(p.value)).sort((a, b) => a.ts - b.ts),
  }));
  const values = prepared.flatMap(s => s.points.flatMap(p => [p.min ?? p.value!, p.max ?? p.value!]));
  const minimum = values.length ? Math.min(...values) : 0;
  const maximum = values.length ? Math.max(...values) : 100;
  const padding = Math.max(2, (maximum - minimum) * 0.15);
  const lower = unit === '%' ? 0 : Math.floor((minimum - padding) / 5) * 5;
  const upper = unit === '%' ? 100 : Math.ceil((maximum + padding) / 5) * 5;
  const x = (ts: number) => (ts - start) / Math.max(1, end - start) * width;
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
