<script lang="ts">
  import { onMount, tick } from 'svelte';
  import { ArrowUpRight, ChevronDown, Container, RefreshCw, TriangleAlert } from '@lucide/svelte';
  import { fetchJson } from '../lib/infrastructure';
  import { containerVersion, effectiveState, groupContainers, monitorMatches, parseMonitorTarget, wudAge, type ContainerSnapshot } from '../lib/container-updates';

  let { revision = 0, monitorFocus = null }: { revision?: number; monitorFocus?: string | null } = $props();
  const focusTarget = $derived(parseMonitorTarget(monitorFocus));
  let focusHandled = $state<string | null>(null);
  let targetNotice = $state('');
  let expanded = $state<string[]>([]);
  function projectId(id: string) { return `wud-project-${encodeURIComponent(id)}`; }
  function containerId(watcher: string, id: string) { return `wud-container-${encodeURIComponent(JSON.stringify([watcher, id]))}`; }
  function toggleProject(id: string, open: boolean) {
    expanded = open ? [...new Set([...expanded, id])] : expanded.filter(key => key !== id);
  }
  let snapshot = $state<ContainerSnapshot | null>(null);
  let loading = $state(false);
  let error = $state('');
  let filter = $state<'all' | 'update' | 'unknown'>('all');
  let query = $state('');
  let now = $state(Date.now());
  let mounted = false;
  let disposed = false;
  let controller: AbortController | undefined;
  const projects = $derived(groupContainers(snapshot));
  const stale = $derived(Boolean(snapshot?.stale || error));
  const viewProjects = $derived(stale ? groupContainers(snapshot ? { ...snapshot, stale: true } : null) : projects);
  const visibleProjects = $derived(viewProjects.filter(project => {
    const search = query.trim().toLowerCase();
    return (filter === 'all' || project.state === filter)
      && (!search || `${project.name} ${project.watcher} ${project.containers.map(item => `${item.name} ${item.image}`).join(' ')}`.toLowerCase().includes(search));
  }));
  const updates = $derived(projects.reduce((total, project) => total + project.updates, 0));
  const unknown = $derived(projects.reduce((total, project) => total + project.unknown, 0));
  const notice = $derived(error || snapshot?.error || '');
  const labels = { update: 'Update available', current: 'No update reported', unknown: 'Check incomplete' };

  async function refresh() {
    if (loading || disposed) return;
    loading = true;
    controller = new AbortController();
    try {
      const result = await fetchJson<ContainerSnapshot>('/api/infrastructure/containers', controller.signal);
      if (!disposed) { snapshot = result; error = ''; now = Date.now(); }
    } catch {
      if (!disposed) error = 'Could not refresh container updates. Last available details are kept below.';
    } finally {
      if (!disposed) loading = false;
    }
  }

  $effect(() => { void revision; if (mounted) void refresh(); });

  $effect(() => {
    const target = focusTarget;
    const signature = monitorFocus;
    if (!target) { focusHandled = null; targetNotice = ''; return; }
    if (!snapshot || snapshot.stale || error || focusHandled === signature) return;
    if (!snapshot.configured) { targetNotice = 'Container monitoring is not connected.'; return; }
    const project = projects.find(project => project.containers.some(item => monitorMatches(target, item)));
    if (!project) { targetNotice = 'The linked container or project is not currently reported by WUD. You can review other projects below or refresh the results.'; return; }
    const item = project.containers.find(item => monitorMatches(target, item))!;
    expanded = [...new Set([...expanded, project.id])];
    focusHandled = signature;
    targetNotice = '';
    void tick().then(() => {
      if (disposed || signature !== monitorFocus) return;
      const element = target.kind === 'project' ? document.getElementById(projectId(project.id))?.querySelector<HTMLElement>('summary')
        : document.getElementById(containerId(item.watcher, item.id));
      element?.scrollIntoView({ block: target.kind === 'project' ? 'start' : 'center', behavior: 'instant' });
      element?.focus({ preventScroll: true });
    });
  });

  onMount(() => {
    mounted = true;
    disposed = false;
    void refresh();
    const poll = setInterval(() => { if (!document.hidden) void refresh(); }, 60000);
    const tick = setInterval(() => { if (!document.hidden) now = Date.now(); }, 30000);
    const onVisible = () => { if (!document.hidden) void refresh(); };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      mounted = false; disposed = true; controller?.abort();
      clearInterval(poll); clearInterval(tick);
      document.removeEventListener('visibilitychange', onVisible);
    };
  });
</script>

