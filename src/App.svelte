<script lang="ts">
  import { onMount } from 'svelte';
  import {
    ArrowRight, ArrowUpRight, Cloud,
    Command, GitBranch, HardDrive,
    LockKeyhole, Moon, Search, Sun,
    Terminal, Network
  } from '@lucide/svelte';

  import ReadingList from './components/ReadingList.svelte';

  type Action = 'open' | 'copy';

  const commands = [
    {
      id: 'nas',
      name: 'Synology NAS',
      description: 'Open DSM',
      aliases: ['nas', 'synology', 'dsm'],
      icon: HardDrive,
      color: '#a4b9e4',
      action: 'open' as Action,
      value: '' // Add your NAS URL
    },
    {
      id: 'vault',
      name: 'Vaultwarden',
      description: 'Password manager',
      aliases: ['vault', 'passwords', 'vaultwarden'],
      icon: LockKeyhole,
      color: '#a9c9b4',
      action: 'open' as Action,
      value: 'https://vault.dledwith.com' // Add your Vaultwarden URL
    },
    {
      id: 'github',
      name: 'GitHub',
      description: 'Code repositories',
      aliases: ['github', 'git', 'repos'],
      icon: GitBranch,
      color: '#bdc3d5',
      action: 'open' as Action,
      value: 'https://github.com/dledw001'
    },
    {
      id: 'ssh',
      name: 'SSH into Arch',
      description: 'Copy SSH command',
      aliases: ['ssh arch', 'arch', 'ssh'],
      icon: Terminal,
      color: '#c6b4e6',
      action: 'copy' as Action,
      value: 'ssh arch'
    },
    {
      id: 'tailscale',
      name: 'Tailscale',
      description: 'Network administration',
      aliases: ['tailscale', 'vpn', 'network'],
      icon: Network,
      color: '#d4b5a3',
      action: 'open' as Action,
      value: 'https://login.tailscale.com/admin/machines'
    },
    {
      id: 'cloudflare',
      name: 'Cloudflare',
      description: 'Domains and DNS',
      aliases: ['cloudflare', 'cf', 'domains'],
      icon: Cloud,
      color: '#e2b780',
      action: 'open' as Action,
      value: 'https://dash.cloudflare.com'
    }
  ];

  type CommandItem = (typeof commands)[number];

  let now = $state(new Date());
  let query = $state('');
  let paletteOpen = $state(false);
  let selected = $state(0);
  let theme = $state<'dark' | 'light'>('dark');
  let toast = $state('');

  let searchInput: HTMLInputElement;
  let searchRegion: HTMLDivElement;

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
    const saved = localStorage.getItem('clonepage-theme');
    if (saved === 'light' || saved === 'dark') {
      theme = saved;
    }

    const interval = setInterval(() => {
      now = new Date();
    }, 1000);

    return () => clearInterval(interval);
  });

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

  async function execute(command: CommandItem) {
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
      notify(`Configure the ${command.name} URL in App.svelte`);
      return;
    }

    window.open(command.value, '_blank', 'noopener,noreferrer');
  }

  function searchWeb() {
    const text = query.trim();
    if (!text) return;

    // Recognize URLs, domains, and local addresses.
    const isUrl = /^(https?:\/\/|localhost(?::\d+)?(?:\/|$)|(?:[\w-]+\.)+[\w-]{2,}(?::\d+)?(?:\/|$))/i.test(text);

    const url = isUrl
      ? (/^https?:\/\//i.test(text) ? text : `https://${text}`)
      : `https://www.google.com/search?q=${encodeURIComponent(text)}`;

    window.open(url, '_blank', 'noopener,noreferrer');
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

      if (matches.length && selected >= 0) {
        execute(matches[selected]);
      } else {
        searchWeb();
      }
    }
  }

  function handleGlobalKeydown(event: KeyboardEvent) {
    if ((event.ctrlKey || event.metaKey) &&
        event.key.toLowerCase() === 'k') {
      event.preventDefault();
      searchInput.focus();
      paletteOpen = true;
    }

    if (event.key === 'Escape') {
      paletteOpen = false;
      searchInput.blur();
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
      <a href="/" class="brand">
        <span class="brand-mark">✳</span>
        <span>clonepage<span class="brand-dot">.</span></span>
      </a>

      <div class="top-actions">
        <span class="top-caption">YOUR PERSONAL SPACE</span>
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
      <section class="hero">
        <div class="eyebrow">
          <span class="eyebrow-line"></span>
          A LITTLE ROOM TO THINK
          <span class="eyebrow-line"></span>
        </div>

        <div class="clock" style="font-family: monospace">{time}</div>
        <div class="date">{date}</div>

        <h1>{greeting}</h1>

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

              {#each matches as command, index (command.id)}
                <button
                  class="result"
                  class:selected={selected === index}
                  onmouseenter={() => selected = index}
                  onclick={() => execute(command)}
                >
                  <span class="result-icon">
                    <command.icon size={19} strokeWidth={1.7} />
                  </span>
                  <span class="result-text">
                    <strong>{command.name}</strong>
                    <small>{command.description}</small>
                  </span>
                  <ArrowRight size={16} />
                </button>
              {/each}

              {#if query.trim()}
                <button class="web-result" onclick={searchWeb}>
                  <Search size={17} />
                  <span>Search the web for "{query}"</span>
                  <ArrowUpRight size={16} />
                </button>
              {/if}
            </div>
          {/if}
        </div>

        <p class="search-hint">
          Try <code>nas</code>, <code>vault</code>,
          or <code>ssh arch</code>
        </p>
      </section>

      <section class="shortcuts">
        <div class="section-header">
          <span>YOUR PLACES</span>
          <span class="section-count">
            {String(commands.length).padStart(2, '0')}
            SHORTCUTS
          </span>
        </div>

        <div class="shortcut-grid">
          {#each commands as command (command.id)}
            <button
              class="shortcut-card"
              style={`--accent:${command.color}`}
              onclick={() => execute(command)}
            >
              <span class="shortcut-icon">
                <command.icon size={21} strokeWidth={1.65} />
              </span>
              <span class="shortcut-bottom">
                <span class="shortcut-name">{command.name}</span>
                <ArrowUpRight size={14} class="shortcut-arrow" />
              </span>
            </button>
          {/each}
        </div>
      </section>
      <ReadingList />
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
