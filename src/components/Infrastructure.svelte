<script lang="ts">
  import { onMount } from 'svelte';
  import { Activity, ArrowUpRight, Cpu, HardDrive, MemoryStick, RefreshCw, ShieldCheck, Thermometer, TriangleAlert, WifiOff } from '@lucide/svelte';
  import MetricChart from './MetricChart.svelte';
  import DriveCard from './DriveCard.svelte';
  import { ageLabel, assessDrive, averageDriveTemperature, collectionDiagnostics, convertTemperature, convertTemperatureSeries, defaultDriveHistory, driveHistoryMetrics,
    fetchJson, formatValue, groupDrives, isStale, metricColors, metricReading, ranges, readingAge, sourceTime, summarizeDrives, updateCounterBaseline,
    type CounterBaseline, type Drive, type TemperatureUnit, type ChartSeries, type Metric, type Series, type Snapshot } from '../lib/infrastructure';

  let snapshot = $state<Snapshot | null>(null);
  let clock = $state(0);
  let receivedAt = $state(0);
  let temperatureUnit = $state<TemperatureUnit>('C');
  const elapsed = $derived(Math.max(0, clock - receivedAt));
  let refreshing = $state(false);
  let refreshError = $state('');
  let range = $state(3600);
  let histories = $state<Record<string, Series>>({});
  let historyErrors = $state<Record<string, string>>({});
  let historyLoading = $state(false);
  let historyRevision = $state(0);
  let chartEnd = $state(Date.now() / 1000);
  let hiddenMetrics = $state<string[]>([]);
  let openDrives = $state<string[]>([]);
  let driveSelections = $state<Record<string, string>>({});
  let counterBaseline = $state<CounterBaseline>({});
  let snapshotController: AbortController | undefined;
  let disposed = false;
  let historyKey = '';

  const metrics = $derived(snapshot?.metrics ?? []);
  const readingMap = $derived(new Map(snapshot?.readings.map(r => [r.metric, r]) ?? []));
  const cpuMetric = $derived(metrics.find(m => m.key === 'cpu.usage'));
  const ramMetric = $derived(metrics.find(m => m.key === 'memory.used_percent' && m.unit === '%'));
  const cpuTempMetric = $derived(metrics.find(m => m.unit === 'C' && !m.key.startsWith('smart.')));
  const driveMetrics = $derived(metrics.filter(m => m.key.startsWith('smart.') && m.unit === 'C'));
  const temperatures = $derived(metrics.filter(m => m.unit === 'C'));
  const cpuReading = $derived(readingMap.get(cpuMetric?.key ?? ''));
  const cpuTempReading = $derived(readingMap.get(cpuTempMetric?.key ?? ''));
  const ramReading = $derived(metricReading(readingMap.get('memory.used_percent'), '%'));
  const ramDetails = $derived([
    { label: 'Used', reading: metricReading(readingMap.get('memory.used_gib'), 'GiB') },
    { label: 'Available', reading: metricReading(readingMap.get('memory.available_gib'), 'GiB') },
    { label: 'Swap', reading: metricReading(readingMap.get('swap.used_gib'), 'GiB') },
  ]);
  const drives = $derived(groupDrives(snapshot));
  const driveStatuses = $derived(drives.map(drive => assessDrive(drive, elapsed, counterBaseline, snapshot?.health?.smartctl_available)));
  const driveSummary = $derived(summarizeDrives(driveStatuses));
  const driveSummaryDetails = $derived([
    driveSummary.failed ? `${driveSummary.failed} failed` : '',
    driveSummary.attention ? `${driveSummary.attention} need attention` : '',
    driveSummary.unknown ? `${driveSummary.unknown} unknown/unavailable` : '',
  ].filter(Boolean).join(' · ') || (drives.length ? 'Current readings from all drives' : 'Waiting for SMART readings'));
  const hddAverage = $derived(averageDriveTemperature(driveMetrics, readingMap, 'hdd', elapsed));
  const nvmeAverage = $derived(averageDriveTemperature(driveMetrics, readingMap, 'nvme', elapsed));
  const sourceErrors = $derived(Object.values(snapshot?.errors ?? {}));
  const diagnostics = $derived(collectionDiagnostics(snapshot, elapsed));
  const collectorStale = $derived(snapshot?.health?.collection_age_seconds != null && snapshot.health.collection_age_seconds + elapsed > 60);
  const notices = $derived([refreshError || (sourceErrors.length ? 'Some readings could not be refreshed. Showing the latest available data.' : ''), ...diagnostics].filter(Boolean));
  const connectionLabel = $derived(refreshError || sourceErrors.length ? 'Connection interrupted'
    : !snapshot ? 'Connecting' : snapshot.health?.ok === false ? 'Collector stopped'
    : collectorStale ? 'Collection delayed' : snapshot.health?.ok ? 'Collector running' : 'Collector status unavailable');
  // Fetch only visible charts; spare/endurance percentages are not CPU or RAM.
  const metricSignature = $derived([...new Set([
    ...temperatures.map(metric => metric.key),
    ...(cpuMetric ? [cpuMetric.key] : []), ...(ramMetric ? [ramMetric.key] : []),
    ...drives.filter(drive => openDrives.includes(drive.id)).map(selectedDriveHistory).filter(Boolean),
  ])].sort().join('\n'));

  function selectedDriveHistory(drive: Drive): string {
    const selected = driveSelections[drive.id];
    return driveHistoryMetrics(drive).some(metric => metric.key === selected) ? selected : defaultDriveHistory(drive);
  }

  function setDriveOpen(id: string, open: boolean) {
    openDrives = open ? [...new Set([...openDrives, id])] : openDrives.filter(key => key !== id);
  }

  function chartSeries(items: Metric[]): ChartSeries[] {
    return items.filter(m => !hiddenMetrics.includes(m.key) && histories[m.key]).map(metric => ({
      ...(metric.unit === 'C' ? convertTemperatureSeries(histories[metric.key], temperatureUnit) : histories[metric.key]), label: metric.label,
      color: metric.unit === '%' ? 'var(--highlight)' : metricColors[temperatures.findIndex(m => m.key === metric.key) % metricColors.length],
    }));
  }

  const utilizationSeries = $derived(chartSeries(cpuMetric ? [cpuMetric] : []));
  const ramSeries = $derived(chartSeries(ramMetric ? [ramMetric] : []));
  const temperatureSeries = $derived(chartSeries(temperatures));

  async function refreshSnapshot() {
    if (refreshing || disposed) return;
    refreshing = true;
    snapshotController = new AbortController();
    try {
      const result = await fetchJson<Snapshot>('/api/infrastructure/snapshot', snapshotController.signal);
      if (!disposed) {
        counterBaseline = updateCounterBaseline(counterBaseline, result.readings);
        snapshot = result;
        receivedAt = performance.now() / 1000;
        clock = receivedAt;
        refreshError = '';
      }
    } catch {
      if (!disposed) refreshError = snapshot?.readings.length
        ? 'Could not reach NAS Temps. Your last readings are kept below.'
        : 'Could not reach NAS Temps. Check the connection and try again.';
    } finally {
      if (!disposed) refreshing = false;
    }
  }

  function displayTemperature(value: number | null | undefined) {
    return formatValue(convertTemperature(value, temperatureUnit), temperatureUnit);
  }

  function setTemperatureUnit(unit: TemperatureUnit) {
    temperatureUnit = unit;
    try { localStorage.setItem('clonepage-temperature-unit', unit); } catch { /* Keep the session preference. */ }
  }

  function refreshAll() {
    void refreshSnapshot();
    historyRevision++;
  }

  function toggleMetric(key: string) {
    hiddenMetrics = hiddenMetrics.includes(key) ? hiddenMetrics.filter(k => k !== key) : [...hiddenMetrics, key];
  }

  $effect(() => {
    const keys = metricSignature ? metricSignature.split('\n') : [];
    const seconds = range;
    const revision = historyRevision;
    void revision;
    if (!keys.length) return;
    const controller = new AbortController();
    const key = String(seconds);
    if (key !== historyKey) {
      histories = {};
      historyKey = key;
    }
    historyLoading = true;
    historyErrors = {};
    const requestedAt = Date.now() / 1000;
    void Promise.allSettled(keys.map(async metric => {
      const params = new URLSearchParams({ metric, since_seconds: String(seconds), max_points: '360' });
      return fetchJson<Series>(`/api/infrastructure/series?${params}`, controller.signal);
    })).then(results => {
      if (controller.signal.aborted) return;
      const next: Record<string, Series> = {};
      const errors: Record<string, string> = {};
      results.forEach((result, index) => {
        if (result.status === 'fulfilled') next[keys[index]] = result.value;
        else errors[keys[index]] = 'History could not be refreshed.';
      });
      histories = { ...histories, ...next };
      historyErrors = errors;
      chartEnd = sourceTime(snapshot, Math.max(0, performance.now() / 1000 - receivedAt)) ?? requestedAt;
      historyLoading = false;
    });
    return () => controller.abort();
  });

  onMount(() => {
    disposed = false;
    clock = performance.now() / 1000;
    try {
      const saved = localStorage.getItem('clonepage-temperature-unit');
      if (saved === 'C' || saved === 'F') temperatureUnit = saved;
    } catch { /* The toggle still works when browser storage is unavailable. */ }
    void refreshSnapshot();
    const poll = setInterval(() => { if (!document.hidden) void refreshSnapshot(); }, 10000);
    const historyPoll = setInterval(() => { if (!document.hidden) historyRevision++; }, 60000);
    const ticker = setInterval(() => { if (!document.hidden) clock = performance.now() / 1000; }, 1000);
    const onVisible = () => { if (!document.hidden) { clock = performance.now() / 1000; refreshAll(); } };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      disposed = true;
      snapshotController?.abort();
      clearInterval(poll);
      clearInterval(historyPoll);
      clearInterval(ticker);
      document.removeEventListener('visibilitychange', onVisible);
    };
  });
