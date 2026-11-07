<script lang="ts">
  import { tick } from 'svelte';
  import { ArrowDown, ArrowUp, ArrowUpRight, Check, Copy, Pencil, Plus, Settings2, Trash2, X } from '@lucide/svelte';
  import { shortcutCatalog, shortcutIcon } from '../lib/shortcut-icons';
  import type { Shortcut, ShortcutDraft, ShortcutState, ShortcutStore } from '../lib/shortcuts';

  let { data, store, onexecute }: { data: ShortcutState; store: ShortcutStore; onexecute: (item: Shortcut) => void } = $props();
  let managing = $state(false);
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

<section class="shortcuts" aria-label="Your places">
  <div class="section-header">
    <span>YOUR PLACES</span>
    <div class="shortcut-heading-actions"><span class="section-count">{data.loaded ? String(data.items.length).padStart(2, '0') : '—'} SHORTCUTS</span>
      <button class="manage-button" class:active={managing} aria-expanded={managing} onclick={() => { managing = !managing; }} disabled={data.busy || editorOpen}><Settings2 size={14} />{managing ? 'Done' : 'Manage shortcuts'}</button>
    </div>
  </div>

  {#if data.error && !editorOpen}<p class="shortcut-error" role="alert">{data.error}<button onclick={() => store.refresh()} disabled={data.loading || data.busy}>Try again</button></p>{/if}
  {#if !data.loaded}<p class="shortcut-status" role="status">{data.loading ? 'Loading your places...' : 'Your shortcuts are unavailable.'}</p>{/if}

  {#if managing && data.loaded}
    <div class="shortcut-manager">
      <div class="manager-heading"><div><h2>Your shortcuts</h2><p>Make this space yours. Changes are shared across your devices.</p></div>
        <button bind:this={addButton} class="editor-primary" onclick={() => edit()} disabled={data.busy || editorOpen}><Plus size={15} />Add shortcut</button>
      </div>
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
      <div class="managed-list" aria-label="Shortcut order">
        {#each data.items as item, index (item.id)}{@const Icon = shortcutIcon(item.icon)}
          <div class="managed-row"><span class="managed-icon" style:color={item.color}><Icon size={19} /></span><div class="managed-name"><strong>{item.name}</strong><small>{item.description || (item.action === 'copy' ? 'Copy text' : item.value)}</small></div>
            <div class="row-actions"><button class="small-icon" disabled={index === 0 || data.busy || editorOpen} aria-label={`Move ${item.name} up`} title="Move up" onclick={() => store.move(item.id, -1)}><ArrowUp size={15} /></button><button class="small-icon" disabled={index === data.items.length - 1 || data.busy || editorOpen} aria-label={`Move ${item.name} down`} title="Move down" onclick={() => store.move(item.id, 1)}><ArrowDown size={15} /></button>
              <button class="small-icon" disabled={data.busy || editorOpen} aria-label={`Edit ${item.name}`} title="Edit" onclick={() => edit(item)}><Pencil size={15} /></button><button class="small-icon" disabled={data.busy || editorOpen} aria-label={`Delete ${item.name}`} title="Delete" onclick={() => deleting = deleting === item.id ? null : item.id}><Trash2 size={15} /></button></div>
          </div>
          {#if deleting === item.id}<div class="delete-confirm" role="group" aria-label={`Confirm deleting ${item.name}`}><span>Delete “{item.name}”?</span><button disabled={data.busy} onclick={() => deleting = null}>Keep shortcut</button><button class="delete-button" disabled={data.busy} onclick={() => remove(item.id)}>Delete shortcut</button></div>{/if}
        {/each}
      </div>
      {#if !data.items.length}<p class="shortcut-status">No shortcuts yet. Add your first place above.</p>{/if}
    </div>
  {:else if data.loaded && !data.items.length}<p class="shortcut-status">Your places, your way. Choose Manage shortcuts to add your first one.</p>{/if}

  {#if data.items.length}<div class="shortcut-grid" aria-label="Service shortcuts">
    {#snippet card(item: Shortcut)}{@const Icon = shortcutIcon(item.icon)}<span class="shortcut-icon"><Icon size={21} strokeWidth={1.65} /></span><span class="shortcut-bottom"><span class="shortcut-name">{item.name}</span>{#if item.action === 'copy'}<Copy size={14} class="shortcut-arrow" />{:else}<ArrowUpRight size={14} class="shortcut-arrow" />{/if}</span>{/snippet}
    {#each data.items as item (item.id)}
      {#if item.action === 'open'}<a class="shortcut-card" style={`--accent:${item.color}`} href={item.value} target="_blank" rel="noopener noreferrer" title={item.description || item.name}>{@render card(item)}</a>
      {:else}<button class="shortcut-card" style={`--accent:${item.color}`} title={item.description || item.name} onclick={() => onexecute(item)}>{@render card(item)}</button>{/if}
    {/each}
  </div>{/if}
</section>

<style>
  .shortcut-heading-actions, .manage-button, .manager-heading, .editor-heading, .editor-primary, .row-actions, .managed-row, .editor-footer, .color-picker, .delete-confirm { display: flex; align-items: center; gap: 10px; }
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
