import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import ts from 'typescript';
import { compile } from 'svelte/compiler';

const component = readFileSync(new URL('../src/components/ReadingList.svelte', import.meta.url), 'utf8');
const script = component.match(/<script[^>]*>([\s\S]*?)<\/script>/)[1];
const tree = ts.createSourceFile('ReadingList.ts', script, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);

// Run the component's actual request handlers without mounting a DOM. Svelte's
// effects/lifecycle are excluded; these tests explicitly drive request ordering.
const declarations = tree.statements.filter(node => !ts.isImportDeclaration(node) && !ts.isExpressionStatement(node));
const body = declarations.map(node => node.getText(tree)).join('\n');
const expose = `
return {
    refresh, patch, remove, retryIndexing, save, startEditing, saveTitle,
    seed(value) { articles = value; },
    setForm(nextUrl, nextTitle) { url = nextUrl; title = nextTitle; },
    setDraft(value) { titleDraft = value; },
    state() { return { articles, busyIds, error, loaded, saving, editingId }; }
};`;
const javascript = ts.transpileModule(body + expose, {
    compilerOptions: { target: ts.ScriptTarget.ES2023, module: ts.ModuleKind.None }
}).outputText;

function createComponent(fetch) {
    return new Function('fetch', 'confirm', '$state', '$derived', javascript)(fetch, () => true, value => value, value => value);
}

function deferred() {
    let resolve;
    let reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
}

const response = (body, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body });
const article = (id, changes = {}) => ({ id, title: id, url: `https://example.com/${id}`, is_read: false, archived_at: null, index_status: 'ready', index_error: null, index_attempts: 1, index_retry_at: null, ...changes });
const settle = () => new Promise(resolve => setImmediate(resolve));

test('component compiles for client and server, including failure/retry controls', () => {
    for (const generate of ['client', 'server']) {
        assert.doesNotThrow(() => compile(component, { filename: 'ReadingList.svelte', generate }));
    }
});

test('a stale refresh cannot reverse a successful update', async () => {
    const old = deferred();
    const updated = article('a', { is_read: true });
    let reads = 0;
    const c = createComponent((url, options) => options?.method === 'PATCH'
        ? Promise.resolve(response(updated))
        : ++reads === 1 ? old.promise : Promise.resolve(response([updated])));
    c.seed([article('a')]);
    const refresh = c.refresh();
    await c.patch(article('a'), { is_read: true });
    old.resolve(response([article('a')]));
    await refresh;
    await settle();
    assert.equal(c.state().articles[0].is_read, true);
});

test('the latest refresh wins even if older requests finish later', async () => {
    const first = deferred();
    const second = deferred();
    let calls = 0;
    const c = createComponent(() => ++calls === 1 ? first.promise : second.promise);
    const a = c.refresh();
    const b = c.refresh();
    second.resolve(response([article('new')]));
    await b;
    first.resolve(response([article('old')]));
    await a;
    assert.equal(c.state().articles[0].id, 'new');
});

test('a refresh during a write waits for completion and fetches fresh state', async () => {
    const write = deferred();
    let reads = 0;
    const updated = article('a', { archived_at: '2026-01-01' });
    const c = createComponent((url, options) => options?.method === 'PATCH'
        ? write.promise : (reads++, Promise.resolve(response([updated]))));
    c.seed([article('a')]);
    const pending = c.patch(article('a'), { archived: true });
    await c.refresh();
    assert.equal(reads, 0);
    write.resolve(response(updated));
    await pending;
    await settle();
    assert.equal(reads, 1);
    assert.equal(c.state().articles[0].archived_at, '2026-01-01');
});

