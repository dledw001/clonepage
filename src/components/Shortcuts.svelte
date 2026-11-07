<script lang="ts">
  import { onDestroy, tick } from 'svelte';
  import { ArrowDown, ArrowUp, ArrowUpRight, Check, Copy, GripVertical, Pencil, Plus, Settings2, Trash2, X } from '@lucide/svelte';
  import { shortcutCatalog, shortcutIcon } from '../lib/shortcut-icons';
  import ServiceSetup from './ServiceSetup.svelte';
  import { ShortcutDrag } from '../lib/shortcut-drag';
  import type { Shortcut, ShortcutDraft, ShortcutState, ShortcutStore } from '../lib/shortcuts';

  let { data, store, onexecute, active = true }: { data: ShortcutState; store: ShortcutStore; onexecute: (item: Shortcut) => void; active?: boolean } = $props();
  let managing = $state(false);
  let serviceOpen = $state(false);
  $effect(() => { if (!active) serviceOpen = false; });
  let editorOpen = $state(false);
  let editingId = $state<string | null>(null);
  let draftRevision = 0;
  const emptyDraft = (): ShortcutDraft => ({ name: '', description: '', aliases: [], icon: 'Link', color: '#a4b9e4', action: 'open', value: '' });
  let draft = $state<ShortcutDraft>(emptyDraft());
  let aliases = $state('');
  let iconSearch = $state('');
  let deleting = $state<string | null>(null);
  let nameInput = $state<HTMLInputElement>();
  let addButton = $state<HTMLButtonElement>();
  const PreviewIcon = $derived(shortcutIcon(draft.icon));
  const icons = $derived(shortcutCatalog.icons.filter(icon => `${icon.label} ${icon.key} ${icon.keywords}`.toLowerCase().includes(iconSearch.toLowerCase().trim())));

  let grid = $state<HTMLDivElement>();
  let drag = $state<{ id: string; active: boolean; slot: number | null; x: number; y: number;
    width: number; height: number; offsetX: number; offsetY: number } | null>(null);
  let dragNotice = $state('');
  let gesture: ShortcutDrag | null = null;
  let captured: HTMLButtonElement | null = null;
  let scrollFrame: number | undefined;
  const draggedItem = $derived(data.items.find(item => item.id === drag?.id));

  function cancelDrag() {
    const pointerId = gesture?.pointerId;
    gesture = null; drag = null;
    if (scrollFrame !== undefined) cancelAnimationFrame(scrollFrame);
    scrollFrame = undefined;
    const handle = captured; captured = null;
    if (pointerId !== undefined && handle?.hasPointerCapture(pointerId)) handle.releasePointerCapture(pointerId);
  }

  function updateDrag(pointerId: number, x: number, y: number) {
    if (!gesture || !drag || pointerId !== gesture.pointerId) return;
    const cards = Array.from(grid?.querySelectorAll<HTMLElement>('[data-shortcut-id]') ?? []).map(card => card.getBoundingClientRect());
    gesture.move(pointerId, x, y, cards);
    drag = { ...drag, x, y, active: gesture.active, slot: gesture.slot };
  }

  function autoScroll() {
    if (!gesture?.active) return;
    const edge = 65;
    const speed = gesture.y < edge ? -Math.min(15, (edge - gesture.y) / 4)
      : gesture.y > window.innerHeight - edge ? Math.min(15, (gesture.y - window.innerHeight + edge) / 4) : 0;
    if (speed) {
      window.scrollBy({ top: speed, behavior: 'instant' });
      updateDrag(gesture.pointerId, gesture.x, gesture.y);
    }
    scrollFrame = requestAnimationFrame(autoScroll);
  }

  function startDrag(event: PointerEvent, item: Shortcut) {
    if (event.button !== 0 || !event.isPrimary || data.busy || editorOpen || serviceOpen || gesture) return;
    const handle = event.currentTarget as HTMLButtonElement;
    const card = handle.closest<HTMLElement>('[data-shortcut-id]');
    if (!card) return;
    event.preventDefault();
    const bounds = card.getBoundingClientRect();
    gesture = new ShortcutDrag(item.id, event.pointerId, data.revision, data.items.map(item => item.id), event.clientX, event.clientY);
    drag = { id: item.id, active: false, slot: null, x: event.clientX, y: event.clientY,
      width: bounds.width, height: bounds.height, offsetX: event.clientX - bounds.left, offsetY: event.clientY - bounds.top };
    dragNotice = ''; deleting = null;
    captured = handle; handle.focus({ preventScroll: true }); handle.setPointerCapture(event.pointerId);
  }

  function moveDrag(event: PointerEvent) {
    if (event.pointerId !== gesture?.pointerId) return;
    event.preventDefault();
    updateDrag(event.pointerId, event.clientX, event.clientY);
    if (gesture?.active && scrollFrame === undefined) scrollFrame = requestAnimationFrame(autoScroll);
  }

  async function endDrag(event: PointerEvent) {
    if (event.pointerId !== gesture?.pointerId) return;
    updateDrag(event.pointerId, event.clientX, event.clientY);
    const order = gesture!.finish(event.pointerId);
    const revision = gesture!.revision;
    cancelDrag();
    if (order) {
      dragNotice = 'Saving shortcut order...';
      dragNotice = await store.reorder(order, revision) ? 'Shortcut order saved.' : store.state.error;
    }
  }

  function cancelPointer(event: PointerEvent) { if (event.pointerId === gesture?.pointerId) cancelDrag(); }
  function dragKey(event: KeyboardEvent) {
    if (event.key === 'Escape' && gesture) { event.preventDefault(); cancelDrag(); dragNotice = 'Reordering cancelled.'; }
  }
  function handleKey(event: KeyboardEvent, item: Shortcut) {
    if (gesture || data.busy || editorOpen || serviceOpen) return;
    if (['ArrowUp', 'ArrowLeft', 'ArrowDown', 'ArrowRight'].includes(event.key)) {
      event.preventDefault();
      void store.move(item.id, event.key === 'ArrowUp' || event.key === 'ArrowLeft' ? -1 : 1);
    }
  }

  $effect(() => {
    const revision = data.revision;
    if (drag && gesture && revision !== gesture.revision) { cancelDrag(); dragNotice = 'Shortcuts changed while arranging. Try again.'; }
  });
  $effect(() => {
    if (!dragNotice || dragNotice === 'Saving shortcut order...') return;
    const timer = setTimeout(() => dragNotice = '', 4000);
    return () => clearTimeout(timer);
  });
  onDestroy(cancelDrag);

  async function edit(item?: Shortcut) {
    managing = true; editorOpen = true; editingId = item?.id ?? null; deleting = null;
    draftRevision = data.revision;
    draft = item ? { name: item.name, description: item.description, aliases: [...item.aliases], icon: item.icon, color: item.color, action: item.action, value: item.value } : emptyDraft();
    aliases = draft.aliases.join(', '); iconSearch = '';
    await tick(); nameInput?.focus();
  }

  async function closeEditor() {
    editorOpen = false;
    await tick(); addButton?.focus();
  }

  async function save(event: SubmitEvent) {
    event.preventDefault();
    const result = await store.save({ ...draft, aliases: aliases.split(',').map(value => value.trim()).filter(Boolean) }, editingId, draftRevision);
    if (result) await closeEditor();
    else if (store.state.conflict) draftRevision = store.state.revision;
  }

  async function remove(id: string) { if (await store.remove(id)) deleting = null; }
