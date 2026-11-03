export type Subscription = { id: string; title: string; url: string; website: string | null; unread: number;
  last_success_at: string | null; error: string | null; refresh_pending: boolean };
export type FeedEntry = { id: string; title: string; url: string; summary: string; feed_id: string; feed_title: string;
  published_at: string | null; first_seen_at: string; is_read: boolean; is_saved: boolean };
export type FeedPage = { subscriptions: Subscription[]; entries: FeedEntry[]; unread: number; total: number;
  through_seq: number; next_cursor: string | null };
export type FeedScope = { feed_id: string; q: string; unread: boolean };
export type FeedPreview = { title: string; url: string; website: string | null; entry_count: number; samples: string[] };
export type Discovery = { preview: FeedPreview | null; candidates: { title: string; url: string }[] };
export type ReaderState = { data: FeedPage | null; loading: boolean; busy: string[]; expanded: boolean; error: string; notice: string };

export class FeedReader {
  state: ReaderState = { data: null, loading: false, busy: [], expanded: false, error: '', notice: '' };
  scope: FeedScope = { feed_id: '', q: '', unread: true };
  private sequence = 0;
  private revision = 0;
  private writes = 0;
  private queued = false;
  private pages = 1;
  private disposed = false;
  private controller = new AbortController();

  constructor(private publish: (state: ReaderState) => void, private fetcher: typeof fetch = fetch,
              private onSaved: () => void = () => {}) {}

  private update(changes: Partial<ReaderState>) {
    if (!this.disposed) { this.state = { ...this.state, ...changes }; this.publish(this.state); }
  }

