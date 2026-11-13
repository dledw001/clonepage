<script lang="ts">
  import { onMount, tick } from 'svelte';
  import { ArrowRight, Search, X } from '@lucide/svelte';
  import { iconSizes } from '../lib/icons';
  import { shortcutIcon } from '../lib/shortcut-icons';
  import type { Shortcut } from '../lib/shortcuts';
  import { webSearchUrl } from '../lib/navigation';
  import { filterCommands, nextCommandIndex } from '../lib/command-search';
  let { commands, loaded, loading, onexecute, onclose }: {
    commands:Shortcut[];loaded:boolean;loading:boolean;
    onexecute:(command:Shortcut)=>void;onclose:()=>void;
  } = $props();
  let dialog:HTMLDialogElement;
  let input:HTMLInputElement;
  let results:HTMLDivElement;
  let viewportHeight=$state<number>();
  let query=$state('');
  let selected=$state(-1);
  const matches=$derived(filterCommands(commands,query));
  export function focusSearch(){input?.focus();}
  function dismiss(){dialog.close();}
  function backdrop(event:MouseEvent){if(event.target===dialog)dismiss();}
  async function keydown(event:KeyboardEvent){
    if(event.isComposing)return;
    if(event.key==='Tab'){
      const controls=Array.from(dialog.querySelectorAll<HTMLElement>('input:not([disabled]),button:not([disabled]),a[href]'));
      const first=controls[0],last=controls.at(-1);
      if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}
      else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}
      return;
    }
    if(event.key==='ArrowDown'||event.key==='ArrowUp'){
      event.preventDefault();
      selected=nextCommandIndex(selected,event.key==='ArrowDown'?1:-1,matches.length);
      await tick();
      if(selected<0)input.focus();
      else (results.querySelectorAll<HTMLElement>('[data-command-result]')[selected])?.focus();
    }else if(event.key==='Enter'&&event.target===input){
      event.preventDefault();
      if(matches.length)onexecute(matches[Math.max(0,Math.min(selected,matches.length-1))]);
      else if(query.trim())window.location.assign(webSearchUrl(query));
    }
  }
  onMount(()=>{
    const resize=()=>{viewportHeight=Math.min(window.innerHeight,window.visualViewport?.height??window.innerHeight);};
    resize();window.addEventListener('resize',resize);window.visualViewport?.addEventListener('resize',resize);
    dialog.showModal();input.focus();
    return()=>{window.removeEventListener('resize',resize);window.visualViewport?.removeEventListener('resize',resize);if(dialog.open)dialog.close();};
  });
</script>

