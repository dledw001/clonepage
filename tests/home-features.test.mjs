import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import ts from 'typescript';
import {compile} from 'svelte/compiler';
async function load(path){const source=readFileSync(new URL(path,import.meta.url),'utf8');const js=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2023,module:ts.ModuleKind.ESNext}}).outputText;return import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);}
const {HomeLayoutStore,HomeDataStore,defaultHomeSettings,copySettings,moveSection,homeSectionFromSearch}=await load('../src/lib/home.ts');
const {captureHTMLPosition,restoreHTMLPosition,validHTMLPosition,listenForReadingInput}=await load('../src/lib/html-progress.ts');
const {ReadingProgress}=await load('../src/lib/pdf-progress.ts');
const response=(data,status=200)=>({ok:status>=200&&status<300,status,json:async()=>structuredClone(data)});
const deferred=()=>{let resolve;const promise=new Promise(done=>resolve=done);return {resolve,promise};};
const settle=()=>new Promise(resolve=>setImmediate(resolve));

test('homepage, customization, continue card, attention, and article reader compile for both renderers',()=>{
  for(const file of ['App.svelte','components/HomeCustomize.svelte','components/NeedsAttention.svelte','components/ContinueReading.svelte','components/SavedArticle.svelte']){
    const source=readFileSync(new URL('../src/'+file,import.meta.url),'utf8');
    for(const generate of ['client','server'])assert.doesNotThrow(()=>compile(source,{filename:file,generate}));
  }
});

test('moving homepage sections preserves visibility and does not mutate the original',()=>{
  const initial=defaultHomeSettings();initial.sections.find(section=>section.id==='shortcuts').visible=false;
  const moved=moveSection(moveSection(initial,'shortcuts',-1),'shortcuts',-1);
  assert.equal(moved.sections[0].id,'shortcuts');assert.equal(moved.sections[0].visible,false);
  assert.equal(initial.sections[0].id,'attention');
  assert.deepEqual(moveSection(initial,'attention',-1),initial);
  assert.deepEqual(copySettings(initial),initial);
});

test('only known section links can reveal hidden homepage sections',()=>{
  assert.equal(homeSectionFromSearch('?section=feeds'),'feeds');
  assert.equal(homeSectionFromSearch('?section=reading'),'reading');
  assert.equal(homeSectionFromSearch('?section=__proto__'),null);
  assert.equal(homeSectionFromSearch('?section=unknown'),null);
});

test('layout loads and saves its revision with all preview settings',async()=>{
  let server={revision:0,settings:defaultHomeSettings()};const calls=[];
  const store=new HomeLayoutStore(()=>{},async(path,options)=>{
    calls.push({path,options});
    if(options.method==='PUT'){const data=JSON.parse(options.body);assert.equal(data.revision,0);server={revision:1,settings:data.settings};}
    return response(server);
  });
  await store.refresh();const draft=copySettings(store.state.settings);draft.previews.reading=10;draft.show_clock=false;
  assert.equal(await store.save(draft),true);assert.equal(store.state.revision,1);assert.equal(store.state.settings.previews.reading,10);
  assert.equal(calls[0].options.cache,'no-store');store.dispose();
});

test('an old GET cannot overwrite a newly saved layout',async()=>{
  const old=deferred();let gets=0;
  const store=new HomeLayoutStore(()=>{},async(path,options)=>options.method==='PUT'?response({revision:1,settings:{...defaultHomeSettings(),show_clock:false}}):++gets===2?old.promise:response({revision:0,settings:defaultHomeSettings()}));
  await store.refresh();const pending=store.refresh();await store.save({...defaultHomeSettings(),show_clock:false});
  old.resolve(response({revision:0,settings:defaultHomeSettings()}));await pending;
  assert.equal(store.state.settings.show_clock,false);assert.equal(store.state.revision,1);assert.equal(store.state.loading,false);store.dispose();
});

test('layout saves use the editing revision and conflicts require an explicit reload',async()=>{
  let revision=0,lastWrite;
  const store=new HomeLayoutStore(()=>{},async(path,options)=>{
    if(options.method==='PUT'){lastWrite=JSON.parse(options.body);return response({detail:'Layout changed elsewhere'},409);}
    return response({revision,settings:defaultHomeSettings()});
  });
  await store.refresh();revision=1;await store.refresh();
  assert.equal(await store.save(defaultHomeSettings(),0),false);assert.equal(lastWrite.revision,0);assert.equal(store.state.conflict,true);
  await store.refresh();assert.equal(store.state.conflict,true);
  await store.refresh(true);assert.equal(store.state.conflict,false);store.dispose();
});

test('layout and summary failures retain prior data instead of reporting a new healthy state',async()=>{
  let fail=false;const store=new HomeDataStore('/api/home/attention',()=>{},async()=>{if(fail)throw Error('Offline');return response({total:2,items:[]});});
  await store.refresh();fail=true;await store.refresh();assert.equal(store.state.data.total,2);assert.equal(store.state.error,'Offline');assert.equal(store.state.loading,false);store.dispose();
});

