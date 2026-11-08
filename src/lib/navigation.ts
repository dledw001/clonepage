/** Internal navigation keeps browser history and scroll positions together. */
export function isPlainClick(event: MouseEvent) {
  return !event.defaultPrevented && event.button === 0 && !event.ctrlKey && !event.metaKey && !event.shiftKey && !event.altKey;
}

type Entry = { id: number; fromHome?: number };
type Position = { left: number; top: number };

export class PageNavigation {
  private entries = new Map<number, Position>();
  private current: Entry = { id: 0 };
  private nextId = 0;
  private revision = 0;
  private cancelRestore = () => {};
  private previousRestoration: ScrollRestoration = 'auto';

  constructor(private browser: Window, private render: () => Promise<void>) {}

  start() {
    this.previousRestoration = this.browser.history.scrollRestoration;
    this.browser.history.scrollRestoration = 'manual';
    this.current = { id: ++this.nextId };
    this.browser.history.replaceState({ ...this.browser.history.state, clonepage: this.current }, '');
    const pop = () => {
      this.save();
      const entry = this.browser.history.state?.clonepage as Entry | undefined;
      this.current = entry ?? { id: ++this.nextId };
      if (!entry) this.browser.history.replaceState({ ...this.browser.history.state, clonepage: this.current }, '');
      void this.show(this.entries.get(this.current.id) ?? { left: 0, top: 0 });
    };
    this.browser.addEventListener('popstate', pop);
    return () => {
      this.revision++;
      this.cancelRestore();
      this.browser.removeEventListener('popstate', pop);
      this.browser.history.scrollRestoration = this.previousRestoration;
    };
  }

  private save() {
    this.entries.set(this.current.id, { left: this.browser.scrollX, top: this.browser.scrollY });
  }

  async go(path: string) {
    this.save();
    const currentPath = this.browser.location.pathname + (this.browser.location.search ?? '') + (this.browser.location.hash ?? '');
    if (currentPath !== path) {
      const fromHome = this.browser.location.pathname === '/' && path.startsWith('/reading/') ? this.current.id : undefined;
      this.current = { id: ++this.nextId, fromHome };
      this.browser.history.pushState({ clonepage: this.current }, '', path);
    }
    await this.show({ left: 0, top: 0 });
  }

  backToList() {
    if (this.current.fromHome !== undefined && this.entries.has(this.current.fromHome)) this.browser.history.back();
    else void this.go('/'); // Direct links and new tabs have no originating list.
  }

  private async show(position: Position) {
    const revision = ++this.revision;
    this.cancelRestore();
    await this.render();
    if (revision !== this.revision) return;
    const browser = this.browser;
    let observer: ResizeObserver | undefined;
    let frame: number | undefined;
    let timeout: number | undefined;
    const events = ['wheel', 'touchstart', 'pointerdown', 'keydown'] as const;
    const cancel = () => {
      observer?.disconnect();
      if (frame !== undefined) browser.cancelAnimationFrame(frame);
      if (timeout !== undefined) browser.clearTimeout(timeout);
      for (const event of events) browser.removeEventListener(event, cancel);
    };
    this.cancelRestore = cancel;
    const restore = () => {
      browser.scrollTo({ ...position, behavior: 'instant' });
      if (Math.abs(browser.scrollY - position.top) < 2) cancel();
    };
    // A reader iframe may not reach its full height until its content loads.
    observer = new ResizeObserver(() => {
      if (frame !== undefined) browser.cancelAnimationFrame(frame);
      frame = browser.requestAnimationFrame(restore);
    });
    observer.observe(browser.document.body);
    for (const event of events) browser.addEventListener(event, cancel, { passive: true });
    timeout = browser.setTimeout(cancel, 10000);
    restore();
  }
}

export function webSearchUrl(query: string) {
  const text = query.trim();
  const isUrl = /^(https?:\/\/|localhost(?::\d+)?(?:\/|$)|(?:[\w-]+\.)+[\w-]{2,}(?::\d+)?(?:\/|$))/i.test(text);
  return isUrl ? (/^https?:\/\//i.test(text) ? text : `https://${text}`)
    : `https://www.google.com/search?q=${encodeURIComponent(text)}`;
}
