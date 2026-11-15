<script lang="ts">
  import { onMount } from 'svelte';
  import { ArrowDownRight, ArrowUpRight, Clock3, Minus, RefreshCw } from '@lucide/svelte';
  import { featureIcons, iconSizes } from '../lib/icons';
  import { HomeDataStore, type HomeDataState } from '../lib/home';
  import { economyGroups, economyValue, historyStart, movement, observationLabel, updatedLabel, type EconomicIndicator, type EconomySnapshot } from '../lib/economy';
  import EconomyChart from './EconomyChart.svelte';

  let dashboardState = $state<HomeDataState<EconomySnapshot>>({ data:null, loading:false, error:'' });
  const store = new HomeDataStore<EconomySnapshot>('/api/economy', next => dashboardState = next);
  let years = $state(5);
  const indicators = $derived(dashboardState.data?.indicators ?? []);
  const total = $derived(indicators.length);
  const available = $derived(indicators.filter(item => item.latest).length);
  const checking = $derived(dashboardState.loading || Boolean(dashboardState.data?.refreshing));
  const asOf = $derived(dashboardState.data?.as_of ?? new Date().toISOString());
  const end = $derived(asOf.slice(0,10));
  function start(item: EconomicIndicator) { return historyStart(asOf, Math.min(years, item.history_limit_years ?? years)); }
  function latestValue(item: EconomicIndicator) { return economyValue(item.latest?.value, item.unit, false, item.id === 'UNRATE' ? 1 : item.unit === 'claims' || item.unit === 'bps' ? 0 : 2); }
  onMount(() => {
    void store.refresh();
    const visible = () => { if (!document.hidden) void store.refresh(); };
    const minute = setInterval(visible, 60000);
    const pending = setInterval(() => { if (!document.hidden && dashboardState.data?.refreshing) void store.refresh(); }, 3000);
    window.addEventListener('focus', visible);
    document.addEventListener('visibilitychange', visible);
    return () => { store.dispose(); clearInterval(minute); clearInterval(pending); window.removeEventListener('focus', visible); document.removeEventListener('visibilitychange', visible); };
  });
</script>