</script>

<section class="infrastructure">
  <header class="infra-heading">
    <div>
      <div class="infra-eyebrow"><span></span>YOUR INFRASTRUCTURE</div>
      <h1>Your NAS, at a glance.</h1>
      <p>CPU, memory, and drive health, with a little history.</p>
    </div>
    <div class="collection-actions">
      <span class="connection" class:delayed={connectionLabel !== 'Collector running' || diagnostics.length > 0}><i></i>{connectionLabel}</span>
      <div class="readout-controls">
        <div class="unit-options" role="group" aria-label="Temperature unit">
          <button class:active={temperatureUnit === 'C'} aria-pressed={temperatureUnit === 'C'} onclick={() => setTemperatureUnit('C')} title="Celsius">°C</button>
          <button class:active={temperatureUnit === 'F'} aria-pressed={temperatureUnit === 'F'} onclick={() => setTemperatureUnit('F')} title="Fahrenheit">°F</button>
        </div>
        <button class="refresh-button" onclick={refreshAll} disabled={refreshing} aria-label="Refresh infrastructure" title="Refresh readings and history">
          <RefreshCw size={15} class={refreshing ? 'refreshing' : ''} /><span>{refreshing ? 'Refreshing' : 'Refresh'}</span>
        </button>
      </div>
    </div>
  </header>

  {#if notices.length}
    <div class="source-notice" role="status"><TriangleAlert size={17} /><div>{#each notices as notice}<p>{notice}</p>{/each}</div></div>
  {/if}

  {#if !refreshing && (refreshError || sourceErrors.length) && (!snapshot || (!snapshot.metrics.length && !snapshot.readings.length))}
    <div class="unavailable"><WifiOff size={30} strokeWidth={1.3} /><h2>The NAS is out of reach.</h2><p>Check the NAS Temps collector and try again.</p><button onclick={refreshAll}>Try again</button></div>
  {:else}
    <div class="summary-grid" aria-busy={refreshing && !snapshot}>
      <article class="summary-card">
        <div class="card-label"><Cpu size={17} strokeWidth={1.6} /><span>CPU UTILIZATION</span></div>
        <div class="summary-value">{formatValue(cpuReading?.value, '%')}</div>
        <div class="usage-meter" aria-hidden="true"><span style:width={`${Math.max(0, Math.min(100, cpuReading?.value ?? 0))}%`}></span></div>
        <div class="reading-age" class:stale={isStale(cpuReading, elapsed)}>{cpuReading && isStale(cpuReading, elapsed) ? 'Stale · ' : ''}{ageLabel(readingAge(cpuReading, elapsed))}</div>
      </article>
      <article class="summary-card ram-card">
        <div class="card-label"><MemoryStick size={17} strokeWidth={1.6} /><span>RAM UTILIZATION</span></div>
        <div class="summary-value">{formatValue(ramReading?.value, '%')}</div>
        <div class="usage-meter" aria-hidden="true"><span style:width={`${Math.max(0, Math.min(100, ramReading?.value ?? 0))}%`}></span></div>
        <div class="ram-details">{#each ramDetails as detail}<span class:stale={isStale(detail.reading, elapsed)}>{detail.label} <b>{formatValue(detail.reading?.value, 'GiB')}</b>{#if detail.reading && isStale(detail.reading, elapsed)} (stale){/if}</span>{/each}</div>
        <div class="reading-age" class:stale={isStale(ramReading, elapsed)}>{ramReading && isStale(ramReading, elapsed) ? 'Stale · ' : ''}{ageLabel(readingAge(ramReading, elapsed))}</div>
      </article>
      <article class="summary-card">
        <div class="card-label"><Thermometer size={17} strokeWidth={1.6} /><span>CPU TEMPERATURE</span></div>
        <div class="summary-value">{displayTemperature(cpuTempReading?.value)}<small>{temperatureUnit}</small></div>
        <div class="metric-context">{cpuTempMetric?.label ?? 'Waiting for the collector'}</div>
        <div class="reading-age" class:stale={isStale(cpuTempReading, elapsed)}>{cpuTempReading && isStale(cpuTempReading, elapsed) ? 'Stale · ' : ''}{ageLabel(readingAge(cpuTempReading, elapsed))}</div>
      </article>
      <article class="summary-card hdd-average">
        <div class="card-label"><HardDrive size={17} strokeWidth={1.6} /><span>AVG HDD TEMP</span></div>
        <div class="summary-value">{displayTemperature(hddAverage.value)}<small>{temperatureUnit}</small></div>
        <div class="metric-context">{hddAverage.count} of {hddAverage.total} HDD {hddAverage.total === 1 ? 'drive' : 'drives'} reporting</div>
        <div class="reading-age" class:stale={hddAverage.count < hddAverage.total}>{hddAverage.oldestAge === null ? 'No current readings' : ageLabel(hddAverage.oldestAge)}</div>
      </article>
      <article class="summary-card nvme-average">
        <div class="card-label"><HardDrive size={17} strokeWidth={1.6} /><span>AVG NVME TEMP</span></div>
        <div class="summary-value">{displayTemperature(nvmeAverage.value)}<small>{temperatureUnit}</small></div>
        <div class="metric-context">{nvmeAverage.count} of {nvmeAverage.total} NVMe {nvmeAverage.total === 1 ? 'drive' : 'drives'} reporting</div>
        <div class="reading-age" class:stale={nvmeAverage.count < nvmeAverage.total}>{nvmeAverage.oldestAge === null ? 'No current readings' : ageLabel(nvmeAverage.oldestAge)}</div>
      </article>
      <article class="summary-card drive-summary">
        <div class="card-label"><ShieldCheck size={17} strokeWidth={1.6} /><span>DRIVE STATUS</span></div>
        <div class="summary-value">{driveSummary.total ? `${driveSummary.passed}/${driveSummary.total}` : '—'}<small>SMART passed</small></div>
        <div class="metric-context" class:stale={driveSummary.failed + driveSummary.attention + driveSummary.unknown > 0}>{driveSummaryDetails}</div>
        <div class="reading-age">SMART self-assessment · see details below</div>
      </article>
    </div>

    <section class="drives-section" aria-label="Drives">
      <div class="infra-section-heading"><h2>DRIVES</h2><span>{drives.length ? `${drives.length} DRIVES` : 'WAITING FOR READINGS'}</span></div>
      <div class="drive-grid">
        {#each drives as drive, index (drive.id)}
          {@const selected = selectedDriveHistory(drive)}
          <DriveCard {drive} status={driveStatuses[index]} {elapsed} {temperatureUnit} baseline={counterBaseline}
                     historyMetric={selected} history={histories[selected]} historyError={historyErrors[selected]} {historyLoading} start={chartEnd - range} end={chartEnd}
                     onOpen={open => setDriveOpen(drive.id, open)} onMetric={key => driveSelections = { ...driveSelections, [drive.id]: key }} />
        {/each}
        {#if !drives.length}<p class="no-drives">{refreshing ? 'Connecting to NAS Temps...' : 'No drive sensors are available from the collector.'}</p>{/if}
      </div>
    </section>

    <section class="history-section" aria-label="Metric history">
      <div class="history-heading">
        <div class="infra-section-heading"><h2>A LITTLE PERSPECTIVE</h2><span>{historyLoading ? 'UPDATING HISTORY' : 'METRIC HISTORY'}</span></div>
        <div class="range-options" role="group" aria-label="History time range">
          {#each ranges as option}<button class:active={range === option.seconds} aria-pressed={range === option.seconds} title={option.label} onclick={() => range = option.seconds}>{option.short}</button>{/each}
        </div>
      </div>
      <div class="resource-history">
        <MetricChart title="CPU Utilization" description="How busy your processor has been." series={utilizationSeries} unit="%" start={chartEnd - range} end={chartEnd} loading={historyLoading || (!snapshot && refreshing)}
                   error={cpuMetric && historyErrors[cpuMetric.key] ? 'CPU history could not be refreshed.' : ''} />
        <MetricChart title="RAM Utilization" description="Memory utilization over time." series={ramSeries} unit="%" start={chartEnd - range} end={chartEnd} loading={historyLoading || (!snapshot && refreshing)}
                     error={ramMetric && historyErrors[ramMetric.key] ? 'RAM history could not be refreshed.' : ''} />
      </div>
      <div class="temperature-legend" role="group" aria-label="Temperature metrics">
        {#each temperatures as metric, index (metric.key)}
          <button class:muted={hiddenMetrics.includes(metric.key)} aria-pressed={!hiddenMetrics.includes(metric.key)} onclick={() => toggleMetric(metric.key)}>
            <span style:background={metricColors[index % metricColors.length]}></span>{metric.label}
          </button>
        {/each}
      </div>
      <MetricChart title="Temperature history" description="Select the sensors above to compare their temperatures." series={temperatureSeries} unit={temperatureUnit} start={chartEnd - range} end={chartEnd} loading={historyLoading || (!snapshot && refreshing)}
                   error={temperatures.some(m => historyErrors[m.key]) ? 'Some temperature history could not be refreshed.' : ''} />
    </section>
    <div class="infra-footnote"><Activity size={13} /><span>Readings refresh every 10 seconds. History refreshes every minute while this page is visible.</span><details><summary>Details</summary><a href="/api/infrastructure/snapshot" target="_blank" rel="noopener noreferrer">View readings <ArrowUpRight size={12} /></a></details></div>
  {/if}
</section>

<style>
  .infrastructure { max-width: 1060px; margin: 66px auto 0; padding-bottom: 10px; }
  .infra-heading { display: flex; align-items: flex-end; justify-content: space-between; gap: 24px; margin-bottom: 33px; }
  .infra-eyebrow { display: flex; align-items: center; gap: 10px; font-size: 9px; letter-spacing: 2.4px; font-weight: 700; color: var(--highlight); }
  .infra-eyebrow > span { width: 20px; height: 1px; background: var(--highlight); opacity: .6; }
  h1 { margin: 17px 0 10px; font-size: 36px; }
  .infra-heading p { margin: 0; font-size: 12px; color: var(--muted); line-height: 1.7; }
  .collection-actions { display: flex; align-items: center; gap: 16px; padding-bottom: 3px; }
  .readout-controls { display: flex; align-items: center; gap: 10px; }
  .unit-options { display: flex; padding: 4px; gap: 3px; background: var(--surface); border: 1px solid var(--line); border-radius: 9px; }
  .unit-options button { padding: 6px 9px; border-radius: 6px; font-size: 11px; background: transparent; }
  .unit-options button.active { color: var(--text); background: var(--surface-hover); }
  .source-notice p { margin: 0; }
  .source-notice p + p { margin-top: 6px; }
  .connection { display: inline-flex; align-items: center; gap: 7px; font-size: 10px; color: var(--muted); white-space: nowrap; }
  .connection i { width: 6px; height: 6px; border-radius: 50%; background: var(--success); box-shadow: 0 0 0 4px color-mix(in srgb, var(--success) 8%, transparent); }
  .connection.delayed i { background: var(--warning); box-shadow: none; }
  button { border: 0; cursor: pointer; color: var(--muted); }
  button:focus-visible, a:focus-visible { outline: 2px solid var(--highlight); outline-offset: 4px; }
  button:disabled { opacity: .5; cursor: default; }
  .refresh-button { display: flex; align-items: center; gap: 8px; border: 1px solid var(--line); border-radius: 9px; background: var(--surface); padding: 10px 12px; font-size: 11px; color: var(--text); }
  .source-notice { display: flex; align-items: center; gap: 11px; padding: 14px 17px; margin-bottom: 20px; border: 1px solid color-mix(in srgb, var(--warning) 25%, transparent); border-radius: 12px; color: var(--warning); background: color-mix(in srgb, var(--warning) 5%, transparent); font-size: 12px; line-height: 1.6; }
  .source-notice :global(svg) { flex-shrink: 0; }
  .summary-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 17px; }
  .summary-card { min-width: 0; padding: 24px 25px 21px; border: 1px solid var(--line); border-radius: 15px; background: var(--surface); }
  .card-label { display: flex; align-items: center; gap: 10px; color: var(--highlight); }
  .card-label span { font-size: 9px; font-weight: 600; letter-spacing: 1.6px; color: var(--muted); }
  .summary-value { font-size: 43px; letter-spacing: -2px; margin: 23px 0 16px; line-height: 1; font-weight: 400; font-variant-numeric: tabular-nums; }
  .summary-value small { color: var(--muted); font-size: 15px; letter-spacing: 0; margin-left: 3px; }
  .usage-meter { height: 4px; margin: 5px 0 15px; border-radius: 10px; background: var(--line); overflow: hidden; }
  .usage-meter span { display: block; height: 100%; background: var(--highlight); border-radius: inherit; transition: width .5s; }
  .metric-context { min-height: 16px; font-size: 11px; color: var(--muted); margin-bottom: 10px; }
  .reading-age { color: var(--muted); font-size: 10px; font-variant-numeric: tabular-nums; }
  .stale { color: var(--warning); }
  .ram-details { display: flex; flex-wrap: wrap; gap: 6px 12px; color: var(--muted); font-size: 10px; line-height: 1.6; margin-bottom: 10px; }
  .ram-details b { font-weight: 500; color: var(--text); }
  .ram-details .stale b { color: var(--warning); }
  .drive-summary .summary-value small { display: inline-block; font-size: 12px; margin-left: 10px; letter-spacing: 0; }
  .hdd-average .card-label { color: var(--chart-2); }
  .nvme-average .card-label { color: var(--chart-3); }
  .drives-section { margin-top: 35px; }
  .infra-section-heading { display: flex; align-items: center; justify-content: space-between; gap: 15px; margin-bottom: 16px; }
  .infra-section-heading h2 { margin: 0; font-size: 9px; font-weight: 600; letter-spacing: 1.8px; color: var(--muted); }
  .infra-section-heading > span { font-size: 9px; letter-spacing: 1px; color: var(--muted); opacity: .75; }
  .drive-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); align-items: start; gap: 14px; }
  .no-drives { margin: 0; grid-column: 1 / -1; padding: 25px; border: 1px dashed var(--line); border-radius: 12px; color: var(--muted); font-size: 12px; }
  .history-section { margin-top: 39px; }
  .resource-history { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 17px; }
  .history-heading { display: flex; align-items: center; justify-content: space-between; gap: 20px; margin-bottom: 18px; }
  .history-heading .infra-section-heading { margin: 0; flex: 1; }
  .history-heading .infra-section-heading > span { display: none; }
  .range-options { display: flex; padding: 4px; gap: 3px; border: 1px solid var(--line); border-radius: 9px; background: var(--surface); }
  .range-options button { padding: 6px 11px; border-radius: 6px; background: transparent; font-size: 10px; font-variant-numeric: tabular-nums; }
  .range-options button.active { background: var(--surface-hover); color: var(--text); }
  .temperature-legend { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin: 26px 0 13px; }
  .temperature-legend button { display: flex; align-items: center; gap: 7px; padding: 7px 10px; background: var(--surface); border: 1px solid var(--line); border-radius: 7px; font-size: 10px; color: var(--text); }
  .temperature-legend button.muted { opacity: .4; }
  .temperature-legend span { display: block; width: 6px; height: 6px; border-radius: 50%; }
  .infra-footnote { display: flex; align-items: center; gap: 7px; margin-top: 20px; color: var(--muted); font-size: 9px; line-height: 1.7; }
  .infra-footnote :global(svg) { flex-shrink: 0; }
  .infra-footnote details { margin-left: auto; }
  .infra-footnote summary { cursor: pointer; }
  .infra-footnote details[open] a { margin-top: 10px; }
  .infra-footnote a { display: flex; gap: 4px; align-items: center; margin-left: auto; color: var(--muted); text-decoration: none; white-space: nowrap; }
  .unavailable { min-height: 370px; display: flex; flex-direction: column; align-items: center; justify-content: center; color: var(--muted); gap: 14px; text-align: center; }
  .unavailable h2 { margin: 6px 0 0; font-size: 19px; font-weight: 500; color: var(--text); }
  .unavailable p { margin: 0; font-size: 12px; }
  .unavailable button { margin-top: 8px; padding: 10px 17px; background: var(--surface-hover); color: var(--text); border-radius: 8px; font-size: 12px; }
  :global(.refreshing) { animation: spin 1.5s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  @media (max-width: 1000px) { .summary-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } .resource-history { grid-template-columns: 1fr; } }
  @media (max-width: 850px) { .infra-heading { align-items: flex-start; flex-direction: column; gap: 22px; } .collection-actions { width: 100%; justify-content: space-between; } .summary-card { padding: 22px 18px; } .card-label { gap: 7px; } .card-label span { font-size: 8px; letter-spacing: 1px; } .summary-value { font-size: 36px; } }
  @media (max-width: 650px) { .history-heading { flex-direction: column; align-items: stretch; gap: 12px; } .range-options { width: 100%; } .range-options button { flex: 1; min-width: 0; } .summary-grid { grid-template-columns: 1fr; gap: 12px; } .drive-grid { grid-template-columns: 1fr; } .summary-card { display: grid; grid-template-columns: 1fr auto; column-gap: 12px; padding: 20px; } .card-label { grid-column: 1; } .summary-value { grid-column: 2; grid-row: 1 / 4; margin: 0; align-self: center; font-size: 35px; } .metric-context { grid-column: 1; margin: 10px 0 7px; } .usage-meter { grid-column: 1; margin: 15px 0 10px; max-width: 160px; } .reading-age { grid-column: 1; } .ram-details { grid-column: 1 / -1; margin-top: 10px; } .ram-card .reading-age { grid-row: 4; } .drive-summary .summary-value small { display: block; text-align: right; margin: 6px 0 0; font-size: 10px; } }
  @media (max-width: 540px) { .infrastructure { margin-top: 42px; } h1 { font-size: 30px; } .history-heading { gap: 8px; } .infra-section-heading h2 { font-size: 8px; letter-spacing: 1.1px; } .range-options button { padding: 6px 9px; } .infra-footnote { align-items: flex-start; } }
  @media (max-width: 540px) { .collection-actions { flex-direction: column; align-items: flex-start; gap: 14px; } .readout-controls { width: 100%; justify-content: space-between; } }
</style>
