import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import ts from 'typescript';
import { compile } from 'svelte/compiler';

const source = readFileSync(new URL('../src/lib/dsm-services.ts', import.meta.url),'utf8');
const js = ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2023,module:ts.ModuleKind.ESNext}}).outputText;
const { DSMServiceClient } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
const response=(body,status=200)=>({ok:status>=200&&status<300,status,json:async()=>body});
const info=(connected=false,csrf='csrf-1')=>({connected,csrf,expires_in:300,dsm_url:'https://dsm.dledwith.com',domain:'dledwith.com',upstream_hosts:['10.0.0.149']});
const options={templates:[{id:'template',name:'qBit',hostname:'qbit.dledwith.com',access_control:true}],certificates:[{id:'wild',name:'Wildcard'}]};
const preview={preview_id:'review-token',url:'https://sonarr.dledwith.com',upstream:'http://10.0.0.149:8989',template:'qBit',resuming:false,name:'Sonarr',access_control:true};
const deferred=()=>{let resolve;const promise=new Promise(yes=>resolve=yes);return{promise,resolve}};

function fixture(extra=()=>undefined){
 const calls=[];
 const client=new DSMServiceClient(()=>{},async(url,request)=>{
  calls.push({url,request});const result=extra(url,request);if(result)return result;
  if(url.endsWith('/connection'))return response(request.method==='GET'?info():{connected:true,expires_in:300});
  if(url.endsWith('/options'))return response(options);
  if(url.endsWith('/preview'))return response(preview);
  if(url.endsWith('/disconnect'))return response({connected:false});
  if(url.endsWith('/services'))return response({ok:true,stage:'ready',disconnected:true,url:preview.url,shortcuts:{items:[],revision:1}});
  throw Error(url);
 });return {client,calls};
}

test('service setup and shortcut integration compile for both renderers',()=>{
 for(const path of ['components/ServiceSetup.svelte','components/Shortcuts.svelte','App.svelte']){
  const text=readFileSync(new URL(`../src/${path}`,import.meta.url),'utf8');
  for(const generate of ['client','server'])assert.doesNotThrow(()=>compile(text,{filename:path,generate}));
 }
});

test('password and OTP travel only in the login body and never enter published state',async()=>{
 const {client,calls}=fixture();await client.connect('dan','secret-password-canary','123456');
 assert.equal(client.state.connection.connected,true);assert.deepEqual(client.state.options,options);
 const login=calls.find(call=>call.url.endsWith('/connection')&&call.request.method==='POST');
 assert.deepEqual(JSON.parse(login.request.body),{username:'dan',password:'secret-password-canary',otp:'123456'});
 assert.equal(login.request.headers['X-Clonepage-CSRF'],'csrf-1');assert.equal(login.request.credentials,'same-origin');
 assert.ok(calls.every(call=>!call.url.includes('secret-password-canary')&&!call.url.includes('123456')));
 assert.ok(!JSON.stringify(client.state).includes('secret-password-canary'));assert.ok(!JSON.stringify(client.state).includes('csrf-1'));
});

test('a stale connection poll cannot undo a newer successful login',async()=>{
 const old=deferred();let reads=0;
 const {client}=fixture((url,request)=>url.endsWith('/connection')&&request.method==='GET'&&++reads===1?old.promise:undefined);
 const polling=client.refresh();await client.connect('dan','pw','');old.resolve(response(info(false,'stale')));await polling;
 assert.equal(client.state.connection.connected,true);assert.deepEqual(client.state.options,options);
});

test('failed sign-in keeps a safe error and releases controls without retaining credentials',async()=>{
 const {client}=fixture((url,request)=>url.endsWith('/connection')&&request.method==='POST'?response({detail:'Enter an authenticator code'},401):undefined);
 await client.connect('dan','password-canary','');assert.equal(client.state.busy,false);
 assert.equal(client.state.connection.connected,false);assert.match(client.state.error,/authenticator/);
 assert.ok(!JSON.stringify(client.state).includes('password-canary'));
});

test('review and creation are separate operations and apply submits only the review token',async()=>{
 const {client,calls}=fixture();await client.connect('dan','pw','');await client.preview({name:'Sonarr'});
 assert.equal(calls.filter(call=>call.url.endsWith('/services')).length,0);
 const result=await client.apply();assert.equal(result.ok,true);assert.equal(client.state.connection.connected,false);
 const apply=calls.find(call=>call.url.endsWith('/services'));assert.deepEqual(JSON.parse(apply.request.body),{preview_id:'review-token'});
});

test('editing invalidates the prior review and duplicate creates are blocked',async()=>{
 const pending=deferred();const {client,calls}=fixture(url=>url.endsWith('/services')?pending.promise:undefined);
 await client.connect('dan','pw','');await client.preview({name:'Sonarr'});client.clearPreview();assert.equal(await client.apply(),null);
 await client.preview({name:'Sonarr'});const creating=client.apply();assert.equal(await client.apply(),null);
 pending.resolve(response({ok:true,stage:'ready',disconnected:true}));await creating;
 assert.equal(calls.filter(call=>call.url.endsWith('/services')).length,1);
});

test('partial setup stays visible and requires reconnecting before retry',async()=>{
 const {client}=fixture(url=>url.endsWith('/services')?response({ok:false,stage:'rule_created',error:'Certificate check unfinished',disconnected:true}):undefined);
 await client.connect('dan','pw','');await client.preview({name:'Sonarr'});await client.apply();
 assert.equal(client.state.connection.connected,false);assert.equal(client.state.preview,null);assert.match(client.state.error,/Certificate/);
 assert.equal(client.state.result.stage,'rule_created');
});

test('closing the panel aborts requests and asks DSM to disconnect without putting secrets in a URL',async()=>{
 const {client,calls}=fixture();await client.connect('dan','pw','');client.dispose();
 const disconnect=calls.find(call=>call.url.endsWith('/disconnect'));
 assert.equal(disconnect.request.keepalive,true);assert.equal(disconnect.request.headers['X-Clonepage-CSRF'],'csrf-1');
 assert.equal(disconnect.url,'/api/dsm/disconnect');
});

test('provisioned shortcuts invalidate older list refreshes and do not overwrite a newer list',async()=>{
 const text=readFileSync(new URL('../src/lib/shortcuts.ts',import.meta.url),'utf8');
 const javascript=ts.transpileModule(text,{compilerOptions:{target:ts.ScriptTarget.ES2023,module:ts.ModuleKind.ESNext}}).outputText;
 const {ShortcutStore}=await import(`data:text/javascript;base64,${Buffer.from(javascript).toString('base64')}`);
 const old=deferred();const store=new ShortcutStore(()=>{},()=>old.promise);const reading=store.refresh();
 store.accept({items:[{id:'new'}],revision:2});old.resolve(response({items:[],revision:1}));await reading;
 assert.deepEqual(store.state.items,[{id:'new'}]);store.accept({items:[],revision:1});assert.equal(store.state.revision,2);
});


test('reopening an authenticated setup loads its service options',async()=>{
 const {client}=fixture((url,request)=>url.endsWith('/connection')&&request.method==='GET'?response(info(true)):undefined);
 await client.refresh();assert.equal(client.state.connection.connected,true);assert.deepEqual(client.state.options,options);assert.equal(client.state.busy,false);
});
