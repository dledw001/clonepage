import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import ts from 'typescript';
import {compile} from 'svelte/compiler';
const transpile=source=>ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2023,module:ts.ModuleKind.ESNext}}).outputText;
const {filterCommands,nextCommandIndex}=await import(`data:text/javascript;base64,${Buffer.from(transpile(readFileSync(new URL('../src/lib/command-search.ts',import.meta.url),'utf8'))).toString('base64')}`);
const {webSearchUrl}=await import(`data:text/javascript;base64,${Buffer.from(transpile(readFileSync(new URL('../src/lib/navigation.ts',import.meta.url),'utf8'))).toString('base64')}`);
const commands=[{id:'nas',name:'Synology NAS',aliases:['DSM'],action:'open',value:'https://nas.example'}, {id:'ssh',name:'SSH command',aliases:['Arch'],action:'copy',value:'ssh example'}];
function functionFrom(path,name){const text=readFileSync(new URL(path,import.meta.url),'utf8');const script=text.match(/<script[^>]*>([\s\S]*?)<\/script>/)[1];const tree=ts.createSourceFile('component.ts',script,ts.ScriptTarget.Latest,true);return tree.statements.find(node=>ts.isFunctionDeclaration(node)&&node.name.text===name).getText(tree);}
function event(key,extra={}){return {key,target:null,isComposing:false,prevented:false,preventDefault(){this.prevented=true;},...extra};}
function palette(items=commands,query=''){
  const calls=[],visits=[],focused=[];
  const document={activeElement:null};
  const input={focus(){focused.push('input');document.activeElement=input;}};
  const close={focus(){focused.push('close');document.activeElement=close;}};
  const nodes=items.map(item=>{const node={focus(){focused.push(item.id);document.activeElement=node;}};return node;});
  document.activeElement=input;
  const factory=new Function('tick','nextCommandIndex','webSearchUrl','onexecute','window','matches','query','input','results','dialog','document',transpile(`let selected=-1;${functionFrom('../src/components/CommandPalette.svelte','keydown')}
return {keydown,get selected(){return selected;}};`).replace('export {};',''));
  return {...factory(async()=>{},nextCommandIndex,webSearchUrl,command=>calls.push(command),{location:{assign:url=>visits.push(url)}},items,query,input,{querySelectorAll:()=>nodes},{querySelectorAll:()=>[input,close,...nodes]},document),input,calls,visits,focused};
}

test('shortcut search ignores case and surrounding whitespace while preserving configured order',()=>{
  assert.deepEqual(filterCommands(commands,' DSM '),[commands[0]]);
  assert.deepEqual(filterCommands(commands,'arch'),[commands[1]]);
  assert.deepEqual(filterCommands(commands,'s'),commands);
  assert.deepEqual(filterCommands(commands,'   '),commands);
  assert.deepEqual(filterCommands(commands,'missing'),[]);
});
test('keyboard selection includes the input and clamps at valid results',()=>{
  assert.equal(nextCommandIndex(-1,1,2),0);assert.equal(nextCommandIndex(0,-1,2),-1);
  assert.equal(nextCommandIndex(1,1,2),1);assert.equal(nextCommandIndex(-1,-1,2),-1);assert.equal(nextCommandIndex(0,1,0),-1);
});
test('Ctrl and Cmd K open search without navigating away from the current page',()=>{
  let opens=0;const code=transpile(functionFrom('../src/App.svelte','handleGlobalKeydown')+';return handleGlobalKeydown;').replace('export {};','');
  const handler=new Function('openSearch',code)(()=>opens++);
  for(const extra of [{ctrlKey:true},{metaKey:true}]){const key=event('K',extra);handler(key);assert.equal(key.prevented,true);}
  for(const extra of [{},{ctrlKey:true,isComposing:true},{ctrlKey:true,defaultPrevented:true}])handler(event('k',extra));
  assert.equal(opens,2);
});
test('palette arrows move actual focus between input and native result controls',async()=>{
  const p=palette();await p.keydown(event('ArrowDown',{target:p.input}));await p.keydown(event('ArrowDown'));await p.keydown(event('ArrowUp'));await p.keydown(event('ArrowUp'));
  assert.deepEqual(p.focused,['nas','ssh','nas','input']);
});
test('Enter on the input executes the selected shortcut once and leaves native result activation alone',async()=>{
  const p=palette();await p.keydown(event('ArrowDown'));await p.keydown(event('ArrowDown'));await p.keydown(event('Enter',{target:p.input}));
  assert.deepEqual(p.calls,[commands[1]]);await p.keydown(event('Enter',{target:{}}));assert.equal(p.calls.length,1);
});
test('Enter with no matches uses the existing web and URL navigation rules',async()=>{
  const p=palette([],'local news');await p.keydown(event('Enter',{target:p.input}));assert.deepEqual(p.visits,['https://www.google.com/search?q=local%20news']);
  const url=palette([],'example.com');await url.keydown(event('Enter',{target:url.input}));assert.deepEqual(url.visits,['https://example.com']);
});
test('composition and an empty search cannot accidentally launch a command or search',async()=>{
  const p=palette();await p.keydown(event('Enter',{target:p.input,isComposing:true}));assert.deepEqual(p.calls,[]);
  const empty=palette([]);await empty.keydown(event('Enter',{target:empty.input}));assert.deepEqual(empty.visits,[]);
});
test('global search dialog and compact app compile for client and server',()=>{
  for(const file of ['components/CommandPalette.svelte','App.svelte'])for(const generate of ['client','server'])assert.doesNotThrow(()=>compile(readFileSync(new URL('../src/'+file,import.meta.url),'utf8'),{filename:file,generate}));
});

test('Tab and Shift Tab wrap within the dialog instead of leaving focus behind it',async()=>{
  const p=palette();const previous=event('Tab',{target:p.input,shiftKey:true});await p.keydown(previous);
  const next=event('Tab',{shiftKey:false});await p.keydown(next);
  assert.equal(previous.prevented,true);assert.equal(next.prevented,true);assert.deepEqual(p.focused,['ssh','input']);
  const empty=palette([]);await empty.keydown(event('Tab',{target:empty.input,shiftKey:true}));assert.deepEqual(empty.focused,['close']);
});
