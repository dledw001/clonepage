<script lang="ts">
  import { Activity } from '@lucide/svelte';
  import { buildPlot, formatValue, type ChartSeries } from '../lib/infrastructure';

  let { title, description, series, unit, start, end, loading = false, error = '' }: {
    title: string; description: string; series: ChartSeries[]; unit: string;
    start: number; end: number; loading?: boolean; error?: string;
  } = $props();

  let cursor = $state<number | null>(null);
  const plot = $derived(buildPlot(series, start, end, unit));
  const cursorPosition = $derived(cursor === null ? null : Math.max(0, Math.min(100, (cursor - start) / (end - start) * 100)));
  const hovered = $derived(cursor === null ? [] : plot.paths.flatMap(s => {
    const point = s.points.reduce<(typeof s.points)[number] | undefined>((best, p) => !best || Math.abs(p.ts - cursor!) < Math.abs(best.ts - cursor!) ? p : best, undefined);
    return point ? [{ ...s, point }] : [];
  }));

  function timeLabel(ts: number) {
    return new Date(ts * 1000).toLocaleString(undefined, end - start >= 86400
      ? { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }
      : { hour: 'numeric', minute: '2-digit' });
  }

  function pointAt(event: PointerEvent) {
    const rect = event.currentTarget instanceof HTMLElement ? event.currentTarget.getBoundingClientRect() : null;
    if (rect) cursor = start + Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)) * (end - start);
  }

  function moveCursor(event: KeyboardEvent) {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End', 'Escape'].includes(event.key)) return;
    event.preventDefault();
    if (event.key === 'Escape') cursor = null;
    else if (event.key === 'Home') cursor = start;
    else if (event.key === 'End') cursor = end;
    else cursor = Math.max(start, Math.min(end, (cursor ?? end) + (event.key === 'ArrowRight' ? 1 : -1) * (end - start) / 100));
  }
</script>

