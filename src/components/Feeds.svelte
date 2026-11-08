<script lang="ts">
  import { onMount } from 'svelte';
  import { ArrowUpRight, BookmarkPlus, Check, Circle, CircleCheck, Plus, RefreshCw, Rss, Search, Settings2, Trash2, X } from '@lucide/svelte';
  import { FeedReader, type ReaderState, type Discovery, type FeedPreview } from '../lib/feed-reader';

  let { active = true }: { active?: boolean } = $props();
  let refreshOnReturn = false;
  $effect(() => { if (active && refreshOnReturn) { refreshOnReturn = false; void reader.refresh(); } });

  let readerState = $state<ReaderState>({ data: null, loading: false, busy: [], expanded: false, error: '', notice: '' });
  const reader = new FeedReader(next => readerState = next, (...args) => fetch(...args),
    () => window.dispatchEvent(new Event('clonepage:reading-changed')));
  let selectedFeed = $state('');
  let search = $state('');
  let view = $state<'unread' | 'all'>('unread');
  let adding = $state(false);
  let managing = $state(false);
  let sourceUrl = $state('');
  let discovering = $state(false);
  let formError = $state('');
  let preview = $state<FeedPreview | null>(null);
  let candidates = $state<Discovery['candidates']>([]);
  let discoverySequence = 0;
  const subscriptions = $derived(readerState.data?.subscriptions ?? []);
  const entries = $derived(readerState.data?.entries ?? []);
  const visible = $derived(readerState.expanded ? entries : entries.slice(0, 3));
  const failedFeeds = $derived(subscriptions.filter(feed => feed.error));

  $effect(() => {
    const scope = { feed_id: selectedFeed, q: search.trim(), unread: view === 'unread' };
    const timer = setTimeout(() => reader.setScope(scope), 250);
    return () => clearTimeout(timer);
  });

  onMount(() => {
    void reader.refresh();
    const refreshVisible = () => {
      if (active && !document.hidden && (!readerState.expanded || subscriptions.some(feed => feed.refresh_pending))) void reader.refresh();
    };
    const timer = setInterval(refreshVisible, 5000);
    const readingChanged = () => { if (active) void reader.refresh(); else refreshOnReturn = true; };
    document.addEventListener('visibilitychange', refreshVisible);
    window.addEventListener('focus', refreshVisible);
    window.addEventListener('clonepage:reading-changed', readingChanged);
    return () => {
      reader.dispose(); clearInterval(timer);
      document.removeEventListener('visibilitychange', refreshVisible);
      window.removeEventListener('focus', refreshVisible);
      window.removeEventListener('clonepage:reading-changed', readingChanged);
    };
  });

  async function discover(url = sourceUrl) {
    if (discovering) return;
    const sequence = ++discoverySequence;
    discovering = true; formError = ''; preview = null; candidates = [];
    try {
      const input = /^https?:\/\//i.test(url.trim()) ? url.trim() : `https://${url.trim()}`;
      let result = await reader.request<Discovery>('/api/feeds/discover', { method: 'POST', body: JSON.stringify({ url: input }) });
      if (sequence !== discoverySequence) return;
      if (!result.preview && result.candidates.length === 1) result = await reader.request<Discovery>('/api/feeds/discover', {
        method: 'POST', body: JSON.stringify({ url: result.candidates[0].url }),
      });
      if (sequence === discoverySequence) { preview = result.preview; candidates = result.candidates; }
    } catch (error) { if (sequence === discoverySequence) formError = error instanceof Error ? error.message : 'Could not find a feed.'; }
    finally { if (sequence === discoverySequence) discovering = false; }
  }

  function resetDiscovery() { discoverySequence++; discovering = false; preview = null; candidates = []; formError = ''; }

  async function subscribe() {
    if (preview && await reader.subscribe(preview.url)) {
      adding = false; preview = null; candidates = []; sourceUrl = ''; selectedFeed = ''; search = ''; view = 'unread';
    }
  }

  async function unsubscribe(id: string, title: string) {
    if (!confirm(`Unsubscribe from ${title}? Its feed entries will be removed. Saved reading-list articles will stay.`)) return;
    if (await reader.unsubscribe(id) && selectedFeed === id) selectedFeed = '';
  }

  function dateLabel(value: string | null) {
    return value ? new Date(value).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : 'Not updated yet';
  }
