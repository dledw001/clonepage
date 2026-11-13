<script lang="ts">
  import SectionHeader from './SectionHeader.svelte';
  import { onMount,untrack } from 'svelte';
  import { ArrowRight,BookOpen } from '@lucide/svelte';
  import { HomeDataStore,type ContinueSnapshot,type HomeDataState } from '../lib/home';
  let {active=true,previewCount=3,onnavigate}:{active?:boolean;previewCount?:number;onnavigate?:(event:MouseEvent,path:string)=>void}=$props();
  let reading=$state<HomeDataState<ContinueSnapshot>>({data:null,loading:false,error:''});
  const store=new HomeDataStore<ContinueSnapshot>('/api/home/continue',next=>reading=next);
  let mounted=false;
  $effect(()=>{const visible=active;if(mounted&&visible)untrack(()=>{void store.refresh();});});
  onMount(()=>{mounted=true;void store.refresh();const refresh=()=>{if(active&&!document.hidden)void store.refresh();};const timer=setInterval(refresh,60000);document.addEventListener('visibilitychange',refresh);window.addEventListener('focus',refresh);window.addEventListener('clonepage:reading-changed',refresh);return()=>{mounted=false;store.dispose();clearInterval(timer);document.removeEventListener('visibilitychange',refresh);window.removeEventListener('focus',refresh);window.removeEventListener('clonepage:reading-changed',refresh);};});
</script>
{#if reading.data?.items.length||reading.error}
<section class="continue home-surface" aria-label="Continue reading">
  <SectionHeader title="Continue reading">
    {#snippet icon()}<BookOpen size={19} strokeWidth={1.7}/>{/snippet}
    {#snippet actions()}<a class="section-action" href="/?section=reading#home-reading" onclick={event=>onnavigate?.(event,'/?section=reading#home-reading')}>Reading list<ArrowRight size={15}/></a>{/snippet}
  </SectionHeader>
  {#if reading.error}<p class="error" role="status">{reading.error}</p>{/if}
  <div class="resume-list">
    {#each reading.data?.items.slice(0,previewCount)??[] as item(item.id)}
      <a class="resume-link" href={`/reading/${item.id}`} onclick={event=>onnavigate?.(event,`/reading/${item.id}`)}><strong>{item.title}</strong><span>{item.label}</span><ArrowRight size={15}/></a>
    {/each}
  </div>
</section>
{/if}
<style>
  .continue { margin:0; }
  .resume-link { display:grid; grid-template-columns:minmax(0,1fr) auto 16px; align-items:center; gap:14px; padding:14px 0; border-bottom:1px solid var(--line); text-decoration:none; color:var(--text); }
  .resume-link:first-child { padding-top:0; }.resume-link:last-child { padding-bottom:0; border:0; }
  .resume-link strong { font-size:13px; font-weight:500; line-height:1.7; overflow-wrap:anywhere; }
  .resume-link span { font-size:11px; color:var(--muted); white-space:nowrap; }
  .resume-link :global(svg) { color:var(--highlight); }
  .resume-link:hover strong { color:var(--highlight); }
  .resume-link:focus-visible { outline:2px solid var(--highlight); outline-offset:4px; }
  .error { color:var(--warning); font-size:11px; line-height:1.7; }
  @media (max-width:540px) { .resume-link { grid-template-columns:minmax(0,1fr) 16px; gap:4px 12px; }.resume-link span { grid-row:2; }.resume-link :global(svg) { grid-column:2; grid-row:1/3; } }
  @media (hover:none) and (pointer:coarse) { .resume-link { min-height:56px; } }
</style>
