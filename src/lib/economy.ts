export type EconomyPoint = { date: string; value: number | null; secondary: number | null };
export type EconomyChange = { value: number | null; unit: string; label: string; comparison_date: string | null; current_average?: number | null; previous_average?: number | null };
export type EconomicIndicator = {
  id: string; title: string; group: string; frequency: string; source: string; adjustment: string;
  unit: string; basis: string; interpretation: string; methodology: string; history_limit_years: number | null;
  drawdown: { value: number; high: number; high_date: string; window_start: string } | null;
  provider: "fred" | "nyfed"; source_label: string; source_published_month: string | null; source_data_url: string | null;
  source_url: string; latest: EconomyPoint | null; change: EconomyChange | null; points: EconomyPoint[];
  checked_at: string | null; fetched_at: string | null; fred_updated_at: string | null; next_check_at: string | null;
  stale: boolean; error: string | null; metadata_warning: string | null;
};
export type EconomySnapshot = { indicators: EconomicIndicator[]; refreshing: boolean; as_of: string };
export const economyGroups = ['Labor market', 'Inflation & growth', 'Interest rates & financial conditions', 'Market conditions', 'Household finances'];

export function observationLabel(value: string | null | undefined, frequency: string) {
  if (!value) return 'Not available';
  const date = new Date(value + 'T00:00:00Z');
  if (!Number.isFinite(date.getTime())) return 'Not available';
  if (frequency === 'Quarterly') return `Q${Math.floor(date.getUTCMonth() / 3) + 1} ${date.getUTCFullYear()}`;
  const text = date.toLocaleDateString('en-US', { timeZone: 'UTC', month: 'short', year: 'numeric', ...(frequency === 'Monthly' ? {} : { day: 'numeric' }) });
  return frequency === 'Weekly' ? `Week ending ${text}` : text;
}

export function updatedLabel(value: string | null | undefined) {
  if (!value) return 'Unavailable';
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? date.toLocaleString(undefined, { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short' }) : 'Unavailable';
}

export function economyValue(value: number | null | undefined, unit: string, signed = false, digits = unit === 'claims' || unit === 'bps' ? 0 : 2) {
  if (value == null || !Number.isFinite(value)) return '—';
  const rounded = Math.abs(value) < .5 * 10 ** -digits ? 0 : value;
  const text = new Intl.NumberFormat('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits, signDisplay: signed ? 'exceptZero' : 'auto' }).format(rounded);
  return unit === '%' ? text + '%' : text + (unit === 'claims' || unit === 'index' ? '' : ` ${unit}`);
}

export function movement(change: EconomyChange | null) {
  const value = change?.value;
  if (value == null || !Number.isFinite(value)) return 'unavailable';
  const precision = change?.unit === 'claims' || change?.unit === 'bps' ? 0 : 2;
  if (Math.abs(value) < .5 * 10 ** -precision) return 'flat';
  return value > 0 ? 'up' : 'down';
}

export function historyStart(asOf: string, years: number) {
  const now = new Date(asOf);
  const year = now.getUTCFullYear() - years, month = now.getUTCMonth();
  const day = Math.min(now.getUTCDate(), new Date(Date.UTC(year, month + 1, 0)).getUTCDate());
  return new Date(Date.UTC(year, month, day)).toISOString().slice(0, 10);
}

const seconds = (day: string) => Date.parse(day + 'T00:00:00Z') / 1000;
export function economicPlot(points: EconomyPoint[], start: string, end: string) {
  const visible = points.filter(point => point.date >= start && point.date <= end);
  const values = visible.flatMap(point => [point.value, point.secondary]).filter((value): value is number => value !== null && Number.isFinite(value));
  if (!values.length) return { points: visible, paths: ['', ''], minimum: 0, maximum: 1, zero: null, selected: [] as EconomyPoint[] };
  const low = Math.min(...values), high = Math.max(...values), pad = Math.max((high - low) * .12, Math.abs(high) * .015, .1);
  const minimum = low - pad, maximum = high + pad;
  const beginning = seconds(start), duration = Math.max(1, seconds(end) - beginning);
  const x = (day: string) => Math.max(0, Math.min(600, (seconds(day) - beginning) / duration * 600));
  const y = (value: number) => (maximum - value) / (maximum - minimum) * 160;
  const path = (key: 'value' | 'secondary') => {
    let pen = false;
    return visible.map(point => {
      const value = point[key];
      if (value === null || !Number.isFinite(value)) { pen = false; return ''; }
      const command = `${pen ? 'L' : 'M'}${x(point.date).toFixed(2)},${y(value).toFixed(2)}`;
      pen = true;
      return command;
    }).join(' ');
  };
  return { points: visible, paths: [path('value'), path('secondary')], minimum, maximum,
    zero: minimum < 0 && maximum > 0 ? y(0) : null,
    selected: visible.filter(point => point.value !== null && Number.isFinite(point.value)) };
}

export function closestPoint(points: EconomyPoint[], day: string) {
  const target = seconds(day);
  return points.reduce((best, point, index) => Math.abs(seconds(point.date) - target) < Math.abs(seconds(points[best].date) - target) ? index : best, 0);
}
