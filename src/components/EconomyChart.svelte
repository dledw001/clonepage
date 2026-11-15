<script lang="ts">
  import { economicPlot, economyValue, observationLabel, closestPoint, type EconomyPoint } from '../lib/economy';
  let { points, start, end, unit, frequency, title, secondary = false }: {
    points: EconomyPoint[]; start: string; end: string; unit: string; frequency: string; title: string; secondary?: boolean;
  } = $props();
  let active = $state<number | null>(null);
  const plot = $derived(economicPlot(points, start, end));
  const index = $derived(Math.max(0, Math.min(active ?? plot.selected.length - 1, plot.selected.length - 1)));
  const selected = $derived(plot.selected[index]);
  const selectedLabel = $derived(selected ? `${observationLabel(selected.date, frequency)}: ${secondary ? 'Year over year ' : ''}${selected.value === null ? 'unavailable' : economyValue(selected.value, unit)}${secondary ? '; 3-month annualized ' + (selected.secondary === null ? 'unavailable' : economyValue(selected.secondary, '%')) : ''}` : 'No observations');
  function pointAt(event: PointerEvent) {
    const box = (event.currentTarget as HTMLElement).getBoundingClientRect();
    const fraction = Math.max(0, Math.min(1, (event.clientX - box.left) / box.width));
    const beginning = Date.parse(start + 'T00:00:00Z'), ending = Date.parse(end + 'T00:00:00Z');
    if (plot.selected.length) active = closestPoint(plot.selected, new Date(beginning + fraction * (ending - beginning)).toISOString().slice(0, 10));
  }
  function move(event: KeyboardEvent) {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End', 'Escape'].includes(event.key)) return;
    event.preventDefault();
    if (event.key === 'Escape') active = null;
    else if (event.key === 'Home') active = 0;
    else if (event.key === 'End') active = plot.selected.length - 1;
    else active = Math.max(0, Math.min(plot.selected.length - 1, index + (event.key === 'ArrowLeft' ? -1 : 1)));
  }
</script>

{#if plot.selected.length}
  <div class="economic-chart">
    <div class="chart-body">
      <div class="y-axis" aria-hidden="true"><span>{economyValue(plot.maximum, unit, false, unit === 'claims' || unit === 'bps' ? 0 : 1)}</span><span>{economyValue(plot.minimum, unit, false, unit === 'claims' || unit === 'bps' ? 0 : 1)}</span></div>
      <div class="plot" role="slider" tabindex="0" aria-label={`${title} history. Use arrow keys to explore observations.`}
           aria-valuemin={0} aria-valuemax={Math.max(1, plot.selected.length - 1)} aria-valuenow={index} aria-valuetext={selectedLabel}
           onpointermove={pointAt} onpointerdown={pointAt} onpointerleave={() => active = null} onkeydown={move} onblur={() => active = null}>
        <svg viewBox="0 0 600 160" preserveAspectRatio="none" aria-hidden="true">
          {#each [0, 80, 160] as y}<line x1="0" y1={y} x2="600" y2={y} class="gridline" />{/each}
          {#if plot.zero !== null}<line x1="0" y1={plot.zero} x2="600" y2={plot.zero} class="zero-line" />{/if}
          <path d={plot.paths[0]} class="main-line" />
          {#if secondary}<path d={plot.paths[1]} class="secondary-line" />{/if}
          {#each plot.selected.length <= 32 ? plot.selected.filter(point => point.value !== null) : [] as point}
            <circle cx={(Date.parse(point.date+'T00:00:00Z')-Date.parse(start+'T00:00:00Z'))/Math.max(1,Date.parse(end+'T00:00:00Z')-Date.parse(start+'T00:00:00Z'))*600}
                    cy={(plot.maximum-point.value!)/(plot.maximum-plot.minimum)*160} r="2.5" class="observation-dot" />
          {/each}
        </svg>
        {#if active !== null && selected}<span class="cursor" style:left={`${(Date.parse(selected.date+'T00:00:00Z')-Date.parse(start+'T00:00:00Z'))/Math.max(1,Date.parse(end+'T00:00:00Z')-Date.parse(start+'T00:00:00Z'))*100}%`}></span>{/if}
      </div>
    </div>
    <div class="x-axis" aria-hidden="true"><span>{new Date(start+'T00:00:00Z').toLocaleDateString('en-US',{timeZone:'UTC',month:'short',year:'numeric'})}</span><span>{new Date(end+'T00:00:00Z').toLocaleDateString('en-US',{timeZone:'UTC',month:'short',year:'numeric'})}</span></div>
    <div class="chart-reading" aria-live="polite">{#if active !== null && selected}<span>{selectedLabel}</span>{:else}<span>Hover, tap, or use arrow keys to explore</span>{/if}</div>
    {#if secondary}<div class="legend"><span><i></i>Year over year</span><span><i class="secondary"></i>3-month annualized</span></div>{/if}
  </div>
{:else}<p class="empty-chart">No observations are available for this range.</p>{/if}

<style>
  .economic-chart { margin-top:20px; }
  .chart-body { display:flex; gap:12px; }
  .y-axis { width:68px; flex-shrink:0; height:160px; display:flex; flex-direction:column; justify-content:space-between; text-align:right; color:var(--muted); font-size:var(--font-caption); font-variant-numeric:tabular-nums; }
  .plot { flex:1; min-width:0; height:160px; position:relative; touch-action:pan-y; outline-offset:4px; }
  .plot:focus-visible { outline:2px solid var(--highlight); }
  svg { display:block; width:100%; height:100%; overflow:visible; }
  .gridline { stroke:var(--line); stroke-dasharray:3 5; vector-effect:non-scaling-stroke; }
  .zero-line { stroke:var(--muted); stroke-dasharray:5 5; opacity:.5; vector-effect:non-scaling-stroke; }
  path { fill:none; stroke-width:2; stroke-linejoin:round; vector-effect:non-scaling-stroke; }
  .main-line { stroke:var(--highlight); }.secondary-line { stroke:var(--text); stroke-dasharray:5 4; }
  .observation-dot { fill:var(--highlight); }
  .cursor { position:absolute; inset-block:0; width:1px; background:var(--muted); opacity:.5; pointer-events:none; }
  .x-axis { display:flex; justify-content:space-between; margin:9px 0 0 80px; color:var(--muted); font-size:var(--font-caption); }
  .chart-reading { display:flex; flex-wrap:wrap; gap:4px 12px; margin-top:12px; min-height:40px; color:var(--muted); font-size:var(--font-caption); line-height:1.6; font-variant-numeric:tabular-nums; overflow-wrap:anywhere; }
  .legend,.legend span { display:flex; align-items:center; flex-wrap:wrap; gap:6px 12px; font-size:var(--font-caption); color:var(--muted); }
  .legend i { width:18px; border-top:2px solid var(--highlight); }.legend .secondary { border-top:2px dashed var(--text); }
  .empty-chart { min-height:200px; display:grid; place-items:center; text-align:center; color:var(--muted); font-size:var(--font-small); }
  @media (max-width:540px) { .chart-body { gap:8px; }.y-axis { width:58px; }.x-axis { margin-left:66px; } }
</style>
