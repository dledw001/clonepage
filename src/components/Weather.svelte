<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import { Cloud, CloudFog, CloudLightning, CloudRain, CloudSnow, CloudSun, Moon, RefreshCw, Sun, ArrowUpRight } from '@lucide/svelte';
  import SectionHeader from './SectionHeader.svelte';
  import { HomeDataStore, type HomeDataState } from '../lib/home';
  import { forecastDate, temperatureText, weatherKind, weatherUrl, windText, type WeatherPreferences, type WeatherSnapshot } from '../lib/weather';
  let { active=true, preferences }: { active?:boolean; preferences:WeatherPreferences } = $props();
  let state=$state<HomeDataState<WeatherSnapshot>>({data:null,loading:false,error:''});
  const store=new HomeDataStore<WeatherSnapshot>('/api/home/weather',next=>state=next);
  let mounted=false;
  const data=$derived(state.data);
  const current=$derived(data?.current);
  const units=$derived(data?.units??preferences.units);
  const today=$derived(data?.forecast[0]);
  const icons={sun:Sun,moon:Moon,partly:CloudSun,cloud:Cloud,fog:CloudFog,rain:CloudRain,snow:CloudSnow,storm:CloudLightning};
  const CurrentIcon=$derived(icons[weatherKind(current?.code??'',current?.description)]);
  const error=$derived(state.error||data?.error||'');
  $effect(()=>{const visible=active,key=preferences.location+'|'+preferences.units;if(mounted&&visible&&key)untrack(()=>{void store.refresh();});});
  onMount(()=>{
    mounted=true;void store.refresh();
    const refresh=()=>{if(active&&!document.hidden)void store.refresh();};
    const timer=setInterval(refresh,300000);
    window.addEventListener('focus',refresh);document.addEventListener('visibilitychange',refresh);
    return()=>{mounted=false;store.dispose();clearInterval(timer);window.removeEventListener('focus',refresh);document.removeEventListener('visibilitychange',refresh);};
  });
</script>

