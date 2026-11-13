export type PDFPosition = { page: number; page_count: number; left: number; top: number };
export type ProgressState<P = PDFPosition> = { ready: boolean; status: 'loading' | 'saved' | 'pending' | 'saving' | 'error' | 'conflict'; position: P | null; message: string };
type Snapshot<P> = { copy_version: string; revision: number; position: P | null };
type LocalPosition<P> = Snapshot<P> & { pending: boolean };

export class ReadingProgress<Position = PDFPosition> {
  private revision = 0;
  private dirty = false;
  private sequence = 0;
  private inFlight: Promise<void> | null = null;
  private disposed = false;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private state: ProgressState<Position> = { ready: false, status: 'loading', position: null, message: '' };
  private key: string;

  constructor(private id: string, private version: string, private publish: (state: ProgressState<Position>) => void,
              private request: typeof fetch = (...args) => fetch(...args), private storage?: Pick<Storage, 'getItem' | 'setItem'>,
              private onSaved?: () => void, private documentKind: 'pdf' | 'html' = 'pdf') {
    this.key = `clonepage-${documentKind}-progress:${id}`;
  }

  private get path() { return `/api/reading/${this.id}/${this.documentKind === 'pdf' ? 'progress' : 'article-progress'}`; }
  private get label() { return this.documentKind === 'pdf' ? 'PDF' : 'article'; }

  private emit() { if (!this.disposed) this.publish({ ...this.state }); }
  private backup() {
    try { this.storage?.setItem(this.key, JSON.stringify({ copy_version: this.version, revision: this.revision,
      position: this.state.position, pending: this.dirty } satisfies LocalPosition<Position>)); } catch { /* NAS saving remains available. */ }
  }

  async load(discardLocal = false): Promise<Position | null> {
    if (this.inFlight) await this.inFlight;
    const sequence = this.sequence;
    try {
      const response = await this.request(this.path, { cache: 'no-store', signal: AbortSignal.timeout(10000) });
      if (!response.ok) throw new Error();
      const snapshot: Snapshot<Position> = await response.json();
      if (snapshot.copy_version !== this.version) {
        this.state = { ...this.state, ready: false, status: 'conflict', message: `This saved ${this.label} changed. Reopen it to continue.` };
        this.emit(); return null;
      }
      if (sequence !== this.sequence || this.disposed) return this.state.position;
      this.revision = snapshot.revision;
      this.state = { ready: true, status: 'saved', position: snapshot.position, message: '' };
      this.dirty = false;
      if (!discardLocal) {
        try {
          const local: LocalPosition<Position> = JSON.parse(this.storage?.getItem(this.key) ?? 'null');
          if (local?.copy_version === this.version && local.pending && local.revision === this.revision) {
            this.state.position = local.position;
            this.dirty = true;
            this.sequence++;
            this.state.status = 'pending';
          }
        } catch { /* An invalid/unavailable local backup does not block reading. */ }
      }
      this.backup(); this.emit();
      return this.state.position;
    } catch {
      this.state = { ...this.state, ready: false, status: 'error', message: `Reading progress could not be loaded. You can still read or download the ${this.label}.` };
      this.emit(); return null;
    }
  }

  note(position: Position | null) {
    if (!this.state.ready || this.disposed || this.state.status === 'conflict') return;
    if (JSON.stringify(position) === JSON.stringify(this.state.position)) return;
    this.state.position = position;
    this.dirty = true;
    this.sequence++;
    this.state.status = 'pending';
    this.state.message = '';
    this.backup(); this.emit();
    clearTimeout(this.timer);
    this.timer = setTimeout(() => { void this.flush(); }, 800);
  }

  flush(keepalive = false): Promise<void> {
    clearTimeout(this.timer);
    if (this.inFlight) return this.inFlight;
    if (!this.dirty || !this.state.ready || this.state.status === 'conflict') return Promise.resolve();
    const sequence = this.sequence;
    const position = this.state.position;
    this.state.status = 'saving'; this.emit();
    let succeeded = false;
    this.inFlight = (async () => {
      try {
        const response = await this.request(this.path, { method: 'POST',
          headers: { 'Content-Type': 'application/json' }, keepalive,
          body: JSON.stringify({ copy_version: this.version, revision: this.revision, position }), signal: AbortSignal.timeout(10000) });
        if (response.status === 409) {
          const error = await response.json().catch(() => null);
          this.state.status = 'conflict';
          if (error?.detail?.reason === 'copy_changed') this.state.ready = false;
          this.state.message = error?.detail?.reason === 'copy_changed' ? `This saved ${this.label} changed. Reopen it to continue.` : 'A newer reading position was saved in another session. Resume that position or explicitly keep this one.';
          this.emit(); return;
        }
        if (!response.ok) throw new Error();
        const snapshot: Snapshot<Position> = await response.json();
        this.revision = snapshot.revision;
        this.dirty = sequence !== this.sequence;
        this.state.status = this.dirty ? 'pending' : 'saved';
        this.state.message = '';
        this.backup(); this.emit();
        this.onSaved?.();
        succeeded = true;
      } catch {
        this.state.status = 'error';
        this.state.message = 'Progress is kept on this device. Reconnect to the NAS to save it across devices.';
        this.backup(); this.emit();
      } finally {
        this.inFlight = null;
        // A final movement during an in-flight save must also survive navigation away.
        if (succeeded && this.dirty) void this.flush(keepalive || this.disposed);
      }
    })();
    return this.inFlight;
  }

  async keepCurrent(position: Position | null = this.state.position) {
    await this.load(true);
    if (this.state.ready) {
      // Explicitly choosing this position is allowed to supersede the latest server revision.
      this.state.position = position; this.dirty = true; this.sequence++;
      this.backup(); await this.flush();
    }
  }

  async reset() {
    if (this.inFlight) await this.inFlight;
    if (!this.state.ready || this.state.status === 'conflict') return false;
    this.state.position = null; this.dirty = true; this.sequence++;
    this.backup(); await this.flush();
    return this.state.status === 'saved';
  }

  dispose() {
    clearTimeout(this.timer);
    this.disposed = true;
    void this.flush(true);
  }
}

// Preserve the existing PDF interface and storage keys.
export class PDFProgress extends ReadingProgress<PDFPosition> {}