  async request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const response = await this.fetcher(path, { ...options, cache: 'no-store',
      headers: { 'Content-Type': 'application/json', ...options.headers },
      signal: AbortSignal.any([this.controller.signal, AbortSignal.timeout(35000)]) });
    if (!response.ok) {
      let message = 'The feed request could not be completed. Try again.';
      try { const payload = await response.json(); if (typeof payload.detail === 'string') message = payload.detail; } catch { /* Use the fallback message. */ }
      throw new Error(message);
    }
    return response.status === 204 ? undefined as T : response.json();
  }

  private path(cursor?: string | null) {
    const params = new URLSearchParams({ unread: String(this.scope.unread), q: this.scope.q, limit: '25' });
    if (this.scope.feed_id) params.set('feed_id', this.scope.feed_id);
    if (cursor) params.set('cursor', cursor);
    return `/api/feeds?${params}`;
  }

  setScope(scope: FeedScope) {
    if (JSON.stringify(scope) === JSON.stringify(this.scope) && (this.state.data || this.state.loading)) return;
    this.scope = { ...scope };
    this.sequence++;
    this.pages = 1;
    this.update({ data: null, expanded: false, error: '' });
    void this.refresh();
  }

  async refresh() {
    if (this.disposed) return;
    if (this.writes) { this.queued = true; return; }
    const sequence = ++this.sequence;
    const revision = this.revision;
    const current = () => !this.disposed && sequence === this.sequence && revision === this.revision;
    this.update({ loading: true });
    try {
      let first: FeedPage | null = null;
      let cursor: string | null = null;
      const entries: FeedEntry[] = [];
      for (let page = 0; page < this.pages; page++) {
        const result: FeedPage = await this.request(this.path(cursor));
        if (!current()) return;
        first ??= result;
        entries.push(...result.entries.filter(entry => !entries.some(existing => existing.id === entry.id)));
        cursor = result.next_cursor;
        if (!cursor) break;
      }
      if (first && current()) this.update({ data: { ...first, entries, next_cursor: cursor }, error: '',
        notice: first.subscriptions.some(feed => feed.refresh_pending) ? this.state.notice : '' });
    } catch (error) {
      if (current()) this.update({ error: error instanceof Error ? error.message : 'Could not load feeds.' });
    } finally {
      if (current()) this.update({ loading: false });
    }
  }

  async showMore() {
    if (!this.state.expanded) { this.update({ expanded: true }); return; }
    const cursor = this.state.data?.next_cursor;
    if (!cursor || this.state.loading || this.writes) return;
    const sequence = ++this.sequence;
    const revision = this.revision;
    this.update({ loading: true });
    try {
      const result = await this.request<FeedPage>(this.path(cursor));
      if (this.disposed || sequence !== this.sequence || revision !== this.revision || !this.state.data) return;
      const entries = [...this.state.data.entries, ...result.entries.filter(entry => !this.state.data!.entries.some(existing => existing.id === entry.id))];
      this.pages++;
      this.update({ data: { ...this.state.data, entries, next_cursor: result.next_cursor }, error: '' });
    } catch (error) {
      if (sequence === this.sequence && revision === this.revision) this.update({ error: error instanceof Error ? error.message : 'Could not load more stories.' });
    } finally {
      if (sequence === this.sequence) this.update({ loading: false });
    }
  }

  collapse() { this.pages = 1; this.update({ expanded: false }); void this.refresh(); }

  private async write(key: string, action: () => Promise<void>) {
    if (this.disposed || this.state.busy.includes(key)) return false;
    this.writes++;
    this.revision++;
    this.queued = true;
    this.update({ busy: [...this.state.busy, key], error: '' });
    let succeeded = false;
    try { await action(); succeeded = true; }
    catch (error) { this.update({ error: error instanceof Error ? error.message : 'Could not update feeds.' }); }
    finally {
      this.writes--;
      this.revision++;
      const error = this.state.error;
      this.update({ busy: this.state.busy.filter(id => id !== key), loading: false });
      if (!this.writes && this.queued) {
        this.queued = false;
        await this.refresh();
        if (error) this.update({ error });
      }
    }
    return succeeded;
  }

  async setRead(entry: FeedEntry, is_read: boolean) {
    if (this.state.busy.includes('read-all')) return false;
    return this.write(`read:${entry.id}`, async () => {
      await this.request(`/api/feeds/entries/${entry.id}`, { method: 'PATCH', body: JSON.stringify({ is_read }) });
      if (this.state.data) this.update({ data: { ...this.state.data, entries: this.state.data.entries.map(item => item.id === entry.id ? { ...item, is_read } : item) } });
    });
  }

  async markAllRead() {
    if (!this.state.data || this.writes) return false;
    const body = { through_seq: this.state.data.through_seq, feed_id: this.scope.feed_id || null, q: this.scope.q };
    return this.write('read-all', async () => { await this.request('/api/feeds/read', { method: 'POST', body: JSON.stringify(body) }); });
  }

  async save(entry: FeedEntry) {
    return this.write(`save:${entry.id}`, async () => {
      await this.request(`/api/feeds/entries/${entry.id}/save`, { method: 'POST' });
      if (this.state.data) this.update({ data: { ...this.state.data, entries: this.state.data.entries.map(item => item.url === entry.url ? { ...item, is_saved: true } : item) } });
      this.onSaved();
    });
  }

  async subscribe(url: string) {
    return this.write('subscribe', async () => { await this.request('/api/feeds/subscriptions', { method: 'POST', body: JSON.stringify({ url }) }); });
  }

  async unsubscribe(id: string) {
    return this.write(`unsubscribe:${id}`, async () => {
      await this.request(`/api/feeds/subscriptions/${id}`, { method: 'DELETE' });
      if (this.scope.feed_id === id) this.scope = { ...this.scope, feed_id: '' };
    });
  }

  async refreshFeeds() {
    return this.write('refresh', async () => {
      await this.request('/api/feeds/refresh', { method: 'POST', body: JSON.stringify({ feed_id: this.scope.feed_id || null }) });
      this.update({ notice: 'Refresh requested. New stories will appear when collection finishes.' });
    });
  }

  dispose() { this.disposed = true; this.controller.abort(); }
}