{#if snapshot?.configured || error || focusTarget}
  <section id="container-updates" class="container-updates" aria-labelledby="container-updates-title" aria-busy={loading}>
    <header class="section-heading">
      <div><div class="container-eyebrow"><Container size={15} />CONTAINERS</div><h2 id="container-updates-title">Container updates</h2><p>{projects.length} project groups · {snapshot?.containers.length ?? 0} containers watched by WUD</p></div>
      <div class="actions">
        {#if snapshot?.url}<a href={snapshot.url} target="_blank" rel="noopener noreferrer">Open WUD <ArrowUpRight size={13} /></a>{/if}
        <button onclick={refresh} disabled={loading} aria-label="Refresh container update results" title="Read the latest results from WUD"><RefreshCw size={14} /><span>{loading ? 'Refreshing' : 'Refresh'}</span></button>
      </div>
    </header>
    {#if notice}<div class="notice" role="status"><TriangleAlert size={16} /><p>{notice}{snapshot?.fetched_at ? ' Showing previous details; update status is unavailable.' : ''}</p></div>{/if}
    {#if targetNotice}<p class="target-notice" role="status">{targetNotice}</p>{/if}
    <div class="summary-grid">
      <article><span>PROJECTS WITH UPDATES</span><strong>{stale ? '—' : projects.filter(project => project.updates > 0).length}</strong></article>
      <article><span>CONTAINERS WITH UPDATES</span><strong>{stale ? '—' : updates}</strong></article>
      <article class:attention={unknown > 0 || stale}><span>CHECKS INCOMPLETE</span><strong>{stale ? '—' : unknown}</strong></article>
    </div>
    <div class="filters">
      <div role="group" aria-label="Filter container projects">
        <button class:active={filter === 'all'} aria-pressed={filter === 'all'} onclick={() => filter = 'all'}>All</button>
        <button class:active={filter === 'update'} aria-pressed={filter === 'update'} onclick={() => filter = 'update'}>Updates</button>
        <button class:active={filter === 'unknown'} aria-pressed={filter === 'unknown'} onclick={() => filter = 'unknown'}>Needs review</button>
      </div>
      <input type="search" bind:value={query} aria-label="Find a project or container" placeholder="Find a project or container…" />
    </div>
    <div class="projects">
      {#each visibleProjects as project (project.id)}
        <details id={projectId(project.id)} class="project" open={expanded.includes(project.id)} ontoggle={event => toggleProject(project.id, event.currentTarget.open)}>
          <summary>
            <span class="project-name"><Container size={17} /><span><strong>{project.name}</strong><small>{project.containers.length} container{project.containers.length === 1 ? '' : 's'} · {project.watcher}</small></span></span>
            <span class="project-status"><span class="badge" class:update={project.state === 'update'} class:unknown={project.state === 'unknown'}>{stale ? 'Status unavailable' : project.updates ? `${project.updates} update${project.updates === 1 ? '' : 's'}` : project.unknown ? `${project.unknown} incomplete` : 'No updates reported'}</span><ChevronDown size={15} /></span>
          </summary>
          <div class="container-list">
            {#each project.containers as item (`${item.watcher}:${item.id}`)}
              {@const state = effectiveState(item, stale)}
              <article id={containerId(item.watcher, item.id)} class="container-row" class:linked-target={focusTarget && monitorMatches(focusTarget, item)} tabindex="-1" aria-label={item.name}>
                <div class="container-heading"><h3>{item.name}</h3><span class="badge" class:update={state === 'update'} class:unknown={state === 'unknown'}>{stale ? 'Status unavailable' : labels[state]}</span>{#if state === 'update' && item.change === 'major'}<span class="badge major">Major version</span>{/if}</div>
                <p class="image-name">{item.image}{item.current_tag ? `:${item.current_tag}` : ''}</p>
                <dl><div><dt>Installed</dt><dd title={item.current_digest ?? ''}>{containerVersion(item)}</dd></div>{#if state === 'update'}<div><dt>Available</dt><dd title={item.available_digest ?? ''}>{containerVersion(item, true)}</dd></div>{/if}<div><dt>Container</dt><dd>{item.runtime_status}</dd></div></dl>
                {#if state === 'unknown'}<p class="reason">{stale ? 'Waiting for a successful refresh from WUD.' : item.reason}</p>{/if}
                <p class="checked">{item.checked_at ? `WUD checked ${wudAge(item.checked_at, now)}` : 'WUD check time unavailable'}</p>
              </article>
            {/each}
          </div>
        </details>
      {:else}
        <p class="empty">{!snapshot?.containers.length ? 'WUD has not reported any watched containers yet.' : filter === 'update' ? 'No projects with reported updates match this view.' : 'No projects match this view.'}</p>
      {/each}
    </div>
    <p class="footnote">{snapshot?.fetched_at ? `Synced with WUD ${wudAge(snapshot.fetched_at, now)}. ` : ''}Results follow WUD’s watch settings. Refresh reads its saved results; scans and updates are managed in WUD.</p>
  </section>
{/if}

<style>
  .container-updates, .project, .container-row { scroll-margin-top: 25px; }
  .linked-target { box-shadow: inset 3px 0 var(--highlight); }
  .target-notice { color: var(--muted); font-size: 12px; line-height: 1.7; margin: 0 0 17px; }
  .container-updates { margin-top: 38px; padding-top: 30px; border-top: 1px solid var(--line); }
  .section-heading, .actions, .container-eyebrow, .project-name, .project-status, .container-heading, .filters { display: flex; align-items: center; gap: 12px; }
  .section-heading { justify-content: space-between; flex-wrap: wrap; margin-bottom: 21px; }
  .container-eyebrow { justify-content: flex-start; color: var(--highlight); font-size: 9px; letter-spacing: 2px; font-weight: 700; gap: 8px; }
  h2 { margin: 10px 0 7px; font-size: 23px; letter-spacing: -.5px; font-weight: 500; }
  .section-heading p, .footnote, .checked, .reason, .empty { color: var(--muted); font-size: 11px; line-height: 1.65; }
  .section-heading p { margin: 0; }
  button, a { display: inline-flex; align-items: center; justify-content: center; gap: 7px; padding: 9px 11px; border: 1px solid var(--line); background: var(--surface); color: var(--muted); border-radius: 7px; font: inherit; font-size: 11px; text-decoration: none; cursor: pointer; }
  button:hover, a:hover, button.active { color: var(--highlight); border-color: var(--highlight); }
  button:disabled { cursor: wait; opacity: .6; }
  .notice { display: flex; align-items: flex-start; gap: 9px; color: var(--warning); padding: 13px 16px; border: 1px solid var(--line); border-radius: 10px; margin-bottom: 16px; }
  .notice p { margin: 0; font-size: 12px; line-height: 1.5; }
  .notice :global(svg) { flex-shrink: 0; }
  .summary-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-bottom: 18px; }
  .summary-grid article { background: var(--surface); border: 1px solid var(--line); border-radius: 13px; padding: 19px; }
  .summary-grid article > span { color: var(--muted); font-size: 9px; letter-spacing: 1px; display: block; }
  .summary-grid strong { display: block; margin-top: 12px; font-size: 30px; font-weight: 500; font-variant-numeric: tabular-nums; }
  .attention strong { color: var(--warning); }
  .filters { justify-content: space-between; flex-wrap: wrap; margin-bottom: 15px; }
  .filters > div { display: flex; gap: 6px; }
  input { min-width: 0; width: 230px; border: 1px solid var(--line); background: var(--surface); color: var(--text); padding: 10px 12px; font: inherit; font-size: 11px; border-radius: 7px; }
  .projects { display: grid; gap: 10px; }
  .project { min-width: 0; border: 1px solid var(--line); border-radius: 12px; background: var(--surface); overflow: hidden; }
  summary { display: flex; align-items: center; justify-content: space-between; gap: 15px; padding: 18px; list-style: none; cursor: pointer; }
  summary::-webkit-details-marker { display: none; }
  .project-name { color: var(--highlight); min-width: 0; }
  .project-name strong { display: block; font-size: 13px; font-weight: 500; color: var(--text); overflow-wrap: anywhere; }
  .project-name small { display: block; color: var(--muted); font-size: 10px; margin-top: 5px; }
  .project-status { flex-shrink: 0; color: var(--muted); gap: 9px; }
  .badge { display: inline-block; color: var(--muted); background: var(--bg); border: 1px solid var(--line); border-radius: 5px; padding: 4px 7px; font-size: 10px; white-space: nowrap; }
  .badge.update, .badge.major { color: var(--warning); }
  .badge.unknown { color: var(--muted); }
  .container-list { border-top: 1px solid var(--line); }
  .container-row { padding: 17px 19px; border-bottom: 1px solid var(--line); }
  .container-row:last-child { border-bottom: 0; }
  .container-heading { flex-wrap: wrap; gap: 8px; }
  h3 { margin: 0 auto 0 0; font-size: 12px; font-weight: 500; overflow-wrap: anywhere; }
  .image-name { color: var(--muted); margin: 8px 0 15px; font-family: var(--font-mono, monospace); font-size: 10px; overflow-wrap: anywhere; }
  dl { display: flex; flex-wrap: wrap; gap: 14px 28px; margin: 0; }
  dl > div { min-width: 0; flex: 1 1 120px; }
  dt { font-size: 9px; color: var(--muted); margin-bottom: 5px; }
  dd { margin: 0; font-size: 11px; overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }
  .checked, .reason { margin: 11px 0 0; font-size: 10px; }
  .reason { color: var(--warning); }
  .empty { padding: 24px 12px; text-align: center; }
  .footnote { margin: 16px 0 0; font-size: 10px; }
  @media (max-width: 600px) {
    .summary-grid { gap: 8px; }
    .summary-grid article { padding: 14px 11px; }
    .summary-grid article > span { font-size: 8px; letter-spacing: .4px; }
    .summary-grid strong { font-size: 26px; }
    .filters input { width: 100%; font-size: 16px; }
    summary { padding: 15px 12px; gap: 8px; }
    .project-status { gap: 5px; }
    .badge { font-size: 9px; padding: 4px 5px; }
    .container-row { padding: 16px 13px; }
  }
</style>
