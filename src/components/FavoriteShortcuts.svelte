<script lang="ts">
  import { onDestroy, tick } from 'svelte';
  import { LayoutGrid, Pin } from '@lucide/svelte';
  import { iconSizes } from '../lib/icons';
  import { shortcutIcon } from '../lib/shortcut-icons';
  import { favoriteShortcuts, type Shortcut, type ShortcutState } from '../lib/shortcuts';
  import { FavoriteHold } from '../lib/favorite-hold';
  let { data, onopen, onexecute }: { data:ShortcutState; onopen:(edit?:boolean)=>void; onexecute:(item:Shortcut)=>void } = $props();
  const favorites = $derived(favoriteShortcuts(data));
  let hint = $state<{id:string;label:string;x:number;y:number}|null>(null);
  let tooltip = $state<HTMLDivElement>();
  let controls:HTMLDivElement;
  let version = 0;
  let touchHintTimer:ReturnType<typeof setTimeout>|undefined;
  async function showName(item:Shortcut, target:HTMLElement) {
    clearTimeout(touchHintTimer);
    const current = ++version, box = target.getBoundingClientRect();
    hint = { id:item.id, label:item.name, x:box.left, y:box.bottom + 8 };
    await tick();
    if (current !== version || !tooltip || !hint) return;
    const size = tooltip.getBoundingClientRect();
    hint = { ...hint, x:Math.max(12,Math.min(box.left + (box.width - size.width)/2,window.innerWidth - size.width - 12)),
      y:box.top - size.height - 8 >= 8 ? box.top - size.height - 8 : box.bottom + 8 };
  }
  function hideName() { ++version; hint = null; }
  const hold = new FavoriteHold(id => {
    const item = favorites.find(item => item.id === id);
    const target = Array.from(controls.querySelectorAll<HTMLElement>('[data-favorite-id]')).find(el => el.dataset.favoriteId === id);
    if (item && target) { void showName(item,target); clearTimeout(touchHintTimer); touchHintTimer=setTimeout(hideName,2500); }
  });
  function leave() { hold.cancel(); clearTimeout(touchHintTimer); hideName(); }
  function activate(event:MouseEvent,item:Shortcut) {
    if (event.detail > 0 && hold.consumeClick(item.id)) { event.preventDefault(); return; }
    hideName();
    if (item.action === 'copy') onexecute(item);
  }
  onDestroy(() => { hold.cancel(); clearTimeout(touchHintTimer); });
</script>

