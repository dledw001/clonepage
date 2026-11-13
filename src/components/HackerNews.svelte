<script lang="ts">
  import { featureIcons, iconSizes } from '../lib/icons';
  import SectionHeader from './SectionHeader.svelte';
  import { onMount, untrack } from 'svelte';
  import { ArrowRight, ArrowUpRight, BookmarkPlus, Check, Circle, CircleCheck, MessageSquare, RefreshCw } from '@lucide/svelte';
  import { HackerNewsReader, storyAge, storyDomain, type HNState, type HNScope } from '../lib/hacker-news';

  let { preview = false, active = true, previewCount = 3, previewScope = {view:'top',window:'24h'}, onnavigate }: {
    preview?: boolean; active?: boolean; previewCount?: number; previewScope?: HNScope; onnavigate?: (event: MouseEvent, path: string) => void;
  } = $props();
  const initialScope = untrack((): HNScope => ({ view: preview ? previewScope.view : 'front', window: preview ? previewScope.window : '24h' }));
  let readerState = $state<HNState>({ scope: initialScope, data: null, loading: false, busy: [], error: '', notice: '' });
  const reader = new HackerNewsReader(next => readerState = next, (...args) => fetch(...args), kind => {
    window.dispatchEvent(new Event('clonepage:hn-changed'));
    if (kind === 'save') window.dispatchEvent(new Event('clonepage:reading-changed'));
  }, initialScope);
  let mounted = false;
  let hideRead = $state(false);
  let shown = $state(25);
  let now = $state(Date.now());
  const stories = $derived((readerState.data?.stories ?? []).filter(story => !hideRead || !story.is_read));
  const visible = $derived(stories.slice(0, preview ? previewCount : shown));
  const notice = $derived(readerState.error || readerState.data?.error || readerState.data?.notice || '');

  $effect(() => { const current = active; if (mounted && current) untrack(() => { void reader.refresh(); }); });

  $effect(()=>{const view=previewScope.view,window=previewScope.window;if(mounted&&preview)untrack(()=>reader.setScope({view,window}));});

  onMount(() => {
    mounted = true;
    void reader.refresh();
    const refresh = () => { if (active && !document.hidden) void reader.refresh(); };
    const poll = setInterval(refresh, 180000);
    const clock = setInterval(() => { if (active && !document.hidden) now = Date.now(); }, 30000);
    document.addEventListener('visibilitychange', refresh);
    window.addEventListener('focus', refresh);
    window.addEventListener('clonepage:hn-changed', refresh);
    window.addEventListener('clonepage:reading-changed', refresh);
    return () => {
      mounted = false; reader.dispose(); clearInterval(poll); clearInterval(clock);
      document.removeEventListener('visibilitychange', refresh);
      window.removeEventListener('focus', refresh);
      window.removeEventListener('clonepage:hn-changed', refresh);
      window.removeEventListener('clonepage:reading-changed', refresh);
    };
  });

  function scope(changes: Partial<HNScope>) { shown = 25; reader.setScope(changes); }
</script>

