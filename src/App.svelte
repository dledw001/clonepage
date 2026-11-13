<script lang="ts">
  import { onMount, tick } from 'svelte';
  import { ArrowRight, Command, Moon, Search, Sun, House, Activity, Newspaper, Settings2 } from '@lucide/svelte';

  import ReadingList from './components/ReadingList.svelte';
  import Feeds from './components/Feeds.svelte';
  import HackerNews from './components/HackerNews.svelte';
  import Weather from './components/Weather.svelte';
  import NeedsAttention from './components/NeedsAttention.svelte';
  import ContinueReading from './components/ContinueReading.svelte';
  import HomeCustomize from './components/HomeCustomize.svelte';
  import { HomeLayoutStore, defaultHomeSettings, homeSectionFromSearch, type LayoutState } from './lib/home';
  import Infrastructure from './components/Infrastructure.svelte';
  import SavedArticle from './components/SavedArticle.svelte';
  import { PageNavigation, isPlainClick, webSearchUrl } from './lib/navigation';

  import Shortcuts from './components/Shortcuts.svelte';
  import { shortcutIcon } from './lib/shortcut-icons';
  import { ShortcutStore, type Shortcut, type ShortcutState } from './lib/shortcuts';

  let shortcutState = $state<ShortcutState>({ items: [], revision: 0, loaded: false, loading: false, busy: false, conflict: false, error: '' });
  const shortcutStore = new ShortcutStore(next => shortcutState = next);
  const commands = $derived(shortcutState.items.map(item => ({ ...item, icon: shortcutIcon(item.icon) })));
  type CommandItem = (typeof commands)[number];

  let layoutState=$state<LayoutState>({settings:defaultHomeSettings(),revision:0,loaded:false,loading:false,busy:false,conflict:false,error:''});
  const layoutStore=new HomeLayoutStore(next=>layoutState=next);
  let customizingHome=$state(false);
  let homeFocus=$state(typeof window==='undefined'?null:homeSectionFromSearch(window.location.search));

  let now = $state(new Date());
  let query = $state('');
  let paletteOpen = $state(false);
  let selected = $state(0);
  let theme = $state<'dark' | 'light'>('dark');
  let toast = $state('');

  $effect(() => {
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'light' ? '#efeee9' : '#11151b');
  });
  function routeFromPath(path: string) {
    const reader = /^\/reading\/([A-Za-z0-9_-]{1,80})\/?$/.exec(path);
    return { page: reader ? 'reader' as const : path.replace(/\/$/, '') === '/infrastructure' ? 'infrastructure' as const : path.replace(/\/$/, '') === '/hacker-news' ? 'hacker-news' as const : 'home' as const, articleId: reader?.[1] ?? '' };
  }
  const initialRoute = routeFromPath(typeof window !== 'undefined' ? window.location.pathname : '/');
  let page = $state<'home' | 'infrastructure' | 'reader' | 'hacker-news'>(initialRoute.page);
  let readerId = $state(initialRoute.articleId);
  let monitorFocus = $state(typeof window === 'undefined' ? null : new URLSearchParams(window.location.search).get('monitor'));
  let homeVisited = $state(initialRoute.page === 'home');
  const navigation = typeof window === 'undefined' ? undefined : new PageNavigation(window, async () => {
    syncRoute();
    if (page === 'home') homeVisited = true;
    paletteOpen = false;
    await tick();
  });

  function syncRoute() { const route = routeFromPath(window.location.pathname); page = route.page; readerId = route.articleId; monitorFocus = new URLSearchParams(window.location.search).get('monitor'); homeFocus=homeSectionFromSearch(window.location.search); }

  let searchInput = $state<HTMLInputElement>();
  let searchRegion = $state<HTMLDivElement>();

  const matches = $derived(
    query.trim()
      ? commands.filter(c => {
          const text = query.toLowerCase().trim();
          return c.name.toLowerCase().includes(text) ||
            c.aliases.some(alias => alias.includes(text));
        })
      : commands
  );

  const time = $derived(
    now.toLocaleTimeString('en-US', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hourCycle: 'h23'
    })
  );

  const date = $derived(
    now.toLocaleDateString('en-US', {
      weekday: 'long',
      month: 'long',
      day: 'numeric',
      year: 'numeric',
    })
  );

  const greeting = $derived(
    now.getHours() < 12 ? 'A fresh start to the day.' :
    now.getHours() < 17 ? 'A moment to refocus.' :
    'Make yourself at home.'
  );

  onMount(() => {
    void shortcutStore.refresh();
    void layoutStore.refresh().then(async()=>{await tick();scrollHomeSection();});
    const refreshShortcuts = () => { if (!document.hidden) { void shortcutStore.refresh(); if(!customizingHome)void layoutStore.refresh(); } };
    document.addEventListener('visibilitychange', refreshShortcuts);
    window.addEventListener('focus', refreshShortcuts);
    const saved = localStorage.getItem('clonepage-theme');
    if (saved === 'light' || saved === 'dark') {
      theme = saved;
    }

    const interval = setInterval(() => {
      now = new Date();
    }, 1000);

    const stopNavigation = navigation?.start();
    return () => {
      shortcutStore.dispose(); layoutStore.dispose();
      document.removeEventListener('visibilitychange', refreshShortcuts);
      window.removeEventListener('focus', refreshShortcuts);
      clearInterval(interval);
      stopNavigation?.();
    };
  });

  function scrollHomeSection(){if(page==='home'&&homeFocus)document.getElementById(`home-${homeFocus}`)?.scrollIntoView({block:'start',behavior:'instant'});}

  async function navigate(event: MouseEvent, path: string) {
    if (!isPlainClick(event)) return;
    event.preventDefault();
    await navigation?.go(path);
    scrollHomeSection();
  }

  function backToList(event: MouseEvent) {
    if (!isPlainClick(event)) return;
    event.preventDefault();
    navigation?.backToList();
  }

  function closePalette() { paletteOpen = false; query = ''; }

  function toggleTheme() {
    theme = theme === 'dark' ? 'light' : 'dark';
    localStorage.setItem('clonepage-theme', theme);
  }

  function notify(message: string) {
    toast = message;
    setTimeout(() => {
      if (toast === message) toast = '';
    }, 3000);
  }

  async function execute(command: Pick<Shortcut, 'action' | 'value' | 'name'>) {
    paletteOpen = false;
    query = '';

    if (command.action === 'copy') {
      try {
        await navigator.clipboard.writeText(command.value);
        notify(`Copied: ${command.value}`);
      } catch {
        notify(`Could not copy: ${command.value}`);
      }
      return;
    }

    if (!command.value) {
      notify(`Edit ${command.name} in Manage shortcuts to set its URL.`);
      return;
    }

    window.open(command.value, '_blank', 'noopener,noreferrer');
  }

  function searchWeb() {
    const text = query.trim();
    if (!text) return;

    window.location.assign(webSearchUrl(text));
    paletteOpen = false;
    query = '';
  }

  function handleSearchKeydown(event: KeyboardEvent) {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      selected = Math.min(selected + 1, matches.length - 1);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      selected = Math.max(selected - 1, 0);
    } else if (event.key === 'Enter') {
      event.preventDefault();

      if (matches.length) {
        execute(matches[Math.max(0, Math.min(selected, matches.length - 1))]);
      } else {
        searchWeb();
      }
    }
  }

  async function handleGlobalKeydown(event: KeyboardEvent) {
    if ((event.ctrlKey || event.metaKey) &&
        event.key.toLowerCase() === 'k') {
      event.preventDefault();
      if (page !== 'home') {
        await navigation?.go('/');
      }
      searchInput?.focus();
      paletteOpen = true;
    }

    if (event.key === 'Escape') {
      paletteOpen = false;
      searchInput?.blur();
    }
  }

  function handleOutsideClick(event: PointerEvent) {
    if (searchRegion &&
        !searchRegion.contains(event.target as Node)) {
      paletteOpen = false;
    }
  }
