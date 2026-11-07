<script lang="ts">
  import { onMount, tick } from 'svelte';
  import { Check, Link, LockKeyhole, LogOut, Plus, Search, Server, X } from '@lucide/svelte';
  import { DSMServiceClient, type DSMState, type ServiceDraft } from '../lib/dsm-services';
  import { shortcutCatalog, shortcutIcon } from '../lib/shortcut-icons';
  import type { ShortcutSnapshot } from '../lib/shortcuts';

  let { onclose, onsaved }: { onclose: () => void; onsaved: (snapshot: ShortcutSnapshot) => void } = $props();
  let view = $state<DSMState>({ connection: null, options: null, preview: null, result: null, busy: false, error: '' });
  const client = new DSMServiceClient(next => view = next);
  let username = $state(''); let password = $state(''); let otp = $state('');
  let draft = $state<ServiceDraft>({ name: '', description: '', aliases: [], icon: 'Server', color: '#a4b9e4', subdomain: '', upstream: '', template_id: '', certificate_id: '' });
  let reviewHeading = $state<HTMLHeadingElement>();
  let nameInput = $state<HTMLInputElement>();
  let errorMessage = $state<HTMLParagraphElement>();
  let resultPanel = $state<HTMLDivElement>();
  let aliases = $state(''); let iconSearch = $state('');
  const icons = $derived(shortcutCatalog.icons.filter(icon => `${icon.label} ${icon.key} ${icon.keywords}`.toLowerCase().includes(iconSearch.toLowerCase().trim())));
  const PreviewIcon = $derived(shortcutIcon(draft.icon));
  const stageLabel = $derived(view.result?.stage === 'creating_rule' ? 'Rule creation needs checking'
    : view.result?.stage === 'rule_created' ? 'Rule created; certificate setup is unfinished'
    : view.result?.stage === 'certificate_assigned' ? 'Certificate assigned; HTTPS check is unfinished'
    : view.result?.stage === 'https_verified' ? 'HTTPS verified; shortcut saving is unfinished' : 'Setup needs another review');

  onMount(() => {
    void client.refresh();
    const timer = setInterval(() => { if (!document.hidden && view.connection?.connected) void client.refresh(); }, 15000);
    return () => { clearInterval(timer); password = ''; otp = ''; client.dispose(); };
  });

  $effect(() => {
    if (view.error) void tick().then(() => { errorMessage?.focus({ preventScroll: true }); errorMessage?.scrollIntoView({ block: 'center', behavior: 'instant' }); });
  });

  async function connect(event: SubmitEvent) {
    event.preventDefault();
    const pending = client.connect(username.trim(), password, otp.trim()); password = ''; otp = '';
    await pending;
    if (view.options?.certificates.length === 1) draft.certificate_id = view.options.certificates[0].id;
    if (view.options) { await tick(); nameInput?.focus(); }
  }

  async function review(event: SubmitEvent) {
    event.preventDefault(); await client.preview({ ...draft, aliases: aliases.split(',').map(value => value.trim()).filter(Boolean) });
    if (view.preview) { await tick(); reviewHeading?.focus({ preventScroll: true }); reviewHeading?.scrollIntoView({ block: 'center', behavior: 'instant' }); }
  }
  async function create() {
    const result = await client.apply();
    if (result?.ok && result.shortcuts) { onsaved(result.shortcuts); await tick(); resultPanel?.focus({ preventScroll: true }); resultPanel?.scrollIntoView({ block: 'center', behavior: 'instant' }); }
  }
  async function close() { await client.disconnect(); onclose(); }
</script>

