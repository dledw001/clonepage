import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import ts from 'typescript';

const source=readFileSync(new URL('../src/lib/availability.ts',import.meta.url),'utf8');
const js=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2023,module:ts.ModuleKind.ESNext}}).outputText;
const {AvailabilityStore,availabilityStatus,emptyAvailability}=await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);
const now=Date.parse('2026-11-09T15:00:00Z');
const item={id:'plex',name:'Plex',action:'open',value:'https://plex.example.com',availability:true};
const reading={id:item.id,value:item.value,state:'reachable',detail:'Responded with HTTP 200.',status_code:200,checked_at:new Date(now).toISOString(),last_success_at:new Date(now).toISOString(),stale:false};
const data=(changes={})=>({...emptyAvailability(),readings:[reading],...changes});
const ok=payload=>({ok:true,json:async()=>payload});

test('disabled websites and copy actions have no availability indicator',()=>{
  assert.equal(availabilityStatus({...item,availability:false},data(),now),null);
  assert.equal(availabilityStatus({...item,action:'copy'},data(),now),null);
});

test('availability distinguishes reachable, HTTP failures, and unreachable services',()=>{
  assert.equal(availabilityStatus(item,data(),now).label,'Reachable');
  assert.equal(availabilityStatus(item,data({readings:[{...reading,state:'http_error',status_code:503}]}),now).label,'HTTP 503');
  assert.equal(availabilityStatus(item,data({readings:[{...reading,state:'unreachable',status_code:null}]}),now).label,'Unreachable');
  assert.match(availabilityStatus(item,data(),now).title,/Clonepage server/);
});

test('stale and failed refreshes never leave a green reachable indicator',()=>{
  for(const state of [data({readings:[{...reading,stale:true}]}),data({error:'Refresh failed'})]) {
    const result=availabilityStatus(item,state,now);
    assert.equal(result.state,'unknown');assert.equal(result.label,'Status unavailable');
  }
  assert.equal(availabilityStatus(item,data(),now+151000).state,'unknown');
});

test('changing a shortcut URL prevents showing the old URL status',()=>{
  const result=availabilityStatus({...item,value:'https://new.example.com'},data(),now);
  assert.equal(result.state,'unknown');assert.equal(result.label,'Checking');
  assert.equal(result.lastSuccess,'Not yet');
});

test('missing or malformed timestamps are unknown and last success survives a failure',()=>{
  assert.equal(availabilityStatus(item,data({readings:[{...reading,checked_at:'invalid'}]}),now).state,'unknown');
  const result=availabilityStatus(item,data({readings:[{...reading,state:'unreachable',last_success_at:'2026-11-08T15:00:00Z'}]}),now);
  assert.notEqual(result.lastSuccess,'Not yet');
  assert.equal(availabilityStatus(item,emptyAvailability(),now).label,'Checking');
});

test('a failed refresh retains check times and recovery replaces the error',async()=>{
  let fail=false;let calls=0;
  const store=new AvailabilityStore(()=>{},async(path,options)=>{
    calls++;assert.equal(path,'/api/shortcuts/availability');assert.equal(options.cache,'no-store');
    if(fail)throw Error('private URL');return ok({readings:[reading],checking:false});
  });
  await store.refresh();fail=true;await store.refresh();
  assert.equal(store.state.readings[0].last_success_at,reading.last_success_at);
  assert.match(store.state.error,/could not be refreshed/);assert.doesNotMatch(store.state.error,/private/);
  fail=false;await store.refresh();assert.equal(store.state.error,'');assert.equal(calls,3);store.dispose();
});

test('concurrent requests queue one refresh and disposing ignores a late response',async()=>{
  let release;let calls=0;let publications=0;
  const store=new AvailabilityStore(()=>publications++,async()=>{
    calls++;await new Promise(resolve=>release=resolve);return ok({readings:[reading],checking:false});
  });
  const first=store.refresh();await store.refresh();await store.refresh();
  assert.equal(calls,1);release();await first;
  assert.equal(calls,2);
  store.dispose();const count=publications;release();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(publications,count);
});