<section class="weather home-surface" aria-label="Weather" aria-busy={state.loading}>
  <SectionHeader title="Weather" subtitle={data?.place ? `${data.place} · ${data.location}` : preferences.location}>
    {#snippet icon()}<CloudSun size={19} strokeWidth={1.7}/>{/snippet}
    {#snippet actions()}<button class="section-icon-action" aria-label="Refresh weather" title="Refresh cached weather; wttr.in is checked at most every 15 minutes" disabled={state.loading} onclick={()=>store.refresh()}><RefreshCw size={17}/></button>{/snippet}
  </SectionHeader>
  {#if error}<p class="weather-error" role="status">{error}{current?' Showing previously retrieved weather.':''}</p>{/if}
  {#if current}
    <div class="weather-body">
      <div class="conditions">
        <div class="temperature"><CurrentIcon size={39} strokeWidth={1.3}/><strong>{temperatureText(current.temperature,units)}</strong></div>
        <p class="description">{current.description}</p>
        <p class="feels">Feels like {temperatureText(current.feels_like,units)}</p>
      </div>
      <dl class="details">
        <div><dt>{today ? `${forecastDate(today.date)} high / low` : 'High / low'}</dt><dd>{temperatureText(today?.high,units)} / {temperatureText(today?.low,units)}</dd></div>
        <div><dt>Wind</dt><dd>{windText(current,units)}</dd></div>
        <div><dt title="Highest three-hour rain probability in this day's forecast, not a daily probability">Peak rain chance</dt><dd>{today?.rain_chance!==null&&today?.rain_chance!==undefined?`${Math.round(today.rain_chance)}%`:'—'}</dd></div>
        <div><dt>Humidity</dt><dd>{current.humidity===null?'—':`${Math.round(current.humidity)}%`}</dd></div>
      </dl>
    </div>
    {#if data?.forecast.length}
      <div class="outlook" aria-label="Three-day weather outlook">
        {#each data.forecast as day(day.date)}
          {@const ForecastIcon=icons[weatherKind(day.code,day.description??'',true)]}
          <div class="forecast-day"><time datetime={day.date}>{forecastDate(day.date)}</time><div class="forecast-condition"><ForecastIcon size={22} strokeWidth={1.5}/><span>{day.description??'Forecast unavailable'}</span></div><p>{temperatureText(day.high,units)}<span> / {temperatureText(day.low,units)}</span></p></div>
        {/each}
      </div>
    {/if}
  {:else if !error}<p class="weather-empty" role="status">Loading weather…</p>{/if}
  <footer class="weather-footer"><span>{data?.fetched_at?`${data.stale||state.error?'Previous weather · ':''}Retrieved ${new Date(data.fetched_at).toLocaleTimeString([], {hour:'numeric',minute:'2-digit'})}`:'Weather from wttr.in'}</span><a href={weatherUrl(data?.location??preferences.location,units)} target="_blank" rel="noopener noreferrer">wttr.in<ArrowUpRight size={13}/></a></footer>
</section>

<style>
  .weather { margin:0; }
  .weather-body { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1.35fr); gap:30px; align-items:center; }
  .temperature { display:flex; align-items:center; gap:17px; color:var(--highlight); }
  .temperature strong { font-size:42px; font-weight:500; letter-spacing:-1.5px; color:var(--text); font-variant-numeric:tabular-nums; }
  .description { margin:9px 0 0; font-size:14px; line-height:1.7; color:var(--text); }
  .feels { margin:3px 0 0; color:var(--muted); font-size:11px; line-height:1.7; }
  .details { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:20px 24px; margin:0; }
  dt { color:var(--muted); font-size:11px; line-height:1.7; }dd { margin:5px 0 0; font-size:13px; color:var(--text); line-height:1.6; }
  .outlook { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:22px; border-top:1px solid var(--line); margin-top:25px; padding-top:20px; }
  .forecast-day { min-width:0; }.forecast-day time { font-size:11px; color:var(--muted); }.forecast-condition { display:flex; align-items:center; gap:9px; margin-top:12px; color:var(--highlight); }.forecast-condition span { font-size:12px; line-height:1.6; color:var(--text); overflow-wrap:anywhere; }.forecast-condition :global(svg) { flex-shrink:0; }
  .forecast-day p { margin:9px 0 0; font-size:12px; line-height:1.7; color:var(--text); }.forecast-day p span { color:var(--muted); }
  .weather-error { margin:0 0 18px; font-size:12px; line-height:1.8; color:var(--warning); }.weather-empty { font-size:12px; color:var(--muted); padding:15px 0; }
  .weather-footer { display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px; margin-top:20px; padding:0; border:0; font-size:10px; font-weight:400; letter-spacing:0; line-height:1.7; color:var(--muted); }.weather-footer a { display:inline-flex; align-items:center; gap:4px; color:var(--highlight); text-decoration:none; }.weather-footer a:hover { text-decoration:underline; }
  a:focus-visible { outline:2px solid var(--highlight); outline-offset:4px; }
  @media (max-width:650px) { .weather-body { grid-template-columns:1fr; gap:22px; }.temperature strong { font-size:38px; }.details { gap:16px; }.outlook { gap:0; }.forecast-day { padding:0 12px; border-left:1px solid var(--line); }.forecast-day:first-child { padding-left:0; border:0; }.forecast-day:last-child { padding-right:0; }.forecast-condition { flex-direction:column; align-items:flex-start; gap:7px; }.forecast-condition span { font-size:11px; } }
  @media (max-width:420px) {
    .outlook { grid-template-columns:1fr; gap:0; padding-top:4px; }
    .forecast-day { display:grid; grid-template-columns:minmax(0,1fr) auto; align-items:center; gap:6px 12px; padding:14px 0; border:0; border-top:1px solid var(--line); }
    .forecast-day:first-child { border:0; }.forecast-day:last-child { padding-bottom:0; }
    .forecast-day time { grid-column:1; }.forecast-condition { grid-column:1; grid-row:2; flex-direction:row; align-items:center; margin:0; }
    .forecast-day p { grid-column:2; grid-row:1/3; margin:0; white-space:nowrap; }
  }
</style>
