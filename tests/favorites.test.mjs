import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import ts from 'typescript';
import {compile} from 'svelte/compiler';
async function moduleAt(path) {
  const source=readFileSync(new URL(path,import.meta.url),'utf8');
  const js=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2023,module:ts.ModuleKind.ESNext}}).outputText;
  return import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
}
const {ShortcutStore,favoriteShortcuts}=await moduleAt('../src/lib/shortcuts.ts');
const {FavoriteHold}=await moduleAt('../src/lib/favorite-hold.ts');
const items=['a','b','c'].map(id=>({id,name:id,description:'',aliases:[],icon:'Server',color:'#a4b9e4',action:id==='c'?'copy':'open',value:id==='c'?'ssh example':'https://example.com'}));
const snapshot=(favorite_ids=[],revision=0)=>({items,revision,favorite_ids});
const response=(data,status=200)=>({ok:status>=200&&status<300,status,json:async()=>data});
const deferred=()=>{let resolve;const promise=new Promise(yes=>resolve=yes);return {promise,resolve};};

test('favorites retain their own order, omit deleted IDs, and support older snapshots',()=>{
  assert.deepEqual(favoriteShortcuts(snapshot(['c','a','c','deleted'])).map(item=>item.id),['c','a']);
  assert.deepEqual(favoriteShortcuts({items,revision:0}),[]);
});
test('pin, move, and unpin send the latest revision without changing card order',async()=>{
  let server=snapshot(['a'],4);const writes=[];
  const store=new ShortcutStore(()=>{},async(url,options)=>{
    if(!options.method)return response(server);
    const body=JSON.parse(options.body);writes.push({url,body});
    assert.equal(body.revision,server.revision);
    server=snapshot(body.ids,server.revision+1);return response(server);
  });
  await store.refresh();
  assert.equal(await store.toggleFavorite('c'),true);
  assert.equal(await store.moveFavorite('c',-1),true);
  assert.equal(await store.toggleFavorite('a'),true);
  assert.deepEqual(writes.map(write=>write.body),[
    {ids:['a','c'],revision:4},{ids:['c','a'],revision:5},{ids:['c'],revision:6}]);
  assert.ok(writes.every(write=>write.url==='/api/shortcuts/favorites'));
  assert.deepEqual(store.state.items.map(item=>item.id),['a','b','c']);
  assert.deepEqual(store.state.favorite_ids,['c']);
});
test('invalid pins and boundary moves do not write',async()=>{
  let writes=0;
  const store=new ShortcutStore(()=>{},async(_,options)=>{if(options.method)writes++;return response(snapshot(['a']));});
  await store.refresh();
  for(const action of [()=>store.setFavorites(['a','a']),()=>store.toggleFavorite('missing'),()=>store.moveFavorite('a',-1),()=>store.moveFavorite('a',1),()=>store.moveFavorite('b',1)])assert.equal(await action(),false);
  assert.equal(writes,0);assert.deepEqual(store.state.favorite_ids,['a']);
});
test('a failed favorite save retains the last saved list and can be retried',async()=>{
  let fail=true;
  const store=new ShortcutStore(()=>{},async(_,options)=>{
    if(!options.method)return response(snapshot(['a'],3));
    if(fail)throw Error('offline');
    return response(snapshot(JSON.parse(options.body).ids,4));
  });
  await store.refresh();assert.equal(await store.toggleFavorite('b'),false);
  assert.deepEqual(store.state.favorite_ids,['a']);assert.ok(store.state.error);
  fail=false;assert.equal(await store.toggleFavorite('b'),true);
  assert.deepEqual(store.state.favorite_ids,['a','b']);assert.equal(store.state.error,'');
});
test('a conflicting device pin reloads its saved order without overwriting it',async()=>{
  let reads=0;
  const store=new ShortcutStore(()=>{},async(_,options)=>options.method?response({detail:'Changed on another device'},409):response(++reads===1?snapshot(['a'],2):snapshot(['c'],3)));
  await store.refresh();assert.equal(await store.toggleFavorite('b'),false);
  assert.deepEqual(store.state.favorite_ids,['c']);assert.equal(store.state.revision,3);assert.equal(store.state.conflict,true);
});
test('late reads and a second rapid click cannot replace an in-flight favorite write',async()=>{
  const old=deferred(),saved=deferred();let reads=0,writes=0;
  const store=new ShortcutStore(()=>{},(_,options)=>{
    if(options.method){writes++;return saved.promise;}
    return ++reads===1?Promise.resolve(response(snapshot(['a'],1))):old.promise;
  });
  await store.refresh();const pendingRead=store.refresh();const pendingSave=store.toggleFavorite('b');
  assert.equal(await store.toggleFavorite('c'),false);assert.deepEqual(store.state.favorite_ids,['a']);
  saved.resolve(response(snapshot(['a','b'],2)));assert.equal(await pendingSave,true);
  old.resolve(response(snapshot(['a'],1)));await pendingRead;
  assert.equal(writes,1);assert.deepEqual(store.state.favorite_ids,['a','b']);
});
function gesture(){
  let timerId=0;const pending=new Map(),revealed=[];
  const hold=new FavoriteHold(id=>revealed.push(id),(callback,ms)=>{assert.equal(ms,450);pending.set(++timerId,callback);return timerId;},id=>pending.delete(id));
  const event=(extra={})=>({pointerId:7,pointerType:'touch',clientX:50,clientY:50,...extra});
  const advance=()=>{for(const [id,callback] of [...pending]){pending.delete(id);callback();}};
  return {hold,event,advance,revealed};
}
test('touch holding reveals the name and suppresses exactly one following click',()=>{
  const {hold,event,advance,revealed}=gesture();hold.start('a',event());advance();hold.end(7);
  assert.deepEqual(revealed,['a']);assert.equal(hold.isHeld('a'),true);
  assert.equal(hold.consumeClick('a'),true);assert.equal(hold.consumeClick('a'),false);
});
test('ordinary taps and mouse presses retain their normal action',()=>{
  for(const pointerType of ['touch','mouse','pen']){
    const {hold,event,advance,revealed}=gesture();hold.start('a',event({pointerType}));hold.end(7);advance();
    assert.deepEqual(revealed,[]);assert.equal(hold.consumeClick('a'),false);
  }
});
test('scroll gestures and pointer cancellation never trigger a delayed label',()=>{
  const {hold,event,advance,revealed}=gesture();hold.start('a',event());
  assert.equal(hold.move(event({clientY:61})),true);advance();assert.deepEqual(revealed,[]);
  hold.start('a',event());hold.cancel();advance();assert.deepEqual(revealed,[]);
  assert.equal(hold.consumeClick('a'),false);
});
test('small finger movements and unrelated pointers do not interrupt a hold',()=>{
  const {hold,event,advance,revealed}=gesture();hold.start('a',event());
  assert.equal(hold.move(event({pointerId:8,clientY:100})),false);hold.end(8);
  assert.equal(hold.move(event({clientY:55})),false);advance();assert.deepEqual(revealed,['a']);
});
test('a new touch cancels the previous label and stale click suppression',()=>{
  const {hold,event,advance,revealed}=gesture();hold.start('a',event());hold.start('b',event());advance();
  assert.deepEqual(revealed,['b']);hold.start('a',event());hold.end(7);advance();
  assert.equal(hold.consumeClick('b'),false);
});
test('the favorites bar compiles for both client and server rendering',()=>{
  const source=readFileSync(new URL('../src/components/FavoriteShortcuts.svelte',import.meta.url),'utf8');
  for(const generate of ['client','server'])assert.doesNotThrow(()=>compile(source,{filename:'FavoriteShortcuts.svelte',generate}));
});
