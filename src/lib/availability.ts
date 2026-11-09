import type { Shortcut } from './shortcuts';

export type AvailabilityReading = {
  id: string; value: string; state: 'reachable' | 'http_error' | 'unreachable' | 'unknown';
  detail: string; status_code: number | null; checked_at: string | null; last_success_at: string | null; stale: boolean;
};
export type AvailabilityState = { readings: AvailabilityReading[]; checking: boolean; loading: boolean; error: string };
export const emptyAvailability = (): AvailabilityState => ({ readings: [], checking: false, loading: false, error: '' });

export function availabilityStatus(item: Shortcut, data: AvailabilityState, now = Date.now()) {
  if (item.action !== 'open' || !item.availability) return null;
  const reading = data.readings.find(reading => reading.id === item.id && reading.value === item.value);
  const checked = reading?.checked_at ? Date.parse(reading.checked_at) : NaN;
  const stale = reading?.stale || !Number.isFinite(checked) || now - checked > 150000;
  const state = data.error || stale ? 'unknown' : reading?.state ?? 'unknown';
  const label = state === 'reachable' ? 'Reachable' : state === 'http_error' ? `HTTP ${reading?.status_code ?? 'error'}`
    : state === 'unreachable' ? 'Unreachable' : !reading?.checked_at && !data.error ? 'Checking' : 'Status unavailable';
  const detail = data.error || (!reading?.checked_at ? 'Waiting for the first check.' : stale ? 'The last check is too old to show a current status.' : reading?.detail ?? '');
  const format = (value: string | null | undefined) => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString() : 'Not yet';
  const checkedAt = format(reading?.checked_at);
  const lastSuccess = format(reading?.last_success_at);
  return { state, label, detail, checkedAt, lastSuccess,
    title: `${item.name}: ${label}. ${detail} Checked from the Clonepage server: ${checkedAt}. Last reachable: ${lastSuccess}.` };
}

export class AvailabilityStore {
  state = emptyAvailability();
  private disposed = false;
  private queued = false;
  private controller = new AbortController();
  constructor(private publish: (state: AvailabilityState) => void, private fetcher: typeof fetch = (...args) => fetch(...args)) {}
  private update(changes: Partial<AvailabilityState>) {
    if (!this.disposed) { this.state = { ...this.state, ...changes }; this.publish(this.state); }
  }
  async refresh() {
    if (this.disposed) return;
    if (this.state.loading) { this.queued = true; return; }
    this.update({ loading: true });
    try {
      const response = await this.fetcher('/api/shortcuts/availability', { cache: 'no-store', signal: AbortSignal.any([this.controller.signal, AbortSignal.timeout(10000)]) });
      if (!response.ok) throw Error('Unavailable');
      const data = await response.json() as { readings: AvailabilityReading[]; checking: boolean };
      this.update({ readings: data.readings, checking: data.checking, error: '' });
    } catch {
      this.update({ checking: false, error: 'Availability could not be refreshed. Previous check times are kept.' });
    } finally {
      this.update({ loading: false });
      if (this.queued && !this.disposed) { this.queued = false; void this.refresh(); }
    }
  }
  dispose() { this.disposed = true; this.controller.abort(); }
}