test('separate articles update concurrently and retain both results', async () => {
    const a = deferred();
    const b = deferred();
    const calls = [];
    const c = createComponent(url => { calls.push(url); return url.endsWith('/a') ? a.promise : b.promise; });
    c.seed([article('a'), article('b')]);
    const first = c.patch(article('a'), { is_read: true });
    const second = c.patch(article('b'), { archived: true });
    assert.equal(calls.length, 2);
    assert.deepEqual(c.state().busyIds, ['a', 'b']);
    b.resolve(response(article('b', { archived_at: '2026-01-01' })));
    await second;
    assert.deepEqual(c.state().busyIds, ['a']);
    a.resolve(response(article('a', { is_read: true })));
    await first;
    assert.equal(c.state().articles[0].is_read, true);
    assert.equal(c.state().articles[1].archived_at, '2026-01-01');
    assert.deepEqual(c.state().busyIds, []);
});

test('duplicate writes/deletes on the same article are blocked until completion', async () => {
    const pending = deferred();
    let calls = 0;
    const c = createComponent(() => { calls++; return pending.promise; });
    c.seed([article('a')]);
    const first = c.patch(article('a'), { is_read: true });
    assert.equal(await c.patch(article('a'), { archived: true }), false);
    await c.remove(article('a'));
    await c.retryIndexing(article('a'));
    assert.equal(calls, 1);
    pending.resolve(response(article('a', { is_read: true })));
    await first;
    assert.deepEqual(c.state().busyIds, []);
});

test('a late refresh cannot resurrect a deleted article', async () => {
    const old = deferred();
    let reads = 0;
    const c = createComponent((url, options) => options?.method === 'DELETE'
        ? Promise.resolve(response(null, 204))
        : ++reads === 1 ? old.promise : Promise.resolve(response([])));
    c.seed([article('a')]);
    const refresh = c.refresh();
    await c.remove(article('a'));
    old.resolve(response([article('a')]));
    await refresh;
    await settle();
    assert.deepEqual(c.state().articles, []);
});

test('failed writes release their article and keep a useful error', async () => {
    const c = createComponent(async () => response(null, 500));
    c.seed([article('a')]);
    assert.equal(await c.patch(article('a'), { is_read: true }), false);
    assert.equal(c.state().articles[0].is_read, false);
    assert.deepEqual(c.state().busyIds, []);
    assert.match(c.state().error, /Could not update/);
});

test('retry requests update the article and release controls', async () => {
    const c = createComponent(async (url, options) => {
        assert.equal(url, '/api/reading/a/retry');
        assert.equal(options.method, 'POST');
        return response(article('a', { index_status: 'pending', index_attempts: 0 }));
    });
    c.seed([article('a', { index_status: 'failed' })]);
    await c.retryIndexing(article('a'));
    assert.equal(c.state().articles[0].index_status, 'pending');
    assert.deepEqual(c.state().busyIds, []);
});

test('adding an article while another edit is pending refreshes after both finish', async () => {
    const edit = deferred();
    let reads = 0;
    const updated = article('a', { is_read: true });
    const c = createComponent((url, options) => {
        if (options?.method === 'PATCH') return edit.promise;
        if (options?.method === 'POST') return Promise.resolve(response(article('new'), 201));
        reads++;
        return Promise.resolve(response([article('new'), updated]));
    });
    c.seed([article('a')]);
    const pending = c.patch(article('a'), { is_read: true });
    c.setForm('example.com/new', 'New article');
    await c.save({ preventDefault() {} });
    assert.equal(reads, 0);
    edit.resolve(response(updated));
    await pending;
    await settle();
    assert.equal(reads, 1);
    assert.equal(c.state().articles.length, 2);
    assert.equal(c.state().articles[1].is_read, true);
});

test('completing an earlier title save does not close another article editor', async () => {
    const pending = deferred();
    const c = createComponent(() => pending.promise);
    c.seed([article('a'), article('b')]);
    c.startEditing(article('a'));
    c.setDraft('Renamed');
    const save = c.saveTitle(article('a'));
    c.startEditing(article('b'));
    pending.resolve(response(article('a', { title: 'Renamed' })));
    await save;
    assert.equal(c.state().editingId, 'b');
});
