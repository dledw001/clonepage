<script lang="ts">
  import { onMount } from 'svelte';
  import { ArrowLeft, ArrowUpRight, Download, RefreshCw } from '@lucide/svelte';

  let { articleId, theme }: { articleId: string; theme: 'light' | 'dark' } = $props();
  type SavedCopy = { id: string; title: string; url: string; copy_available: boolean; copy_status: string;
    copy_saved_at: string | null; copy_format: 'html' | 'pdf' | null; copy_size: number | null;
    copy_error: string | null; copy_warning: string | null; copy_retry_at: string | null };
  let info = $state<SavedCopy | null>(null);
  let error = $state('');
  let loading = $state(true);
  let refreshing = $state(false);
  let frame = $state<HTMLIFrameElement>();
  let frameHeight = $state(650);
  let observer: ResizeObserver | undefined;
  let resizeFrame: number | undefined;
  let controller = new AbortController();
  let sequence = 0;
  let disposed = false;
  const pending = $derived(info?.copy_status === 'pending' || info?.copy_status === 'processing');
  const contentUrl = $derived(`/api/reading/${articleId}/copy/content?${new URLSearchParams({ theme, version: info?.copy_saved_at ?? '' })}`);

  function fileSize(bytes: number) {
    return bytes < 1024 ? `${bytes} B` : bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  }

  async function load() {
    const current = ++sequence;
    try {
      const response = await fetch(`/api/reading/${articleId}/copy`, { cache: 'no-store', signal: AbortSignal.any([controller.signal, AbortSignal.timeout(12000)]) });
      if (!response.ok) throw new Error(response.status === 404 ? 'This saved article could not be found.' : 'Could not load the saved copy.');
      const result: SavedCopy = await response.json();
      if (!disposed && current === sequence) { info = result; error = ''; }
    } catch (failure) {
      if (!disposed && current === sequence) error = failure instanceof Error ? failure.message : 'Could not load the saved copy.';
    } finally {
      if (!disposed && current === sequence) loading = false;
    }
  }

  async function refreshCopy() {
    if (refreshing || pending) return;
    refreshing = true;
    sequence++;
    try {
      const response = await fetch(`/api/reading/${articleId}/copy/refresh`, { method: 'POST', signal: AbortSignal.any([controller.signal, AbortSignal.timeout(12000)]) });
      if (!response.ok) throw new Error();
      const result: SavedCopy = await response.json();
      if (!disposed) { info = result; error = ''; window.dispatchEvent(new Event('clonepage:reading-changed')); }
    } catch {
      if (!disposed) error = 'Could not request a new saved copy. Try again.';
    } finally {
      if (!disposed) refreshing = false;
    }
  }

  function fitFrame() {
    observer?.disconnect();
    if (resizeFrame !== undefined) cancelAnimationFrame(resizeFrame);
    resizeFrame = undefined;
    if (info?.copy_format !== 'html' || !frame?.contentDocument?.body) return;
    const body = frame.contentDocument.body;
    const scheduleResize = () => {
      if (resizeFrame !== undefined || disposed) return;
      // Measure natural content height, not a viewport-sized scrollHeight.
      // Apply the parent-frame update outside ResizeObserver delivery.
      resizeFrame = requestAnimationFrame(() => {
        resizeFrame = undefined;
        if (!disposed) frameHeight = Math.max(400, Math.min(150000, Math.ceil(body.getBoundingClientRect().height) + 2));
      });
    };
    scheduleResize();
    observer = new ResizeObserver(scheduleResize);
    observer.observe(body);
  }

  onMount(() => {
    void load();
    const poll = setInterval(() => { if (!document.hidden && !refreshing && (pending || info?.copy_retry_at)) void load(); }, 5000);
    const visible = () => { if (!document.hidden && !refreshing) void load(); };
    document.addEventListener('visibilitychange', visible);
    window.addEventListener('focus', visible);
    return () => { disposed = true; controller.abort(); observer?.disconnect(); if (resizeFrame !== undefined) cancelAnimationFrame(resizeFrame); clearInterval(poll); document.removeEventListener('visibilitychange', visible); window.removeEventListener('focus', visible); };
  });
</script>