</script>

<svelte:window
  onkeydown={handleGlobalKeydown}
  onpointerdown={handleOutsideClick}
/>

<div class="app" class:light={theme === 'light'}>
  <div class="page">
    <header class="topbar">
      <a href="/" class="brand" onclick={event => navigate(event, '/')}>
        <span class="brand-mark">✳</span>
        <span>clonepage<span class="brand-dot">.</span></span>
      </a>

      <nav class="page-navigation" aria-label="Main navigation">
        <a href="/" class:current={page === 'home'} aria-current={page === 'home' ? 'page' : undefined} onclick={event => navigate(event, '/')}><House size={15} strokeWidth={1.7} />Home</a>
        <a href="/infrastructure" class:current={page === 'infrastructure'} aria-current={page === 'infrastructure' ? 'page' : undefined} onclick={event => navigate(event, '/infrastructure')}><Activity size={15} strokeWidth={1.7} />Infrastructure</a>
        <a href="/hacker-news" class:current={page === 'hacker-news'} aria-current={page === 'hacker-news' ? 'page' : undefined} onclick={event => navigate(event, '/hacker-news')}><Newspaper size={15} strokeWidth={1.7} />Hacker News</a>
      </nav>

      <div class="top-actions">
        {#if page==='home'}<button class="icon-button" aria-label="Customize homepage" title="Customize homepage" aria-expanded={customizingHome} onclick={()=>customizingHome=!customizingHome}><Settings2 size={18}/></button>{/if}
        <button
          class="icon-button"
          onclick={toggleTheme}
          aria-label="Toggle color theme"
        >
          {#if theme === 'dark'}
            <Sun size={19} strokeWidth={1.7} />
          {:else}
            <Moon size={19} strokeWidth={1.7} />
          {/if}
        </button>
      </div>
    </header>

    <main>
      {#if page === 'reader'}
        {#key readerId}<SavedArticle articleId={readerId} {theme} onback={backToList} />{/key}
      {:else if page === 'hacker-news'}
        <HackerNews onnavigate={navigate} />
      {:else if page === 'infrastructure'}
        <Infrastructure {monitorFocus} />
      {/if}
      {#if homeVisited}
      <div hidden={page !== 'home'}>
      <section class="hero" class:compact={!layoutState.settings.show_clock}>
        {#if layoutState.settings.show_clock}
        <div class="eyebrow">
          <span class="eyebrow-line"></span>
          A LITTLE ROOM TO THINK
          <span class="eyebrow-line"></span>
        </div>

        <div class="clock" style="font-family: monospace">{time}</div>
        <div class="date">{date}</div>

        <h1>{greeting}</h1>

        {/if}
        <div class="search-region" bind:this={searchRegion}>
          <div class="search-box" class:active={paletteOpen}>
            <Search size={21} strokeWidth={1.8} />

            <input
              bind:this={searchInput}
              bind:value={query}
              onfocus={() => paletteOpen = true}
              oninput={() => selected = 0}
              onkeydown={handleSearchKeydown}
              placeholder="Search anything, or enter a command..."
              aria-label="Search or command"
              autocomplete="off"
              spellcheck="false"
            />

            <kbd>Ctrl K</kbd>
          </div>

          {#if paletteOpen}
            <div class="command-results">
              <div class="results-heading">
                {query.trim() ? 'MATCHING COMMANDS' : 'QUICK ACTIONS'}
              </div>

              {#snippet resultContent(command: CommandItem)}
                <span class="result-icon"><command.icon size={19} strokeWidth={1.7} /></span>
                <span class="result-text"><strong>{command.name}</strong><small>{command.description}</small></span>
                <ArrowRight size={16} />
              {/snippet}
              {#if !shortcutState.loaded}<p class="command-status">{shortcutState.loading ? 'Loading shortcuts...' : 'Shortcuts could not be loaded.'}</p>{:else if !commands.length}<p class="command-status">Add your first shortcut in Your Places.</p>{/if}
              {#each matches as command, index (command.id)}
                {#if command.action === 'open' && command.value}
                  <a class="result" class:selected={selected === index} href={command.value} target="_blank" rel="noopener noreferrer"
                    onmouseenter={() => selected = index} onclick={closePalette}>{@render resultContent(command)}</a>
                {:else}
                <button
                  class="result"
                  class:selected={selected === index}
                  onmouseenter={() => selected = index}
                  onclick={() => execute(command)}
                >
                  {@render resultContent(command)}
                </button>
                {/if}
              {/each}

              {#if query.trim()}
                <a class="web-result" href={webSearchUrl(query)}>
                  <Search size={17} />
                  <span>Search the web for "{query}"</span>
                  <ArrowRight size={16} />
                </a>
              {/if}
            </div>
          {/if}
        </div>

        <p class="search-hint">
          Try <code>nas</code>, <code>vault</code>,
          or <code>ssh arch</code>
        </p>
      </section>

      {#if customizingHome}<HomeCustomize data={layoutState} store={layoutStore} onclose={()=>customizingHome=false}/>{/if}
      {#if layoutState.error&&!customizingHome}<p class="home-layout-error" role="status">{layoutState.error} Your last available homepage layout is kept. <button onclick={()=>layoutStore.refresh(true)}>Try again</button></p>{/if}
      <div class="home-sections">
      {#each layoutState.settings.sections as section(section.id)}
        {#if section.visible||homeFocus===section.id}
          <div id={`home-${section.id}`} class="home-section">
            {#if section.id==='attention'}<NeedsAttention active={page==='home'} onnavigate={navigate}/>
            {:else if section.id==='weather'}<Weather active={page==='home'} preferences={layoutState.settings.weather}/>
            {:else if section.id==='shortcuts'}<Shortcuts data={shortcutState} store={shortcutStore} onexecute={execute} onmonitor={navigate} active={page==='home'}/>
            {:else if section.id==='continue'}<ContinueReading active={page==='home'} previewCount={layoutState.settings.previews.continue_reading} onnavigate={navigate}/>
            {:else if section.id==='reading'}<ReadingList active={page==='home'} previewCount={layoutState.settings.previews.reading} onnavigate={navigate}/>
            {:else if section.id==='feeds'}<Feeds active={page==='home'} previewCount={layoutState.settings.previews.feeds}/>
            {:else if section.id==='hacker-news'}<HackerNews preview active={page==='home'} previewCount={layoutState.settings.previews.hacker_news} previewScope={layoutState.settings.hacker_news} onnavigate={navigate}/>{/if}
          </div>
        {/if}
      {/each}
      </div>
      </div>
      {/if}
    </main>

    <footer>
      <span>Designed for a quieter internet.</span>
      <span>CLONEPAGE · V0.1</span>
    </footer>
  </div>

  {#if toast}
    <div class="toast">
      <Command size={16} />
      {toast}
    </div>
  {/if}
</div>
