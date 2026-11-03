import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import ts from 'typescript';
import { compile } from 'svelte/compiler';

const source = readFileSync(new URL('../src/lib/feed-reader.ts', import.meta.url), 'utf8');
const javascript = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2023, module: ts.ModuleKind.ESNext } }).outputText;
const { FeedReader } = await import(`data:text/javascript;base64,${Buffer.from(javascript).toString('base64')}`);
const response = (body, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body });
const entry = (id, extra = {}) => ({ id, title: id, url: `https://example.com/${id}`, summary: '', feed_id: 'feed', feed_title: 'Example', is_read: false, is_saved: false, ...extra });
const page = (entries = [], extra = {}) => ({ entries, subscriptions: [], unread: entries.filter(item => !item.is_read).length, total: entries.length, through_seq: 10, next_cursor: null, ...extra });
const deferred = () => { let resolve; const promise = new Promise(yes => resolve = yes); return { promise, resolve }; };
const settle = () => new Promise(resolve => setImmediate(resolve));

test('feed widget compiles for client and server', () => {
  const component = readFileSync(new URL('../src/components/Feeds.svelte', import.meta.url), 'utf8');
  for (const generate of ['client', 'server']) assert.doesNotThrow(() => compile(component, { filename: 'Feeds.svelte', generate }));
});