<svelte:document onpointerdown={event=>{if(!(event.target instanceof Element)||!event.target.closest('[data-favorite-id]'))leave();}} />
<svelte:window onscroll={leave} onresize={leave} onkeydown={event=>{if(event.key==='Escape')leave();}} />
<nav class="favorite-bar" aria-label="Favorite shortcuts">
  <span class="favorite-heading"><Pin size={iconSizes.inline}/>Favorites</span>
  <div class="favorite-list" bind:this={controls}>
    {#each favorites as item(item.id)}
      {@const Icon=shortcutIcon(item.icon)}
      {#if item.action==='open'}
        <a class="favorite-control" href={item.value} target="_blank" rel="noopener noreferrer" style:--favorite-color={item.color}
          data-favorite-id={item.id} aria-label={item.name} aria-describedby={hint?.id===item.id?'favorite-shortcut-name':undefined}
          onpointerenter={event=>{if(event.pointerType!=='touch')void showName(item,event.currentTarget);}} onpointerleave={event=>{if(event.pointerType!=='touch')leave();}}
          onfocus={event=>{if(event.currentTarget.matches(':focus-visible'))void showName(item,event.currentTarget);}} onblur={leave}
          onpointerdown={event=>hold.start(item.id,event)} onpointermove={event=>{if(hold.move(event))hideName();}}
          onpointerup={event=>hold.end(event.pointerId)} onpointercancel={leave}
          oncontextmenu={event=>{if(hold.isHeld(item.id))event.preventDefault();}} onclick={event=>activate(event,item)}><Icon size={iconSizes.shortcut}/></a>
      {:else}
        <button type="button" class="favorite-control" style:--favorite-color={item.color} data-favorite-id={item.id}
          aria-label={item.name} aria-describedby={hint?.id===item.id?'favorite-shortcut-name':undefined}
          onpointerenter={event=>{if(event.pointerType!=='touch')void showName(item,event.currentTarget);}} onpointerleave={event=>{if(event.pointerType!=='touch')leave();}}
          onfocus={event=>{if(event.currentTarget.matches(':focus-visible'))void showName(item,event.currentTarget);}} onblur={leave}
          onpointerdown={event=>hold.start(item.id,event)} onpointermove={event=>{if(hold.move(event))hideName();}}
          onpointerup={event=>hold.end(event.pointerId)} onpointercancel={leave}
          oncontextmenu={event=>{if(hold.isHeld(item.id))event.preventDefault();}} onclick={event=>activate(event,item)}><Icon size={iconSizes.shortcut}/></button>
      {/if}
    {/each}
    {#if data.loaded&&!favorites.length}<button type="button" class="choose-favorites" onclick={()=>onopen(true)}>Choose favorites</button>{/if}
    {#if !data.loaded}<span class="pending">{data.loading?'Loading shortcuts…':'Shortcuts unavailable'}</span>{/if}
  </div>
  <button type="button" class="all-shortcuts" aria-haspopup="dialog" onclick={()=>{leave();onopen();}}><LayoutGrid size={iconSizes.action}/><span>All shortcuts</span></button>
</nav>
{#if data.error}<p class="favorite-error" role="alert">{data.error}</p>{/if}
{#if hint}<div class="favorite-name" id="favorite-shortcut-name" role="tooltip" bind:this={tooltip} style:left={`${hint.x}px`} style:top={`${hint.y}px`}>{hint.label}</div>{/if}

<style>
  .favorite-bar { display:flex; align-items:center; flex-wrap:wrap; gap:10px; width:100%; max-width:1060px; margin:13px auto 0; }
  .favorite-heading { display:flex; align-items:center; gap:6px; color:var(--muted); font-size:var(--font-caption); }
  .favorite-list { display:flex; flex:1; align-items:center; flex-wrap:wrap; gap:8px; min-width:0; }
  .favorite-control { width:46px; height:46px; display:grid; place-items:center; flex-shrink:0; padding:0; border:1px solid var(--line); border-radius:11px; background:var(--surface); color:var(--favorite-color,var(--highlight)); text-decoration:none; touch-action:pan-y; -webkit-touch-callout:none; user-select:none; }
  :global([data-theme="light"]) .favorite-control { color:color-mix(in srgb,var(--favorite-color,var(--highlight)) 55%,var(--text)); }
  .favorite-control:hover { background:var(--surface-hover); border-color:color-mix(in srgb,var(--highlight) 45%,var(--line)); }
  button { font:inherit; cursor:pointer; }
  .all-shortcuts,.choose-favorites { display:flex; align-items:center; gap:7px; padding:9px 0 9px 5px; min-height:44px; border:0; background:transparent; color:var(--highlight); font-size:var(--font-small); }
  .pending { color:var(--muted); font-size:var(--font-small); }
  .favorite-error { max-width:1060px; width:100%; margin:8px auto 0; color:var(--danger); font-size:var(--font-small); }
  .favorite-name { position:fixed; z-index:1000; max-width:min(260px,calc(100vw - 24px)); padding:6px 10px; border:1px solid var(--line); border-radius:8px; background:var(--surface-hover); color:var(--text); font-size:var(--font-small); line-height:1.5; overflow-wrap:anywhere; pointer-events:none; box-shadow:0 4px 14px rgb(0 0 0 / .15); }
  :is(a,button):focus-visible { outline:2px solid var(--highlight); outline-offset:3px; }
  @media(max-width:540px) { .favorite-bar { display:grid; grid-template-columns:1fr auto; gap:9px; }.favorite-list { grid-column:1/-1; grid-row:2; }.all-shortcuts { padding:0; }.favorite-control { width:44px; height:44px; } }
</style>
