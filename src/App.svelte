<script lang="ts">
  import { featureIcons, iconSizes, lucideDefaults } from './lib/icons';
  import { onMount, tick } from 'svelte';
  import { setLucideProps } from '@lucide/svelte/context';

  import { Command, Moon, Search, Sun, Settings2 } from '@lucide/svelte';

  import CommandPalette from './components/CommandPalette.svelte';
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
  import { PageNavigation, isPlainClick } from './lib/navigation';

  import Shortcuts from './components/Shortcuts.svelte';
  import { ShortcutStore, type Shortcut, type ShortcutState } from './lib/shortcuts';

  setLucideProps(lucideDefaults);

  let shortcutState = $state<ShortcutState>({ items: [], revision: 0, loaded: false, loading: false, busy: false, conflict: false, error: '' });
  const shortcutStore = new ShortcutStore(next => shortcutState = next);

  let layoutState=$state<LayoutState>({settings:defaultHomeSettings(),revision:0,loaded:false,loading:false,busy:false,conflict:false,error:''});
  const layoutStore=new HomeLayoutStore(next=>layoutState=next);
  let customizingHome=$state(false);
  let homeFocus=$state(typeof window==='undefined'?null:homeSectionFromSearch(window.location.search));

  let now = $state(new Date());
  let paletteOpen = $state(false);
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

  let searchPalette = $state<{focusSearch:()=>void}>();

  const time = $derived(now.toLocaleTimeString('en-US', {hour:'numeric',minute:'2-digit',hour12:true}));
  const date = $derived(now.toLocaleDateString('en-US', {weekday:'short',month:'short',day:'numeric'}));

  onMount(() => {
    void shortcutStore.refresh();
    void layoutStore.refresh().then(async()=>{await tick();scrollHomeSection();});
    const refreshShortcuts = () => { if (!document.hidden) { now=new Date(); void shortcutStore.refresh(); if(!customizingHome)void layoutStore.refresh(); } };
    document.addEventListener('visibilitychange', refreshShortcuts);
    window.addEventListener('focus', refreshShortcuts);
    const saved = localStorage.getItem('clonepage-theme');
    if (saved === 'light' || saved === 'dark') {
      theme = saved;
    }

    const interval = setInterval(() => {
      const next = new Date();
      if (Math.floor(next.getTime()/60000)!==Math.floor(now.getTime()/60000)) now=next;
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

  async function openSearch() {
    paletteOpen=true;
    await tick();
    searchPalette?.focusSearch();
  }

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

  function handleGlobalKeydown(event: KeyboardEvent) {
    if (!event.defaultPrevented && !event.isComposing && (event.ctrlKey || event.metaKey) && event.key.toLowerCase()==='k') {
      event.preventDefault();
      void openSearch();
    }
  }
</script>

<svelte:window
  onkeydown={handleGlobalKeydown}
/>

<div class="app" class:light={theme === 'light'}>
  <div class="page">
    <header class="topbar" class:without-clock={!layoutState.settings.show_clock}>
      <a href="/" class="brand" aria-label="Clonepage home" onclick={event => navigate(event, '/')}>
        <span class="brand-mark">✳</span>
        <span class="brand-label">clonepage<span class="brand-dot">.</span></span>
      </a>

      {#if layoutState.settings.show_clock}
        <time class="header-clock" datetime={now.toISOString()} title={now.toLocaleString()} aria-label={`${time}, ${date}`}><span>{time}</span><small>{date}</small></time>
      {/if}

      <nav class="page-navigation" aria-label="Main navigation">
        <a href="/" class:current={page === 'home'} aria-current={page === 'home' ? 'page' : undefined} onclick={event => navigate(event, '/')}><featureIcons.home size={iconSizes.navigation} />Home</a>
        <a href="/infrastructure" class:current={page === 'infrastructure'} aria-current={page === 'infrastructure' ? 'page' : undefined} onclick={event => navigate(event, '/infrastructure')}><featureIcons.infrastructure size={iconSizes.navigation} />Infrastructure</a>
        <a href="/hacker-news" class:current={page === 'hacker-news'} aria-current={page === 'hacker-news' ? 'page' : undefined} onclick={event => navigate(event, '/hacker-news')}><featureIcons.hackerNews size={iconSizes.navigation} />Hacker News</a>
      </nav>

      <div class="top-actions">
        <button class="header-search" aria-label="Search shortcuts or the web" aria-haspopup="dialog" aria-expanded={paletteOpen} aria-controls={paletteOpen?'command-search':undefined} onclick={openSearch}><Search size={iconSizes.action}/><span>Search</span><kbd>Ctrl K</kbd></button>
        {#if page==='home'}<button class="icon-button" aria-label="Customize homepage" title="Customize homepage" aria-expanded={customizingHome} onclick={()=>customizingHome=!customizingHome}><Settings2 size={iconSizes.action}/></button>{/if}
        <button
          class="icon-button"
          onclick={toggleTheme}
          aria-label="Toggle color theme"
        >
          {#if theme === 'dark'}
            <Sun size={iconSizes.action} />
          {:else}
            <Moon size={iconSizes.action} />
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
      <h1 class="sr-only">Home</h1>
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

  </div>

  {#if paletteOpen}<CommandPalette bind:this={searchPalette} commands={shortcutState.items} loaded={shortcutState.loaded} loading={shortcutState.loading} onexecute={execute} onclose={()=>paletteOpen=false}/>{/if}
  {#if toast}
    <div class="toast">
      <Command size={iconSizes.action} />
      {toast}
    </div>
  {/if}
</div>
