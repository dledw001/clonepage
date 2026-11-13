<script lang="ts">
  import { iconSizes } from '../lib/icons';
  import { onMount } from 'svelte';
  import { ChevronLeft, ChevronRight, Minus, Plus, RotateCcw } from '@lucide/svelte';
  import { PDFProgress, type PDFPosition, type ProgressState } from '../lib/pdf-progress';
  import type { PDFViewer } from 'pdfjs-dist/legacy/web/pdf_viewer.mjs';
  import workerUrl from 'pdfjs-dist/legacy/build/pdf.worker.min.mjs?url';

  let { articleId, version, url }: { articleId: string; version: string; url: string } = $props();
  let container: HTMLDivElement;
  let pages: HTMLDivElement;
  let viewer: PDFViewer | undefined;
  let progress: PDFProgress;
  let page = $state(1);
  let pageInput = $state<number | undefined>(1);
  let pageCount = $state(0);
  let ready = $state(false);
  let error = $state('');
  let sync = $state.raw<ProgressState>({ ready: false, status: 'loading', position: null, message: '' });
  let allowTracking = false;
  let currentPosition: PDFPosition | null = null;
  let changingLayout = $state(false);
  let layoutSequence = 0;
  let cancelLayoutWait: (() => void) | undefined;

  function interacted() { if (ready) allowTracking = true; }
  function goTo(number: number) {
    if (!viewer || !ready || !Number.isFinite(number)) return;
    allowTracking = true;
    viewer.currentPageNumber = Math.max(1, Math.min(pageCount, Math.trunc(number)));
    const [left, top] = viewer.getPageView(viewer.currentPageNumber - 1).viewport.convertToPdfPoint(0, 0);
    currentPosition = { page: viewer.currentPageNumber, page_count: pageCount, left, top };
    progress.note(currentPosition);
    pageInput = viewer.currentPageNumber;
  }
  function zoom(direction: number) {
    if (!viewer || !ready) return;
    void rescale(() => { if (viewer) viewer.currentScale = Math.min(3, Math.max(.3, viewer.currentScale * (direction > 0 ? 1.2 : 1 / 1.2))); });
  }
  function fitWidth() { if (viewer && ready) void rescale(() => { if (viewer) viewer.currentScaleValue = 'page-width'; }); }
  function restore(position: PDFPosition | null) {
    if (!viewer || !ready) return;
    allowTracking = false;
    currentPosition = position;
    if (position) viewer.scrollPageIntoView({ pageNumber: Math.min(position.page, pageCount),
      destArray: [null, { name: 'XYZ' }, position.left, position.top, null], allowNegativeOffset: true, ignoreDestinationZoom: true });
    else viewer.scrollPageIntoView({ pageNumber: 1 });
  }
  function waitForPageSize(number: number): Promise<void> {
    cancelLayoutWait?.();
    const view = viewer?.getPageView(number - 1);
    if (!view) return Promise.resolve();
    return new Promise(resolve => {
      let timer: ReturnType<typeof setTimeout>;
      const observer = new ResizeObserver(check);
      const finish = () => { clearTimeout(timer); observer.disconnect(); cancelLayoutWait = undefined; resolve(); };
      function check() {
        const box = view.div.getBoundingClientRect();
        if (Math.abs(box.height - Math.floor(view.viewport.height)) <= 2 &&
            Math.abs(box.width - Math.floor(view.viewport.width)) <= 2) finish();
      }
      cancelLayoutWait = finish;
      timer = setTimeout(finish, 2000);
      observer.observe(view.div);
      check();
    });
  }
  async function rescale(change: () => void) {
    const sequence = ++layoutSequence;
    const position = currentPosition;
    changingLayout = true;
    allowTracking = false;
    change();
    // Wait for the actual scaled page box, rather than assuming a fixed number of frames.
    await waitForPageSize(position?.page ?? 1);
    if (!viewer || sequence !== layoutSequence) return;
    restore(position);
    await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
    if (sequence === layoutSequence) changingLayout = false;
  }
  async function resumeSaved() { restore(await progress.load(true)); }
  async function startOver() { restore(null); await progress.reset(); }

  onMount(() => {
    let disposed = false;
    const abort = new AbortController();
    for (const name of ['wheel', 'touchstart', 'pointerdown', 'keydown']) container.addEventListener(name, interacted, { passive: true, signal: abort.signal });
    let loadingTask: import('pdfjs-dist/legacy/build/pdf.mjs').PDFDocumentLoadingTask | undefined;
    let resize: ResizeObserver | undefined;
    let lastWidth = 0;
    let storage: Storage | undefined;
    try { storage = localStorage; } catch { /* Private browsing can limit local storage. */ }
    progress = new PDFProgress(articleId, version, next => sync = next, undefined, storage,
      () => window.dispatchEvent(new Event('clonepage:reading-changed')));

    const start = async () => {
      try {
        const position = await progress.load();
        if (disposed) return;
        const pdfjs = await import('pdfjs-dist/legacy/build/pdf.mjs');
        pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;
        const { EventBus, PDFViewer, PDFLinkService, LinkTarget } = await import('pdfjs-dist/legacy/web/pdf_viewer.mjs');
        await import('pdfjs-dist/legacy/web/pdf_viewer.css');
        if (disposed) return;
        const eventBus = new EventBus();
        const links = new PDFLinkService({ eventBus, externalLinkTarget: LinkTarget.BLANK, externalLinkRel: 'noopener noreferrer' });
        const viewerOptions = { container, viewer: pages, eventBus, linkService: links, abortSignal: abort.signal,
          maxCanvasPixels: 2097152, maxCanvasDim: 4096, enableDetailCanvas: false,
          annotationMode: pdfjs.AnnotationMode.ENABLE, enableAutoLinking: false, imageResourcesPath: '/pdfjs/web/images/' };
        viewer = new PDFViewer(viewerOptions);
        links.setViewer(viewer);
        eventBus.on('pagechanging', (event: { pageNumber: number }) => { page = event.pageNumber; pageInput = page; });
        eventBus.on('updateviewarea', (event: { location: { pageNumber: number; left: number; top: number } }) => {
          if (disposed || !ready || changingLayout || container.clientWidth !== lastWidth) return;
          if (!allowTracking && currentPosition) return;
          const { pageNumber, left, top } = event.location;
          currentPosition = { page: pageNumber, page_count: pageCount, left: Math.round(left * 100) / 100, top: Math.round(top * 100) / 100 };
          if (allowTracking) progress.note(currentPosition);
        });
        eventBus.on('pagesinit', async () => {
          if (!viewer || disposed) return;
          try {
            viewer.currentScaleValue = 'page-width';
            // Page-size discovery can move later page boxes; restore after that discovery.
            await viewer.pagesPromise;
            await waitForPageSize(position?.page ?? 1);
            if (!viewer || disposed) return;
            ready = true;
            restore(position);
            lastWidth = container.clientWidth;
            resize = new ResizeObserver(() => {
              if (viewer && Math.abs(container.clientWidth - lastWidth) > 1) {
                lastWidth = container.clientWidth;
                if (viewer.currentScaleValue === 'page-width') void rescale(() => { if (viewer) viewer.currentScaleValue = 'page-width'; });
              }
            });
            resize.observe(container);
          } catch {
            if (!disposed) error = 'The PDF layout could not be prepared. Download the original or reopen the reader.';
          }
        });
        eventBus.on('pagerendered', (event: { error?: unknown }) => { if (event.error && !disposed) error = 'A PDF page could not be rendered. Try downloading the original PDF.'; });
        loadingTask = pdfjs.getDocument({ url, enableXfa: false,
          cMapUrl: '/pdfjs/cmaps/', cMapPacked: true, standardFontDataUrl: '/pdfjs/standard_fonts/',
          wasmUrl: '/pdfjs/wasm/', iccUrl: '/pdfjs/iccs/' });
        loadingTask.onPassword = () => {
          if (!disposed) error = 'This PDF is password protected. Download it to open it in a PDF reader.';
          void loadingTask?.destroy();
        };
        const document = await loadingTask.promise;
        if (disposed) { void loadingTask.destroy(); return; }
        pageCount = document.numPages;
        viewer.setDocument(document);
        links.setDocument(document);
      } catch {
        if (!disposed && !error) error = 'This PDF could not be opened here. Download the original, or reopen the reader to try again.';
      }
    };
    void start();
    const flush = () => { void progress.flush(true); };
    const visible = () => {
      if (document.hidden) flush();
      else if (ready) void progress.load().then(position => { if (!disposed && !document.hidden && ready) restore(position); });
    };
    document.addEventListener('visibilitychange', visible);
    window.addEventListener('pagehide', flush);
    const interval = setInterval(() => { void progress.flush(); }, 3000);
    return () => {
      disposed = true; ready = false; layoutSequence++; cancelLayoutWait?.(); progress.dispose(); abort.abort(); resize?.disconnect();
      document.removeEventListener('visibilitychange', visible); window.removeEventListener('pagehide', flush); clearInterval(interval);
      viewer?.setDocument(null); viewer = undefined; void loadingTask?.destroy();
    };
  });
