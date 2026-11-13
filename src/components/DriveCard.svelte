<script lang="ts">
  import { iconSizes } from '../lib/icons';
  import { ChevronDown, HardDrive } from '@lucide/svelte';
  import MetricChart from './MetricChart.svelte';
  import { ageLabel, convertTemperature, counterIncrease, decodeCriticalWarning, driveFields, driveHistoryMetrics, driveReading,
    formatValue, isStale, readingAge, type CounterBaseline, type Drive, type DriveStatus, type Series, type TemperatureUnit } from '../lib/infrastructure';

  let { drive, status, elapsed, temperatureUnit, baseline, historyMetric, history, historyError, historyLoading,
    start, end, onOpen, onMetric }: {
    drive: Drive; status: DriveStatus; elapsed: number; temperatureUnit: TemperatureUnit; baseline: CounterBaseline;
    historyMetric: string; history?: Series; historyError?: string; historyLoading: boolean;
    start: number; end: number; onOpen: (open: boolean) => void; onMetric: (key: string) => void;
  } = $props();

  const temperature = $derived(driveReading(drive, 'temp'));
  const endurance = $derived(driveReading(drive, 'endurance_used_percent'));
  const fields = $derived(driveFields.filter(field => drive.metrics.some(metric => metric.key === `smart.${drive.id}.${field.suffix}`) || drive.readings[field.suffix]));
  const historyOptions = $derived(driveHistoryMetrics(drive));
  const selected = $derived(historyOptions.find(metric => metric.key === historyMetric));
  const series = $derived(history && selected ? [{ ...history, label: selected.label, color: 'var(--highlight)' }] : []);
  const temperatureStale = $derived(isStale(temperature, elapsed));
</script>