<dialog id="command-search" class="command-palette" style:--search-viewport-height={viewportHeight?`${viewportHeight}px`:undefined} aria-label="Search Clonepage" bind:this={dialog} onclose={onclose} oncancel={event=>{event.preventDefault();dismiss();}} onclick={backdrop} onkeydown={keydown}>
  <div class="search-input">
    <Search size={iconSizes.search}/>
    <input bind:this={input} bind:value={query} oninput={()=>selected=-1} aria-label="Search shortcuts or the web" placeholder="Search shortcuts or the web…" autocomplete="off" spellcheck="false"/>
    <button type="button" class="close-search" aria-label="Close search" onclick={dismiss}><kbd>Esc</kbd><X size={iconSizes.action}/></button>
  </div>
  <div class="command-results" bind:this={results}>
    <div class="results-heading">{query.trim()?'Matching shortcuts':'Shortcuts'}</div>
    {#if !loaded}<p class="command-status" role="status">{loading?'Loading shortcuts…':'Shortcuts could not be loaded.'}</p>
    {:else if !commands.length}<p class="command-status">Add shortcuts in Your places.</p>
    {:else if !matches.length}<p class="command-status">No matching shortcuts.</p>{/if}
    {#snippet resultContent(command:Shortcut)}
      {@const Icon=shortcutIcon(command.icon)}
      <span class="result-icon"><Icon size={iconSizes.shortcut}/></span>
      <span class="result-text"><strong>{command.name}</strong><small>{command.description}</small></span>
      <ArrowRight size={iconSizes.action}/>
    {/snippet}
    {#each matches as command,index(command.id)}
      {#if command.action==='open'&&command.value}
        <a data-command-result href={command.value} target="_blank" rel="noopener noreferrer" class="result" class:selected={selected===index} onfocus={()=>selected=index} onclick={dismiss}>{@render resultContent(command)}</a>
      {:else}
        <button data-command-result type="button" class="result" class:selected={selected===index} onfocus={()=>selected=index} onclick={()=>onexecute(command)}>{@render resultContent(command)}</button>
      {/if}
    {/each}
    {#if query.trim()}<a class="web-result" href={webSearchUrl(query)}><Search size={iconSizes.action}/><span>Search the web for “{query}”</span><ArrowRight size={iconSizes.action}/></a>{/if}
  </div>
</dialog>

<style>
  .command-palette { position:fixed; inset:0; --palette-top:min(12vh,90px); margin:var(--palette-top) auto auto; width:640px; max-width:calc(100vw - 32px); max-height:calc(var(--search-viewport-height,100dvh) - var(--palette-top) - 16px); padding:0; border:1px solid var(--line); border-radius:18px; background:var(--surface); color:var(--text); box-shadow:0 24px 70px rgba(0,0,0,.3); overflow:hidden; }
  .command-palette::backdrop { background:rgba(0,0,0,.5); }
  .search-input { display:flex; align-items:center; gap:13px; padding:12px 16px; border-bottom:1px solid var(--line); color:var(--muted); }
  input { flex:1; min-width:0; border:0; background:transparent; color:var(--text); font:inherit; font-size:16px; line-height:1.5; padding:8px 0; outline:0; }
  input::placeholder { color:var(--muted); }
  .close-search { display:flex; align-items:center; gap:6px; min-width:44px; min-height:44px; padding:5px; border:0; border-radius:8px; color:var(--muted); background:transparent; cursor:pointer; }
  .close-search:hover { background:var(--surface-hover); color:var(--text); }
  kbd { font:inherit; font-size:10px; }
  .command-results { max-height:min(540px,calc(var(--search-viewport-height,100dvh) - var(--palette-top) - 96px)); overflow-y:auto; overscroll-behavior:contain; padding:8px; }
  .results-heading { padding:9px 12px; font-size:10px; font-weight:600; letter-spacing:1.2px; color:var(--muted); text-transform:uppercase; }
  .result,.web-result { display:flex; align-items:center; gap:12px; width:100%; padding:11px 12px; border:0; border-radius:9px; background:transparent; color:var(--text); text-align:left; text-decoration:none; font:inherit; cursor:pointer; }
  .result.selected,.result:hover,.web-result:hover { background:var(--surface-hover); }
  .result-icon { width:35px; height:35px; flex-shrink:0; display:grid; place-items:center; border-radius:9px; background:color-mix(in srgb,var(--highlight) 9%,transparent); color:var(--highlight); }
  .result-text { flex:1; min-width:0; display:flex; flex-direction:column; gap:4px; overflow-wrap:anywhere; }.result-text strong { font-size:13px; font-weight:500; line-height:1.6; }.result-text small { font-size:11px; color:var(--muted); line-height:1.6; }
  .result>:global(svg:last-child),.web-result>:global(svg:last-child) { flex-shrink:0; color:var(--muted); }
  .web-result { margin-top:6px; border-top:1px solid var(--line); border-radius:0 0 8px 8px; font-size:12px; line-height:1.7; color:var(--highlight); }.web-result span { flex:1; min-width:0; overflow-wrap:anywhere; }
  .command-status { margin:10px 12px; color:var(--muted); font-size:12px; line-height:1.7; }
  :is(a,button):focus-visible { outline:2px solid var(--highlight); outline-offset:-2px; }
  @media (max-width:540px) { .command-palette { --palette-top:16px; }.search-input { padding:8px 12px; gap:10px; }.close-search kbd { display:none; }.result,.web-result { padding:10px; } }
</style>
