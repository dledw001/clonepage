export type HNView = 'front' | 'top' | 'discussed';
export type HNWindow = '24h' | '7d' | '30d';
export type HNStory = {
  id: number; title: string; url: string; discussion_url: string; author: string;
  points: number; comments: number; created_at: string; rank: number | null;
  is_read: boolean; is_saved: boolean; article_id: string | null;
};
export type HNSnapshot = {
  view: HNView; window: HNWindow; stories: HNStory[]; fetched_at: string;
  stale: boolean; error: string; notice: string; limit: number;
};
export type HNScope = { view: HNView; window: HNWindow };
export type HNState = { scope: HNScope; data: HNSnapshot | null; loading: boolean; busy: string[]; error: string; notice: string };

export function storyAge(value: string, now = Date.now()): string {
  const seconds = Math.max(0, (now - Date.parse(value)) / 1000);
  if (!Number.isFinite(seconds)) return 'Time unavailable';
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

export function storyDomain(url: string) {
  try { return new URL(url).hostname.replace(/^www\./, ''); } catch { return ''; }
}

export class HackerNewsReader {
  state: HNState;
  private sequence = 0;
  private revision = 0;
  private writes = 0;
  private queued = false;
  private disposed = false;
  private lifetime = new AbortController();
  private load: AbortController | null = null;

  constructor(private publish: (state: HNState) => void, private fetcher: typeof fetch = fetch,
              private changed: (kind: 'read' | 'save') => void = () => {}, scope: HNScope = { view: 'front', window: '24h' }) {
    this.state = { scope, data: null, loading: false, busy: [], error: '', notice: '' };
  }

  private update(changes: Partial<HNState>) {
    if (!this.disposed) { this.state = { ...this.state, ...changes }; this.publish(this.state); }
  }

  private async request<T>(path: string, options: RequestInit = {}, signal?: AbortSignal): Promise<T> {
    const signals = [this.lifetime.signal, AbortSignal.timeout(35000)];
    if (signal) signals.push(signal);
    const response = await this.fetcher(path, { ...options, cache: 'no-store',
      headers: { 'Content-Type': 'application/json', ...options.headers }, signal: AbortSignal.any(signals) });
    if (!response.ok) {
      let message = 'Hacker News could not be loaded. Try again shortly.';
      try { const data = await response.json(); if (typeof data.detail === 'string') message = data.detail; } catch { /* Keep the safe fallback. */ }
      throw new Error(message);
    }
    return response.json();
  }

  setScope(changes: Partial<HNScope>) {
    const scope = { ...this.state.scope, ...changes };
    if (JSON.stringify(scope) === JSON.stringify(this.state.scope) && (this.state.loading || this.state.data)) return;
    this.load?.abort(); this.sequence++;
    this.update({ scope, data: null, loading: false, error: '', notice: '' });
    void this.refresh();
  }

  async refresh(force = false) {
    if (this.disposed || this.state.loading) return;
    if (this.writes) { this.queued = true; return; }
    const sequence = ++this.sequence, revision = this.revision;
    const controller = new AbortController(); this.load = controller;
    const current = () => !this.disposed && sequence === this.sequence;
    this.update({ loading: true });
    const params = new URLSearchParams({ ...this.state.scope, force: String(force) });
    try {
      const result = await this.request<HNSnapshot>(`/api/hacker-news?${params}`, {}, controller.signal);
      if (current() && revision === this.revision) this.update({ data: result, error: '' });
    } catch (error) {
      if (current() && revision === this.revision) this.update({ error: error instanceof Error ? error.message : 'Could not load Hacker News.' });
    } finally {
      if (current()) {
        this.update({ loading: false });
        if (this.queued && !this.writes) { this.queued = false; void this.refresh(); }
      }
    }
  }

  private replaceStory(id: number, changes: Partial<HNStory>) {
    if (this.state.data) this.update({ data: { ...this.state.data, stories: this.state.data.stories.map(story => story.id === id ? { ...story, ...changes } : story) } });
  }

  private async write(key: string, action: () => Promise<void>) {
    if (this.disposed || this.state.busy.includes(key)) return false;
    this.writes++; this.revision++; this.queued = true;
    this.update({ busy: [...this.state.busy, key], error: '', notice: '' });
    let success = false;
    try { await action(); success = true; }
    catch (error) { this.update({ error: error instanceof Error ? error.message : 'Could not update the story.' }); }
    finally {
      this.writes--; this.revision++;
      this.update({ busy: this.state.busy.filter(item => item !== key) });
      if (!this.writes && this.queued && !this.state.loading) {
        this.queued = false;
        const error = this.state.error;
        await this.refresh();
        if (error) this.update({ error });
      }
    }
    return success;
  }

  setRead(story: HNStory, is_read: boolean) {
    return this.write(`read:${story.id}`, async () => {
      await this.request(`/api/hacker-news/${story.id}/read`, { method: 'PATCH', body: JSON.stringify({ is_read }) });
      this.replaceStory(story.id, { is_read });
      this.changed('read');
    });
  }

  save(story: HNStory) {
    if (story.is_saved) return Promise.resolve(true);
    return this.write(`save:${story.id}`, async () => {
      const article = await this.request<{ id: string }>(`/api/hacker-news/${story.id}/save`, { method: 'POST' });
      this.replaceStory(story.id, { is_saved: true, article_id: article.id });
      this.update({ notice: 'Saved to your reading list. An offline copy will be prepared.' });
      this.changed('save');
    });
  }

  dispose() { this.disposed = true; this.sequence++; this.lifetime.abort(); this.load?.abort(); }
}
