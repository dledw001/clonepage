<script lang="ts">
  import { iconSizes } from '../lib/icons';
  import { untrack } from 'svelte';
  import { ArrowDown, ArrowUp, RotateCcw, X } from '@lucide/svelte';
  import { copySettings, defaultHomeSettings, moveSection, sectionLabels, type HomeLayoutStore, type LayoutState } from '../lib/home';
  let { data, store, onclose }: {data:LayoutState;store:HomeLayoutStore;onclose:()=>void}=$props();
  let draft=$state(untrack(()=>copySettings(store.state.settings)));
  let draftRevision=untrack(()=>store.state.revision);
  async function save(){if(await store.save(copySettings(draft),draftRevision))onclose();}
  async function reload(){await store.refresh(true);if(!store.state.error){draft=copySettings(store.state.settings);draftRevision=store.state.revision;}}
</script>
<section class="customize home-surface" aria-labelledby="customize-title">
  <header><div><h2 id="customize-title">Homepage settings</h2></div><button class="icon" onclick={onclose} aria-label="Close homepage customization"><X size={iconSizes.action}/></button></header>
  {#if data.error}<p class="error" role="alert">{data.error}</p>{/if}
  {#if data.conflict}<button class="reload" onclick={reload} disabled={data.loading}>Reload latest layout</button><p class="hint">Your draft stays here until you reload or cancel.</p>{/if}
  <label class="clock-toggle"><input type="checkbox" bind:checked={draft.show_clock}/>Show clock in top bar</label>
  <div class="sections">
    {#each draft.sections as section,index(section.id)}
      <div class="section-row"><label><input type="checkbox" bind:checked={section.visible}/>{sectionLabels[section.id]}</label><div><button class="icon" disabled={index===0} aria-label={`Move ${sectionLabels[section.id]} up`} onclick={()=>draft=moveSection(draft,section.id,-1)}><ArrowUp size={iconSizes.action}/></button><button class="icon" disabled={index===draft.sections.length-1} aria-label={`Move ${sectionLabels[section.id]} down`} onclick={()=>draft=moveSection(draft,section.id,1)}><ArrowDown size={iconSizes.action}/></button></div></div>
    {/each}
  </div>
  <fieldset class="weather-settings"><legend>Weather</legend><div>
    <label>US ZIP code or city, country<input aria-label="Weather location" bind:value={draft.weather.location} maxlength="80" placeholder="22206 or London, UK"/></label>
    <label>Weather units<select aria-label="Weather units" bind:value={draft.weather.units}><option value="F">Fahrenheit · mph</option><option value="C">Celsius · km/h</option></select></label>
  </div><p>Location is shared across your devices. US ZIP codes are qualified with United States when requesting weather.</p></fieldset>
  <div class="previews">
    <label>Reading-list preview<select bind:value={draft.previews.reading}>{#each [3,5,10] as count}<option value={count}>{count} articles</option>{/each}</select></label>
    <label>Feed preview<select bind:value={draft.previews.feeds}>{#each [3,5,10] as count}<option value={count}>{count} stories</option>{/each}</select></label>
    <label>Continue reading<select bind:value={draft.previews.continue_reading}>{#each [3,5,10] as count}<option value={count}>{count} articles</option>{/each}</select></label>
    <label>Hacker News preview<select bind:value={draft.previews.hacker_news}>{#each [3,5,10] as count}<option value={count}>{count} stories</option>{/each}</select></label>
    <label>Hacker News view<select bind:value={draft.hacker_news.view}><option value="front">Front page</option><option value="top">Top stories</option><option value="discussed">Most discussed</option></select></label>
    <label>Hacker News window<select bind:value={draft.hacker_news.window} disabled={draft.hacker_news.view==='front'}><option value="24h">Past 24 hours</option><option value="7d">Past 7 days</option><option value="30d">Past 30 days</option></select></label>
  </div>
  <footer><button class="defaults" onclick={()=>draft=defaultHomeSettings()} disabled={data.busy}><RotateCcw size={iconSizes.inline}/>Restore defaults</button><div><button onclick={onclose} disabled={data.busy}>Cancel</button><button class="primary" onclick={save} disabled={!data.loaded||data.busy||data.conflict}>{data.busy?'Saving…':'Save layout'}</button></div></footer>
</section>
<style>
  .customize{max-width:1060px;margin:0 auto var(--section-gap)}header,footer,.section-row{display:flex;justify-content:space-between;align-items:center;gap:16px}h2{font-size:20px;font-weight:500;margin:0}p,.hint{font-size:12px;line-height:1.7;color:var(--muted)}button,select{font:inherit;color:var(--text);background:var(--bg);border:1px solid var(--line);border-radius:7px;padding:9px 12px;font-size:12px}button:hover{background:var(--surface-hover)}.icon{display:inline-flex;justify-content:center;align-items:center;padding:8px;background:transparent}.clock-toggle,.section-row label{display:flex;align-items:center;gap:10px;font-size:12px}.clock-toggle{margin:24px 0 15px}.section-row{padding:11px 0;border-bottom:1px solid var(--line)}.section-row>div{display:flex;gap:5px}.previews{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px;margin:24px 0}.previews label{font-size:11px;color:var(--muted)}select{display:block;width:100%;margin-top:8px}.primary{background:var(--highlight);color:var(--bg)}footer{margin-top:22px}footer>div{display:flex;gap:8px}.defaults{display:flex;gap:7px;align-items:center;background:transparent;color:var(--muted)}.error{color:var(--warning)}.reload{color:var(--highlight)}input{accent-color:var(--highlight)}button:disabled{opacity:.45;cursor:default}:is(input,button,select):focus-visible{outline:2px solid var(--highlight);outline-offset:3px}@media (max-width:600px){.previews{grid-template-columns:repeat(2,minmax(0,1fr))}footer{flex-wrap:wrap}footer>div{margin-left:auto}}@media (hover:none) and (pointer:coarse){button,select,.section-row label,.clock-toggle{min-height:44px}}
  .weather-settings { border:0; padding:0; margin:24px 0; min-width:0; }.weather-settings legend { font-size:12px; font-weight:500; padding:0; margin-bottom:14px; }.weather-settings>div { display:grid; grid-template-columns:minmax(0,1.5fr) minmax(0,1fr); gap:18px; }.weather-settings label { font-size:11px; color:var(--muted); min-width:0; }.weather-settings input { display:block; width:100%; margin-top:8px; padding:9px 12px; border:1px solid var(--line); border-radius:7px; background:var(--bg); color:var(--text); font:inherit; font-size:12px; }.weather-settings p { margin:10px 0 0; font-size:11px; }
  @media (max-width:600px) { .weather-settings>div { grid-template-columns:1fr; } }
</style>
