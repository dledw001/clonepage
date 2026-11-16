<script lang="ts">
  import { onMount, tick } from 'svelte';
  import { ArrowDown, ArrowRight, ArrowUp, Pin, Search, X } from '@lucide/svelte';
  import { iconSizes } from '../lib/icons';
  import { shortcutIcon } from '../lib/shortcut-icons';
  import { favoriteShortcuts, groupShortcuts, type Shortcut } from '../lib/shortcuts';
  import { webSearchUrl } from '../lib/navigation';
  import { filterCommands, nextCommandIndex } from '../lib/command-search';
  let { commands, loaded, loading, onexecute, onclose, favoriteIds=[], busy=false, error='', initialEditing=false,
    onpin, onmove, onrefresh }: {
    commands:Shortcut[];loaded:boolean;loading:boolean;
    onexecute:(command:Shortcut)=>void;onclose:()=>void; favoriteIds?:string[]; busy?:boolean; error?:string; initialEditing?:boolean;
    onpin?:(id:string)=>Promise<boolean>; onmove?:(id:string,offset:number)=>Promise<boolean>; onrefresh?:()=>void;
  } = $props();
  let dialog:HTMLDialogElement;
  let input:HTMLInputElement;
  let results:HTMLDivElement;
  let viewportHeight=$state<number>();
  let query=$state('');
  let selected=$state(-1);
  let editingFavorites=$state(false);
  const favorites=$derived(favoriteShortcuts({items:commands,favorite_ids:favoriteIds}));
  const favoriteSet=$derived(new Set(favorites.map(item=>item.id)));
  const otherGroups=$derived(groupShortcuts(commands.filter(item=>!favoriteSet.has(item.id))));
  const groups=$derived(query.trim()?groupShortcuts(filterCommands(commands,query)):
    editingFavorites?[{name:'Favorites',items:favorites},...otherGroups].filter(group=>group.items.length):otherGroups);
  const favoriteGrid=$derived(!query.trim()&&!editingFavorites?favorites:[]);
  const matches=$derived([...favoriteGrid,...groups.flatMap(group=>group.items)]);
  export function focusSearch(){input?.focus();}
  function dismiss(){dialog.close();}
  function backdrop(event:MouseEvent){if(event.target===dialog)dismiss();}
  async function toggleEditing(){editingFavorites=!editingFavorites;query='';selected=-1;await tick();input.focus();}
  async function togglePin(id:string){
    await onpin?.(id);await tick();
    const button=Array.from(results.querySelectorAll<HTMLButtonElement>('[data-pin-id]')).find(button=>button.dataset.pinId===id);
    if(button)button.focus();else input.focus();
  }
  async function moveFavorite(id:string,offset:number){
    await onmove?.(id,offset);await tick();
    Array.from(results.querySelectorAll<HTMLButtonElement>('[data-pin-id]')).find(button=>button.dataset.pinId===id)?.focus();
  }
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
      if(typeof HTMLElement!=='undefined'&&event.target instanceof HTMLElement&&event.target.closest('.favorite-actions'))return;
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
    editingFavorites=initialEditing;
    const resize=()=>{viewportHeight=Math.min(window.innerHeight,window.visualViewport?.height??window.innerHeight);};
    resize();window.addEventListener('resize',resize);window.visualViewport?.addEventListener('resize',resize);
    dialog.showModal();input.focus();
    return()=>{window.removeEventListener('resize',resize);window.visualViewport?.removeEventListener('resize',resize);if(dialog.open)dialog.close();};
  });
</script>

