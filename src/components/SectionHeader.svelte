<script lang="ts">
  import type { Snippet } from 'svelte';
  let { title, count, subtitle, icon, actions }: {
    title: string; count?: string; subtitle?: string; icon?: Snippet; actions?: Snippet;
  } = $props();
</script>

<header class="section-heading">
  <div class="section-identity">
    {#if icon}<span class="section-symbol" aria-hidden="true">{@render icon()}</span>{/if}
    <div class="section-labels">
      <div class="section-title-row"><h2>{title}</h2>{#if count}<span class="section-total">{count}</span>{/if}</div>
      {#if subtitle}<p>{subtitle}</p>{/if}
    </div>
  </div>
  {#if actions}<div class="section-actions">{@render actions()}</div>{/if}
</header>

<style>
  .section-heading { display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:12px 18px; min-height:36px; margin-bottom:20px; }
  .section-identity { display:flex; align-items:center; gap:10px; min-width:0; }
  .section-symbol { display:grid; place-items:center; color:var(--highlight); flex-shrink:0; width:20px; }
  .section-labels { min-width:0; }
  .section-title-row { display:flex; align-items:baseline; flex-wrap:wrap; gap:6px 12px; }
  h2 { margin:0; color:var(--text); font-size:11px; font-weight:600; letter-spacing:1.4px; text-transform:uppercase; line-height:1.6; }
  .section-total { color:var(--muted); font-size:10px; font-weight:400; line-height:1.6; white-space:nowrap; }
  p { margin:5px 0 0; font-size:11px; line-height:1.6; color:var(--muted); }
  .section-actions { display:flex; align-items:center; justify-content:flex-end; flex-wrap:wrap; gap:8px; margin-left:auto; }
</style>