</script>

<svelte:window onpointermove={moveDrag} onpointerup={endDrag} onpointercancel={cancelPointer} onkeydown={dragKey} onblur={cancelDrag} />

{#snippet card(item: Shortcut)}
  {@const Icon = shortcutIcon(item.icon)}
  <span class="shortcut-icon"><Icon size={21} strokeWidth={1.65} /></span>
  <span class="shortcut-bottom"><span class="shortcut-name">{item.name}</span>{#if item.action === 'copy'}<Copy size={14} class="shortcut-arrow" />{:else}<ArrowUpRight size={14} class="shortcut-arrow" />{/if}</span>
{/snippet}

{#snippet cards()}
  {#if data.items.length}
    <div bind:this={grid} class="shortcut-grid" class:arranging={managing} aria-label="Service shortcuts">
      {#each data.items as item, index (item.id)}
        {#if managing}
          <article class="shortcut-card sortable-card" data-shortcut-id={item.id} style={`--accent:${item.color}`} class:drag-source={drag?.active && drag.id === item.id}
            class:drop-before={drag?.active && drag.slot === index} class:drop-after={drag?.active && drag.slot === data.items.length && index === data.items.length - 1}>
            {@render card(item)}
            <button class="drag-handle" aria-label={`Drag to reorder ${item.name}`} title="Drag to reorder; arrow keys also move this shortcut" disabled={data.busy || editorOpen || serviceOpen}
              onpointerdown={event => startDrag(event, item)} onlostpointercapture={cancelPointer} onkeydown={event => handleKey(event, item)} onclick={event => event.preventDefault()}><GripVertical size={19} /></button>
          </article>
        {:else if item.action === 'open'}<a class="shortcut-card" style={`--accent:${item.color}`} href={item.value} target="_blank" rel="noopener noreferrer" title={item.description || item.name}>{@render card(item)}</a>
        {:else}<button class="shortcut-card" style={`--accent:${item.color}`} title={item.description || item.name} onclick={() => onexecute(item)}>{@render card(item)}</button>{/if}
      {/each}
    </div>
  {/if}
{/snippet}

<section class="shortcuts" aria-label="Your places">
  <div class="section-header">
    <span>YOUR PLACES</span>
    <div class="shortcut-heading-actions"><span class="section-count">{data.loaded ? String(data.items.length).padStart(2, '0') : '—'} SHORTCUTS</span>
      <button class="manage-button" class:active={managing} aria-expanded={managing} onclick={() => { managing = !managing; }} disabled={data.busy || editorOpen || serviceOpen || drag !== null}><Settings2 size={14} />{managing ? 'Done' : 'Manage shortcuts'}</button>
    </div>
  </div>

  {#if data.error && !editorOpen}<p class="shortcut-error" role="alert">{data.error}<button onclick={() => store.refresh()} disabled={data.loading || data.busy}>Try again</button></p>{/if}
  {#if !data.loaded}<p class="shortcut-status" role="status">{data.loading ? 'Loading your places...' : 'Your shortcuts are unavailable.'}</p>{/if}

  {#if managing && data.loaded}
    <div class="shortcut-manager">
      <div class="manager-heading"><div><h2>Your shortcuts</h2><p>Drag the card handles to arrange your places. Changes save across your devices.</p></div>
        <div class="manager-add-actions"><button class="editor-primary" onclick={() => serviceOpen = true} disabled={data.busy || editorOpen || serviceOpen || drag !== null}><Plus size={15} />Add service</button><button bind:this={addButton} class="editor-primary" onclick={() => edit()} disabled={data.busy || editorOpen || serviceOpen || drag !== null}><Plus size={15} />Add shortcut</button></div>
      </div>
      {#if serviceOpen}<ServiceSetup onclose={() => serviceOpen = false} onsaved={snapshot => store.accept(snapshot)} />{/if}
      {#if editorOpen}
        <form class="shortcut-editor" onsubmit={save}>
          <div class="editor-heading"><h3>{editingId ? 'Edit shortcut' : 'New shortcut'}</h3><button type="button" class="small-icon" aria-label="Close shortcut editor" onclick={closeEditor} disabled={data.busy}><X size={18} /></button></div>
          <fieldset disabled={data.busy}>
            <div class="editor-fields">
              <label>Name<input bind:this={nameInput} bind:value={draft.name} required maxlength="80" placeholder="qBittorrent" /></label>
              <label>Description<input bind:value={draft.description} maxlength="200" placeholder="Torrent client" /></label>
              <label>Action<select bind:value={draft.action}><option value="open">Open website</option><option value="copy">Copy text</option></select></label>
              {#if draft.action === 'open'}<label>Website URL<input bind:value={draft.value} type="url" required maxlength="4000" placeholder="https://service.example.com" /></label>
              {:else}<label>Text to copy<textarea bind:value={draft.value} required maxlength="4000" rows="3" placeholder="ssh arch"></textarea></label>{/if}
              <label class="wide">Search aliases<input bind:value={aliases} maxlength="1600" placeholder="qbit, torrent, downloads" /><small>Separate aliases with commas.</small></label>
            </div>
            <div class="appearance">
              <div class="appearance-settings"><label>Find an icon<input bind:value={iconSearch} type="search" aria-label="Search shortcut icons" placeholder="Search icons..." /></label>
                <div class="icon-picker" role="group" aria-label="Shortcut icon">
                  {#each icons as icon (icon.key)}{@const Icon = shortcutIcon(icon.key)}<button type="button" class:selected={draft.icon === icon.key} aria-pressed={draft.icon === icon.key} aria-label={icon.label} title={icon.label} onclick={() => draft.icon = icon.key}><Icon size={21} />{#if draft.icon === icon.key}<Check size={10} class="selected-check" />{/if}</button>{/each}
                  {#if !icons.length}<p>No matching icons.</p>{/if}
                </div>
                <div class="color-label">Color</div><div class="color-picker" role="group" aria-label="Shortcut color">
                  {#each shortcutCatalog.colors as color}<button type="button" style:background={color} aria-label={`Use color ${color}`} aria-pressed={draft.color === color} class:selected={draft.color === color} onclick={() => draft.color = color}>{#if draft.color === color}<Check size={13} />{/if}</button>{/each}
                  <label class="custom-color">Custom<input type="color" bind:value={draft.color} aria-label="Custom shortcut color" /></label>
                </div>
              </div>
              <div class="preview"><span class="preview-label">CARD PREVIEW</span>
                <div class="shortcut-card" style={`--accent:${draft.color}`}><span class="shortcut-icon"><PreviewIcon size={21} strokeWidth={1.65} /></span><span class="shortcut-bottom"><span class="shortcut-name">{draft.name || 'Your shortcut'}</span>{#if draft.action === 'copy'}<Copy size={14} class="shortcut-arrow" />{:else}<ArrowUpRight size={14} class="shortcut-arrow" />{/if}</span></div>
                <p>{draft.description || (draft.action === 'copy' ? 'Copies text to your clipboard' : 'Opens in a new tab')}</p>
              </div>
            </div>
            {#if data.error}<p class="shortcut-error" role="alert">{data.error}</p>{/if}
            <div class="editor-footer"><button type="button" onclick={closeEditor}>Cancel</button><button type="submit" class="editor-primary">{data.busy ? 'Saving...' : 'Save shortcut'}</button></div>
          </fieldset>
        </form>
      {/if}
      {@render cards()}
      <div class="managed-list" aria-label="Shortcut order">
        {#each data.items as item, index (item.id)}{@const Icon = shortcutIcon(item.icon)}
          <div class="managed-row"><span class="managed-icon" style:color={item.color}><Icon size={19} /></span><div class="managed-name"><strong>{item.name}</strong><small>{item.description || (item.action === 'copy' ? 'Copy text' : item.value)}</small></div>
            <div class="row-actions"><button class="small-icon" disabled={index === 0 || data.busy || editorOpen || serviceOpen || drag !== null} aria-label={`Move ${item.name} up`} title="Move up" onclick={() => store.move(item.id, -1)}><ArrowUp size={15} /></button><button class="small-icon" disabled={index === data.items.length - 1 || data.busy || editorOpen || serviceOpen || drag !== null} aria-label={`Move ${item.name} down`} title="Move down" onclick={() => store.move(item.id, 1)}><ArrowDown size={15} /></button>
              <button class="small-icon" disabled={data.busy || editorOpen || serviceOpen || drag !== null} aria-label={`Edit ${item.name}`} title="Edit" onclick={() => edit(item)}><Pencil size={15} /></button><button class="small-icon" disabled={data.busy || editorOpen || serviceOpen || drag !== null} aria-label={`Delete ${item.name}`} title="Delete" onclick={() => deleting = deleting === item.id ? null : item.id}><Trash2 size={15} /></button></div>
          </div>
          {#if deleting === item.id}<div class="delete-confirm" role="group" aria-label={`Confirm deleting ${item.name}`}><span>Delete “{item.name}”?</span><button disabled={data.busy} onclick={() => deleting = null}>Keep shortcut</button><button class="delete-button" disabled={data.busy} onclick={() => remove(item.id)}>Delete shortcut</button></div>{/if}
        {/each}
      </div>
      {#if !data.items.length}<p class="shortcut-status">No shortcuts yet. Add your first place above.</p>{/if}
    </div>
  {:else if data.loaded && !data.items.length}<p class="shortcut-status">Your places, your way. Choose Manage shortcuts to add your first one.</p>{/if}

  {#if !managing}{@render cards()}{/if}
  <p class="drag-status" aria-live="polite" aria-atomic="true">{dragNotice}</p>
  {#if drag?.active && draggedItem}
    <div class="shortcut-card drag-ghost" aria-hidden="true" style={`--accent:${draggedItem.color};width:${drag.width}px;height:${drag.height}px;left:${drag.x - drag.offsetX}px;top:${drag.y - drag.offsetY}px`}>
      {@render card(draggedItem)}
    </div>
  {/if}
</section>

<style>
  .arranging { margin-top: 22px; }
  .sortable-card { position: relative; }
  .sortable-card:hover { transform: none; }
  .drag-source { opacity: .3; }
  .drag-handle { position: absolute; right: 7px; top: 7px; display: grid; place-items: center; min-width: 34px; min-height: 34px; padding: 5px; color: var(--muted); cursor: grab; touch-action: none; user-select: none; border-radius: 6px; }
  .drag-handle:hover { color: var(--highlight); background: var(--bg); }
  .drag-handle:active { cursor: grabbing; }
  .drop-before::before, .drop-after::after { content: ''; position: absolute; top: -3px; bottom: -3px; width: 3px; border-radius: 3px; background: var(--highlight); box-shadow: 0 0 9px color-mix(in srgb, var(--highlight) 40%, transparent); }
  .drop-before::before { left: -8px; }
  .drop-after::after { right: -8px; }
  .drag-ghost { position: fixed; z-index: 100; pointer-events: none; opacity: .96; border-color: var(--highlight); box-shadow: 0 15px 40px rgba(0,0,0,.35); transform: rotate(-2deg); }
  .drag-status { position: fixed; z-index: 110; bottom: 24px; left: 50%; transform: translateX(-50%); max-width: calc(100vw - 32px); width: max-content; padding: 12px 17px; border: 1px solid var(--line); border-radius: 10px; background: var(--surface-hover); color: var(--text); box-shadow: 0 8px 25px rgba(0,0,0,.25); font-size: 11px; line-height: 1.7; margin: 0; pointer-events: none; }
  .drag-status:empty { display: none; }

  .shortcut-heading-actions, .manage-button, .manager-heading, .editor-heading, .editor-primary, .row-actions, .managed-row, .editor-footer, .color-picker, .delete-confirm { display: flex; align-items: center; gap: 10px; }
  .manager-add-actions { display: flex; gap: 9px; flex-wrap: wrap; justify-content: flex-end; }
  .shortcut-heading-actions { gap: 18px; }
  button, input, select, textarea { font: inherit; }
  button { border: 0; background: transparent; color: var(--muted); cursor: pointer; }
  button:disabled { cursor: default; opacity: .4; }
  button:focus-visible, input:focus-visible, select:focus-visible, textarea:focus-visible { outline: 2px solid var(--highlight); outline-offset: 3px; }
  .manage-button { font-size: 10px; padding: 5px 0; white-space: nowrap; }
  .manage-button:hover, .manage-button.active, .small-icon:hover { color: var(--highlight); }
  .shortcut-manager { margin-bottom: 22px; padding: 24px; background: var(--surface); border: 1px solid var(--line); border-radius: 14px; }
  .manager-heading, .editor-heading { justify-content: space-between; }
  h2, h3 { color: var(--text); font-weight: 500; font-size: 15px; margin: 0; }
  .manager-heading p, .preview p { color: var(--muted); font-size: 11px; line-height: 1.7; margin: 7px 0 0; }
  .editor-primary { padding: 10px 13px; background: var(--surface-hover); border: 1px solid var(--line); color: var(--text); border-radius: 8px; font-size: 11px; justify-content: center; white-space: nowrap; }
  .shortcut-editor { margin-top: 23px; padding: 22px; background: var(--bg); border: 1px solid var(--line); border-radius: 11px; }
  fieldset { border: 0; padding: 0; margin: 20px 0 0; min-width: 0; }
  .editor-fields { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
  label, .color-label { display: flex; flex-direction: column; gap: 8px; font-size: 11px; color: var(--text); min-width: 0; }
  label small { color: var(--muted); font-size: 10px; }
  input, select, textarea { width: 100%; min-width: 0; color: var(--text); background: var(--surface); border: 1px solid var(--line); padding: 11px 12px; border-radius: 7px; font-size: 12px; outline-color: var(--highlight); }
  textarea { resize: vertical; }
  .wide { grid-column: 1 / -1; }
  .appearance { display: grid; grid-template-columns: minmax(0, 1fr) 210px; gap: 30px; margin-top: 25px; }
  .icon-picker { display: grid; grid-template-columns: repeat(auto-fill, minmax(41px, 1fr)); gap: 7px; margin: 12px 0 20px; max-height: 185px; overflow-y: auto; padding: 3px; }
  .icon-picker button { position: relative; display: grid; place-items: center; height: 40px; border: 1px solid var(--line); border-radius: 7px; background: var(--surface); }
  .icon-picker button.selected { color: var(--highlight); border-color: var(--highlight); background: var(--surface-hover); }
  .icon-picker :global(.selected-check) { position: absolute; right: 2px; bottom: 2px; }
  .icon-picker p { color: var(--muted); font-size: 11px; grid-column: 1 / -1; }
  .color-picker { flex-wrap: wrap; gap: 8px; margin-top: 10px; padding: 3px; }
  .color-picker > button { width: 23px; height: 23px; padding: 0; border-radius: 50%; color: var(--bg); display: grid; place-items: center; }
  .color-picker > button.selected { outline: 1px solid var(--text); outline-offset: 3px; }
  .custom-color { flex-direction: row; align-items: center; margin-left: 7px; color: var(--muted); font-size: 10px; }
  .custom-color input { width: 30px; height: 28px; padding: 2px; }
  .preview-label { display: block; margin: 0 0 12px; color: var(--muted); font-size: 9px; letter-spacing: 1.3px; }
  .preview .shortcut-card { width: 100%; }
  .preview .shortcut-card:hover { transform: none; }
  .editor-footer { justify-content: flex-end; margin-top: 25px; font-size: 11px; }
  .managed-list { margin-top: 18px; }
  .managed-row { padding: 14px 0; border-bottom: 1px solid var(--line); }
  .managed-icon { flex-shrink: 0; display: grid; place-items: center; width: 32px; }
  .managed-name { min-width: 0; flex: 1; }
  .managed-name strong { color: var(--text); font-size: 12px; font-weight: 500; }
  .managed-name small { display: block; color: var(--muted); font-size: 10px; margin-top: 5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .row-actions { gap: 6px; }
  .small-icon { display: inline-flex; padding: 6px; align-items: center; justify-content: center; }
  .delete-confirm { justify-content: flex-end; flex-wrap: wrap; padding: 15px 0; font-size: 11px; color: var(--text); }
  .delete-confirm > span { margin-right: auto; }
  .delete-button { color: var(--warning); }
  .shortcut-status, .shortcut-error { font-size: 12px; line-height: 1.8; color: var(--muted); }
  .shortcut-error { color: var(--warning); }
  .shortcut-error button { color: var(--highlight); margin-left: 12px; }
  @media (max-width: 650px) { .section-header { flex-wrap: wrap; gap: 12px; } .shortcut-heading-actions { gap: 12px; } .manager-heading { align-items: flex-start; gap: 15px; } .shortcut-manager { padding: 18px; } .shortcut-editor { padding: 16px; } .editor-fields { grid-template-columns: 1fr; } .appearance { grid-template-columns: 1fr; gap: 20px; } .preview { max-width: 210px; } .row-actions { gap: 0; } .small-icon { padding: 7px 5px; } }
</style>
