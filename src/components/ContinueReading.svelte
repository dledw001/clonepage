<script lang="ts">
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
<section class="continue" aria-label="Continue reading"><header><h2><BookOpen size={17}/>CONTINUE READING</h2><a href="/?section=reading#home-reading" onclick={event=>onnavigate?.(event,'/?section=reading#home-reading')}>Reading list <ArrowRight size={14}/></a></header>{#if reading.error}<p class="error" role="status">{reading.error}</p>{/if}<div class="cards">{#each reading.data?.items.slice(0,previewCount)??[] as item(item.id)}<a class="card" href={`/reading/${item.id}`} onclick={event=>onnavigate?.(event,`/reading/${item.id}`)}><small>{item.format==='pdf'?'PDF':'ARTICLE'}</small><strong>{item.title}</strong><span>{item.label}<ArrowRight size={14}/></span></a>{/each}</div></section>
{/if}
<style>
.continue{margin:35px 0;padding:26px 30px;background:var(--surface);border:1px solid var(--line);border-radius:16px}header,header a,h2,.card>span{display:flex;align-items:center;gap:8px}header{justify-content:space-between;gap:15px}h2{margin:0;font-size:10px;letter-spacing:.12em;font-weight:600}header a{font-size:11px;color:var(--highlight);text-decoration:none}.cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-top:22px}.card{display:flex;flex-direction:column;gap:9px;padding:16px;border:1px solid var(--line);border-radius:10px;text-decoration:none;color:var(--text);background:var(--bg);min-width:0}.card:hover{background:var(--surface-hover)}.card>small{font-size:9px;letter-spacing:.1em;color:var(--muted)}.card>strong{font-size:12px;font-weight:500;line-height:1.7;overflow-wrap:anywhere}.card>span{justify-content:space-between;font-size:11px;color:var(--highlight);margin-top:auto;padding-top:5px}.error{color:var(--warning);font-size:11px;line-height:1.7}a:focus-visible{outline:2px solid var(--highlight);outline-offset:3px}@media (max-width:650px){.continue{padding:22px 18px}.cards{grid-template-columns:1fr}}
</style>
