import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import ts from 'typescript';
import { compile } from 'svelte/compiler';

const source = readFileSync(new URL('../src/lib/shortcuts.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2023, module: ts.ModuleKind.ESNext } }).outputText;
const { ShortcutStore } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
const item = (id, extra = {}) => ({ id, name: id, description: '', aliases: [], icon: 'Server', color: '#a4b9e4', action: 'open', value: 'https://example.com', ...extra });
const snapshot = (items, revision = 0) => ({ items, revision });
const response = (data, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => data });
const deferred = () => { let resolve; const promise = new Promise(yes => resolve = yes); return { promise, resolve }; };
const settle = () => new Promise(resolve => setImmediate(resolve));
const draft = { name: 'Service', description: '', aliases: [], icon: 'Server', color: '#a4b9e4', action: 'open', value: 'https://example.com' };

test('shortcut editor and its app integration compile for both renderers', () => {
  for (const path of ['components/Shortcuts.svelte', 'App.svelte']) {
    const text = readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8');
    for (const generate of ['client', 'server']) assert.doesNotThrow(() => compile(text, { filename: path, generate }));
  }
});

test('every server-approved icon has a bundled component and defaults remain valid', () => {
  const catalog = JSON.parse(readFileSync(new URL('../backend/shortcut_catalog.json', import.meta.url), 'utf8'));
  const icons = readFileSync(new URL('../src/lib/shortcut-icons.ts', import.meta.url), 'utf8').match(/const icons = \{([^}]+)\}/)[1].split(',').map(key => key.trim());
  assert.deepEqual(new Set(icons), new Set(catalog.icons.map(icon => icon.key)));
  for (const item of JSON.parse(readFileSync(new URL('../backend/shortcut_defaults.json', import.meta.url), 'utf8'))) assert.ok(icons.includes(item.icon));
});

test('a deliberately empty shortcut list is loaded successfully', async () => {
  const store = new ShortcutStore(() => {}, async () => response(snapshot([], 7)));
  await store.refresh(); assert.equal(store.state.loaded, true); assert.deepEqual(store.state.items, []); assert.equal(store.state.revision, 7);
});

test('a load failure does not invent defaults and a later refresh retains the last good list', async () => {
  let fail = true;
  const store = new ShortcutStore(() => {}, async () => { if (fail) throw Error('offline'); return response(snapshot([item('a')])); });
  await store.refresh(); assert.equal(store.state.loaded, false); assert.deepEqual(store.state.items, []);
  fail = false; await store.refresh(); fail = true; await store.refresh();
  assert.equal(store.state.loaded, true); assert.equal(store.state.items[0].id, 'a'); assert.match(store.state.error, /last loaded/);
});

test('a late GET cannot undo a successful edit', async () => {
  const old = deferred();
  const store = new ShortcutStore(() => {}, (url, options) => options.method ? Promise.resolve(response(snapshot([item('a', { name: 'Changed' })], 1))) : old.promise);
  const loading = store.refresh(); assert.equal(await store.save({ ...draft, name: 'Changed' }, 'a', 0), true);
  old.resolve(response(snapshot([item('a')]))); await loading;
  assert.equal(store.state.items[0].name, 'Changed'); assert.equal(store.state.revision, 1);
});

test('a failed edit keeps the list and releases controls', async () => {
  const store = new ShortcutStore(() => {}, async (_, options) => response(options.method ? { detail: 'Invalid URL' } : snapshot([item('a')]), options.method ? 422 : 200));
  await store.refresh(); assert.equal(await store.save(draft, 'a', 0), false);
  assert.equal(store.state.items[0].name, 'a'); assert.equal(store.state.busy, false); assert.equal(store.state.error, 'Invalid URL');
});

test('conflicting edits refresh the list and keep an actionable error through focus refreshes', async () => {
  let body;
  const store = new ShortcutStore(() => {}, async (_, options) => {
    if (options.method) { body = JSON.parse(options.body); return response({ detail: 'Changed on another device. Review and save again.' }, 409); }
    return response(snapshot([item('a', { name: 'Other device' })], 4));
  });
  await store.refresh(); assert.equal(await store.save({ ...draft, name: 'My draft' }, 'a', 2), false);
  assert.equal(body.revision, 2); assert.equal(body.name, 'My draft'); assert.equal(store.state.revision, 4);
  assert.equal(store.state.items[0].name, 'Other device'); assert.equal(store.state.conflict, true);
  await store.refresh(); assert.match(store.state.error, /Review and save again/);
});

test('duplicate writes are blocked and focus refresh waits until saving finishes', async () => {
  const pending = deferred(); let reads = 0; let writes = 0;
  const store = new ShortcutStore(() => {}, (_, options) => {
    if (options.method) { writes++; return pending.promise; }
    reads++; return Promise.resolve(response(snapshot([item('a')], 1)));
  });
  const save = store.save(draft, null, 0); await store.refresh();
  assert.equal(await store.save(draft, null, 0), false); assert.equal(reads, 0); assert.equal(writes, 1);
  pending.resolve(response(snapshot([item('a')], 1))); await save; await settle(); assert.equal(reads, 1);
});

test('ordering and deletion use the current revision without optimistic data loss', async () => {
  const requests = [];
  const store = new ShortcutStore(() => {}, async (url, options) => {
    if (!options.method) return response(snapshot([item('a'), item('b')], 3));
    requests.push({ url, options });
    return options.method === 'DELETE' ? response(snapshot([item('b')], 5)) : response(snapshot([item('b'), item('a')], 4));
  });
  await store.refresh(); assert.equal(await store.move('a', -1), false);
  await store.move('b', -1); assert.deepEqual(JSON.parse(requests[0].options.body), { ids: ['b', 'a'], revision: 3 });
  await store.remove('a'); assert.match(requests[1].url, /a\?revision=4$/); assert.deepEqual(store.state.items.map(item => item.id), ['b']);
});

test('disposal cancels requests and prevents late publication', async () => {
  const pending = deferred(); let signal; let publications = 0;
  const store = new ShortcutStore(() => publications++, (_, options) => { signal = options.signal; return pending.promise; });
  const read = store.refresh(); store.dispose(); const before = publications;
  assert.equal(signal.aborted, true); pending.resolve(response(snapshot([item('a')]))); await read;
  assert.equal(publications, before); assert.deepEqual(store.state.items, []);
});


test('the default browser fetch is called without binding it to the shortcut store', async () => {
  const previous = globalThis.fetch;
  let context;
  globalThis.fetch = function () { context = this; return Promise.resolve(response(snapshot([]))); };
  try {
    const store = new ShortcutStore(() => {}); await store.refresh();
    assert.equal(context, undefined); assert.equal(store.state.loaded, true);
  } finally { globalThis.fetch = previous; }
});