<section class="chart-card" aria-label={title}>
  <header>
    <div><h2>{title}</h2><p>{description}</p></div>
    {#if plot.hasData}<span class="peak">PEAK <strong>{formatValue(plot.maximum, unit)}</strong></span>{/if}
  </header>
  {#if error}<p class="chart-error" role="status">{error}</p>{/if}
  {#if plot.hasData}
    <div class="chart-body">
      <div class="y-axis" aria-hidden="true">{#each plot.ticks as tick}<span>{Math.round(tick)}{unit === 'C' ? '°' : '%'}</span>{/each}</div>
      <div class="plot-column">
        <div class="plot" role="slider" tabindex="0" aria-label={`${title.endsWith('history') ? title : title + ' history'}. Use arrow keys to explore readings.`}
             aria-valuemin={start} aria-valuemax={end} aria-valuenow={cursor ?? end} aria-valuetext={timeLabel(cursor ?? end)}
             onpointermove={pointAt} onpointerdown={pointAt} onpointerleave={() => cursor = null}
             onkeydown={moveCursor} onblur={() => cursor = null}>
          <svg viewBox="0 0 900 180" preserveAspectRatio="none" aria-hidden="true">
            {#each [0, 45, 90, 135, 180] as y}<line x1="0" y1={y} x2="900" y2={y} class="gridline" />{/each}
            {#each plot.paths as path (path.metric)}
              <path d={path.band} fill={path.color} opacity="0.12" />
              <path d={path.line} stroke={path.color} fill="none" stroke-width="1.8" stroke-linejoin="round" vector-effect="non-scaling-stroke" />
              {#each path.dots as dot}<circle cx={dot.x} cy={dot.y} r="2.5" fill={path.color} />{/each}
            {/each}
          </svg>
          {#if cursorPosition !== null}
            <span class="cursor-line" style:left={`${cursorPosition}%`}></span>
            <div class="chart-tooltip" style:left={`${Math.max(18, Math.min(82, cursorPosition))}%`}>
              <strong>{timeLabel(cursor!)}</strong>
              {#each hovered as item (item.metric)}
                <div><span class="dot" style:background={item.color}></span><span>{item.label}</span><b>{formatValue(item.point.value, unit)}</b></div>
                <small>{timeLabel(item.point.ts)} · {formatValue(item.point.min ?? item.point.value, unit)} – {formatValue(item.point.max ?? item.point.value, unit)}</small>
              {/each}
            </div>
          {/if}
        </div>
        <div class="x-axis" aria-hidden="true"><span>{timeLabel(start)}</span><span>{timeLabel((start + end) / 2)}</span><span>{timeLabel(end)}</span></div>
      </div>
    </div>
    <div class="chart-footer"><span class="legend-line"></span>Average <span class="legend-band"></span>Min / max <span class="chart-hint">Hover, tap, or use arrow keys to explore</span></div>
  {:else}
    <div class="chart-empty"><Activity size={24} strokeWidth={1.4} /><span>{loading ? 'Loading history...' : series.length ? 'No readings in this time range.' : 'Select a metric to view its history.'}</span></div>
  {/if}
</section>

<style>
  .chart-card { min-width: 0; padding: 25px 26px 19px; border: 1px solid var(--line); border-radius: 16px; background: var(--surface); }
  header { display: flex; align-items: flex-start; justify-content: space-between; gap: 15px; margin-bottom: 27px; }
  h2 { margin: 0; font-size: 14px; font-weight: 600; }
  p { margin: 7px 0 0; font-size: 11px; line-height: 1.6; color: var(--muted); }
  .peak { white-space: nowrap; font-size: 9px; letter-spacing: 1px; color: var(--muted); }
  .peak strong { display: block; margin-top: 5px; text-align: right; color: var(--text); font-size: 19px; font-weight: 500; letter-spacing: -.5px; font-variant-numeric: tabular-nums; }
  .chart-body { display: flex; gap: 12px; }
  .y-axis { display: flex; flex-direction: column; justify-content: space-between; width: 33px; flex-shrink: 0; height: 180px; color: var(--muted); font-size: 10px; font-variant-numeric: tabular-nums; text-align: right; }
  .plot-column { min-width: 0; flex: 1; }
  .plot { height: 180px; position: relative; outline-offset: 5px; outline-color: var(--highlight); }
  svg { display: block; width: 100%; height: 100%; overflow: visible; }
  .gridline { stroke: var(--line); stroke-dasharray: 3 5; vector-effect: non-scaling-stroke; }
  .x-axis { margin-top: 12px; display: flex; justify-content: space-between; gap: 8px; font-size: 9px; color: var(--muted); }
  .x-axis span:nth-child(2) { text-align: center; }
  .x-axis span:last-child { text-align: right; }
  .chart-footer { display: flex; align-items: center; flex-wrap: wrap; gap: 7px; margin-top: 22px; color: var(--muted); font-size: 9px; }
  .legend-line { width: 15px; height: 2px; background: var(--highlight); }
  .legend-band { width: 15px; height: 8px; background: var(--highlight); opacity: .2; margin-left: 8px; }
  .chart-hint { margin-left: auto; font-size: 9px; }
  .chart-empty { min-height: 230px; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 14px; color: var(--muted); font-size: 12px; }
  .chart-error { color: var(--warning); margin: -13px 0 15px; }
  .cursor-line { position: absolute; top: 0; bottom: 0; width: 1px; background: var(--muted); opacity: .6; pointer-events: none; }
  .chart-tooltip { position: absolute; z-index: 6; top: 12px; transform: translateX(-50%); min-width: 175px; padding: 12px; border: 1px solid var(--line); border-radius: 10px; background: var(--bg); box-shadow: 0 8px 25px #0002; pointer-events: none; font-size: 10px; }
  .chart-tooltip > strong { display: block; margin-bottom: 10px; color: var(--muted); font-size: 9px; font-weight: 500; }
  .chart-tooltip div { display: flex; align-items: center; gap: 6px; margin-top: 7px; }
  .chart-tooltip b { margin-left: auto; font-weight: 500; font-variant-numeric: tabular-nums; }
  .chart-tooltip small { display: block; margin: 3px 0 0 12px; color: var(--muted); font-size: 9px; }
  .dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
  @media (max-width: 540px) { .chart-card { padding: 20px 16px 17px; } .chart-hint { display: none; } .chart-tooltip { min-width: 145px; font-size: 9px; } .peak strong { font-size: 17px; } }
</style>