</script>

<div class="pdf-reader" aria-label="PDF reader">
  <div class="toolbar" role="group" aria-label="PDF controls">
    <div class="page-controls">
      <button onclick={() => goTo(page - 1)} disabled={!ready || changingLayout || page <= 1} aria-label="Previous PDF page"><ChevronLeft size={iconSizes.action} /></button>
      <form onsubmit={event => { event.preventDefault(); goTo(Number(pageInput)); }}>
        <label>Page <input aria-label="PDF page number" type="number" min="1" max={pageCount || 1} bind:value={pageInput} disabled={!ready || changingLayout} /></label><span>of {pageCount || '…'}</span>
      </form>
      <button onclick={() => goTo(page + 1)} disabled={!ready || changingLayout || page >= pageCount} aria-label="Next PDF page"><ChevronRight size={iconSizes.action} /></button>
    </div>
    <div class="zoom-controls">
      <button onclick={() => zoom(-1)} disabled={!ready || changingLayout} aria-label="Zoom out PDF"><Minus size={iconSizes.action} /></button>
      <button class="fit" onclick={fitWidth} disabled={!ready || changingLayout}>Fit width</button>
      <button onclick={() => zoom(1)} disabled={!ready || changingLayout} aria-label="Zoom in PDF"><Plus size={iconSizes.action} /></button>
      <button class="restart" onclick={startOver} disabled={!ready || changingLayout || !sync.ready || sync.status === 'conflict'} title="Clear saved reading position"><RotateCcw size={iconSizes.inline} />Start over</button>
    </div>
  </div>
  <div class="status" aria-live="polite">
    {#if !ready && !error}Opening PDF…{:else if sync.status === 'saving'}Saving position…{:else if sync.status === 'pending'}Position waiting to save{:else if sync.status === 'saved'}Position saved across your devices{:else if sync.message}{sync.message}{/if}
    {#if sync.status === 'conflict' && sync.ready}<button onclick={resumeSaved}>Resume saved position</button><button onclick={() => progress.keepCurrent(currentPosition)}>Keep this position</button>
    {:else if sync.status === 'conflict'}<button onclick={() => window.location.reload()}>Reopen PDF</button>
    {:else if sync.status === 'error'}<button onclick={() => sync.ready ? progress.flush() : resumeSaved()}>Retry progress sync</button>{/if}
  </div>
  {#if error}<p class="error" role="alert">{error}</p>{/if}
  <div class="viewer-shell" class:hidden={!!error}>
    <!-- svelte-ignore a11y_no_noninteractive_tabindex (This labelled scroll region supports keyboard scrolling.) -->
    <div class="viewer-scroll" bind:this={container} role="region" aria-label="PDF document pages" tabindex="0">
      <div class="pdfViewer" bind:this={pages}></div>
    </div>
  </div>
</div>

<style>
  .pdf-reader { border: 1px solid var(--line); border-radius: 14px; background: var(--surface); overflow: hidden; }
  .toolbar, .page-controls, .zoom-controls, form, label { display: flex; align-items: center; gap: 8px; }
  .toolbar { justify-content: space-between; flex-wrap: wrap; gap: 10px; padding: 12px; border-bottom: 1px solid var(--line); }
  button, input { font: inherit; font-size: 12px; color: var(--text); }
  button { display: inline-flex; align-items: center; justify-content: center; gap: 6px; min-width: 38px; min-height: 38px; border: 1px solid var(--line); border-radius: 7px; background: var(--bg); cursor: pointer; }
  button:disabled { opacity: .4; cursor: default; }
  button:focus-visible, input:focus-visible, .viewer-scroll:focus-visible { outline: 2px solid var(--highlight); outline-offset: -2px; }
  form, label { gap: 6px; color: var(--muted); font-size: 11px; }
  input { width: 65px; min-width: 0; padding: 8px; background: var(--bg); border: 1px solid var(--line); border-radius: 7px; text-align: center; }
  .fit, .restart { padding: 0 10px; }
  .status { min-height: 33px; padding: 8px 14px; font-size: 10px; color: var(--muted); line-height: 1.7; }
  .status button { padding: 3px 7px; margin-left: 8px; min-height: 30px; font-size: 10px; color: var(--highlight); }
  .viewer-shell { position: relative; height: 76vh; height: 76dvh; min-height: 380px; background: #34363b; }
  .viewer-scroll { position: absolute; inset: 0; overflow: auto; overscroll-behavior: contain; touch-action: pan-x pan-y pinch-zoom; }
  .viewer-scroll :global(.pdfViewer .page) { min-height: 0; max-width: none; padding: 0; display: block; }
  .viewer-scroll :global(.pdfViewer) { --pdfViewer-padding-bottom: 8px; }
  .hidden { display: none; }
  .error { color: var(--warning); font-size: 12px; line-height: 1.8; padding: 20px; margin: 0; }
  @media(max-width:540px) { .toolbar { padding: 9px; } .page-controls { flex: 1; justify-content: center; } .zoom-controls { width: 100%; justify-content: center; flex-wrap: wrap; } button { min-height: 44px; min-width: 38px; } .status { font-size: 11px; } }
</style>