test('overlapping home refreshes queue one follow-up without looping',async()=>{
  const first=deferred();let calls=0;
  const store=new HomeDataStore('/api/home/continue',()=>{},async()=>++calls===1?first.promise:response({items:[]}));
  const pending=store.refresh();void store.refresh();void store.refresh();first.resolve(response({items:[]}));await pending;await settle();
  assert.equal(calls,2);assert.equal(store.state.loading,false);store.dispose();
});

test('home request disposal aborts and does not publish late results',async()=>{
  const pending=deferred();const published=[];let signal;
  const store=new HomeDataStore('/api/home/attention',value=>published.push(value),async(path,options)=>{signal=options.signal;return pending.promise;});
  const work=store.refresh();store.dispose();const count=published.length;pending.resolve(response({total:0}));await work;
  assert.equal(signal.aborted,true);assert.equal(published.length,count);
});

test('HTML positions restore the text anchor across different screen widths',()=>{
  const before={height:2000,viewport:500,blocks:[{top:0,height:100},{top:200,height:200},{top:600,height:400}]};
  const position=captureHTMLPosition(650,before);
  assert.equal(position.anchor,2);assert.equal(position.offset,.125);
  const after={height:3500,viewport:800,blocks:[{top:0,height:150},{top:400,height:400},{top:1000,height:600}]};
  assert.equal(restoreHTMLPosition(position,after),1075);
});

test('HTML position fallback handles missing anchors, short articles, and invalid local data',()=>{
  const geometry={height:2000,viewport:500,blocks:[]};
  assert.equal(restoreHTMLPosition({fraction:.5,anchor:100,offset:0},geometry),750);
  assert.equal(captureHTMLPosition(5000,geometry).fraction,1);
  assert.equal(restoreHTMLPosition(null,geometry),0);
  assert.equal(validHTMLPosition({fraction:NaN,anchor:0,offset:0}),false);
  assert.equal(validHTMLPosition({fraction:.5,anchor:-1,offset:0}),false);
  assert.equal(restoreHTMLPosition({fraction:.9,anchor:0,offset:.5},{height:200,viewport:800,blocks:[]}),0);
});

test('HTML progress sync uses its own endpoint and local backup namespace',async()=>{
  let server={copy_version:'copy-a',revision:0,position:null};const stored=new Map();const calls=[];
  const request=async(path,options={})=>{calls.push(path);if(options.method==='POST'){const update=JSON.parse(options.body);assert.equal(update.revision,server.revision);server={copy_version:'copy-a',revision:server.revision+1,position:update.position};}return response(server);};
  const storage={getItem:key=>stored.get(key)??null,setItem:(key,value)=>stored.set(key,value)};
  const progress=new ReadingProgress('article','copy-a',()=>{},request,storage,undefined,'html');
  await progress.load();progress.note({fraction:.45,anchor:4,offset:.2});await progress.flush();progress.dispose();
  assert.ok(calls.every(path=>path==='/api/reading/article/article-progress'));
  assert.equal(stored.has('clonepage-html-progress:article'),true);assert.equal(stored.has('clonepage-pdf-progress:article'),false);
  const other=new ReadingProgress('article','copy-a',()=>{},request,undefined,undefined,'html');
  assert.deepEqual(await other.load(),server.position);other.dispose();
});

test('HTML older sessions stop on conflicts and Start over invalidates their position',async()=>{
  let server={copy_version:'copy-a',revision:0,position:null};
  const request=async(path,options={})=>{if(options.method==='POST'){const value=JSON.parse(options.body);if(value.revision!==server.revision)return response({detail:{reason:'progress_changed'}},409);server={...server,revision:server.revision+1,position:value.position};}return response(server);};
  let oldState;
  const first=new ReadingProgress('a','copy-a',()=>{},request,undefined,undefined,'html');
  const old=new ReadingProgress('a','copy-a',next=>oldState=next,request,undefined,undefined,'html');
  await first.load();await old.load();first.note({fraction:.6,anchor:8,offset:0});await first.flush();assert.equal(await first.reset(),true);
  old.note({fraction:.8,anchor:10,offset:0});await old.flush();assert.equal(oldState.status,'conflict');assert.equal(server.position,null);
  first.dispose();old.dispose();
});


test('article input inside a separate document marks interaction and removes listeners on cleanup',()=>{
  const parent=new EventTarget(),article=new EventTarget();let inputs=0;
  const stopParent=listenForReadingInput(parent,()=>inputs++);
  const stopArticle=listenForReadingInput(article,()=>inputs++);
  for(const name of ['wheel','touchstart','pointerdown','keydown'])article.dispatchEvent(new Event(name));
  parent.dispatchEvent(new Event('wheel'));assert.equal(inputs,5);
  stopParent();stopArticle();article.dispatchEvent(new Event('wheel'));parent.dispatchEvent(new Event('keydown'));
  assert.equal(inputs,5);
});
