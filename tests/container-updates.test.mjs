import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import ts from 'typescript';
import { compile } from 'svelte/compiler';

const source = readFileSync(new URL('../src/lib/container-updates.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2023, module: ts.ModuleKind.ESNext } }).outputText;
const { groupContainers, effectiveState, containerVersion, wudAge } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
const item = (id, project, state = 'current', extra = {}) => ({ id, name: id, watcher: 'nas', project, state, image: 'example/image', ...extra });
const snapshot = (containers, stale = false) => ({ containers, stale });

test('containers group by project and watcher, with updates and incomplete checks first', () => {
  const groups = groupContainers(snapshot([
    item('plex', 'media'), item('sonarr', 'media', 'update'), item('postgres', 'photos', 'unknown'),
    item('other-plex', 'media', 'current', { watcher: 'second-nas' }), item('standalone', null),
  ]));
  assert.equal(groups.length, 4);
  assert.equal(groups[0].name, 'media'); assert.equal(groups[0].updates, 1);
  assert.equal(groups[0].containers[0].id, 'sonarr');
  assert.equal(groups[1].name, 'photos'); assert.equal(groups[1].unknown, 1);
  assert.equal(groups.filter(group => group.name === 'media').length, 2);
});

test('standalone fallback cannot merge with a real project that has the fallback name', () => {
  assert.equal(groupContainers(snapshot([item('a', null), item('b', 'Standalone containers')])).length, 2);
});

test('stale cached data never reports current projects or current update counts', () => {
  const groups = groupContainers(snapshot([item('a', 'media', 'update'), item('b', 'media')], true));
  assert.equal(groups[0].state, 'unknown'); assert.equal(groups[0].updates, 0); assert.equal(groups[0].unknown, 2);
  assert.equal(effectiveState(item('a', 'media'), true), 'unknown');
  assert.deepEqual(groupContainers(null), []);
});

test('version display distinguishes same-tag digest updates and prefers actual version labels', () => {
  const reading = item('a', 'media', 'update', { current_tag: 'latest', available_tag: 'latest', current_digest: 'sha256:aaaaaaaaaaaaaaaa', available_digest: 'sha256:bbbbbbbbbbbbbbbb', update_kind: 'digest' });
  assert.notEqual(containerVersion(reading), containerVersion(reading, true));
  assert.equal(containerVersion({ ...reading, available_version: '2.3.1' }, true), '2.3.1');
  assert.equal(containerVersion({}), 'Unavailable');
});

test('sync age does not turn missing or invalid WUD check times into a new check', () => {
  assert.equal(wudAge(null), 'Time unavailable');
  assert.equal(wudAge('invalid'), 'Time unavailable');
  assert.equal(wudAge('2026-11-07T00:00:00Z', Date.parse('2026-11-07T00:02:00Z')), '2m ago');
});

test('container panel and infrastructure integration compile for client and server', () => {
  for (const file of ['ContainerUpdates.svelte', 'Infrastructure.svelte']) {
    const text = readFileSync(new URL(`../src/components/${file}`, import.meta.url), 'utf8');
    for (const generate of ['client', 'server']) assert.doesNotThrow(() => compile(text, { filename: file, generate }));
  }
});
