export type Shortcut = { id: string; name: string; description: string; aliases: string[]; icon: string;
  color: string; action: 'open' | 'copy'; value: string };
export type ShortcutDraft = Omit<Shortcut, 'id'>;
export type ShortcutSnapshot = { items: Shortcut[]; revision: number };
export type ShortcutState = ShortcutSnapshot & { loaded: boolean; loading: boolean; busy: boolean; conflict: boolean; error: string };

export class ShortcutStore {
  state: ShortcutState = { items: [], revision: 0, loaded: false, loading: false, busy: false, conflict: false, error: '' };
  private sequence = 0;
  private queued = false;
  private disposed = false;
  private controller = new AbortController();
  constructor(private publish: (state: ShortcutState) => void, private fetcher: typeof fetch = (...args) => fetch(...args)) {}

  private update(changes: Partial<ShortcutState>) {
    if (!this.disposed) { this.state = { ...this.state, ...changes }; this.publish(this.state); }
  }

  private async request(path: string, options: RequestInit = {}) {
    const response = await this.fetcher(`/api/shortcuts${path}`, { ...options, cache: 'no-store',
      headers: { 'Content-Type': 'application/json' },
      signal: AbortSignal.any([this.controller.signal, AbortSignal.timeout(15000)]) });
    if (!response.ok) {
      let message = 'Could not save shortcuts. Try again.';
      try {
        const payload = await response.json();
        if (typeof payload.detail === 'string') message = payload.detail;
        else if (Array.isArray(payload.detail)) message = payload.detail.map((item: { msg: string }) => item.msg.replace(/^Value error, /, '')).join(' ');
      } catch { /* Keep the readable fallback. */ }
      throw Object.assign(new Error(message), { status: response.status });
    }
    return await response.json() as ShortcutSnapshot;
  }

  async refresh(preserveError = false) {
    if (this.disposed) return;
    if (this.state.busy) { this.queued = true; return; }
    const sequence = ++this.sequence;
    this.update({ loading: true });
    try {
      const result = await this.request('');
      if (sequence === this.sequence) this.update({ ...result, loaded: true, error: this.state.conflict || preserveError ? this.state.error : '' });
    } catch {
      if (sequence === this.sequence) this.update({ error: this.state.loaded ? 'Could not refresh shortcuts. Showing the last loaded list.' : 'Could not load your shortcuts. Try again.' });
    } finally {
      if (sequence === this.sequence) this.update({ loading: false });
    }
  }

  private async write(path: string, options: RequestInit) {
    if (this.disposed || this.state.busy) return false;
    ++this.sequence; // Invalidate reads that started before this write.
    this.update({ busy: true, loading: false, conflict: false, error: '' });
    try {
      const result = await this.request(path, options);
      this.update({ ...result, loaded: true });
      return !this.disposed;
    } catch (error) {
      // A conflicting edit reloads the latest list without discarding the editor's draft.
      if ((error as { status?: number }).status === 409) {
        try { const result = await this.request(''); this.update({ ...result, loaded: true }); } catch { /* Keep the draft and conflict message. */ }
      }
      this.update({ conflict: (error as { status?: number }).status === 409, error: error instanceof Error ? error.message : 'Could not save shortcuts. Try again.' });
      return false;
    } finally {
      this.update({ busy: false });
      if (this.queued && !this.disposed) { this.queued = false; void this.refresh(Boolean(this.state.error)); }
    }
  }

  save(draft: ShortcutDraft, id: string | null, revision: number) {
    return this.write(id ? `/${encodeURIComponent(id)}` : '', { method: id ? 'PUT' : 'POST', body: JSON.stringify({ ...draft, revision }) });
  }
  remove(id: string) {
    return this.write(`/${encodeURIComponent(id)}?revision=${this.state.revision}`, { method: 'DELETE' });
  }
  move(id: string, offset: number) {
    const ids = this.state.items.map(item => item.id);
    const from = ids.indexOf(id); const to = from + offset;
    if (from < 0 || to < 0 || to >= ids.length) return Promise.resolve(false);
    [ids[from], ids[to]] = [ids[to], ids[from]];
    return this.reorder(ids);
  }
  async reorder(ids: string[], revision = this.state.revision) {
    if (this.disposed || this.state.busy) return false;
    const previous = this.state.items;
    const byId = new Map(previous.map(item => [item.id, item]));
    if (ids.length !== previous.length || new Set(ids).size !== ids.length || ids.some(id => !byId.has(id))) return false;
    if (ids.every((id, index) => id === previous[index].id)) return false;
    const optimistic = ids.map(id => byId.get(id)!);
    this.update({ items: optimistic });
    const saved = await this.write('/order', { method: 'PUT', body: JSON.stringify({ ids, revision }) });
    if (!saved && !this.disposed) {
      // Keep a freshly loaded conflicting order; otherwise restore our last saved order.
      if (this.state.items === optimistic) this.update({ items: previous });
      this.update({ error: this.state.conflict
        ? 'Shortcuts changed on another device. Check the order and try again.'
        : 'Could not save the new order. The previous order has been restored. Try again.' });
    }
    return saved;
  }
  dispose() { this.disposed = true; ++this.sequence; this.controller.abort(); }
}
