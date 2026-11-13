import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import ts from 'typescript';
import { compile } from 'svelte/compiler';

const source = readFileSync(new URL('../src/lib/hacker-news.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2023, module: ts.ModuleKind.ESNext } }).outputText;
const { HackerNewsReader, storyAge, storyDomain } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
const story = (id = 1, extra = {}) => ({ id, title: `Story ${id}`, url: `https://example.com/${id}`, discussion_url: `https://news.ycombinator.com/item?id=${id}`,
  author: 'reader', points: 20, comments: 5, created_at: '2026-11-13T12:00:00Z', rank: 1, is_read: false, is_saved: false, article_id: null, ...extra });
const page = (stories = [story()], extra = {}) => ({ view: 'front', window: '24h', stories, fetched_at: '2026-11-13T12:00:00Z', stale: false, error: '', notice: '', limit: 100, ...extra });
const response = (data, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => structuredClone(data) });
const deferred = () => { let resolve; const promise = new Promise(done => resolve = done); return { resolve, promise }; };
const settle = () => new Promise(resolve => setImmediate(resolve));

test('HN reader and route integration compile for client and server', () => {
  for (const file of ['components/HackerNews.svelte', 'components/ReadingList.svelte', 'App.svelte']) {
    const text = readFileSync(new URL(`../src/${file}`, import.meta.url), 'utf8');
    for (const generate of ['client', 'server']) assert.doesNotThrow(() => compile(text, { filename: file, generate }));
  }
});

test('direct HN routes preserve existing home, infrastructure, and saved-reader routes', () => {
  const app = readFileSync(new URL('../src/App.svelte', import.meta.url), 'utf8');
  const route = app.match(/function routeFromPath\(path: string\) \{([\s\S]*?)\n  \}/)[0];
  const compiled = ts.transpileModule(route + '\nexport { routeFromPath };', { compilerOptions: { module: ts.ModuleKind.ESNext } }).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`).then(({ routeFromPath }) => {
    assert.equal(routeFromPath('/hacker-news').page, 'hacker-news');
    assert.equal(routeFromPath('/hacker-news/').page, 'hacker-news');
    assert.equal(routeFromPath('/').page, 'home');
    assert.equal(routeFromPath('/infrastructure').page, 'infrastructure');
    assert.deepEqual(routeFromPath('/reading/article-one'), { page: 'reader', articleId: 'article-one' });
  });
});

test('a successful refresh settles without creating a request loop and force is explicit', async () => {
  const calls = [];
  const reader = new HackerNewsReader(() => {}, async (url, options) => { calls.push({url, options}); return response(page()); });
  await reader.refresh(); await settle();
  assert.equal(reader.state.loading, false); assert.equal(calls.length, 1);
  assert.equal(reader.state.data.stories.length, 1);
  assert.equal(calls[0].options.cache, 'no-store');
  await reader.refresh(true);
  assert.match(calls[1].url, /force=true/);
  reader.dispose();
});

test('switching filters aborts the old request and ignores its late response', async () => {
  const old = deferred(); const calls = [];
  const reader = new HackerNewsReader(() => {}, async (url, options) => {
    calls.push({url, options});
    return calls.length === 1 ? old.promise : response(page([story(2)], {view:'discussed',window:'30d'}));
  });
  const first = reader.refresh();
  reader.setScope({view:'discussed',window:'30d'});
  await settle();
  assert.equal(calls[0].options.signal.aborted, true);
  old.resolve(response(page())); await first;
  assert.equal(reader.state.data.stories[0].id, 2);
  assert.equal(reader.state.scope.window, '30d');
  assert.equal(reader.state.loading, false);
  reader.dispose();
});

test('read writes cannot be undone by an older in-flight GET', async () => {
  const old = deferred(); let gets = 0;
  const reader = new HackerNewsReader(() => {}, async (url, options) => {
    if (options.method === 'PATCH') return response({id:1,is_read:true});
    gets++;
    if (gets === 2) return old.promise;
    return response(page([story(1,{is_read:gets > 2})]));
  });
  await reader.refresh();
  const stale = reader.refresh();
  await reader.setRead(reader.state.data.stories[0], true);
  assert.equal(reader.state.data.stories[0].is_read, true);
  old.resolve(response(page())); await stale; await settle();
  assert.equal(reader.state.data.stories[0].is_read, true);
  assert.equal(reader.state.loading, false);
  reader.dispose();
});

test('write failures leave read state intact and keep their message after reconciliation', async () => {
  const reader = new HackerNewsReader(() => {}, async (url, options) => options.method === 'PATCH'
    ? response({detail:'Could not store read status'}, 503) : response(page()));
  await reader.refresh();
  assert.equal(await reader.setRead(reader.state.data.stories[0], true), false);
  assert.equal(reader.state.data.stories[0].is_read, false);
  assert.equal(reader.state.error, 'Could not store read status');
  assert.deepEqual(reader.state.busy, []);
  reader.dispose();
});

test('saving notifies the reading list and duplicate saves do not issue a POST', async () => {
  const changes = []; let saved = false; let posts = 0;
  const reader = new HackerNewsReader(() => {}, async (url, options) => {
    if (options.method === 'POST') { saved = true; posts++; return response({id:'saved-article'}); }
    return response(page([story(1,{is_saved:saved,article_id:saved ? 'saved-article' : null})]));
  }, kind => changes.push(kind));
  await reader.refresh();
  assert.equal(await reader.save(reader.state.data.stories[0]), true);
  assert.deepEqual(changes, ['save']);
  assert.match(reader.state.notice, /offline copy/);
  assert.equal(reader.state.data.stories[0].article_id, 'saved-article');
  await reader.save(reader.state.data.stories[0]);
  assert.equal(posts, 1);
  reader.dispose();
});

test('repeated read actions share an active write and switching scope waits for reconciliation', async () => {
  const write = deferred(); let patches = 0;
  const reader = new HackerNewsReader(() => {}, async (url, options) => {
    if (options.method === 'PATCH') { patches++; return write.promise; }
    return response(page([story()], {view:new URL(url,'https://clonepage.test').searchParams.get('view')}));
  });
  await reader.refresh();
  const pending = reader.setRead(reader.state.data.stories[0], true);
  await reader.setRead(reader.state.data.stories[0], true);
  reader.setScope({view:'top'});
  write.resolve(response({id:1,is_read:true})); await pending;
  assert.equal(patches, 1);
  assert.equal(reader.state.data.view, 'top');
  assert.equal(reader.state.loading, false);
  reader.dispose();
});

test('failed refreshes keep prior stories with an error and can recover', async () => {
  let fail = false;
  const reader = new HackerNewsReader(() => {}, async () => {
    if (fail) throw new Error('Connection lost');
    return response(page());
  });
  await reader.refresh(); fail = true; await reader.refresh();
  assert.equal(reader.state.data.stories.length, 1);
  assert.equal(reader.state.loading, false);
  assert.equal(reader.state.error, 'Connection lost');
  fail = false; await reader.refresh();
  assert.equal(reader.state.error, '');
  reader.dispose();
});

test('disposal aborts active requests and late responses cannot publish', async () => {
  const pending = deferred(); const updates = []; let signal;
  const reader = new HackerNewsReader(value => updates.push(value), async (url, options) => { signal = options.signal; return pending.promise; });
  const refresh = reader.refresh(); reader.dispose();
  const before = updates.length;
  pending.resolve(response(page())); await refresh;
  assert.equal(signal.aborted, true);
  assert.equal(updates.length, before);
  assert.equal(reader.state.data, null);
});

test('story labels handle invalid dates, future times, and source domains', () => {
  assert.equal(storyAge('invalid'), 'Time unavailable');
  assert.equal(storyAge('2026-11-13T12:00:00Z', Date.parse('2026-11-13T11:00:00Z')), 'just now');
  assert.equal(storyAge('2026-11-13T12:00:00Z', Date.parse('2026-11-13T14:00:00Z')), '2h ago');
  assert.equal(storyDomain('https://www.example.com/article'), 'example.com');
  assert.equal(storyDomain('invalid'), '');
});