<section class="saved-article">
  <a class="back" href="/"><ArrowLeft size={15} />Back to reading list</a>
  <header><div class="eyebrow">YOUR SAVED COPY</div><h1>{info?.title ?? 'Saved article'}</h1>
    {#if info?.copy_available}<p>Captured {info.copy_saved_at ? new Date(info.copy_saved_at).toLocaleString() : ''} · {info.copy_format === 'pdf' ? 'Original PDF' : 'Readable HTML'}{info.copy_size ? ` · ${fileSize(info.copy_size)}` : ''}</p>{/if}
    {#if info}<div class="actions">
      {#if info.copy_available}<a class="primary" href={`/api/reading/${articleId}/copy/download`}><Download size={15} />Download {info.copy_format === 'pdf' ? 'PDF' : 'HTML'}</a>{/if}
      <a href={info.url} target="_blank" rel="noopener noreferrer">Open original<ArrowUpRight size={14} /></a>
      <button disabled={refreshing || pending} onclick={refreshCopy}><RefreshCw size={14} />{pending || refreshing ? 'Saving copy...' : info.copy_available ? 'Refresh saved copy' : 'Retry saved copy'}</button>
    </div>{/if}
  </header>
  {#if error}<p class="notice" role="alert">{error}<button onclick={load}>Try again</button></p>{/if}
  {#if pending}<p class="notice" role="status">{info?.copy_available ? 'A new capture is being prepared. Your previous copy stays available below.' : 'Your saved copy is being prepared by the NAS.'}</p>{/if}
  {#if info?.copy_status === 'failed'}<p class="notice" role="status">{info.copy_available ? 'The latest capture failed. Your previous copy is kept.' : 'A saved copy could not be created.'} {info.copy_error}{info.copy_retry_at ? ' Another attempt is scheduled.' : ''}</p>{/if}
  {#if info?.copy_warning}<p class="notice">{info.copy_warning}</p>{/if}
  {#if info?.copy_available}
    {#if info.copy_format === 'pdf'}
      <iframe class="pdf-frame" title="Saved PDF" src={contentUrl}></iframe>
      <p class="hint">If your browser does not display the PDF, use Download PDF to open the saved file.</p>
    {:else}
      <iframe bind:this={frame} class="reader-frame" title="Saved article content" src={contentUrl} sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox" onload={fitFrame} style:height={`${frameHeight}px`}></iframe>
    {/if}
    <p class="hint">This copy is stored on your NAS. Download it to read without a connection to the NAS.</p>
  {:else if loading}<div class="empty">Loading saved copy...</div>
  {:else if info && !pending && info.copy_status !== 'failed'}<div class="empty">The saved file is unavailable. Use Retry saved copy to retrieve it again.</div>{/if}
</section>

<style>
  .saved-article { max-width: 1060px; margin: 50px auto 0; }
  .back { display: inline-flex; align-items: center; gap: 8px; color: var(--muted); font-size: 11px; text-decoration: none; }
  header { margin: 30px 0 24px; }
  .eyebrow { font-size: 9px; letter-spacing: 2px; color: var(--highlight); }
  h1 { margin: 16px 0; font-size: 34px; line-height: 1.3; overflow-wrap: anywhere; }
  header p, .hint { font-size: 11px; color: var(--muted); line-height: 1.8; }
  .actions { display: flex; flex-wrap: wrap; align-items: center; gap: 12px 20px; margin-top: 22px; }
  .actions a, .actions button { display: inline-flex; align-items: center; gap: 7px; border: 0; background: transparent; color: var(--highlight); padding: 0; text-decoration: none; font: inherit; font-size: 11px; cursor: pointer; }
  .actions .primary { padding: 10px 13px; border: 1px solid var(--line); background: var(--surface); border-radius: 8px; color: var(--text); }
  button:disabled { cursor: default; opacity: .5; }
  a:focus-visible, button:focus-visible { outline: 2px solid var(--highlight); outline-offset: 4px; }
  .notice { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 13px 16px; font-size: 12px; color: var(--warning); line-height: 1.8; overflow-wrap: anywhere; }
  .notice button { background: transparent; border: 0; color: var(--highlight); cursor: pointer; font: inherit; margin-left: 12px; }
  iframe { width: 100%; display: block; border: 1px solid var(--line); border-radius: 14px; background: var(--surface); }
  .pdf-frame { height: 78vh; min-height: 500px; }
  .empty { text-align: center; padding: 70px 20px; color: var(--muted); font-size: 13px; }
  @media(max-width:540px) { .saved-article { margin-top: 32px; } h1 { font-size: 27px; } }
</style>