test('editing the discovery URL invalidates an older in-flight preview', async () => {
  const component = readFileSync(new URL('../src/components/Feeds.svelte', import.meta.url), 'utf8');
  const script = component.match(/<script[^>]*>([\s\S]*?)<\/script>/)[1];
  const tree = ts.createSourceFile('Feeds.ts', script, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  const functions = tree.statements.filter(node => ts.isFunctionDeclaration(node) && ['discover', 'resetDiscovery'].includes(node.name?.text)).map(node => node.getText(tree)).join('\n');
  const body = `let sourceUrl='https://old.example/', discovering=false, formError='', preview=null, candidates=[], discoverySequence=0;
    ${functions}
    return { discover, resetDiscovery, state:()=>({preview,discovering}) };`;
  const js = ts.transpileModule(body, { compilerOptions: { target: ts.ScriptTarget.ES2023 } }).outputText;
  const old = deferred(); const recent = deferred(); let calls = 0;
  const discovery = new Function('reader', js)({ request: () => ++calls === 1 ? old.promise : recent.promise });
  const first = discovery.discover(); discovery.resetDiscovery(); const second = discovery.discover('https://new.example/');
  recent.resolve({ preview: { title: 'New feed' }, candidates: [] }); await second;
  old.resolve({ preview: { title: 'Old feed' }, candidates: [] }); await first;
  assert.equal(discovery.state().preview.title, 'New feed');
  assert.equal(discovery.state().discovering, false);
});

test('an older refresh cannot overwrite the latest feed response', async () => {
  const old = deferred(); const next = deferred(); let calls = 0;
  const reader = new FeedReader(() => {}, () => ++calls === 1 ? old.promise : next.promise);
  const a = reader.refresh(); const b = reader.refresh();
  next.resolve(response(page([entry('new')]))); await b;
  old.resolve(response(page([entry('old')]))); await a;
  assert.equal(reader.state.data.entries[0].id, 'new');
  reader.dispose();
});

test('a filter change invalidates in-flight responses from the old feed', async () => {
  const old = deferred(); let calls = 0;
  const reader = new FeedReader(() => {}, () => ++calls === 1 ? old.promise : Promise.resolve(response(page([entry('matching')]))));
  const pending = reader.refresh();
  reader.setScope({ feed_id: 'other', q: 'word', unread: false });
  await settle(); old.resolve(response(page([entry('wrong')]))); await pending;
  assert.equal(reader.state.data.entries[0].id, 'matching');
  reader.dispose();
});

test('a stale refresh cannot reverse a read mutation and refreshes wait for writes', async () => {
  const old = deferred(); const write = deferred(); let reads = 0;
  const reader = new FeedReader(() => {}, (url, options) => options.method === 'PATCH' ? write.promise
    : ++reads === 1 ? old.promise : Promise.resolve(response(page([entry('a', { is_read: true })]))));
  reader.state.data = page([entry('a')]);
  const pending = reader.refresh(); const mutation = reader.setRead(entry('a'), true);
  await reader.refresh(); assert.equal(reads, 1);
  old.resolve(response(page([entry('a')]))); await pending;
  write.resolve(response({ id: 'a', is_read: true })); await mutation;
  assert.equal(reader.state.data.entries[0].is_read, true);
  assert.equal(reader.state.loading, false);
  reader.dispose();
});

test('failed saves retain entries, release controls, and do not notify the reading list', async () => {
  let notifications = 0;
  const reader = new FeedReader(() => {}, (url, options) => Promise.resolve(options.method === 'POST'
    ? response({ detail: 'Could not save.' }, 500) : response(page([entry('a')]))), () => notifications++);
  reader.state.data = page([entry('a')]);
  assert.equal(await reader.save(entry('a')), false);
  assert.equal(reader.state.data.entries[0].is_saved, false);
  assert.equal(reader.state.error, 'Could not save.');
  assert.deepEqual(reader.state.busy, []);
  assert.equal(notifications, 0);
  reader.dispose();
});

test('concurrent mutations keep an earlier failure visible when the other succeeds', async () => {
  const a = deferred(); const b = deferred();
  const reader = new FeedReader(() => {}, (url, options) => options.method === 'PATCH'
    ? url.endsWith('/a') ? a.promise : b.promise : Promise.resolve(response(page([entry('a'), entry('b', { is_read: true })]))));
  reader.state.data = page([entry('a'), entry('b')]);
  const first = reader.setRead(entry('a'), true); const second = reader.setRead(entry('b'), true);
  a.resolve(response({ detail: 'Read failed.' }, 500)); await first;
  b.resolve(response({ id: 'b', is_read: true })); await second;
  assert.equal(reader.state.error, 'Read failed.');
  assert.equal(reader.state.data.entries.find(item => item.id === 'b').is_read, true);
  assert.deepEqual(reader.state.busy, []);
  reader.dispose();
});

test('duplicate saves are blocked and successful saves notify once', async () => {
  const saving = deferred(); let posts = 0; let notifications = 0;
  const reader = new FeedReader(() => {}, (url, options) => options.method === 'POST'
    ? (posts++, saving.promise) : Promise.resolve(response(page([entry('a', { is_saved: true })]))), () => notifications++);
  reader.state.data = page([entry('a')]);
  const pending = reader.save(entry('a'));
  assert.equal(await reader.save(entry('a')), false);
  saving.resolve(response({ id: 'article' })); await pending;
  assert.equal(posts, 1); assert.equal(notifications, 1);
  assert.equal(reader.state.data.entries[0].is_saved, true);
  reader.dispose();
});

test('mark-all captures the loaded watermark and search/feed scope', async () => {
  let posted;
  const reader = new FeedReader(() => {}, (url, options) => {
    if (options.method === 'POST') posted = JSON.parse(options.body);
    return Promise.resolve(response(options.method === 'POST' ? { updated: 1 } : page()));
  });
  reader.state.data = page([entry('a')], { through_seq: 42 });
  reader.scope = { feed_id: 'selected', q: 'Python', unread: true };
  await reader.markAllRead();
  assert.deepEqual(posted, { through_seq: 42, feed_id: 'selected', q: 'Python' });
  reader.dispose();
});

test('pagination follows the cursor and avoids duplicate entries', async () => {
  let requested;
  const reader = new FeedReader(() => {}, (url) => { requested = url; return Promise.resolve(response(page([entry('a'), entry('b')]))); });
  reader.state.data = page([entry('a')], { next_cursor: '2026-01-01|1' });
  await reader.showMore(); assert.equal(reader.state.expanded, true);
  await reader.showMore();
  assert.deepEqual(reader.state.data.entries.map(item => item.id), ['a', 'b']);
  assert.equal(new URL(requested, 'https://clonepage').searchParams.get('cursor'), '2026-01-01|1');
  reader.dispose();
});

test('disposing the widget cancels requests and prevents late publication', async () => {
  const pending = deferred(); let publications = 0; let signal;
  const reader = new FeedReader(() => publications++, (url, options) => { signal = options.signal; return pending.promise; });
  const refresh = reader.refresh(); reader.dispose(); const before = publications;
  pending.resolve(response(page([entry('a')]))); await refresh;
  assert.equal(publications, before); assert.equal(signal.aborted, true);
});
