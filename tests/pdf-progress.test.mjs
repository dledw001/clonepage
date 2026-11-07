import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import ts from 'typescript';

const source = readFileSync(new URL('../src/lib/pdf-progress.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2023, module: ts.ModuleKind.ESNext } }).outputText;
const { PDFProgress } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
const position = { page: 3, page_count: 20, left: 0, top: 456 };
const response = (body, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => body });
const deferred = () => { let resolve; const promise = new Promise(done => resolve = done); return { promise, resolve }; };
const settle = () => new Promise(resolve => setImmediate(resolve));
const storage = () => { const data = new Map(); return { getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, value) }; };

function server() {
  let revision = 0, current = null;
  const calls = [];
  const fetch = async (url, options) => {
    calls.push({url, options});
    if (options?.method === 'POST') {
      const update = JSON.parse(options.body);
      if (update.revision !== revision) return response({detail:{reason:'progress_changed'}}, 409);
      current = update.position; revision++;
    }
    return response({ copy_version:'version', revision, position:current });
  };
  return { fetch, calls };
}

test('positions save sequentially and reload on another device', async () => {
  const s = server(); const states = [];
  const a = new PDFProgress('article', 'version', state => states.push(state), s.fetch, storage());
  await a.load(); a.note(position); await a.flush();
  const b = new PDFProgress('article','version',()=>{},s.fetch,storage());
  assert.deepEqual(await b.load(), position);
  assert.equal(states.at(-1).status, 'saved');
  a.dispose(); b.dispose();
});

test('an older session stops instead of overwriting another device', async () => {
  const s = server(); const states = [];
  const a = new PDFProgress('article','version',()=>{},s.fetch);
  const b = new PDFProgress('article','version',state=>states.push(state),s.fetch);
  await a.load(); await b.load(); a.note(position); await a.flush();
  b.note({...position,page:2}); await b.flush();
  assert.equal(states.at(-1).status,'conflict');
  await b.flush();
  assert.equal(s.calls.filter(call=>call.options?.method==='POST').length,2);
  await b.keepCurrent({...position,page:4});
  const latest = await s.fetch('',{});
  assert.equal((await latest.json()).position.page,4);
  a.dispose(); b.dispose();
});

test('movement during an in-flight save survives leaving the reader', async () => {
  const delayed = deferred(); const calls = [];
  const fetch = async (url, options) => {
    if (options?.method !== 'POST') return response({copy_version:'version',revision:0,position:null});
    calls.push(options);
    if (calls.length===1) return delayed.promise;
    return response({copy_version:'version',revision:2,position:JSON.parse(options.body).position});
  };
  const p = new PDFProgress('article','version',()=>{},fetch);
  await p.load(); p.note(position); const saving = p.flush();
  p.note({...position,page:4}); p.dispose();
  delayed.resolve(response({copy_version:'version',revision:1,position}));
  await saving; await settle();
  assert.equal(calls.length,2);
  assert.equal(JSON.parse(calls[1].body).position.page,4);
  assert.equal(calls[1].keepalive,true);
});

test('a failed NAS save is restored locally and retried using the original revision', async () => {
  const local=storage(); const s=server();
  const p=new PDFProgress('article','version',()=>{},async (url,options)=> {
    if (options?.method==='POST') throw Error('offline');
    return s.fetch(url,options);
  },local);
  await p.load(); p.note(position); await p.flush();
  const states=[];
  const reopened=new PDFProgress('article','version',state=>states.push(state),s.fetch,local);
  assert.deepEqual(await reopened.load(),position);
  await reopened.flush(); assert.equal(states.at(-1).status,'saved');
  // Avoid an extra failed network attempt from this intentionally offline old instance.
  await p.load(true); p.dispose(); reopened.dispose();
});

test('a stale local backup cannot supersede newer server progress', async () => {
  const local=storage(), s=server();
  local.setItem('clonepage-pdf-progress:article',JSON.stringify({copy_version:'version',revision:0,position:{...position,page:2},pending:true}));
  await s.fetch('',{method:'POST',body:JSON.stringify({revision:0,position})});
  const p=new PDFProgress('article','version',()=>{},s.fetch,local);
  assert.deepEqual(await p.load(),position); p.dispose();
});

test('start over clears saved progress, including while a prior save finishes', async () => {
  const s=server(); const p=new PDFProgress('article','version',()=>{},s.fetch);
  await p.load(); p.note(position); const saving=p.flush();
  await p.reset(); await saving;
  assert.equal((await (await s.fetch('',{})).json()).position,null); p.dispose();
});

test('a changed PDF version blocks progress writes', async () => {
  const calls=[], states=[];
  const p=new PDFProgress('article','old',state=>states.push(state),async (url,options)=> {
    calls.push(options); return response({copy_version:'new',revision:0,position:null});
  });
  await p.load(); p.note(position); await p.flush();
  assert.equal(states.at(-1).status,'conflict');
  assert.equal(calls.length,1); p.dispose();
});


test('a final save notifies the reading list even after the viewer is disposed', async () => {
  const deferredSave = deferred(); let notified = 0;
  const p = new PDFProgress('article','version',()=>{},async (url,options)=> options?.method==='POST'
    ? deferredSave.promise : response({copy_version:'version',revision:0,position:null}),undefined,()=>notified++);
  await p.load(); p.note(position); p.dispose();
  deferredSave.resolve(response({copy_version:'version',revision:1,position})); await settle();
  assert.equal(notified,1);
});

test('a delayed foreground reload cannot replace movement made while it was loading', async () => {
  const delayed=deferred(); let reads=0;
  const s=server();
  const p=new PDFProgress('article','version',()=>{},async (url,options)=> {
    if (options?.method!=='POST' && ++reads===2) return delayed.promise;
    return s.fetch(url,options);
  });
  await p.load(); const reload=p.load(); p.note(position);
  delayed.resolve(response({copy_version:'version',revision:0,position:null}));
  assert.deepEqual(await reload,position); await p.flush(); p.dispose();
});


test('a PDF replaced during a reading session requires reopening rather than overriding its position', async () => {
  const states=[];
  const p=new PDFProgress('article','version',state=>states.push(state),async (url,options)=> options?.method==='POST'
    ? response({detail:{reason:'copy_changed'}},409) : response({copy_version:'version',revision:0,position:null}));
  await p.load(); p.note(position); await p.flush();
  assert.equal(states.at(-1).status,'conflict');
  assert.equal(states.at(-1).ready,false); p.dispose();
});
