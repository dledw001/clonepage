import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import ts from 'typescript';
import { compile } from 'svelte/compiler';
const source = readFileSync(new URL('../src/lib/economy.ts', import.meta.url), 'utf8');
const js = ts.transpileModule(source, {compilerOptions:{target:ts.ScriptTarget.ES2023,module:ts.ModuleKind.ESNext}}).outputText;
const {economyGroups,economyValue,movement,historyStart,observationLabel,economicPlot,closestPoint,updatedLabel} = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);

test('native observation labels use UTC periods, including quarters and weekly endings',()=>{
  assert.equal(observationLabel('2026-07-01','Quarterly'),'Q3 2026');
  assert.equal(observationLabel('2026-09-01','Monthly'),'Sep 2026');
  assert.match(observationLabel('2026-11-07','Weekly'),/Week ending Nov 7, 2026/);
  assert.equal(observationLabel(null,'Monthly'),'Not available');
  assert.equal(updatedLabel(null),'Unavailable');
});
test('display formatting distinguishes rates, percentage points, basis points, and counts',()=>{
  assert.equal(economyValue(3.12,'%'),'3.12%');
  assert.equal(economyValue(.25,'pp',true),'+0.25 pp');
  assert.equal(economyValue(320,'bps'),'320 bps');
  assert.equal(economyValue(7561.67,'index'),'7,561.67');
  assert.equal(economyValue(18.7,'index'),'18.70');
  assert.equal(economyValue(2.7,'points',true),'+2.70 points');
  assert.equal(economyValue(-10,'%',true),'-10.00%');
  assert.equal(economyValue(195250,'claims'),'195,250');
  assert.equal(economyValue(-.0000001,'pp',true),'0.00 pp');
  assert.equal(economyValue(null,'%'),'—');
  assert.equal(economyValue(Infinity,'%'),'—');
});
test('direction describes displayed movement without positive or negative economic judgments',()=>{
  assert.equal(movement({value:1,unit:'pp'}),'up');
  assert.equal(movement({value:-1,unit:'bps'}),'down');
  assert.equal(movement({value:.00001,unit:'pp'}),'flat');
  assert.equal(movement({value:null,unit:'pp'}),'unavailable');
});
test('history ranges clamp leap days rather than drifting into March',()=>{
  assert.equal(historyStart('2024-02-29T18:00:00Z',1),'2023-02-28');
  assert.equal(historyStart('2026-11-14T18:00:00Z',5),'2021-11-14');
  assert.equal(economyGroups.length,5);
  assert.ok(economyGroups.includes('Market conditions'));
});
test('plots preserve native dates, gaps, negative values, and the end of released history',()=>{
  const points=[{date:'2026-01-01',value:-25,secondary:null},{date:'2026-02-01',value:null,secondary:null},{date:'2026-03-01',value:27,secondary:30}];
  const plot=economicPlot(points,'2026-01-01','2026-04-01');
  assert.equal(plot.points.length,3);
  assert.equal(plot.selected.length,2);
  assert.equal(plot.paths[0].match(/M/g).length,2);
  assert.doesNotMatch(plot.paths[0],/L/);
  assert.ok(plot.zero>0&&plot.zero<160);
  assert.ok(Number(plot.paths[0].match(/M([0-9.]+),[^M]*$/)[1])<600);
  assert.equal(closestPoint(plot.selected,'2026-02-28'),1);
  assert.equal(economicPlot([], '2026-01-01','2026-04-01').selected.length,0);
});
test('flat and single-observation charts have a finite nonzero vertical range',()=>{
  const plot=economicPlot([{date:'2026-01-01',value:0,secondary:null}], '2026-01-01','2026-02-01');
  assert.ok(plot.maximum>plot.minimum);
  assert.doesNotMatch(plot.paths[0],/NaN|Infinity/);
});
test('economic dashboard and keyboard chart compile for client and server',()=>{
  for(const name of ['Economy','EconomyChart']) {
    const component=readFileSync(new URL(`../src/components/${name}.svelte`,import.meta.url),'utf8');
    for(const generate of ['client','server']) assert.doesNotThrow(()=>compile(component,{filename:`${name}.svelte`,generate}));
  }
});
test('direct economic page routes alongside existing pages',()=>{
  const app=readFileSync(new URL('../src/App.svelte',import.meta.url),'utf8').match(/<script[^>]*>([\s\S]*?)<\/script>/)[1];
  const ast=ts.createSourceFile('App.ts',app,ts.ScriptTarget.Latest,true,ts.ScriptKind.TS);
  const node=ast.statements.find(node=>ts.isFunctionDeclaration(node)&&node.name?.text==='routeFromPath');
  const code=ts.transpileModule(node.getText(ast)+'\nreturn routeFromPath;', {compilerOptions:{target:ts.ScriptTarget.ES2023}}).outputText;
  const route=new Function(code)();
  assert.equal(route('/economy').page,'economy');
  assert.equal(route('/economy/').page,'economy');
  assert.equal(route('/infrastructure').page,'infrastructure');
  assert.equal(route('/hacker-news').page,'hacker-news');
  assert.equal(route('/reading/article').page,'reader');
});
