<script lang="ts">
  import { iconSizes } from '../lib/icons';
  import { onMount } from 'svelte';
  import PDFReader from './PDFReader.svelte';
  import { ReadingProgress, type ProgressState } from '../lib/pdf-progress';
  import { captureHTMLPosition, restoreHTMLPosition, listenForReadingInput, type ArticleGeometry, type HTMLPosition } from '../lib/html-progress';
  import { ArrowLeft, ArrowUpRight, Download, RefreshCw } from '@lucide/svelte';

  let { articleId, theme, onback }: { articleId: string; theme: 'light' | 'dark'; onback?: (event: MouseEvent) => void } = $props();
  type SavedCopy = { source_kind?: 'url' | 'upload'; upload_filename?: string | null; id: string; title: string; url: string; copy_available: boolean; copy_status: string;
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

  let progressToolbar=$state<HTMLDivElement>();
  let articleProgress: ReadingProgress<HTMLPosition> | undefined;
  let progressVersion = '';
  let stopFrameInputs=()=>{};
  function articleInteracted(){userInteracted=true;restoreObserver?.disconnect();}
  let articleSync = $state.raw<ProgressState<HTMLPosition>>({ready:false,status:'loading',position:null,message:''});
  let userInteracted = false;
  let restoring = false;
  let restoreObserver: ResizeObserver | undefined;
  let restoreDeadline = 0;
  let scrollFrame: number | undefined;
  let restoreEnd: ReturnType<typeof setTimeout> | undefined;

  function articleGeometry(): ArticleGeometry | null {
    const body = frame?.contentDocument?.body;
    if (!body || info?.copy_format !== 'html') return null;
    const root = body.querySelector('article') ?? body;
    const blocks = Array.from(root.querySelectorAll('h1,h2,h3,h4,p,li,pre,blockquote,figure,table')).slice(0,100001)
      .map(element => { const box=element.getBoundingClientRect(); return {top:box.top,height:box.height}; });
    return {height:body.getBoundingClientRect().height,viewport:Math.max(1,window.innerHeight-(progressToolbar?.offsetHeight??0)-20),blocks};
  }

  function currentArticlePosition(): HTMLPosition | null {
    const geometry=articleGeometry();
    if (!geometry || !frame) return null;
    return captureHTMLPosition(-frame.getBoundingClientRect().top+(progressToolbar?.offsetHeight??0)+20,geometry);
  }

  function noteArticlePosition() {
    if (restoring || !userInteracted || !articleSync.ready || !frame) return;
    const position=currentArticlePosition();
    if (position && (position.fraction>0.005 || articleSync.position)) articleProgress?.note(position);
  }

  function restoreArticlePosition(position: HTMLPosition | null) {
    const geometry=articleGeometry();
    if (!geometry || !frame || disposed) return;
    restoring=true;
    const top=Math.max(0,window.scrollY+frame.getBoundingClientRect().top+restoreHTMLPosition(position,geometry)-(progressToolbar?.offsetHeight??0)-20);
    window.scrollTo({top,behavior:'instant'});
    requestAnimationFrame(()=>requestAnimationFrame(()=>{restoring=false;}));
  }

  async function setupArticleProgress() {
    const version=info?.copy_saved_at;
    if (!version || info?.copy_format!=='html' || !frame?.contentDocument?.body) return;
    const reloaded=Boolean(articleProgress&&progressVersion===version);
    if (!reloaded) {
      articleProgress?.dispose();
      progressVersion=version;
      let storage: Storage|undefined;
      try {storage=window.localStorage;} catch { /* NAS saving does not require local storage. */ }
      articleProgress=new ReadingProgress<HTMLPosition>(articleId,version,next=>articleSync=next,undefined,storage,
        ()=>window.dispatchEvent(new Event('clonepage:reading-changed')),'html');
      await articleProgress.load();
    }
    if (disposed || progressVersion!==version || !articleSync.ready) return;
    const position=articleSync.position;
    // Restore only before the user starts interacting; theme reloads retain the
    // current local position. Late images can settle for a short bounded period.
    requestAnimationFrame(()=>requestAnimationFrame(()=>{
      if(!disposed&&progressVersion===version&&(reloaded||!userInteracted)&&position)restoreArticlePosition(position);
    }));
    restoreObserver?.disconnect();clearTimeout(restoreEnd);
    restoreDeadline=Date.now()+2500;
    restoreObserver=new ResizeObserver(()=>{
      if(!userInteracted&&Date.now()<restoreDeadline&&position)requestAnimationFrame(()=>restoreArticlePosition(position));
    });
    restoreObserver.observe(frame.contentDocument.body);
    restoreEnd=setTimeout(()=>restoreObserver?.disconnect(),2500);
    if(articleSync.status==='pending')void articleProgress?.flush();
  }

  function articleFrameLoaded(){
    stopFrameInputs();
    if(frame?.contentDocument)stopFrameInputs=listenForReadingInput(frame.contentDocument,articleInteracted);
    fitFrame();void setupArticleProgress();
  }
  async function resumeSavedPosition(){const position=await articleProgress?.load(true);if(articleSync.ready)restoreArticlePosition(position??null);}
  async function startArticleOver(){if(await articleProgress?.reset())restoreArticlePosition(null);}

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
    const stopWindowInputs=listenForReadingInput(window,articleInteracted);
    const positionTimer=setInterval(()=>{if(!document.hidden){noteArticlePosition();void articleProgress?.flush();}},15000);
    const scroll=()=>{if(scrollFrame===undefined)scrollFrame=requestAnimationFrame(()=>{scrollFrame=undefined;noteArticlePosition();});};
    const leave=()=>{noteArticlePosition();void articleProgress?.flush(true);};
    const hide=()=>{if(document.hidden)leave();};
    window.addEventListener('scroll',scroll,{passive:true});
    window.addEventListener('pagehide',leave);
    document.addEventListener('visibilitychange',hide);
    const poll = setInterval(() => { if (!document.hidden && !refreshing && (pending || info?.copy_retry_at)) void load(); }, 5000);
    const visible = () => { if (!document.hidden && !refreshing) void load(); };
    document.addEventListener('visibilitychange', visible);
    window.addEventListener('focus', visible);
    return () => { noteArticlePosition();articleProgress?.dispose();restoreObserver?.disconnect();clearTimeout(restoreEnd);
      if(scrollFrame!==undefined)cancelAnimationFrame(scrollFrame);
      window.removeEventListener('scroll',scroll);stopWindowInputs();stopFrameInputs();clearInterval(positionTimer);window.removeEventListener('pagehide',leave);document.removeEventListener('visibilitychange',hide);
      disposed = true; controller.abort(); observer?.disconnect(); if (resizeFrame !== undefined) cancelAnimationFrame(resizeFrame); clearInterval(poll); document.removeEventListener('visibilitychange', visible); window.removeEventListener('focus', visible); };
  });