</script>

<section class="feeds" aria-label="Feeds">
  <header class="feeds-header">
    <div class="heading"><Rss size={19} strokeWidth={1.7} /><h2>FEEDS</h2><span class="count">{readerState.data?.unread ?? 0} UNREAD</span></div>
    <div class="actions">
      <button class="icon-action" onclick={() => reader.refreshFeeds()} disabled={!subscriptions.length || readerState.busy.includes('refresh')} aria-label="Refresh feeds" title="Check feeds for new stories"><RefreshCw size={17} /></button>
      <button class="icon-action" class:active={managing} onclick={() => managing = !managing} aria-label="Manage feeds" aria-expanded={managing} title="Manage subscriptions"><Settings2 size={17} /></button>
      <button class="add-button" onclick={() => adding = !adding} aria-expanded={adding}><Plus size={16} />Add feed</button>
    </div>
  </header>

  <div class="toolbar">
    <div class="tabs" role="tablist" aria-label="Feed views">
      <button role="tab" class:active={view === 'unread'} aria-selected={view === 'unread'} onclick={() => view = 'unread'}>Unread</button>
      <button role="tab" class:active={view === 'all'} aria-selected={view === 'all'} onclick={() => view = 'all'}>All</button>
    </div>
    <select bind:value={selectedFeed} aria-label="Filter by feed"><option value="">All feeds</option>{#each subscriptions as feed}<option value={feed.id}>{feed.title} ({feed.unread})</option>{/each}</select>
    <div class="feed-search"><Search size={16} /><input bind:value={search} maxlength="200" aria-label="Search feed entries" placeholder="Search stories..." /></div>
    <button class="mark-all" disabled={!entries.length || readerState.busy.length > 0 || readerState.loading} onclick={() => reader.markAllRead()}>Mark all read</button>
  </div>

  {#if adding}
    <div class="add-panel">
      <form onsubmit={event => { event.preventDefault(); void discover(); }}>
        <input bind:value={sourceUrl} required maxlength="2000" disabled={readerState.busy.includes('subscribe')} aria-label="Website or feed URL" placeholder="Paste a website or RSS/Atom feed URL..." oninput={resetDiscovery} />
        <button type="submit" disabled={discovering || readerState.busy.includes('subscribe')}>{discovering ? 'Finding feed...' : 'Find feed'}</button>
        <button type="button" class="icon-action" onclick={() => adding = false} aria-label="Close add feed"><X size={17} /></button>
      </form>
      {#if formError}<p class="error" role="alert">{formError}</p>{/if}
      {#if candidates.length}<div class="candidates"><p>Choose a feed to preview:</p>{#each candidates as candidate}<button disabled={discovering} onclick={() => discover(candidate.url)}>{candidate.title}<small>{candidate.url}</small></button>{/each}</div>{/if}
      {#if preview}
        <div class="preview"><strong>{preview.title}</strong><small>{preview.url}</small><p>{preview.entry_count} recent {preview.entry_count === 1 ? 'entry' : 'entries'}</p>
          <ul>{#each preview.samples as title}<li>{title}</li>{/each}</ul>
          <button class="subscribe" disabled={readerState.busy.includes('subscribe')} onclick={subscribe}>{readerState.busy.includes('subscribe') ? 'Subscribing...' : 'Subscribe'}</button>
        </div>
      {/if}
    </div>
  {/if}

  {#if managing}
    <div class="subscriptions" aria-label="Subscriptions">
      {#each subscriptions as feed}
        <div class="subscription"><div><strong>{feed.title}</strong><details><summary>Feed details</summary><a href={feed.url} target="_blank" rel="noopener noreferrer">Open RSS/Atom feed</a><small>{feed.url}</small></details><small>{feed.refresh_pending ? 'Refresh pending · ' : ''}Last updated: {dateLabel(feed.last_success_at)}</small>{#if feed.error}<small class="error">{feed.error} Existing stories are kept; collection will retry.</small>{/if}</div>
          <button class="icon-action" disabled={readerState.busy.includes(`unsubscribe:${feed.id}`)} onclick={() => unsubscribe(feed.id, feed.title)} aria-label={`Unsubscribe from ${feed.title}`} title="Unsubscribe"><Trash2 size={17} /></button>
        </div>
      {:else}<p class="status">No subscriptions yet. Add a feed to get started.</p>{/each}
      <p class="retention">Feeds are checked every 15 minutes. Entries are kept for 90 days; saved reading-list articles stay independently.</p>
    </div>
  {/if}
  {#if failedFeeds.length && !managing}<button class="feed-warning" onclick={() => managing = true}>{failedFeeds.length} {failedFeeds.length === 1 ? 'feed could' : 'feeds could'} not be refreshed. View details.</button>{/if}
  {#if readerState.error}<p class="error" role="alert">{readerState.error}</p>{/if}
  {#if readerState.notice}<p class="status" role="status">{readerState.notice}</p>{/if}

  {#if !readerState.data}
    <div class="empty"><Rss size={27} strokeWidth={1.3} /><p>{readerState.loading ? 'Loading your feeds...' : 'Feeds could not be loaded.'}</p>{#if !readerState.loading}<button onclick={() => reader.refresh()}>Try again</button>{/if}</div>
  {:else if !subscriptions.length}
    <div class="empty"><Rss size={27} strokeWidth={1.3} /><p>Your sources, in one place.</p><small>Add a website or feed URL to start collecting stories.</small></div>
  {:else if !entries.length}
    <div class="empty"><CircleCheck size={27} strokeWidth={1.3} /><p>{search.trim() ? 'No matching stories.' : view === 'unread' ? 'You’re caught up.' : 'No stories collected yet.'}</p><small>{view === 'unread' && !search.trim() ? 'Switch to All to browse stories you’ve read.' : 'New entries appear here after feeds update.'}</small></div>
  {:else}
    <div class="entry-list" aria-busy={readerState.loading}>
      {#each visible as entry (entry.id)}
        <article class="entry" class:read={entry.is_read}>
          <button class="icon-action read-toggle" aria-label={`${entry.is_read ? 'Mark unread' : 'Mark read'}: ${entry.title}`} title={entry.is_read ? 'Mark unread' : 'Mark read'} disabled={readerState.busy.includes(`read:${entry.id}`) || readerState.busy.includes('read-all')} onclick={() => reader.setRead(entry, !entry.is_read)}>{#if entry.is_read}<CircleCheck size={21} />{:else}<Circle size={21} />{/if}</button>
          <div class="entry-info"><a href={entry.url} target="_blank" rel="noopener noreferrer" onclick={() => { if (!entry.is_read) void reader.setRead(entry, true); }}>{entry.title}<ArrowUpRight size={14} /></a>
            <small>{entry.feed_title} · {dateLabel(entry.published_at ?? entry.first_seen_at)}{entry.published_at ? '' : ' · collected'}</small>
            {#if entry.summary}<p>{entry.summary}</p>{/if}
          </div>
          <button class="save-entry" class:saved={entry.is_saved} disabled={entry.is_saved || readerState.busy.includes(`save:${entry.id}`)} aria-label={`${entry.is_saved ? 'Saved to reading list' : 'Save to reading list'}: ${entry.title}`} title={entry.is_saved ? 'Already in your reading list' : 'Save to reading list'} onclick={() => reader.save(entry)}>{#if entry.is_saved}<Check size={16} /><span>Saved</span>{:else}<BookmarkPlus size={17} /><span>{readerState.busy.includes(`save:${entry.id}`) ? 'Saving...' : 'Save'}</span>{/if}</button>
        </article>
      {/each}
    </div>
    <div class="list-footer"><span>{visible.length} of {readerState.data.total} {readerState.data.total === 1 ? 'story' : 'stories'}{readerState.loading ? ' · updating' : ''}</span><div>
      {#if (!readerState.expanded && entries.length > 3) || readerState.data.next_cursor}<button disabled={readerState.loading} onclick={() => reader.showMore()}>{readerState.loading ? 'Loading...' : 'Show more'}</button>{/if}
      {#if readerState.expanded}<button onclick={() => reader.collapse()}>Show less</button>{/if}
    </div></div>
  {/if}
</section>

<style>
  .feeds { max-width: 1060px; margin: 38px auto 0; padding: 24px; border: 1px solid var(--line); border-radius: 16px; background: var(--surface); }
  .feeds-header, .heading, .actions, .toolbar, .tabs, .feed-search, .entry, .list-footer, .subscription { display: flex; align-items: center; }
  .feeds-header { justify-content: space-between; gap: 16px; margin-bottom: 22px; }
  .heading { gap: 11px; color: var(--highlight); flex-wrap: wrap; }
  h2 { font-size: 11px; letter-spacing: 1.8px; font-weight: 600; margin: 0; color: var(--text); }
  .count { font-size: 9px; letter-spacing: 1px; color: var(--muted); margin-left: 5px; }
  .actions { gap: 13px; }
  button { border: 0; cursor: pointer; font: inherit; color: var(--muted); background: transparent; }
  button:disabled { cursor: default; opacity: .5; }
  button:focus-visible, a:focus-visible, select:focus-visible { outline: 2px solid var(--highlight); outline-offset: 4px; }
  .icon-action { display: inline-flex; align-items: center; justify-content: center; padding: 5px; flex-shrink: 0; }
  .icon-action:hover, .icon-action.active { color: var(--highlight); }
  .add-button, form > button[type=submit], .subscribe { display: inline-flex; align-items: center; justify-content: center; gap: 7px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface-hover); color: var(--text); padding: 9px 12px; font-size: 11px; }
  .toolbar { gap: 12px; padding-bottom: 18px; border-bottom: 1px solid var(--line); flex-wrap: wrap; }
  .tabs { gap: 4px; padding: 3px; border-radius: 8px; background: var(--bg); }
  .tabs button { padding: 7px 10px; border-radius: 6px; font-size: 11px; }
  .tabs .active { color: var(--text); background: var(--surface-hover); }
  select { min-width: 0; max-width: 190px; border: 1px solid var(--line); background: var(--bg); color: var(--text); border-radius: 7px; padding: 8px; font: inherit; font-size: 11px; }
  .feed-search { gap: 8px; margin-left: auto; color: var(--muted); flex: 1; max-width: 245px; min-width: 130px; }
  input { min-width: 0; border: 1px solid var(--line); background: var(--bg); color: var(--text); border-radius: 7px; padding: 10px; font: inherit; font-size: 11px; outline-color: var(--highlight); }
  .feed-search input { padding: 7px 0; width: 100%; border: 0; background: transparent; }
  .mark-all { font-size: 10px; white-space: nowrap; }
  .add-panel, .subscriptions { padding: 18px; background: var(--bg); border: 1px solid var(--line); border-radius: 10px; margin: 18px 0; }
  form { display: flex; gap: 10px; }
  form input { flex: 1; }
  .preview { margin-top: 18px; font-size: 12px; }
  .preview > small { display: block; color: var(--muted); overflow-wrap: anywhere; margin-top: 7px; }
  .preview p, .preview li { color: var(--muted); font-size: 11px; line-height: 1.7; }
  .preview ul { padding-left: 18px; }
  .candidates p { font-size: 11px; color: var(--muted); }
  .candidates button { display: block; width: 100%; text-align: left; padding: 12px 0; color: var(--text); font-size: 12px; border-bottom: 1px solid var(--line); }
  .candidates small { display: block; font-size: 10px; color: var(--muted); overflow-wrap: anywhere; margin-top: 6px; }
  .subscription { justify-content: space-between; gap: 15px; padding: 12px 0; border-bottom: 1px solid var(--line); }
  .subscription > div { min-width: 0; }
  .subscription strong { font-size: 12px; font-weight: 500; }
  .subscription a, .subscription small { display: block; font-size: 10px; color: var(--muted); margin-top: 7px; overflow-wrap: anywhere; line-height: 1.6; }
  .subscription details { margin-top: 7px; font-size: 10px; color: var(--muted); }
  .subscription summary { cursor: pointer; }
  .retention, .status { font-size: 11px; line-height: 1.7; color: var(--muted); }
  .retention { margin-bottom: 0; }
  .error { color: var(--warning) !important; font-size: 12px; line-height: 1.7; }
  .feed-warning { color: var(--warning); text-align: left; font-size: 11px; margin: 15px 0 0; padding: 0; }
  .empty { display: flex; flex-direction: column; align-items: center; justify-content: center; color: var(--muted); min-height: 170px; text-align: center; gap: 9px; }
  .empty p { font-size: 13px; margin: 5px 0 0; color: var(--text); }
  .empty small { font-size: 11px; line-height: 1.7; }
  .entry { align-items: flex-start; gap: 15px; padding: 21px 0; border-bottom: 1px solid var(--line); }
  .read-toggle { margin-top: 0; color: var(--highlight); }
  .entry-info { flex: 1; min-width: 0; }
  .entry-info > a { color: var(--text); text-decoration: none; font-size: 13px; font-weight: 500; line-height: 1.7; overflow-wrap: anywhere; }
  .entry-info > a:hover { color: var(--highlight); }
  .entry-info > a :global(svg) { display: inline; vertical-align: middle; margin-left: 4px; color: var(--muted); }
  .entry-info > small { display: block; color: var(--muted); font-size: 10px; margin-top: 6px; line-height: 1.6; }
  .entry-info > p { margin: 10px 0 0; color: var(--muted); font-size: 11px; line-height: 1.8; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  .entry.read .entry-info > a { color: var(--muted); }
  .entry.read .read-toggle { color: var(--success); }
  .save-entry { display: inline-flex; align-items: center; gap: 6px; font-size: 10px; padding: 6px 0 6px 6px; flex-shrink: 0; }
  .save-entry.saved { color: var(--success); opacity: 1; }
  .list-footer { justify-content: space-between; gap: 10px; margin-top: 17px; color: var(--muted); font-size: 10px; }
  .list-footer > div { display: flex; gap: 16px; }
  .list-footer button { color: var(--highlight); padding: 4px 0; font-size: 11px; }
  @media (max-width: 700px) { .toolbar { gap: 10px; } .feed-search { max-width: none; } .mark-all { margin-left: auto; } .feeds-header { flex-wrap: wrap; } }
  @media (max-width: 540px) { .feeds { padding: 20px; margin-top: 24px; } .feeds-header { gap: 15px; } .heading { gap: 8px; } .actions { gap: 10px; margin-left: auto; } .count { margin-left: 0; font-size: 8px; } .toolbar { display: grid; grid-template-columns: auto minmax(0,1fr); } select { max-width: none; width: 100%; } .feed-search { margin: 0; min-width: 0; } .mark-all { justify-self: end; margin: 0; } form { flex-wrap: wrap; } form input { flex-basis: 100%; } .add-panel, .subscriptions { padding: 13px; } .entry { gap: 9px; position: relative; padding-bottom: 40px; } .save-entry { position: absolute; right: 0; bottom: 12px; } .entry-info > a { font-size: 12px; } .list-footer { align-items: flex-start; } }
</style>
