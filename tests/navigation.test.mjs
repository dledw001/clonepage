import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../src/lib/navigation.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2023, module: ts.ModuleKind.ESNext } }).outputText;
const { PageNavigation, isPlainClick, webSearchUrl } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
const settle = () => new Promise(resolve => setImmediate(resolve));
let resized;
globalThis.ResizeObserver = class {
  constructor(callback) { this.callback = callback; resized = this; this.disconnected = false; }
  observe() {} disconnect() { this.disconnected = true; }
};

function browser(path = '/') {
  const listeners = new Map(); const frames = new Map(); const timers = new Map();
  const stack = [{ path, state: { existing: true } }]; let index = 0; let id = 0;
  const location = new URL(path, 'https://clonepage.example');
  const setLocation = path => { const url = new URL(path, location.origin); b.location.pathname = url.pathname; b.location.search = url.search; b.location.hash = url.hash; };
  const b = {
    location: { pathname: location.pathname, search: location.search, hash: location.hash }, scrollX: 0, scrollY: 0, maxScroll: Infinity, document: { body: {} },
    addEventListener(type, fn) { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(fn); },
    removeEventListener(type, fn) { listeners.get(type)?.delete(fn); },
    emit(type) { for (const fn of [...(listeners.get(type) ?? [])]) fn(); },
    scrollTo({ left = 0, top = 0 }) { b.scrollX = left; b.scrollY = Math.min(top, b.maxScroll); },
    requestAnimationFrame(fn) { frames.set(++id, fn); return id; }, cancelAnimationFrame(id) { frames.delete(id); },
    setTimeout(fn) { timers.set(++id, fn); return id; }, clearTimeout(id) { timers.delete(id); },
    flush() { const callbacks = [...frames.values()]; frames.clear(); callbacks.forEach(fn => fn()); },
    history: {
      scrollRestoration: 'auto', get state() { return stack[index].state; },
      replaceState(state) { stack[index].state = state; },
      pushState(state, _, path) { stack.splice(++index); stack.push({ state, path }); setLocation(path); },
      back() { if (index > 0) { setLocation(stack[--index].path); b.emit('popstate'); } },
      forward() { if (index + 1 < stack.length) { setLocation(stack[++index].path); b.emit('popstate'); } },
      get length() { return stack.length; }
    }
  };
  return b;
}

test('saved-copy Back and browser Forward restore the correct per-entry scroll', async () => {
  const b = browser(); const routes = [];
  const nav = new PageNavigation(b, async () => { routes.push(b.location.pathname); });
  const stop = nav.start();
  assert.equal(b.history.state.existing, true);
  b.scrollY = 940; await nav.go('/reading/article-a'); assert.equal(b.scrollY, 0);
  b.scrollY = 620; nav.backToList(); await settle();
  assert.equal(b.location.pathname, '/'); assert.equal(b.scrollY, 940);
  assert.equal(b.history.length, 2);
  b.history.forward(); await settle(); assert.equal(b.scrollY, 620);
  b.history.back(); await settle(); assert.equal(b.scrollY, 940);
  assert.deepEqual(routes, ['/reading/article-a', '/', '/reading/article-a', '/']);
  stop(); assert.equal(b.history.scrollRestoration, 'auto');
});

test('a direct reader link returns to Home without leaving the site', async () => {
  const b = browser('/reading/direct'); const nav = new PageNavigation(b, async () => {}); const stop = nav.start();
  nav.backToList(); await settle();
  assert.equal(b.location.pathname, '/'); assert.equal(b.history.length, 2); assert.equal(b.scrollY, 0);
  stop();
});

test('navigation does not create duplicate history for the current page', async () => {
  const b = browser(); const nav = new PageNavigation(b, async () => {}); const stop = nav.start();
  b.scrollY = 500; await nav.go('/');
  assert.equal(b.history.length, 1); assert.equal(b.scrollY, 0);
  stop();
});

test('scroll restoration waits for reader content and stops after user input', async () => {
  const b = browser(); const nav = new PageNavigation(b, async () => {}); const stop = nav.start();
  await nav.go('/reading/a'); b.scrollY = 1100; nav.backToList(); await settle();
  b.maxScroll = 200; b.history.forward(); await settle();
  assert.equal(b.scrollY, 200); assert.equal(resized.disconnected, false);
  b.maxScroll = 2000; resized.callback(); b.flush();
  assert.equal(b.scrollY, 1100); assert.equal(resized.disconnected, true);
  b.history.back(); await settle(); b.maxScroll = 200; b.history.forward(); await settle();
  b.emit('wheel'); assert.equal(resized.disconnected, true);
  stop();
});

test('a stale render cannot scroll a newer navigation', async () => {
  const b = browser(); let finish; let calls = 0;
  const nav = new PageNavigation(b, () => ++calls === 1 ? new Promise(resolve => finish = resolve) : Promise.resolve());
  const stop = nav.start(); const old = nav.go('/reading/a');
  await nav.go('/infrastructure'); b.scrollY = 400; finish(); await old;
  assert.equal(b.scrollY, 400); assert.equal(b.location.pathname, '/infrastructure'); stop();
});

test('modified clicks, middle clicks and already-handled events retain browser behavior', () => {
  const plain = { button: 0, defaultPrevented: false };
  assert.equal(isPlainClick(plain), true);
  for (const changes of [{ ctrlKey: true }, { metaKey: true }, { shiftKey: true }, { altKey: true }, { button: 1 }, { defaultPrevented: true }])
    assert.equal(isPlainClick({ ...plain, ...changes }), false);
});

test('web search encodes queries and retains direct URL entry', () => {
  assert.equal(webSearchUrl(' cpu & ram '), 'https://www.google.com/search?q=cpu%20%26%20ram');
  assert.equal(webSearchUrl('example.com/article'), 'https://example.com/article');
  assert.equal(webSearchUrl('http://localhost:8000'), 'http://localhost:8000');
});


test('monitoring query and fragment links preserve history and can return to the full infrastructure view', async () => {
  const b = browser(); const nav = new PageNavigation(b, async () => {}); const stop = nav.start();
  const path = '/infrastructure?monitor=linked-project#container-updates';
  await nav.go(path);
  assert.equal(b.location.pathname, '/infrastructure'); assert.equal(b.location.search, '?monitor=linked-project');
  assert.equal(b.location.hash, '#container-updates'); assert.equal(b.history.length, 2);
  await nav.go(path); assert.equal(b.history.length, 2);
  await nav.go('/infrastructure'); assert.equal(b.location.search, ''); assert.equal(b.location.hash, '');
  b.history.back(); await settle(); assert.equal(b.location.search, '?monitor=linked-project');
  b.history.back(); await settle(); assert.equal(b.location.pathname, '/');
  stop();
});
