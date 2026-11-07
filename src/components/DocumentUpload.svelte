<script lang="ts">
  import { FileText, Upload, X } from '@lucide/svelte';

  let { saving, onupload, onclose }: {
    saving: boolean;
    onupload: (file: File, title: string) => Promise<string>;
    onclose: () => void;
  } = $props();
  let files = $state<FileList>();
  let title = $state('');
  let error = $state('');

  async function submit(event: SubmitEvent) {
    event.preventDefault();
    if (saving) return;
    const file = files?.[0];
    if (!file) { error = 'Choose a PDF to upload.'; return; }
    error = await onupload(file, title);
  }
</script>

<form class="document-upload" onsubmit={submit} aria-label="Upload a PDF">
  <header><h3><FileText size={17} />Upload a PDF</h3><button type="button" class="close" aria-label="Cancel PDF upload" disabled={saving} onclick={onclose}><X size={17} /></button></header>
  <p>Keep a document on your NAS and find it alongside your saved articles.</p>
  <div class="fields">
    <label>PDF file<input type="file" accept=".pdf,application/pdf" bind:files required disabled={saving} onchange={() => error = ''} /></label>
    <label>Title (optional)<input bind:value={title} maxlength="200" placeholder="Use the document title or filename" disabled={saving} /></label>
  </div>
  <div class="form-footer"><small>PDFs up to 25 MB. The original is kept; text is indexed for search when available.</small><button class="upload" type="submit" disabled={saving || !files?.length}><Upload size={15} />{saving ? 'Uploading...' : 'Upload PDF'}</button></div>
  {#if error}<p class="error" role="alert">{error}</p>{/if}
</form>

<style>
  .document-upload { margin-top: 20px; padding: 20px; border: 1px solid var(--line); border-radius: 11px; background: var(--bg); }
  header, h3, .form-footer, .upload { display: flex; align-items: center; gap: 9px; }
  header { justify-content: space-between; }
  h3 { margin: 0; color: var(--text); font-size: 13px; font-weight: 500; }
  h3 :global(svg) { color: var(--highlight); }
  p { margin: 10px 0 20px; font-size: 11px; line-height: 1.8; color: var(--muted); }
  button { font: inherit; border: 0; cursor: pointer; }
  button:disabled { opacity: .5; cursor: default; }
  .close { display: grid; place-items: center; padding: 6px; background: transparent; color: var(--muted); }
  .fields { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  label { display: flex; flex-direction: column; gap: 8px; font-size: 11px; color: var(--text); min-width: 0; }
  small { color: var(--muted); }
  input { width: 100%; min-width: 0; padding: 11px 12px; font: inherit; font-size: 12px; color: var(--text); background: var(--surface); border: 1px solid var(--line); border-radius: 8px; }
  input[type="file"] { padding: 6px; }
  input::file-selector-button { padding: 7px 10px; margin-right: 10px; border: 0; border-radius: 5px; background: var(--surface-hover); color: var(--text); cursor: pointer; }
  .form-footer { justify-content: space-between; align-items: flex-start; margin-top: 18px; gap: 20px; }
  small { font-size: 10px; line-height: 1.8; max-width: 430px; }
  .upload { flex-shrink: 0; padding: 11px 15px; border-radius: 8px; background: var(--highlight); color: var(--bg); font-size: 12px; font-weight: 600; }
  .error { margin: 15px 0 0; color: var(--warning); }
  input:focus-visible, button:focus-visible { outline: 2px solid var(--highlight); outline-offset: 3px; }
  @media(max-width:640px) { .document-upload { padding: 15px; } .fields { grid-template-columns: 1fr; } .form-footer { flex-wrap: wrap; gap: 12px; } .upload { width: 100%; justify-content: center; } }
</style>