</script>

<section class="saved-article">
  <a class="back" href="/" onclick={onback}><ArrowLeft size={iconSizes.action} />Back to reading list</a>
  <header><div class="eyebrow">YOUR SAVED COPY</div><h1>{info?.title ?? 'Saved article'}</h1>
    {#if info?.copy_available}<p>{info.source_kind === 'upload' ? 'Uploaded' : 'Captured'} {info.copy_saved_at ? new Date(info.copy_saved_at).toLocaleString() : ''} · {info.copy_format === 'pdf' ? 'Original PDF' : 'Readable HTML'}{info.copy_size ? ` · ${fileSize(info.copy_size)}` : ''}</p>{/if}
    {#if info}<div class="actions">
      {#if info.copy_available}<a class="primary" href={`/api/reading/${articleId}/copy/download`}><Download size={iconSizes.action} />Download {info.copy_format === 'pdf' ? 'PDF' : 'HTML'}</a>{/if}
      {#if info.source_kind !== 'upload'}
        <a href={info.url} target="_blank" rel="noopener noreferrer">Open original<ArrowUpRight size={iconSizes.inline} /></a>
        <button disabled={refreshing || pending} onclick={refreshCopy}><RefreshCw size={iconSizes.action} />{pending || refreshing ? 'Saving copy...' : info.copy_available ? 'Refresh saved copy' : 'Retry saved copy'}</button>
      {/if}
    </div>{/if}
    {#if info?.upload_filename}<p>{info.upload_filename}</p>{/if}
  </header>
  {#if error}<p class="notice" role="alert">{error}<button onclick={load}>Try again</button></p>{/if}
  {#if pending}<p class="notice" role="status">{info?.copy_available ? 'A new capture is being prepared. Your previous copy stays available below.' : 'Your saved copy is being prepared by the NAS.'}</p>{/if}
  {#if info?.copy_status === 'failed'}<p class="notice" role="status">{info.copy_available ? 'The latest capture failed. Your previous copy is kept.' : 'A saved copy could not be created.'} {info.copy_error}{info.copy_retry_at ? ' Another attempt is scheduled.' : ''}</p>{/if}
  {#if info?.copy_warning}<p class="notice">{info.copy_warning}</p>{/if}
  {#if info?.copy_available}
    {#if info.copy_format === 'pdf'}
      {#key info.copy_saved_at}<PDFReader {articleId} version={info.copy_saved_at ?? ''} url={contentUrl} />{/key}
    {:else}
      <div class="article-progress" bind:this={progressToolbar} role="status"><a href="/" onclick={onback} aria-label="Back to reading list" class="progress-back"><ArrowLeft size={iconSizes.inline}/>Back</a><span>{articleSync.ready?`${Math.round((articleSync.position?.fraction??0)*100)}% through`:'Loading reading position…'}<small>{articleSync.status==='saving'||articleSync.status==='pending'?'Saving position…':articleSync.status==='saved'?'Position saved':''}</small></span><button disabled={!articleSync.ready||articleSync.status==='conflict'} onclick={startArticleOver}>Start over</button></div>
      {#if articleSync.message}<div class="progress-notice"><p>{articleSync.message}</p>{#if articleSync.status==='conflict'&&articleSync.ready}<button onclick={resumeSavedPosition}>Resume saved position</button><button onclick={()=>articleProgress?.keepCurrent(currentArticlePosition())}>Keep this position</button>{:else if articleSync.status==='conflict'}<button onclick={load}>Reload saved copy</button>{:else if articleSync.ready}<button onclick={()=>articleProgress?.flush()}>Retry saving position</button>{:else}<button onclick={resumeSavedPosition}>Retry loading position</button>{/if}</div>{/if}
      <iframe bind:this={frame} class="reader-frame" title="Saved article content" src={contentUrl} sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox" onload={articleFrameLoaded} style:height={`${frameHeight}px`}></iframe>
    {/if}
    <p class="hint">This copy is stored on your NAS. Download it to read without a connection to the NAS.</p>
  {:else if loading}<div class="empty">Loading saved copy...</div>
  {:else if info && !pending && info.copy_status !== 'failed'}<div class="empty">{info.source_kind === 'upload' ? 'The uploaded PDF is missing. Upload the same file again to restore it.' : 'The saved file is unavailable. Use Retry saved copy to retrieve it again.'}</div>{/if}
</section>

<style>
  .article-progress{position:sticky;top:calc(8px + env(safe-area-inset-top));z-index:2;display:flex;justify-content:space-between;align-items:center;gap:15px;margin:20px 0 12px;padding:10px 15px;border:1px solid var(--line);border-radius:10px;background:var(--surface);font-size:12px;color:var(--text)}
  .article-progress{flex-wrap:wrap}.article-progress>span{flex:1;min-width:80px}.progress-back{display:inline-flex;align-items:center;gap:5px;color:var(--highlight);font-size:11px;text-decoration:none;min-height:34px}
  .article-progress small{display:block;margin-top:4px;font-size:10px;color:var(--muted)}
  .article-progress button,.progress-notice button{border:1px solid var(--line);background:var(--bg);color:var(--highlight);padding:8px 11px;border-radius:7px;font:inherit;font-size:11px}
  .article-progress button:disabled{opacity:.5;cursor:default}.progress-notice{margin:12px 0;color:var(--warning);font-size:12px;line-height:1.7}.progress-notice button{margin-right:8px}
  @media (hover:none) and (pointer:coarse){.article-progress button,.progress-notice button{min-height:44px}}

  .saved-article { max-width: 1060px; margin:0 auto; }
  .back { display: inline-flex; align-items: center; gap: 8px; color: var(--muted); font-size: 11px; text-decoration: none; }
  header { margin: 30px 0 24px; }
  .eyebrow { font-size: 9px; letter-spacing: 2px; color: var(--highlight); }
  h1 { margin: 16px 0; font-size: 34px; line-height: 1.3; overflow-wrap: anywhere; }
  header p, .hint { overflow-wrap: anywhere; font-size: 11px; color: var(--muted); line-height: 1.8; }
  .actions { display: flex; flex-wrap: wrap; align-items: center; gap: 12px 20px; margin-top: 22px; }
  .actions a, .actions button { display: inline-flex; align-items: center; gap: 7px; border: 0; background: transparent; color: var(--highlight); padding: 0; text-decoration: none; font: inherit; font-size: 11px; cursor: pointer; }
  .actions .primary { padding: 10px 13px; border: 1px solid var(--line); background: var(--surface); border-radius: 8px; color: var(--text); }
  button:disabled { cursor: default; opacity: .5; }
  a:focus-visible, button:focus-visible { outline: 2px solid var(--highlight); outline-offset: 4px; }
  .notice { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 13px 16px; font-size: 12px; color: var(--warning); line-height: 1.8; overflow-wrap: anywhere; }
  .notice button { background: transparent; border: 0; color: var(--highlight); cursor: pointer; font: inherit; margin-left: 12px; }
  iframe { width: 100%; display: block; border: 1px solid var(--line); border-radius: 14px; background: var(--surface); }
  .empty { text-align: center; padding: 70px 20px; color: var(--muted); font-size: 13px; }
  @media (max-width:540px) { h1 { font-size: 27px; } }
</style>