<dialog id="command-search" class="command-palette" style:--search-viewport-height={viewportHeight?`${viewportHeight}px`:undefined} aria-label="Search Clonepage" bind:this={dialog} onclose={onclose} oncancel={event=>{event.preventDefault();dismiss();}} onclick={backdrop} onkeydown={keydown}>
  <div class="search-input">
    <Search size={iconSizes.search}/>
    <input bind:this={input} bind:value={query} oninput={()=>selected=-1} aria-label="Search shortcuts or the web" placeholder={editingFavorites?'Find a shortcut to pin…':'Search shortcuts or the web…'} autocomplete="off" spellcheck="false"/>
    <button type="button" class="close-search" aria-label="Close search" onclick={dismiss}><kbd>Esc</kbd><X size={iconSizes.action}/></button>
  </div>
  <div class="command-results" bind:this={results}>
    <div class="palette-heading"><span class="results-heading">{query.trim()?'Matching shortcuts':editingFavorites?'Edit favorites':favorites.length?'Favorites':'Shortcuts'}</span>
      {#if loaded&&commands.length&&onpin}<button type="button" class="edit-favorites" aria-pressed={editingFavorites} onclick={toggleEditing}>{editingFavorites?'Done':'Edit favorites'}</button>{/if}
    </div>
    {#if error}<p class="command-status save-error" role="alert">{error}</p>{/if}
    {#if !loaded}<p class="command-status" role="status">{loading?'Loading shortcuts…':'Shortcuts could not be loaded.'}{#if !loading&&onrefresh}<button type="button" class="edit-favorites" onclick={onrefresh}>Try again</button>{/if}</p>
    {:else if !commands.length}<p class="command-status">Add shortcuts in Your places.</p>
    {:else if !matches.length}<p class="command-status">No matching shortcuts.</p>{/if}
    {#if editingFavorites&&!query.trim()}<p class="favorite-help">Pin your most-used shortcuts. Use the arrows to arrange favorites across your devices.</p>{/if}
    {#snippet resultContent(command:Shortcut,compact=false)}
      {@const Icon=shortcutIcon(command.icon)}
      <span class="result-icon" style:--shortcut-color={command.color}><Icon size={iconSizes.shortcut}/></span>
      <span class="result-text"><strong>{command.name}</strong>{#if !compact}<small>{command.description}</small>{/if}</span>
      {#if !compact}<ArrowRight size={iconSizes.action}/>{/if}
    {/snippet}
    {#if favoriteGrid.length}<div class="favorite-grid" aria-label="Favorite shortcuts">
      {#each favoriteGrid as command,index(command.id)}
        {#if command.action==='open'}<a data-command-result href={command.value} target="_blank" rel="noopener noreferrer" class="result favorite-result" class:selected={selected===index} onfocus={()=>selected=index} onclick={dismiss}>{@render resultContent(command,true)}</a>
        {:else}<button data-command-result type="button" class="result favorite-result" class:selected={selected===index} onfocus={()=>selected=index} onclick={()=>onexecute(command)}>{@render resultContent(command,true)}</button>{/if}
      {/each}
    </div>{/if}
    {#each groups as group}
      <div class="result-group" aria-label={group.name||'Other shortcuts'}>
        <div class="results-heading">{group.name||'Other shortcuts'}</div>
        {#each group.items as command(command.id)}
          {@const index=matches.findIndex(item=>item.id===command.id)}
          {@const favoriteIndex=favorites.findIndex(item=>item.id===command.id)}
          <div class="result-row">
            {#if command.action==='open'&&command.value}
              <a data-command-result href={command.value} target="_blank" rel="noopener noreferrer" class="result" class:selected={selected===index} onfocus={()=>selected=index} onclick={dismiss}>{@render resultContent(command)}</a>
            {:else}<button data-command-result type="button" class="result" class:selected={selected===index} onfocus={()=>selected=index} onclick={()=>onexecute(command)}>{@render resultContent(command)}</button>{/if}
            {#if onpin}<div class="favorite-actions">
              {#if editingFavorites&&favoriteIndex>=0&&onmove}<button type="button" class="favorite-action" disabled={busy||favoriteIndex===0} aria-label={`Move ${command.name} earlier in favorites`} onclick={()=>moveFavorite(command.id,-1)}><ArrowUp size={iconSizes.inline}/></button><button type="button" class="favorite-action" disabled={busy||favoriteIndex===favorites.length-1} aria-label={`Move ${command.name} later in favorites`} onclick={()=>moveFavorite(command.id,1)}><ArrowDown size={iconSizes.inline}/></button>{/if}
              <button type="button" class="favorite-action" data-pin-id={command.id} disabled={busy} aria-label={`${favoriteSet.has(command.id)?'Unpin':'Pin'} ${command.name}`} aria-pressed={favoriteSet.has(command.id)} onclick={()=>togglePin(command.id)}><Pin size={iconSizes.action}/></button>
            </div>{/if}
          </div>
        {/each}
      </div>
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
  kbd { font:inherit; font-size: var(--font-caption); }
  .command-results { max-height:min(540px,calc(var(--search-viewport-height,100dvh) - var(--palette-top) - 96px)); overflow-y:auto; overscroll-behavior:contain; padding:8px; }
  .results-heading { padding:9px 12px; font-size: var(--font-caption); font-weight:600; letter-spacing:.7px; color:var(--muted); text-transform:uppercase; }
  .result,.web-result { display:flex; align-items:center; gap:12px; width:100%; padding:11px 12px; border:0; border-radius:9px; background:transparent; color:var(--text); text-align:left; text-decoration:none; font:inherit; cursor:pointer; }
  .result.selected,.result:hover,.web-result:hover { background:var(--surface-hover); }
  .result-icon { width:35px; height:35px; flex-shrink:0; display:grid; place-items:center; border-radius:9px; background:color-mix(in srgb,var(--highlight) 9%,transparent); color:var(--shortcut-color,var(--highlight)); }
  :global([data-theme="light"]) .result-icon { color:color-mix(in srgb,var(--shortcut-color,var(--highlight)) 55%,var(--text)); }
  .result-text { flex:1; min-width:0; display:flex; flex-direction:column; gap:4px; overflow-wrap:anywhere; }.result-text strong { font-size: var(--font-title); font-weight:500; line-height:1.6; }.result-text small { font-size: var(--font-small); color:var(--muted); line-height:1.6; }
  .result>:global(svg:last-child),.web-result>:global(svg:last-child) { flex-shrink:0; color:var(--muted); }
  .web-result { margin-top:6px; border-top:1px solid var(--line); border-radius:0 0 8px 8px; font-size: var(--font-body); line-height:1.7; color:var(--highlight); }.web-result span { flex:1; min-width:0; overflow-wrap:anywhere; }
  .command-status { margin:10px 12px; color:var(--muted); font-size: var(--font-body); line-height:1.7; }
  :is(a,button):focus-visible { outline:2px solid var(--highlight); outline-offset:-2px; }
  @media (max-width:540px) { .command-palette { --palette-top:16px; }.search-input { padding:8px 12px; gap:10px; }.close-search kbd { display:none; }.result,.web-result { padding:10px; } }
  .palette-heading,.result-row,.favorite-actions { display:flex; align-items:center; }
  .palette-heading { justify-content:space-between; gap:8px; padding-right:8px; }
  .edit-favorites { border:0; background:transparent; color:var(--highlight); padding:9px 4px; font-size:var(--font-small); cursor:pointer; }
  .favorite-help { margin:0 12px 10px; color:var(--muted); font-size:var(--font-small); line-height:1.6; }
  .favorite-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:6px; padding:0 4px 12px; }
  .favorite-result { border:1px solid var(--line); background:var(--bg); gap:8px; padding:10px 8px; }
  .favorite-result .result-icon { width:26px; height:30px; background:transparent; }
  .favorite-result .result-text strong { font-size:var(--font-small); }
  .result-row>.result { flex:1; min-width:0; }
  .result-group+.result-group { border-top:1px solid var(--line); padding-top:5px; margin-top:5px; }
  .favorite-actions { flex-shrink:0; }
  .favorite-action { display:grid; place-items:center; padding:6px; width:30px; min-height:40px; border:0; border-radius:7px; background:transparent; color:var(--muted); cursor:pointer; }
  .favorite-action[aria-pressed="true"] { color:var(--highlight); background:var(--surface-hover); }
  .favorite-action:hover { background:var(--surface-hover); color:var(--text); }
  .favorite-action:disabled { opacity:.4; cursor:default; }
  .save-error { color:var(--danger); }
  @media(max-width:540px) { .favorite-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }.favorite-action { width:29px; }.result-row .result { padding:10px 6px; gap:8px; }.result-row .result-icon { width:26px; }.result-row .result:has(+.favorite-actions)>:global(svg:last-child) { display:none; } }
  @media(hover:none) and (pointer:coarse) { .edit-favorites,.favorite-action { min-height:44px; }.favorite-action { width:36px; } }
</style>