<section class="economy" aria-label="Economic dashboard">
  <header class="page-heading">
    <div class="page-title"><featureIcons.economy size={iconSizes.page}/><div><h1>Economic dashboard</h1><p>U.S. economic conditions · {total ? `${total} indicators from FRED` : 'FRED'}</p></div></div>
    <div class="page-actions">
      <div class="history-options" role="group" aria-label="Economic history range">
        {#each [1,5] as range}<button class:active={years === range} aria-pressed={years === range} onclick={() => years = range}>{range} year{range === 1 ? '' : 's'}</button>{/each}
      </div>
      <button class="refresh" disabled={checking} onclick={() => store.refresh()} title="Refresh the dashboard. FRED is checked daily, including historical revisions."><RefreshCw size={iconSizes.action}/>{checking ? 'Checking…' : 'Refresh'}</button>
    </div>
  </header>
  <div class="dashboard-note"><Clock3 size={iconSizes.inline}/><p>Checked daily. Each indicator retains its native release frequency; observation dates differ.</p>{#if total}<span>{available} of {total} available</span>{/if}</div>
  {#if dashboardState.error}<p class="notice" role="alert">{dashboardState.error} {dashboardState.data ? 'Your last available dashboard is kept below.' : ''}</p>{/if}
  {#if indicators.some(item => item.error || item.stale) && !checking}<p class="notice" role="status">Some FRED checks are incomplete. Available observations are retained and labeled on each card.</p>{/if}
  {#if dashboardState.loading && !dashboardState.data}<div class="loading" role="status">Loading economic indicators…</div>{/if}
  {#if checking && available < total && dashboardState.data}<p class="loading-inline" role="status">Retrieving available observations from FRED. Indicators appear as each check finishes.</p>{/if}

  {#each economyGroups as group}
    {@const items = indicators.filter(item => item.group === group)}
    {#if items.length}
      <section class="indicator-group" aria-label={group}>
        <div class="group-heading"><h2>{group}</h2><span>{items.length} indicators</span></div>
        <div class="indicator-grid">
          {#each items as item (item.id)}
            {@const direction = movement(item.change)}
            <article class="indicator-card" aria-label={item.title}>
              <header class="card-heading"><h3>{item.title}</h3><a href={item.source_url} target="_blank" rel="noopener noreferrer" aria-label={`${item.id} on FRED`}>{item.id}<ArrowUpRight size={iconSizes.inline}/></a></header>
              <p class="frequency">{item.frequency} · {item.adjustment}</p>
              <div class="value-row"><strong class="current-value">{latestValue(item)}</strong>{#if item.stale && item.latest}<span class="stale">Cached data</span>{/if}</div>
              <p class="basis">{item.basis}{item.unit === 'claims' ? ' · claims' : ''}</p>
              <p class="observation">Observation: <strong>{observationLabel(item.latest?.date, item.frequency)}</strong></p>
              <div class="change" aria-label={`${item.change?.label ?? 'Recent change'}: ${direction === 'up' ? 'rising' : direction === 'down' ? 'falling' : direction === 'flat' ? 'unchanged' : 'unavailable'}`}>
                {#if direction === 'up'}<ArrowUpRight size={iconSizes.action}/>{:else if direction === 'down'}<ArrowDownRight size={iconSizes.action}/>{:else}<Minus size={iconSizes.action}/>{/if}
                <strong>{economyValue(item.change?.value, item.change?.unit ?? 'pp', true)}</strong><span>{item.change?.label ?? 'Comparison unavailable'}</span>
              </div>
              {#if item.id === 'PCEPILFE'}<p class="secondary-value">3-month annualized <strong>{economyValue(item.latest?.secondary, '%')}</strong></p>{/if}
              {#if item.id === 'SP500'}<p class="secondary-value">From 52-week closing high <strong>{economyValue(item.drawdown?.value, '%', true)}</strong></p>{/if}
              {#if item.error}<p class="card-error" role="status">{item.error} {item.latest ? 'Showing the last successful download.' : 'No observations are available yet.'}</p>{/if}
              {#if item.history_limit_years && years > item.history_limit_years}<p class="history-note">FRED limits this series to {item.history_limit_years} years; available history is shown.</p>{/if}
              {#if item.latest}<EconomyChart points={item.points} start={start(item)} {end} unit={item.unit} frequency={item.frequency} title={item.title} secondary={item.id === 'PCEPILFE'}/>{:else}<div class="empty-history">{checking ? 'Waiting for FRED observations…' : 'History unavailable'}</div>{/if}
              <div class="publication"><span>FRED updated</span><time datetime={item.fred_updated_at ?? undefined}>{updatedLabel(item.fred_updated_at)}</time></div>
              <div class="publication"><span>Last checked</span><time datetime={item.checked_at ?? undefined}>{updatedLabel(item.checked_at)}</time></div>
              <details class="context"><summary>Interpretation & calculation</summary><p>{item.interpretation}</p><p>{item.methodology}</p>
                {#if item.change?.comparison_date}<p>{item.id === 'UNRATE' ? 'Previous three-month period ends' : 'Comparison observation'}: {observationLabel(item.change.comparison_date, item.frequency)}.</p>{/if}
                {#if item.id === 'UNRATE' && item.change}<p>Latest three-month average: {economyValue(item.change.current_average, '%')}. Preceding three-month average: {economyValue(item.change.previous_average, '%')}.</p>{/if}
                {#if item.id === 'SP500'}
                  {#if item.drawdown}<p>52-week closing high: {economyValue(item.drawdown.high, 'index')} on {observationLabel(item.drawdown.high_date, 'Daily')}. Window starts {observationLabel(item.drawdown.window_start, 'Daily')}.</p>
                  {:else}<p>A full 52-week history of closing values is needed to calculate drawdown.</p>{/if}
                {/if}
                <p>Source: {item.source}, via FRED.</p>
                {#if item.metadata_warning}<p>{item.metadata_warning}</p>{/if}
              </details>
            </article>
          {/each}
        </div>
      </section>
    {/if}
  {/each}
  <footer>Data from FRED, Federal Reserve Bank of St. Louis, and the listed providers. Historical releases may be revised. Arrows describe movement, not a good/bad score.</footer>
</section>

<style>
  .economy { max-width:1060px; margin:0 auto; }
  .page-heading,.page-title,.page-actions,.history-options,.refresh,.card-heading,.group-heading,.change,.dashboard-note { display:flex; align-items:center; }
  .page-heading { justify-content:space-between; flex-wrap:wrap; gap:18px; margin-bottom:20px; }
  .page-title { gap:12px; color:var(--highlight); min-width:0; }
  h1 { font:inherit; font-size:24px; line-height:1.4; font-weight:500; letter-spacing:-.4px; color:var(--text); margin:0; }
  .page-title p { margin:5px 0 0; color:var(--muted); font-size:var(--font-small); }
  .page-actions { gap:12px; flex-wrap:wrap; }
  button { font:inherit; font-size:var(--font-small); color:var(--muted); cursor:pointer; }
  button:disabled { opacity:.55; cursor:default; }
  .history-options { padding:4px; gap:3px; background:var(--surface); border:1px solid var(--line); border-radius:9px; }
  .history-options button { border:0; padding:7px 11px; border-radius:6px; background:transparent; }
  .history-options button.active { background:var(--surface-hover); color:var(--text); }
  .refresh { gap:7px; padding:10px 12px; background:var(--surface); border:1px solid var(--line); border-radius:9px; }
  .dashboard-note { flex-wrap:wrap; gap:8px; color:var(--muted); font-size:var(--font-small); margin-bottom:24px; }
  .dashboard-note p { margin:0; flex:1; min-width:180px; }.dashboard-note span { margin-left:auto; white-space:nowrap; }
  .notice { border:1px solid var(--line); border-radius:10px; color:var(--warning); background:var(--surface); padding:12px 16px; font-size:var(--font-small); line-height:1.7; }
  .loading,.loading-inline { color:var(--muted); font-size:var(--font-body); padding:20px 0; }.loading-inline { padding:0; margin:0 0 20px; }
  .indicator-group { margin:30px 0; }
  .group-heading { justify-content:space-between; gap:12px; margin-bottom:14px; }
  h2 { margin:0; font-size:var(--font-section); line-height:1.5; font-weight:600; }
  .group-heading span { font-size:var(--font-caption); color:var(--muted); white-space:nowrap; }
  .indicator-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); align-items:start; gap:18px; }
  .indicator-card { min-width:0; padding:24px; border:1px solid var(--line); border-radius:var(--panel-radius); background:var(--surface); }
  .card-heading { justify-content:space-between; gap:10px; flex-wrap:wrap; }
  h3 { font-size:var(--font-title); line-height:1.5; font-weight:600; margin:0; }
  .card-heading a { display:inline-flex; gap:4px; align-items:center; color:var(--muted); text-decoration:none; font-size:var(--font-caption); }
  .card-heading a:hover { color:var(--highlight); }
  .frequency { font-size:var(--font-caption); color:var(--muted); margin:7px 0 0; }
  .value-row { display:flex; flex-wrap:wrap; align-items:baseline; gap:10px; margin-top:18px; }
  .current-value { font-size:34px; font-weight:500; line-height:1.2; letter-spacing:-1px; font-variant-numeric:tabular-nums; }
  .basis,.observation { font-size:var(--font-small); color:var(--muted); margin:6px 0 0; }.observation strong { font-weight:500; color:var(--text); }
  .stale { color:var(--warning); font-size:var(--font-caption); }
  .change { flex-wrap:wrap; gap:6px 8px; margin-top:14px; min-height:27px; color:var(--highlight); font-variant-numeric:tabular-nums; }
  .change strong { font-size:var(--font-body); font-weight:500; }.change span { color:var(--muted); font-size:var(--font-caption); }
  .secondary-value { display:flex; flex-wrap:wrap; gap:6px 12px; font-size:var(--font-small); color:var(--muted); margin:8px 0 0; }.secondary-value strong { color:var(--text); font-weight:500; }
  .card-error,.history-note { font-size:var(--font-caption); line-height:1.6; margin:12px 0 0; }.card-error { color:var(--warning); }.history-note { color:var(--muted); }
  .empty-history { min-height:230px; display:grid; place-items:center; color:var(--muted); font-size:var(--font-small); text-align:center; }
  .publication { display:flex; justify-content:space-between; flex-wrap:wrap; gap:3px 10px; color:var(--muted); font-size:var(--font-caption); margin-top:5px; }.publication time { font-variant-numeric:tabular-nums; }
  .context { border-top:1px solid var(--line); margin-top:15px; padding-top:12px; font-size:var(--font-small); color:var(--muted); }
  summary { cursor:pointer; color:var(--text); }details p { margin:12px 0 0; line-height:1.7; max-width:var(--measure); }
  footer { font-size:var(--font-caption); color:var(--muted); line-height:1.7; margin:25px 0; max-width:var(--measure); }
  :is(a,button,summary):focus-visible { outline:2px solid var(--highlight); outline-offset:4px; }
  @media (max-width:800px) { .indicator-grid { grid-template-columns:1fr; } }
  @media (max-width:540px) { .indicator-card { padding:20px 16px; }h1 { font-size:22px; }.page-title { align-items:flex-start; }.page-actions { width:100%; justify-content:space-between; }.group-heading { align-items:baseline; } }
  @media (hover:none) and (pointer:coarse) { button,summary { min-height:44px; } }
</style>