<section class="hn-reader" class:home-surface={preview} class:preview aria-label="Hacker News">
  {#if preview}
    <SectionHeader title="Hacker News" subtitle={`${readerState.scope.view==='front'?'Front page':readerState.scope.view==='top'?'Top stories':'Most discussed'}${readerState.scope.view!=='front'?` · past ${readerState.scope.window==='24h'?'24 hours':readerState.scope.window==='7d'?'7 days':'30 days'}`:''}`}>
      {#snippet icon()}<featureIcons.hackerNews size={iconSizes.section}/>{/snippet}
      {#snippet actions()}
        <button class="section-icon-action" onclick={() => reader.refresh(true)} disabled={readerState.loading} aria-label="Refresh Hacker News" title="Refresh Hacker News"><RefreshCw size={iconSizes.action} class={readerState.loading ? 'spinning' : ''}/></button>
        <a class="section-action" href="/hacker-news" onclick={event => onnavigate?.(event, '/hacker-news')}>Open reader<ArrowRight size={iconSizes.action}/></a>
      {/snippet}
    </SectionHeader>
  {:else}
    <header>
      <div class="heading"><span class="page-symbol" aria-hidden="true"><featureIcons.hackerNews size={iconSizes.page}/></span><h1>Hacker News</h1></div>
      <div class="header-actions"><button class="icon-button" onclick={() => reader.refresh(true)} disabled={readerState.loading} aria-label="Refresh Hacker News" title="Refresh Hacker News"><RefreshCw size={iconSizes.action} class={readerState.loading ? 'spinning' : ''}/></button></div>
    </header>
  {/if}

  {#if !preview}
    <div class="toolbar">
      <div class="tabs" role="group" aria-label="Hacker News views">
        <button class:active={readerState.scope.view === 'front'} aria-pressed={readerState.scope.view === 'front'} onclick={() => scope({ view: 'front' })}>Front page</button>
        <button class:active={readerState.scope.view === 'top'} aria-pressed={readerState.scope.view === 'top'} onclick={() => scope({ view: 'top' })}>Top stories</button>
        <button class:active={readerState.scope.view === 'discussed'} aria-pressed={readerState.scope.view === 'discussed'} onclick={() => scope({ view: 'discussed' })}>Most discussed</button>
      </div>
      {#if readerState.scope.view !== 'front'}
        <div class="windows" role="group" aria-label="Submission window">
          {#each ['24h', '7d', '30d'] as window}
            <button class:active={readerState.scope.window === window} aria-pressed={readerState.scope.window === window} onclick={() => scope({ window: window as HNScope['window'] })}>{window}</button>
          {/each}
        </div>
      {/if}
      <label class="hide-read"><input type="checkbox" bind:checked={hideRead} onchange={() => shown = 25} />Hide read</label>
    </div>
    <p class="scope-note">{readerState.scope.view === 'front' ? 'HN’s current first-page order. Points and comments are refreshed with the list.' : `Stories submitted in the past ${readerState.scope.window === '24h' ? '24 hours' : readerState.scope.window === '7d' ? '7 days' : '30 days'}, ranked by their current ${readerState.scope.view === 'top' ? 'points' : 'comment count'}.`}</p>
  {/if}

  {#if notice}<p class="notice" role="status">{notice}</p>{/if}
  {#if readerState.notice}<p class="status" role="status">{readerState.notice}</p>{/if}
  {#if !readerState.data}
    <div class="empty"><featureIcons.hackerNews size={iconSizes.empty} /><p>{readerState.loading ? 'Loading stories…' : 'Hacker News could not be loaded.'}</p>{#if !readerState.loading}<button onclick={() => reader.refresh(true)}>Try again</button>{/if}</div>
  {:else if !stories.length}
    <div class="empty"><CircleCheck size={iconSizes.empty} /><p>{hideRead ? 'No unread stories in this view.' : 'No stories found in this view.'}</p>{#if hideRead}<button onclick={() => hideRead = false}>Show read stories</button>{/if}</div>
  {:else}
    <div class="story-list" aria-busy={readerState.loading}>
      {#each visible as story, index (story.id)}
        <article class:read={story.is_read}>
          <button class="read-toggle icon-button" disabled={readerState.busy.includes(`read:${story.id}`)} aria-label={`${story.is_read ? 'Mark unread' : 'Mark read'}: ${story.title}`} title={story.is_read ? 'Mark unread' : 'Mark read'} onclick={() => reader.setRead(story, !story.is_read)}>{#if story.is_read}<CircleCheck size={iconSizes.status} />{:else}<Circle size={iconSizes.status} />{/if}</button>
          <div class="story-info">
            <div class="story-title"><span class="position" aria-hidden="true">{index + 1}.</span><a href={story.url} target="_blank" rel="noopener noreferrer" onclick={() => { if (!story.is_read) void reader.setRead(story, true); }}>{story.title}<ArrowUpRight size={iconSizes.inline} /></a></div>
            <div class="meta"><span class="domain">{storyDomain(story.url)}</span><span>{story.points.toLocaleString()} points</span><a href={story.discussion_url} target="_blank" rel="noopener noreferrer" onclick={() => { if (!story.is_read) void reader.setRead(story, true); }}><MessageSquare size={iconSizes.inline} />{story.comments.toLocaleString()} {story.comments === 1 ? 'comment' : 'comments'}</a><time datetime={story.created_at} title={new Date(story.created_at).toLocaleString()}>{storyAge(story.created_at, now)}</time>{#if story.rank}<span class="rank" title="Position on the HN front page at the last refresh">HN #{story.rank}</span>{/if}</div>
          </div>
          <button class="save" class:saved={story.is_saved} disabled={story.is_saved || readerState.busy.includes(`save:${story.id}`)} aria-label={`${story.is_saved ? 'Saved to reading list' : 'Save to reading list'}: ${story.title}`} onclick={() => reader.save(story)}>{#if story.is_saved}<Check size={iconSizes.action} /><span>Saved</span>{:else}<BookmarkPlus size={iconSizes.action} /><span>{readerState.busy.includes(`save:${story.id}`) ? 'Saving…' : 'Save'}</span>{/if}</button>
        </article>
      {/each}
    </div>
  {/if}
  {#if readerState.data}
    <div class="list-footer"><span>{visible.length} of {stories.length} stories{hideRead ? ' unread' : ''}</span>{#if preview}<a href="/hacker-news" onclick={event => onnavigate?.(event, '/hacker-news')}>Explore Hacker News <ArrowRight size={iconSizes.inline} /></a>{:else if visible.length < stories.length}<button onclick={() => shown += 25}>Show more</button>{:else if shown > 25}<button onclick={() => shown = 25}>Show less</button>{/if}</div>
    {#if !preview}<p class="footnote">{readerState.data.stale || readerState.error ? 'Showing previous results. ' : ''}Updated {storyAge(readerState.data.fetched_at, now)}. {readerState.scope.view === 'front' ? 'Live ordering from the HN API.' : 'Up to 100 stories from HN Search (Algolia); its index can lag behind HN.'} Read status syncs across your devices. Refreshes are cached for five minutes.</p>{/if}
  {/if}
</section>

<style>
  .hn-reader { margin: 48px 0 0; padding: 30px 32px; background: var(--surface); border: 1px solid var(--line); border-radius: 18px; }
  .hn-reader:not(.preview) { max-width:1060px; margin:0 auto; }
  .hn-reader.preview { margin:0; padding:var(--section-padding); border-radius:var(--panel-radius); }
  header, .heading, .header-actions, .toolbar, .tabs, .windows, .meta, .list-footer { display: flex; align-items: center; }
  header { justify-content: space-between; gap: 16px; }
  .heading { gap: 13px; min-width: 0; }
  .page-symbol { display:grid; place-items:center; flex-shrink:0; width:31px; height:31px; color:var(--highlight); }
  h1 { margin:0; font-weight:500; font-size:24px; line-height:1.4; font-family:inherit; font-style:normal; letter-spacing:-.4px; }
  .header-actions { gap: 14px; flex-shrink: 0; }
  button { border: 0; background: transparent; color: var(--muted); }
  a { color: var(--highlight); text-decoration: none; }
  a:hover { text-decoration: underline; }
  .icon-button { display: inline-flex; align-items: center; justify-content: center; min-width: 30px; min-height: 32px; padding: 4px; border-radius: 7px; }
  .icon-button:hover { background: var(--surface-hover); color: var(--text); }
  .list-footer a { display: flex; align-items: center; gap: 7px; font-size: 11px; }
  .toolbar { gap: 14px; flex-wrap: wrap; margin-top: 22px; }
  .tabs, .windows { gap: 3px; padding: 4px; background: var(--bg); border: 1px solid var(--line); border-radius: 9px; }
  .tabs button, .windows button { padding: 9px 12px; border-radius: 6px; font-size: 11px; }
  button.active { background: var(--surface); color: var(--text); }
  .hide-read { display: flex; gap: 7px; align-items: center; margin-left: auto; font-size: 11px; color: var(--muted); }
  input { accent-color: var(--highlight); }
  .scope-note, .footnote { color: var(--muted); font-size: 11px; line-height: 1.8; }
  .scope-note { margin: 15px 0 5px; }
  article { display: flex; align-items: flex-start; gap: 14px; padding: 21px 0; border-bottom: 1px solid var(--line); }
  article:first-child { margin-top: 8px; }
  .read-toggle { flex-shrink: 0; color: var(--highlight); }
  article.read .read-toggle, article.read .story-title a { color: var(--muted); }
  .story-info { min-width: 0; flex: 1; }
  .story-title { display: flex; gap: 8px; align-items: baseline; }
  .position { color: var(--muted); font: 10px monospace; flex-shrink: 0; }
  .story-title a { color: var(--text); font-size: 14px; line-height: 1.7; overflow-wrap: anywhere; }
  .story-title :global(svg) { margin-left: 6px; color: var(--muted); vertical-align: middle; }
  .meta { flex-wrap: wrap; gap: 8px 13px; margin-top: 8px; color: var(--muted); font-size: 10px; line-height: 1.7; }
  .meta a { display: inline-flex; align-items: center; gap: 5px; }
  .domain { overflow-wrap: anywhere; }
  .rank { padding: 1px 6px; border: 1px solid var(--line); border-radius: 5px; }
  .save { display: flex; align-items: center; gap: 6px; flex-shrink: 0; padding: 7px 8px; font-size: 11px; border: 1px solid var(--line); border-radius: 7px; }
  .save:hover { color: var(--highlight); background: var(--surface-hover); }
  .save.saved { color: var(--success); }
  .list-footer { justify-content: space-between; gap: 15px; margin-top: 19px; color: var(--muted); font-size: 11px; }
  .list-footer button, .empty button { color: var(--highlight); font-size: 11px; }
  .footnote { margin: 19px 0 0; }
  .notice { color: var(--warning); padding: 12px 0; font-size: 12px; line-height: 1.7; }
  .status { color: var(--success); font-size: 11px; line-height: 1.7; }
  .empty { padding: 38px 12px; text-align: center; color: var(--muted); font-size: 13px; }
  button:disabled { cursor: default; opacity: .55; }
  :is(a, button, input):focus-visible { outline: 2px solid var(--highlight); outline-offset: 4px; }
  @media (max-width: 650px) { .hn-reader { padding: 22px 18px; } h1 { font-size:22px; } .header-actions { gap: 6px; } .toolbar { gap: 10px; } .tabs { width: 100%; justify-content: space-between; } .tabs button { padding: 9px; flex: 1; } article { gap: 8px; } .story-title a { font-size: 13px; } .save { padding: 8px; } .save span { display: none; } .hide-read { margin-left: auto; } }
  @media (hover: none) and (pointer: coarse) { .icon-button, .save, .tabs button, .windows button { min-width: 44px; min-height: 44px; } .hide-read { min-height: 44px; } }
</style>
