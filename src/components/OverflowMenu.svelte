<script lang="ts">
  import { onMount } from 'svelte';
  import type { Snippet } from 'svelte';
  import { Ellipsis } from '@lucide/svelte';
  let { label, children }: { label:string; children:Snippet } = $props();
  let details: HTMLDetailsElement;
  let opensUp=$state(false);
  let panelMaxHeight=$state('calc(100dvh - 32px)');
  let frame:number|undefined;
  function positionPanel(){
    if(frame!==undefined)cancelAnimationFrame(frame);
    if(!details.open)return;
    frame=requestAnimationFrame(()=>{
      frame=undefined;
      if(!details.open)return;
      const bounds=details.getBoundingClientRect(), height=(details.querySelector('.menu-panel')?.scrollHeight??0)+2;
      const above=bounds.top-12, below=window.innerHeight-bounds.bottom-12;
      opensUp=below<height&&above>below;
      panelMaxHeight=`${Math.max(80,opensUp?above:below)}px`;
    });
  }
  onMount(()=>{
    const outside=(event:PointerEvent)=>{if(details.open&&!event.composedPath().includes(details))details.open=false;};
    const escape=(event:KeyboardEvent)=>{if(details.open&&event.key==='Escape'){event.preventDefault();details.open=false;details.querySelector('summary')?.focus();}};
    const choose=(event:MouseEvent)=>{const control=(event.target as Element).closest('button,a');if(control&&!control.hasAttribute('disabled'))details.open=false;};
    details.addEventListener('toggle',positionPanel);window.addEventListener('resize',positionPanel);
    document.addEventListener('pointerdown',outside);document.addEventListener('keydown',escape);details.querySelector('.menu-panel')?.addEventListener('click',choose as EventListener);
    return()=>{if(frame!==undefined)cancelAnimationFrame(frame);details.removeEventListener('toggle',positionPanel);window.removeEventListener('resize',positionPanel);document.removeEventListener('pointerdown',outside);document.removeEventListener('keydown',escape);details.querySelector('.menu-panel')?.removeEventListener('click',choose as EventListener);};
  });
</script>

<details class="overflow-menu" class:upwards={opensUp} name="reading-article-actions" bind:this={details}>
  <summary aria-label={label} title={label}><Ellipsis size={19}/></summary>
  <div class="menu-panel" style:max-height={panelMaxHeight}>{@render children()}</div>
</details>

<style>
  .overflow-menu { position:relative; }
  summary { list-style:none; display:grid; place-items:center; width:36px; height:36px; border-radius:8px; color:var(--muted); cursor:pointer; }
  summary::-webkit-details-marker { display:none; }
  summary:hover,.overflow-menu[open]>summary { color:var(--text); background:var(--surface-hover); }
  summary:focus-visible { outline:2px solid var(--highlight); outline-offset:3px; }
  .menu-panel { position:absolute; right:0; top:calc(100% + 6px); z-index:12; width:244px; max-width:calc(100vw - 44px); padding:8px; max-height:calc(100dvh - 32px); overflow-y:auto; background:var(--surface); border:1px solid var(--line); border-radius:12px; box-shadow:0 14px 36px rgba(0,0,0,.22); }
  .overflow-menu.upwards .menu-panel { top:auto; bottom:calc(100% + 6px); }
  .menu-panel :global(button),.menu-panel :global(a) { display:flex; align-items:center; gap:9px; width:100%; padding:10px; border:0; border-radius:7px; background:transparent; color:var(--text); text-align:left; text-decoration:none; font:inherit; font-size:12px; line-height:1.5; cursor:pointer; }
  .menu-panel :global(button:hover),.menu-panel :global(a:hover) { background:var(--surface-hover); }
  .menu-panel :global(button:disabled) { opacity:.45; cursor:default; }
  .menu-panel :global(.menu-danger) { color:var(--warning); }
  .menu-panel :global(.menu-note) { display:block; padding:8px 10px; margin:0; color:var(--muted); font-size:11px; line-height:1.7; overflow-wrap:anywhere; }
  .menu-panel :global(hr) { border:0; border-top:1px solid var(--line); margin:6px 0; }
  .menu-panel :global(:is(a,button):focus-visible) { outline:2px solid var(--highlight); outline-offset:1px; }
  @media (hover:none) and (pointer:coarse) { summary { width:44px; height:44px; }.menu-panel :global(button),.menu-panel :global(a) { min-height:44px; } }
</style>
