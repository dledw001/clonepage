import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import ts from 'typescript';
import { compile } from 'svelte/compiler';

const component = readFileSync(new URL('../src/components/ReadingList.svelte', import.meta.url), 'utf8');
const script = component.match(/<script[^>]*>([\s\S]*?)<\/script>/)[1];
const tree = ts.createSourceFile('ReadingList.ts', script, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
const declarations = tree.statements.filter(node => !ts.isImportDeclaration(node) && !ts.isExpressionStatement(node)).map(node => node.getText(tree)).join('\n');
const body = declarations + `\nreturn { refresh, retryCopy, seed(value){articles=value}, state(){return {articles,busyIds,error}} };`;
const js = ts.transpileModule(body, { compilerOptions: { target: ts.ScriptTarget.ES2023 } }).outputText;
const response = (body, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body });
const article = { id: 'a', title: 'A saved article', url: 'https://example.com/a', is_read: true, archived_at: '2026-01-01', copy_status: 'ready', copy_available: true };
const create = fetch => new Function('fetch', '$state', '$derived', js)(fetch, value => value, value => value);
const deferred = () => { let resolve; const promise = new Promise(yes => resolve = yes); return { promise, resolve }; };
const settle = () => new Promise(resolve => setImmediate(resolve));

test('saved-copy reader and reading-list controls compile for client and server', () => {
  for (const name of ['ReadingList', 'SavedArticle']) {
    const source = readFileSync(new URL(`../src/components/${name}.svelte`, import.meta.url), 'utf8');
    for (const generate of ['client', 'server']) assert.doesNotThrow(() => compile(source, { filename: `${name}.svelte`, generate }));
  }
});

test('queuing a copy refresh preserves article read/archive fields and the previous copy', async () => {
  const c = create(() => Promise.resolve(response({ id: 'a', copy_status: 'pending', copy_available: true })));
  c.seed([article]); await c.retryCopy(article);
  const current = c.state().articles[0];
  assert.equal(current.copy_status, 'pending');
  assert.equal(current.copy_available, true);
  assert.equal(current.is_read, true);
  assert.equal(current.archived_at, '2026-01-01');
  assert.deepEqual(c.state().busyIds, []);
});

test('failed copy refresh preserves the last successful metadata and releases controls', async () => {
  const c = create(() => Promise.resolve(response({}, 500)));
  c.seed([article]); await c.retryCopy(article);
  assert.equal(c.state().articles[0].copy_status, 'ready');
  assert.equal(c.state().articles[0].copy_available, true);
  assert.match(c.state().error, /Could not request/);
  assert.deepEqual(c.state().busyIds, []);
});

test('a late reading-list refresh cannot undo a queued saved-copy capture', async () => {
  const old = deferred(); let reads = 0;
  const next = { ...article, copy_status: 'pending' };
  const c = create((url, options) => options?.method === 'POST' ? Promise.resolve(response({ id: 'a', copy_status: 'pending' }))
    : ++reads === 1 ? old.promise : Promise.resolve(response([next])));
  c.seed([article]); const pending = c.refresh();
  await c.retryCopy(article); old.resolve(response([article])); await pending; await settle();
  assert.equal(c.state().articles[0].copy_status, 'pending');
});

test('direct saved-article routes are recognized without changing home and infrastructure routing', () => {
  const app = readFileSync(new URL('../src/App.svelte', import.meta.url), 'utf8');
  const source = app.match(/<script[^>]*>([\s\S]*?)<\/script>/)[1];
  const ast = ts.createSourceFile('App.ts', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  const route = ast.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'routeFromPath');
  const javascript = ts.transpileModule(route.getText(ast) + '\nreturn routeFromPath;', { compilerOptions: { target: ts.ScriptTarget.ES2023 } }).outputText;
  const routeFromPath = new Function(javascript)();
  assert.deepEqual(routeFromPath('/reading/abc-123'), { page: 'reader', articleId: 'abc-123' });
  assert.equal(routeFromPath('/infrastructure').page, 'infrastructure');
  assert.equal(routeFromPath('/').page, 'home');
  assert.equal(routeFromPath('/reading/../../etc/passwd').page, 'home');
});

test('reader sizing is deferred and remains stable when the iframe viewport is taller than its content', () => {
  const source = readFileSync(new URL('../src/components/SavedArticle.svelte', import.meta.url), 'utf8').match(/<script[^>]*>([\s\S]*?)<\/script>/)[1];
  const ast = ts.createSourceFile('SavedArticle.ts', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  const fit = ast.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'fitFrame');
  const body = `let observer, resizeFrame, disposed=false, frameHeight=650, naturalHeight=250;
    const info={copy_format:'html'};
    const frame={contentDocument:{body:{getBoundingClientRect(){return {height:naturalHeight}},get scrollHeight(){return frameHeight+16}}}};
    ${fit.getText(ast)}
    return {fitFrame,height:()=>frameHeight,setHeight(value){naturalHeight=value}};`;
  const javascript = ts.transpileModule(body, { compilerOptions: { target: ts.ScriptTarget.ES2023 } }).outputText;
  let callback; let nextId = 0; const queue = new Map();
  class Observer { constructor(value){callback=value} observe(){} disconnect(){} }
  const reader = new Function('ResizeObserver', 'requestAnimationFrame', 'cancelAnimationFrame', javascript)(Observer,
    fn => { const id = ++nextId; queue.set(id, fn); return id; }, id => queue.delete(id));
  const flush = () => { const callbacks=[...queue.values()]; queue.clear(); callbacks.forEach(fn=>fn()); };
  reader.fitFrame(); callback(); callback();
  assert.equal(queue.size, 1);
  assert.equal(reader.height(), 650);
  flush(); assert.equal(reader.height(), 400);
  callback(); flush(); assert.equal(reader.height(), 400);
  reader.setHeight(900); callback();
  assert.equal(reader.height(), 400);
  flush(); assert.equal(reader.height(), 902);
});
