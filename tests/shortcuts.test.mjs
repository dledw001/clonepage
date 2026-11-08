import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import ts from 'typescript';
import { compile } from 'svelte/compiler';

const source = readFileSync(new URL('../src/lib/shortcuts.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2023, module: ts.ModuleKind.ESNext } }).outputText;
const { ShortcutStore, groupShortcuts, orderShortcutGroup, moveShortcutGroup } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
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


test('drag ordering is optimistic and restores the last saved order on a failed save', async () => {
  const pending = deferred();
  const store = new ShortcutStore(() => {}, (_, options) => options.method ? pending.promise : Promise.resolve(response(snapshot([item('a'), item('b'), item('c')], 2))));
  await store.refresh(); const saving = store.reorder(['c', 'a', 'b']);
  assert.deepEqual(store.state.items.map(item => item.id), ['c', 'a', 'b']); assert.equal(store.state.busy, true);
  pending.resolve(response({ detail: 'Unavailable' }, 500)); assert.equal(await saving, false);
  assert.deepEqual(store.state.items.map(item => item.id), ['a', 'b', 'c']); assert.equal(store.state.revision, 2);
  assert.match(store.state.error, /previous order has been restored/); assert.equal(store.state.busy, false);
});

test('drag conflicts retain the latest server order and use the revision captured at pointer-down', async () => {
  let reads = 0; let body;
  const store = new ShortcutStore(() => {}, async (_, options) => {
    if (options.method) { body = JSON.parse(options.body); return response({ detail: 'Changed' }, 409); }
    return response(++reads === 1 ? snapshot([item('a'), item('b')], 3) : snapshot([item('b'), item('a'), item('new')], 4));
  });
  await store.refresh(); assert.equal(await store.reorder(['b', 'a'], 2), false);
  assert.equal(body.revision, 2); assert.deepEqual(store.state.items.map(item => item.id), ['b', 'a', 'new']);
  assert.equal(store.state.revision, 4); assert.match(store.state.error, /another device/);
});

test('unchanged or incomplete drag orders never save and late reads cannot undo a drop', async () => {
  const old = deferred(); let writes = 0; let reads = 0;
  const store = new ShortcutStore(() => {}, (_, options) => {
    if (options.method) { writes++; return Promise.resolve(response(snapshot([item('b'), item('a')], 1))); }
    return ++reads === 1 ? Promise.resolve(response(snapshot([item('a'), item('b')]))) : old.promise;
  });
  await store.refresh();
  for (const ids of [['a','b'], ['a'], ['a','a'], ['b','missing']]) assert.equal(await store.reorder(ids), false);
  assert.equal(writes, 0);
  const pending = store.refresh(); assert.equal(await store.reorder(['b','a']), true);
  old.resolve(response(snapshot([item('a'), item('b')]))); await pending;
  assert.deepEqual(store.state.items.map(item => item.id), ['b','a']);
});

test('a conflict whose refresh also fails rolls back the optimistic order', async () => {
  let reads = 0;
  const store = new ShortcutStore(() => {}, async (_, options) => {
    if (options.method) return response({ detail: 'Changed' }, 409);
    if (++reads > 1) throw Error('offline');
    return response(snapshot([item('a'), item('b')]));
  });
  await store.refresh(); await store.reorder(['b','a']);
  assert.deepEqual(store.state.items.map(item => item.id), ['a','b']); assert.match(store.state.error, /another device/);
});

const dragSource = readFileSync(new URL('../src/lib/shortcut-drag.ts', import.meta.url), 'utf8');
const dragJs = ts.transpileModule(dragSource, { compilerOptions: { target: ts.ScriptTarget.ES2023, module: ts.ModuleKind.ESNext } }).outputText;
const { ShortcutDrag, shortcutDropSlot, arrangeShortcuts } = await import(`data:text/javascript;base64,${Buffer.from(dragJs).toString('base64')}`);
const bounds = (left, top) => ({ left, right: left + 100, top, bottom: top + 120 });
const grid = [bounds(0,0), bounds(112,0), bounds(224,0), bounds(0,132)];

test('dragging chooses insertion positions across grid columns and a short final row', () => {
  assert.equal(shortcutDropSlot(grid, 10, 60), 0);
  assert.equal(shortcutDropSlot(grid, 200, 60), 2);
  assert.equal(shortcutDropSlot(grid, 320, 60), 3);
  assert.equal(shortcutDropSlot(grid, 10, 190), 3);
  assert.equal(shortcutDropSlot(grid, 300, 190), 4);
  assert.equal(shortcutDropSlot(grid, 700, 60), null);
  assert.equal(shortcutDropSlot([], 0, 0), null);
});

test('dragging adjusts the insertion gap when moving a card forward or backward', () => {
  assert.deepEqual(arrangeShortcuts(['a','b','c','d'], 'a', 3), ['b','c','a','d']);
  assert.deepEqual(arrangeShortcuts(['a','b','c','d'], 'd', 0), ['d','a','b','c']);
  assert.deepEqual(arrangeShortcuts(['a','b','c','d'], 'a', 4), ['b','c','d','a']);
});

test('a click, a different pointer, and a drop outside the grid do not reorder', () => {
  const gesture = new ShortcutDrag('a', 7, 4, ['a','b','c','d'], 80, 30);
  gesture.move(8, 300, 190, grid); assert.equal(gesture.active, false);
  gesture.move(7, 82, 32, grid); assert.equal(gesture.finish(7), null);
  gesture.move(7, 300, 190, grid); assert.deepEqual(gesture.finish(7), ['b','c','d','a']);
  assert.equal(gesture.finish(8), null); assert.equal(gesture.revision, 4);
  gesture.move(7, 900, 900, grid); assert.equal(gesture.finish(7), null);
});

test('dragging within the original position avoids an unnecessary save', () => {
  const gesture = new ShortcutDrag('b', 1, 0, ['a','b','c','d'], 150, 30);
  gesture.move(1, 180, 60, grid); assert.equal(gesture.active, true); assert.equal(gesture.finish(1), null);
});


function dragHandlers(itemGroups = {}) {
  const component = readFileSync(new URL('../src/components/Shortcuts.svelte', import.meta.url), 'utf8');
  const script = component.match(/<script[^>]*>([\s\S]*?)<\/script>/)[1];
  const tree = ts.createSourceFile('Shortcuts.ts', script, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  const names = ['startDrag','moveDrag','updateDrag','endDrag','cancelDrag','cancelPointer','autoScroll','dragKey'];
  const handlers = tree.statements.filter(node => ts.isFunctionDeclaration(node) && names.includes(node.name?.text)).map(node => node.getText(tree)).join('\n');
  const code = `let gesture=null, drag=null, captured=null, scrollFrame, dragNotice='', deleting=null, editorOpen=false, serviceOpen=false, groupEditing=null;
    let data={busy:false,revision:9,items:['a','b','c','d'].map(id=>({id,group:${JSON.stringify(itemGroups)}[id]??''}))};
    const grid={querySelectorAll:()=>bounds.map((rect,index)=>({dataset:{shortcutId:data.items[index].id},getBoundingClientRect:()=>rect}))};
    ${handlers}
    return {startDrag,moveDrag,endDrag,cancelPointer,dragKey,drag:()=>drag,notice:()=>dragNotice,
      setBusy(value){data.busy=value},setEditing(value){editorOpen=value}};`;
  const compiled = ts.transpileModule(code, { compilerOptions: { target: ts.ScriptTarget.ES2023 } }).outputText;
  let pointer; let releases = 0; const saves = [];
  const handle = { closest:()=>({getBoundingClientRect:()=>({ ...grid[0],width:100,height:120 })}), focus(){},
    setPointerCapture(id){pointer=id}, hasPointerCapture:id=>pointer===id, releasePointerCapture(){pointer=undefined;releases++} };
  const controller = new Function('ShortcutDrag','bounds','store','requestAnimationFrame','cancelAnimationFrame','orderShortcutGroup',compiled)(ShortcutDrag,grid,
    {reorder:async(ids,revision)=>{saves.push({ids,revision});return true}},()=>1,()=>{},orderShortcutGroup);
  const event = (extra={}) => ({pointerId:7,pointerType:'touch',isPrimary:true,button:0,clientX:80,clientY:30,currentTarget:handle,preventDefault(){},...extra});
  return {controller,event,saves,releases:()=>releases};
}

test('touch pointer handlers capture the grip, save once on release, and release capture', async () => {
  const {controller:c,event,saves,releases} = dragHandlers();
  c.startDrag(event(),{id:'a'}); c.moveDrag(event({clientX:300,clientY:190}));
  assert.equal(c.drag().active,true);
  await c.endDrag(event({clientX:300,clientY:190}));
  assert.deepEqual(saves,[{ids:['b','c','d','a'],revision:9}]);
  assert.equal(c.drag(),null);assert.equal(releases(),1);assert.match(c.notice(),/saved/);
});

test('pointer cancellation, Escape, and disabled editing never save a drag', async () => {
  const {controller:c,event,saves} = dragHandlers();
  for (const extra of [{isPrimary:false},{button:2}]) { c.startDrag(event(extra),{id:'a'});assert.equal(c.drag(),null); }
  c.setBusy(true);c.startDrag(event(),{id:'a'});assert.equal(c.drag(),null);c.setBusy(false);
  c.setEditing(true);c.startDrag(event(),{id:'a'});assert.equal(c.drag(),null);c.setEditing(false);
  c.startDrag(event(),{id:'a'});c.moveDrag(event({clientX:300,clientY:190}));
  c.cancelPointer(event()); await c.endDrag(event({clientX:300,clientY:190}));assert.equal(c.drag(),null);
  c.startDrag(event(),{id:'a'});c.moveDrag(event({clientX:300,clientY:190}));
  c.dragKey({key:'Escape',preventDefault(){}});await c.endDrag(event({clientX:300,clientY:190}));
  assert.equal(c.drag(),null);assert.deepEqual(saves,[]);
});


test('shortcut groups preserve first-seen group order and member order', () => {
  const items = [item('a', {group:'Media'}), item('b'), item('c', {group:'Media'}), item('d', {group:'Tools'})];
  assert.deepEqual(groupShortcuts(items).map(group => [group.name, group.items.map(item => item.id)]), [['Media',['a','c']],['',['b']],['Tools',['d']]]);
  assert.deepEqual(orderShortcutGroup(items, 'Media', ['c','a']), ['c','a','b','d']);
  assert.deepEqual(orderShortcutGroup(items, 'Media', ['c','foreign']), items.map(item => item.id));
  assert.deepEqual(moveShortcutGroup(items, 'Tools', -1), ['a','c','d','b']);
});

test('shortcut arrows stay within a group and group ordering retains a complete revisioned list', async () => {
  const items = [item('a', {group:'Media'}), item('b', {group:'Tools'}), item('c', {group:'Media'})];
  const writes = [];
  const store = new ShortcutStore(() => {}, async (_, options) => {
    if (!options.method) return response(snapshot(items, 4));
    const body = JSON.parse(options.body); writes.push(body);
    return response(snapshot(body.ids.map(id => items.find(item => item.id === id)), 5));
  });
  await store.refresh();
  assert.equal(await store.move('b', 1), false);
  assert.equal(await store.move('a', 1), true);
  assert.deepEqual(writes[0], {ids:['c','a','b'], revision:4});
  assert.equal(await store.moveGroup('Tools', -1), true);
  assert.deepEqual(writes[1], {ids:['b','c','a'], revision:5});
});

test('group rename conflicts keep the latest server list and can be retried', async () => {
  let reads = 0; let request;
  const store = new ShortcutStore(() => {}, async (url, options) => {
    if (!options.method) return response(snapshot([item('a', {group: ++reads === 1 ? 'Media' : 'Video'})], reads));
    request = {url, body:JSON.parse(options.body)};
    return response({detail:'Changed on another device'}, 409);
  });
  await store.refresh();
  assert.equal(await store.renameGroup('Media', 'Video', 1), false);
  assert.deepEqual(request, {url:'/api/shortcuts/groups',body:{name:'Media',new_name:'Video',revision:1}});
  assert.equal(store.state.items[0].group, 'Video'); assert.equal(store.state.conflict, true);
});


test('grouped pointer dragging reorders only its members and rejects a drop on another group', async () => {
  const {controller:c,event,saves} = dragHandlers({a:'Media',c:'Media',b:'Tools',d:'Tools'});
  c.startDrag(event(),{id:'a',group:'Media'});
  c.moveDrag(event({clientX:10,clientY:190}));
  await c.endDrag(event({clientX:10,clientY:190}));
  assert.deepEqual(saves,[]);
  c.startDrag(event(),{id:'a',group:'Media'});
  c.moveDrag(event({clientX:300,clientY:60}));
  await c.endDrag(event({clientX:300,clientY:60}));
  assert.deepEqual(saves,[{ids:['c','a','b','d'],revision:9}]);
});
