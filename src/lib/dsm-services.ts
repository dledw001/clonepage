import type { ShortcutDraft, ShortcutSnapshot } from './shortcuts';

export type DSMConnectionInfo = { connected: boolean; expires_in: number; dsm_url: string; domain: string; upstream_hosts: string[] };
export type DSMOptions = { templates: { id: string; name: string; hostname: string; access_control: boolean }[];
  certificates: { id: string; name: string; expires: string }[] };
export type ServiceDraft = Omit<ShortcutDraft, 'action' | 'value'> & { subdomain: string; upstream: string; template_id: string; certificate_id: string };
export type ServicePreview = { preview_id: string; url: string; upstream: string; template: string; resuming: boolean; name: string; access_control: boolean };
export type ServiceResult = { ok: boolean; stage: string; url?: string; error?: string; shortcuts?: ShortcutSnapshot; disconnected: boolean };
export type DSMState = { connection: DSMConnectionInfo | null; options: DSMOptions | null; preview: ServicePreview | null;
  result: ServiceResult | null; busy: boolean; error: string };

export class DSMServiceClient {
  state: DSMState = { connection: null, options: null, preview: null, result: null, busy: false, error: '' };
  private csrf = '';
  private revision = 0;
  private disposed = false;
  private controller = new AbortController();
  constructor(private publish: (state: DSMState) => void, private fetcher: typeof fetch = (...args) => fetch(...args)) {}
  private update(changes: Partial<DSMState>) { if (!this.disposed) { this.state = { ...this.state, ...changes }; this.publish(this.state); } }

  private async request<T>(path: string, body?: object, revision = this.revision): Promise<T> {
    const response = await this.fetcher(`/api/dsm/${path}`, { method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin', cache: 'no-store',
      headers: { 'Content-Type': 'application/json', ...(body === undefined ? {} : { 'X-Clonepage-CSRF': this.csrf }) },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.any([this.controller.signal, AbortSignal.timeout(path === 'services' ? 120000 : 45000)]) });
    if (!response.ok) {
      let message = 'The DSM request could not be completed. Try again.';
      try {
        const result = await response.json();
        if (typeof result.detail === 'string') message = result.detail;
        else if (Array.isArray(result.detail)) message = 'Check the service fields and try again.';
      } catch { /* Never display a raw DSM response. */ }
      if (response.status === 401 && revision === this.revision) this.update({ connection: this.state.connection ? { ...this.state.connection, connected: false } : null, options: null, preview: null });
      throw new Error(message);
    }
    return response.json();
  }

  private async connection(revision = this.revision) {
    const result = await this.request<DSMConnectionInfo & { csrf: string }>('connection', undefined, revision);
    const { csrf: _, ...connection } = result;
    if (revision === this.revision && !this.disposed) { this.csrf = result.csrf; this.update({ connection }); }
    return connection;
  }

  async refresh() {
    if (this.state.busy || this.disposed) return;
    const revision = ++this.revision;
    try {
      const info = await this.connection(revision);
      if (revision !== this.revision) return;
      if (!info.connected) this.update({ options: null, preview: null });
      else if (!this.state.options) {
        this.update({ busy: true });
        const options = await this.request<DSMOptions>('options', {}, revision);
        if (revision === this.revision) this.update({ options });
      }
    } catch (error) { if (revision === this.revision) this.update({ error: error instanceof Error ? error.message : 'Could not check the DSM connection.' }); }
    finally { if (revision === this.revision) this.update({ busy: false }); }
  }

  async connect(username: string, password: string, otp: string) {
    if (this.state.busy || this.disposed) return;
    ++this.revision;
    this.update({ busy: true, error: '', preview: null, result: null });
    try {
      await this.connection();
      const info = await this.request<{ connected: boolean; expires_in: number }>('connection', { username, password, otp });
      password = ''; otp = '';
      this.update({ connection: this.state.connection ? { ...this.state.connection, ...info } : null });
      const options = await this.request<DSMOptions>('options', {});
      this.update({ options });
    } catch (error) { this.update({ error: error instanceof Error ? error.message : 'Could not connect to DSM.' }); }
    finally { this.update({ busy: false }); }
  }

  async preview(draft: ServiceDraft) {
    if (this.state.busy || this.disposed) return;
    ++this.revision;
    this.update({ busy: true, error: '', preview: null, result: null });
    try { this.update({ preview: await this.request<ServicePreview>('preview', draft) }); }
    catch (error) { this.update({ error: error instanceof Error ? error.message : 'Could not review the service.' }); }
    finally { this.update({ busy: false }); }
  }

  clearPreview() { if (!this.state.busy) this.update({ preview: null, error: '', result: null }); }

  async apply() {
    if (this.state.busy || !this.state.preview || this.disposed) return null;
    ++this.revision;
    this.update({ busy: true, error: '' });
    try {
      const result = await this.request<ServiceResult>('services', { preview_id: this.state.preview.preview_id });
      this.update({ result, preview: null, options: null, connection: this.state.connection ? { ...this.state.connection, connected: false } : null,
        error: result.ok ? '' : result.error ?? 'Setup did not finish. Connect again to resume.' });
      return result;
    } catch (error) {
      this.update({ error: error instanceof Error ? error.message : 'Could not complete service setup.' });
      // A lost response can still mean DSM changed. Reconnect and preview to reconcile it.
      await this.refreshAfterFailure();
      return null;
    } finally { this.update({ busy: false }); }
  }

  private async refreshAfterFailure() {
    try { await this.connection(); } catch { /* Keep the original error visible. */ }
    this.update({ preview: null });
  }

  async disconnect() {
    if (this.state.busy || this.disposed) return;
    ++this.revision;
    this.update({ busy: true });
    try {
      if (this.csrf) await this.request('disconnect', {});
      this.update({ connection: this.state.connection ? { ...this.state.connection, connected: false } : null, options: null, preview: null, error: '' });
    } catch (error) { this.update({ error: error instanceof Error ? error.message : 'Could not disconnect. The connection will expire automatically.' }); }
    finally { this.update({ busy: false }); }
  }

  dispose() {
    this.disposed = true; ++this.revision; this.controller.abort();
    if (this.csrf && this.state.connection?.connected) void this.fetcher('/api/dsm/disconnect', { method: 'POST', credentials: 'same-origin', keepalive: true,
      headers: { 'Content-Type': 'application/json', 'X-Clonepage-CSRF': this.csrf }, body: '{}' }).catch(() => {});
    this.csrf = '';
  }
}