<section class="service-setup" aria-label="Add service">
  <header><div><h3><Server size={17} />Add a service</h3><p>Create its HTTPS address and shortcut together.</p></div><button class="icon-button" aria-label="Close service setup" disabled={view.busy} onclick={close}><X size={18} /></button></header>
  {#if view.error}<p bind:this={errorMessage} class="error" role="alert" tabindex="-1">{view.error}</p>{/if}
  {#if view.result?.ok}
    <div bind:this={resultPanel} class="success" role="status" tabindex="-1"><Check size={22} /><div><strong>Your service is ready.</strong><a href={view.result.url} target="_blank" rel="noopener noreferrer">{view.result.url}</a><p>The shortcut has been added. Your DSM connection has been closed.</p></div></div>
    <button class="primary" onclick={onclose}>Done</button>
  {:else}
    {#if view.result}<p class="resume-note">{stageLabel}. Your form is kept. Connect again and review it to resume the same setup.</p>{/if}
    {#if !view.connection}<p class="help">{view.error ? 'The connection settings could not be loaded.' : 'Checking the secure connection settings...'}</p>{#if view.error}<button class="primary" disabled={view.busy} onclick={() => client.refresh()}>Try again</button>{/if}{/if}
    {#if view.connection && !view.connection.connected}
      <form class="connect-form" onsubmit={connect} autocomplete="off">
        <div class="connection-title"><LockKeyhole size={16} /><strong>Connect to DSM</strong><span>{view.connection.dsm_url}</span></div>
        <p class="help">Your password and authenticator code are used for this sign-in only. The temporary connection closes after setup or five idle minutes.</p>
        <fieldset disabled={view.busy}>
          <div class="login-fields"><label>DSM username<input bind:value={username} required maxlength="128" autocomplete="off" /></label><label>DSM password<input type="password" bind:value={password} required maxlength="1024" autocomplete="off" /></label><label>Authenticator code <small>if enabled</small><input bind:value={otp} maxlength="16" inputmode="numeric" autocomplete="one-time-code" /></label></div>
          <button class="primary" type="submit">{view.busy ? 'Connecting...' : 'Connect to DSM'}</button>
        </fieldset>
      </form>
    {:else if view.connection?.connected}
      <div class="connected"><span><LockKeyhole size={14} />Connected temporarily to DSM</span><button disabled={view.busy} onclick={() => client.disconnect()}><LogOut size={14} />Disconnect</button></div>
    {/if}

    {#if view.connection?.connected && view.options}
      {#if !view.options.templates.length || !view.options.certificates.length}
        <p class="help">{!view.options.templates.length ? 'No supported HTTPS rule is available as a settings template. Check your existing rules in DSM.' : 'No wildcard certificate was found for this domain. Check its subject alternative names in DSM.'}</p>
      {:else if view.preview}
        <div class="review"><h4 bind:this={reviewHeading} tabindex="-1">Review your service</h4><dl><dt>Name</dt><dd>{view.preview.name}</dd><dt>HTTPS address</dt><dd>{view.preview.url}</dd><dt>Destination</dt><dd>{view.preview.upstream}</dd><dt>Settings copied from</dt><dd>{view.preview.template}</dd><dt>Certificate</dt><dd>{view.options.certificates.find(cert => cert.id === draft.certificate_id)?.name}</dd><dt>Access control</dt><dd>Copied exactly from the selected DSM rule</dd></dl>
          <p class="help">{view.preview.resuming ? 'This resumes the earlier setup without creating a duplicate rule.' : 'This creates one new reverse-proxy rule. Existing rules are kept.'} The shortcut is saved after HTTPS verification, then the DSM connection closes.</p>
          <div class="form-actions"><button disabled={view.busy} onclick={() => client.clearPreview()}>Back to edit</button><button class="primary" disabled={view.busy} onclick={create}><Plus size={14} />{view.busy ? 'Setting up service...' : view.preview.resuming ? 'Resume setup' : 'Create service'}</button></div>
        </div>
      {:else}
        <form class="service-form" onsubmit={review}>
          <fieldset disabled={view.busy}>
            <div class="fields"><label>Name<input bind:this={nameInput} bind:value={draft.name} required maxlength="80" placeholder="Sonarr" /></label><label>Description<input bind:value={draft.description} maxlength="200" placeholder="TV series manager" /></label>
              <label>Subdomain<div class="domain-input"><input bind:value={draft.subdomain} required maxlength="63" pattern={'[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?'} placeholder="sonarr" /><span>.{view.connection.domain}</span></div></label>
              <label>Destination<input bind:value={draft.upstream} type="url" required maxlength="300" placeholder="http://10.0.0.149:8080" /><small>Allowed hosts: {view.connection.upstream_hosts.join(', ')}</small></label>
              <label>Copy connection settings from<select bind:value={draft.template_id} required><option value="" disabled>Choose an existing HTTPS rule</option>{#each view.options.templates as template}<option value={template.id}>{template.name} — {template.hostname}</option>{/each}</select><small>Retains its access profile, HTTPS settings, and supported WebSocket headers.</small></label>
              <label>Wildcard certificate<select bind:value={draft.certificate_id} required><option value="" disabled>Choose a certificate</option>{#each view.options.certificates as cert}<option value={cert.id}>{cert.name}</option>{/each}</select></label>
              <label class="wide">Search aliases<input bind:value={aliases} maxlength="1600" placeholder="sonarr, tv, series" /></label>
            </div>
            <div class="appearance"><div><label>Find an icon<div class="icon-search"><Search size={14} /><input bind:value={iconSearch} aria-label="Search service icons" placeholder="Search icons..." /></div></label><div class="icons" role="group" aria-label="Service icon">{#each icons as icon}{@const Icon = shortcutIcon(icon.key)}<button type="button" title={icon.label} aria-label={icon.label} aria-pressed={draft.icon === icon.key} class:selected={draft.icon === icon.key} onclick={() => draft.icon = icon.key}><Icon size={20} /></button>{/each}</div>
              <div class="colors" role="group" aria-label="Service color">{#each shortcutCatalog.colors as color}<button type="button" style:background={color} aria-label={`Use service color ${color}`} aria-pressed={draft.color === color} onclick={() => draft.color = color}>{#if draft.color === color}<Check size={12} />{/if}</button>{/each}<label>Custom<input type="color" bind:value={draft.color} aria-label="Custom service color" /></label></div>
            </div><div class="preview"><span>CARD PREVIEW</span><div class="shortcut-card" style={`--accent:${draft.color}`}><span class="shortcut-icon"><PreviewIcon size={21} /></span><span class="shortcut-bottom"><span class="shortcut-name">{draft.name || 'Your service'}</span><Link size={13} /></span></div></div></div>
            <div class="form-actions"><button class="primary" type="submit">{view.busy ? 'Checking service...' : 'Review service'}</button></div>
          </fieldset>
        </form>
      {/if}
    {/if}
  {/if}
</section>

<style>
  .service-setup { margin-top: 22px; padding: 23px; border: 1px solid var(--line); background: var(--bg); border-radius: 11px; }
  header, h3, .connection-title, .connected, .connected > span, .connected > button, .primary, .form-actions, .colors, .icon-search { display: flex; align-items: center; gap: 10px; }
  header, .connected { justify-content: space-between; }
  header { align-items: flex-start; }
  h3 { margin: 0; font-size: 15px; color: var(--text); font-weight: 500; }
  header p, .help, .resume-note { color: var(--muted); font-size: 11px; line-height: 1.8; margin: 8px 0 0; }
  button, input, select { font: inherit; }
  button { cursor: pointer; color: var(--muted); background: transparent; border: 0; font-size: 11px; }
  button:disabled { opacity: .45; cursor: default; }
  button:focus-visible, input:focus-visible, select:focus-visible { outline: 2px solid var(--highlight); outline-offset: 3px; }
  .icon-button { padding: 3px; }
  .primary { color: var(--text); background: var(--surface-hover); border: 1px solid var(--line); padding: 10px 14px; border-radius: 8px; justify-content: center; }
  fieldset { margin: 18px 0 0; padding: 0; border: 0; min-width: 0; }
  label { display: flex; flex-direction: column; gap: 8px; min-width: 0; color: var(--text); font-size: 11px; }
  label small { color: var(--muted); font-size: 10px; line-height: 1.6; }
  input, select { width: 100%; min-width: 0; padding: 11px 12px; color: var(--text); background: var(--surface); border: 1px solid var(--line); border-radius: 7px; font-size: 12px; }
  .connect-form { margin-top: 22px; padding-bottom: 5px; }
  .connection-title { flex-wrap: wrap; color: var(--highlight); font-size: 12px; }
  .connection-title span { font-size: 10px; color: var(--muted); overflow-wrap: anywhere; }
  .login-fields { display: grid; grid-template-columns: 1fr 1fr 150px; gap: 15px; margin-bottom: 18px; }
  .login-fields label > small { display: inline; }
  .connected { margin-top: 20px; padding: 12px 0; border-bottom: 1px solid var(--line); color: var(--success); font-size: 11px; flex-wrap: wrap; }
  .fields { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
  .wide { grid-column: 1 / -1; }
  .domain-input { display: flex; align-items: center; gap: 8px; }
  .domain-input input { flex: 1; }
  .domain-input span { color: var(--muted); font-size: 11px; white-space: nowrap; }
  .appearance { display: grid; grid-template-columns: minmax(0, 1fr) 185px; gap: 25px; margin-top: 22px; }
  .icon-search { padding-left: 10px; background: var(--surface); border: 1px solid var(--line); border-radius: 7px; color: var(--muted); }
  .icon-search input { border: 0; }
  .icons { display: grid; grid-template-columns: repeat(auto-fill,minmax(40px,1fr)); gap: 7px; margin: 12px 0 18px; max-height: 180px; overflow-y: auto; padding: 3px; }
  .icons button { border: 1px solid var(--line); border-radius: 7px; background: var(--surface); display: grid; place-items: center; min-height: 38px; }
  .icons button.selected { color: var(--highlight); border-color: var(--highlight); }
  .colors { flex-wrap: wrap; padding: 3px; }
  .colors > button { width: 23px; height: 23px; border-radius: 50%; color: var(--bg); padding: 0; display: grid; place-items: center; }
  .colors label { flex-direction: row; align-items: center; color: var(--muted); font-size: 10px; }
  .colors input { width: 30px; height: 28px; padding: 2px; }
  .preview > span { display: block; font-size: 9px; letter-spacing: 1px; color: var(--muted); margin: 0 0 12px; }
  .form-actions { margin-top: 22px; justify-content: flex-end; }
  .review { margin-top: 24px; }
  h4 { margin: 0; color: var(--text); font-weight: 500; font-size: 14px; }
  dl { display: grid; grid-template-columns: 155px minmax(0,1fr); gap: 14px; padding: 20px 0; margin: 0; font-size: 12px; line-height: 1.7; }
  dt { color: var(--muted); } dd { margin: 0; color: var(--text); overflow-wrap: anywhere; }
  .error { color: var(--warning); font-size: 12px; line-height: 1.8; margin-top: 18px; }
  .success { display: flex; align-items: flex-start; gap: 12px; color: var(--success); font-size: 13px; margin: 25px 0; }
  .success a { display: block; color: var(--highlight); margin-top: 10px; }
  .success p { color: var(--muted); font-size: 11px; line-height: 1.8; }
  @media(max-width:650px) { .service-setup { padding: 17px; } .login-fields, .fields, .appearance { grid-template-columns: 1fr; } .preview { max-width: 185px; } dl { grid-template-columns: 1fr; gap: 5px; } dd { margin-bottom: 12px; } .domain-input { flex-wrap: wrap; } .domain-input input { flex-basis: 100%; } }
</style>
