import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import ts from 'typescript';
import { compileModule } from 'svelte/compiler';
import { flush, untrack } from 'svelte/internal/client';

// Exercise the panel's actual refresh function and revision effect with Svelte's
// reactive runtime. Deferred requests make unintended extra fetches observable.
const component = readFileSync(new URL('../src/components/ContainerUpdates.svelte', import.meta.url), 'utf8');
const script = component.match(/<script[^>]*>([\s\S]*?)<\/script>/)[1];
const ast = ts.createSourceFile('ContainerUpdates.ts', script, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
const refresh = ast.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'refresh');
const revisionEffect = ast.statements.find(node => ts.isExpressionStatement(node)
  && ts.isCallExpression(node.expression) && node.expression.expression.getText(ast) === '$effect'
  && node.getText(ast).includes('void revision'));
assert.ok(refresh && revisionEffect, 'test must exercise the actual panel refresh hook');
const harness = `
  export function createHarness(fetchJson, untrack) {
    let revision = $state(0);
    let snapshot = $state(null);
    let loading = $state(false);
    let error = $state('');
    let now = $state(0);
    let mounted = false;
    let disposed = false;
    let controller;
    ${refresh.getText(ast)}
    const stop = $effect.root(() => { ${revisionEffect.getText(ast)} });
    return {
      mount() { mounted = true; void refresh(); },
      refresh,
      revise() { revision += 1; },
      get state() { return { snapshot, loading, error }; },
      dispose() { disposed = true; controller?.abort(); stop(); },
    };
  }
`;
const javascript = ts.transpileModule(harness, { compilerOptions: { target: ts.ScriptTarget.ES2023, module: ts.ModuleKind.ESNext } }).outputText;
const compiled = compileModule(javascript, { filename: 'ContainerRefresh.svelte.js', generate: 'client' }).js.code;
const runtime = import.meta.resolve('svelte/internal/client');
const runnable = compiled.replace(/(['"])svelte\/internal\/client\1/g, JSON.stringify(runtime));
const { createHarness } = await import(`data:text/javascript;base64,${Buffer.from(runnable).toString('base64')}`);
const settle = async () => { await new Promise(resolve => setImmediate(resolve)); flush(); };

function fixture() {
  const requests = [];
  const panel = createHarness((path, signal) => {
    assert.equal(path, '/api/infrastructure/containers');
    return new Promise((resolve, reject) => {
      requests.push({ resolve, reject, signal });
      signal.addEventListener('abort', () => reject(new Error('Aborted')), { once: true });
    });
  }, untrack);
  flush(() => panel.mount());
  return { panel, requests };
}

test('a completed container refresh becomes idle without automatically fetching again', async () => {
  const { panel, requests } = fixture();
  try {
    assert.equal(requests.length, 1);
    assert.equal(panel.state.loading, true);
    requests[0].resolve({ configured: true, containers: [], stale: false });
    await settle();
    assert.equal(panel.state.loading, false);
    assert.equal(panel.state.snapshot.configured, true);
    assert.equal(requests.length, 1, 'finishing a request must not retrigger the revision effect');
    await settle();
    assert.equal(requests.length, 1);
    const manual = panel.refresh();
    assert.equal(requests.length, 2);
    requests[1].resolve({ configured: true, containers: [], stale: false });
    await manual;
    await settle();
    assert.equal(panel.state.loading, false);
    assert.equal(requests.length, 2);
  } finally { panel.dispose(); }
});

test('failed container refreshes settle and a deliberate revision change can retry', async () => {
  const { panel, requests } = fixture();
  try {
    requests[0].reject(new Error('WUD unavailable'));
    await settle();
    assert.equal(panel.state.loading, false);
    assert.match(panel.state.error, /Could not refresh/);
    assert.equal(requests.length, 1, 'failure must not start a rapid retry loop');
    flush(() => panel.revise());
    assert.equal(requests.length, 2, 'parent refresh still triggers a request');
    requests[1].resolve({ configured: true, containers: [], stale: false });
    await settle();
    assert.equal(panel.state.loading, false);
    assert.equal(panel.state.error, '');
    assert.equal(requests.length, 2);
  } finally { panel.dispose(); }
});

test('concurrent refresh triggers share the active request and disposal aborts it', async () => {
  const { panel, requests } = fixture();
  try {
    await panel.refresh();
    flush(() => panel.revise());
    assert.equal(requests.length, 1);
    panel.dispose();
    await settle();
    assert.equal(requests[0].signal.aborted, true);
    assert.equal(requests.length, 1);
    await panel.refresh();
    assert.equal(requests.length, 1);
  } finally { panel.dispose(); }
});
