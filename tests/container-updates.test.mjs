import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import ts from 'typescript';
import { compile } from 'svelte/compiler';

const source = readFileSync(new URL('../src/lib/container-updates.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2023, module: ts.ModuleKind.ESNext } }).outputText;
const { groupContainers, effectiveState, containerVersion, wudAge, containerTarget, monitorChoices, monitorKey, monitorMatches, monitorSummary, monitorHref, parseMonitorTarget } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
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


test('Compose service links survive container recreation and display-name changes', () => {
  const old = item('old-docker-id', 'media', 'update', {name:'My Plex', container_name:'media-plex-1', service:'plex'});
  const target = containerTarget(old);
  assert.equal(target.service, 'plex'); assert.equal(target.container, null);
  const recreated = {...old, id:'new-docker-id', name:'Plex renamed', container_name:'media-plex-2'};
  assert.equal(monitorMatches(target, recreated), true);
  assert.equal(monitorMatches(target, {...recreated, watcher:'other-nas'}), false);
  assert.equal(monitorMatches(target, {...recreated, project:'other-project'}), false);
  assert.equal(monitorSummary(target, {...snapshot([recreated]), configured:true}).state, 'update');
});

test('standalone links use the raw container name instead of its display label', () => {
  const container = item('first-id', null, 'current', {name:'Pretty name', container_name:'fixed-name'});
  const target = containerTarget(container);
  assert.equal(target.container, 'fixed-name');
  assert.equal(monitorMatches(target, {...container, id:'recreated-id', name:'Different display label'}), true);
});

test('project badges aggregate only linked containers and never show stale data as current', () => {
  const target = {kind:'project',watcher:'nas',project:'media'};
  const data = {...snapshot([item('a','media','update'),item('b','media','unknown'),item('other','other','update')]),configured:true};
  const report = monitorSummary(target, data);
  assert.equal(report.count, 1); assert.match(report.detail, /additional check/);
  assert.equal(monitorSummary(target, {...data,stale:true}).state, 'unknown');
  assert.equal(monitorSummary(target, data, true).state, 'unknown');
  assert.equal(monitorSummary(target, {...data,containers:[]}).state, 'unknown');
  assert.equal(monitorSummary(target, {...data,configured:false}).state, 'unknown');
  assert.equal(monitorSummary(null, data), null);
});

test('monitoring options deduplicate replicated services and preserve project choices', () => {
  const data = snapshot([item('a','media','update',{service:'plex'}),item('b','media','current',{service:'plex'})]);
  const choices = monitorChoices(data);
  assert.equal(choices.filter(choice => choice.kind === 'container').length, 1);
  assert.equal(choices.filter(choice => choice.kind === 'project').length, 1);
  assert.equal(new Set(choices.map(choice => monitorKey(choice.target))).size, choices.length);
});

test('badge links stay internal and their target can be restored from a direct URL', () => {
  const target = {kind:'project',watcher:'nas',project:'Media & tools',service:null,container:null};
  const url = new URL(monitorHref(target), 'https://clonepage.example');
  assert.equal(url.origin, 'https://clonepage.example'); assert.equal(url.pathname, '/infrastructure');
  assert.equal(url.hash, '#container-updates');
  assert.deepEqual(parseMonitorTarget(url.searchParams.get('monitor')), target);
  assert.equal(parseMonitorTarget('invalid'), null);
  assert.equal(parseMonitorTarget(JSON.stringify({kind:'project',watcher:'nas',project:'media',service:'plex'})), null);
  assert.equal(parseMonitorTarget(JSON.stringify({kind:'container',watcher:'nas',service:'plex'})), null);
});