<article class="drive-card" aria-label={drive.label}>
  <div class="drive-heading">
    <div class="drive-name"><HardDrive size={iconSizes.action} /><h3>{drive.label}</h3><span>{drive.kind}</span></div>
    <span class="drive-status" class:passed={status.state === 'passed'} class:failed={status.state === 'failed'} class:attention={status.state === 'attention'}><i></i>{status.label}</span>
  </div>
  <div class="drive-readouts">
    <div>
      <div class="drive-value">{formatValue(convertTemperature(temperature?.value, temperatureUnit), temperatureUnit)}<small>{temperatureUnit}</small></div>
      <div class="reading-age" class:stale={temperatureStale}>{temperature && temperatureStale ? 'Stale · ' : ''}{ageLabel(readingAge(temperature, elapsed))}</div>
    </div>
    {#if drive.kind === 'NVMe'}
      <div class="endurance" class:stale={isStale(endurance, elapsed)}><strong>{formatValue(endurance?.value, '%')}</strong><span>Endurance used{isStale(endurance, elapsed) ? ' · unavailable/stale' : ''}</span></div>
    {/if}
  </div>
  {#if status.reasons.length}
    <ul class="status-reasons" class:failed={status.state === 'failed'}>{#each status.reasons as reason}<li>{reason}</li>{/each}</ul>
  {/if}
  <details ontoggle={event => onOpen(event.currentTarget.open)}>
    <summary aria-label={`Details for ${drive.label}`}><span>Health &amp; details</span><ChevronDown size={iconSizes.inline} /></summary>
    <dl>
      {#each fields as field (field.suffix)}
        {@const reading = driveReading(drive, field.suffix)}
        {@const stale = isStale(reading, elapsed)}
        {@const increase = counterIncrease(reading, baseline, elapsed)}
        <div class:stale>
          <dt>{field.label}</dt>
          <dd>{formatValue(reading?.value, field.unit)}{#if reading && stale}<small>Stale · {ageLabel(readingAge(reading, elapsed))}</small>{:else if !reading}<small>Unavailable</small>{/if}{#if increase > 0}<small class="increase">+{increase.toLocaleString()} since opened</small>{/if}</dd>
          {#if field.suffix === 'critical_warning' && reading && reading.value > 0}<p>{decodeCriticalWarning(reading.value).join('; ')}</p>{/if}
        </div>
      {/each}
    </dl>
    <p class="detail-note">Counters show recorded totals. Increases are measured since this page was opened. SMART passing does not guarantee a drive is fault-free.</p>
    {#if drive.kind === 'NVMe'}<p class="detail-note">Endurance used is the drive’s estimate of rated wear consumed.</p>{/if}
    {#if historyOptions.length}
      <label class="history-select">Drive history
        <select aria-label={`${drive.label} history metric`} value={historyMetric} onchange={event => onMetric(event.currentTarget.value)}>
          {#each historyOptions as metric}<option value={metric.key}>{metric.label}</option>{/each}
        </select>
      </label>
      <MetricChart title={selected?.label ?? 'Drive History'} description="Available history for this drive." {series} unit={selected?.unit ?? 'count'} {start} {end}
                   loading={historyLoading} error={historyError ?? ''} compact />
    {:else}<p class="detail-note">No numeric drive history is available from the collector.</p>{/if}
  </details>
</article>

<style>
  .drive-card { min-width: 0; padding: 22px; border-radius: 14px; border: 1px solid var(--line); background: var(--surface); }
  .drive-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
  .drive-name { display: flex; align-items: center; gap: 8px; color: var(--muted); }
  h3 { margin: 0; font-size: 12px; font-weight: 500; color: var(--text); }
  .drive-name > span { font-size: 8px; letter-spacing: .6px; opacity: .7; }
  .drive-status { display: inline-flex; align-items: center; gap: 6px; font-size: 10px; color: var(--muted); }
  .drive-status i { height: 5px; width: 5px; border-radius: 50%; background: currentColor; }
  .passed { color: var(--success); }
  .attention, .stale, .increase { color: var(--warning); }
  .failed { color: var(--chart-4); }
  .drive-readouts { display: flex; align-items: center; justify-content: space-between; gap: 15px; margin: 22px 0 18px; }
  .drive-value { font-size: 32px; line-height: 1; letter-spacing: -.8px; font-variant-numeric: tabular-nums; }
  .drive-value small { color: var(--muted); margin-left: 3px; font-size: 11px; }
  .reading-age { color: var(--muted); font-size: 10px; margin-top: 9px; }
  .reading-age.stale { color: var(--warning); }
  .endurance { text-align: right; }
  .endurance strong { display: block; font-size: 21px; font-weight: 400; font-variant-numeric: tabular-nums; }
  .endurance span { display: block; font-size: 10px; color: var(--muted); margin-top: 6px; }
  .status-reasons { padding-left: 16px; margin: 0 0 16px; font-size: 11px; line-height: 1.6; color: var(--warning); }
  .status-reasons.failed { color: var(--chart-4); }
  details { border-top: 1px solid var(--line); }
  summary { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding-top: 14px; color: var(--muted); font-size: 11px; list-style: none; cursor: pointer; }
  summary::-webkit-details-marker { display: none; }
  summary:focus-visible, select:focus-visible { outline: 2px solid var(--highlight); outline-offset: 4px; }
  details[open] summary :global(svg) { transform: rotate(180deg); }
  dl { margin: 20px 0 14px; font-size: 11px; }
  dl > div { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 5px 12px; padding: 10px 0; border-bottom: 1px solid var(--line); }
  dt { color: var(--muted); line-height: 1.5; }
  dd { margin: 0; text-align: right; font-variant-numeric: tabular-nums; }
  dd small { display: block; margin-top: 4px; font-size: 9px; color: var(--warning); }
  dl p { grid-column: 1 / -1; font-size: 10px; line-height: 1.6; margin: 3px 0 0; color: var(--muted); }
  .detail-note { color: var(--muted); font-size: 10px; line-height: 1.7; margin: 10px 0; }
  .history-select { display: flex; align-items: center; justify-content: space-between; gap: 12px; color: var(--muted); font-size: 10px; margin: 20px 0 12px; }
  select { min-width: 0; max-width: 70%; font: inherit; color: var(--text); background: var(--surface-hover); border: 1px solid var(--line); border-radius: 7px; padding: 8px; }
  @media (max-width: 540px) { .drive-card { padding: 20px; } .history-select { flex-direction: column; align-items: stretch; gap: 8px; } select { max-width: 100%; width: 100%; } }
</style>
