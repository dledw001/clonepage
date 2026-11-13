import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import ts from 'typescript';
import {compile} from 'svelte/compiler';
const source=readFileSync(new URL('../src/lib/weather.ts',import.meta.url),'utf8');
const js=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2023,module:ts.ModuleKind.ESNext}}).outputText;
const {temperatureText,windText,weatherKind,forecastDate,weatherUrl}=await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`);

test('weather card and customization compile for both renderers',()=>{
  for(const name of ['Weather','HomeCustomize'])for(const generate of ['client','server'])assert.doesNotThrow(()=>compile(readFileSync(new URL(`../src/components/${name}.svelte`,import.meta.url),'utf8'),{filename:name+'.svelte',generate}));
});
test('weather temperature units preserve zero and unknown readings',()=>{
  assert.equal(temperatureText({c:0,f:32},'C'),'0°C');assert.equal(temperatureText({c:0,f:32},'F'),'32°F');
  assert.equal(temperatureText({c:-.1,f:31.8},'C'),'0°C');assert.equal(temperatureText({c:null,f:null},'F'),'—');
  assert.equal(temperatureText(undefined,'C'),'—');assert.equal(temperatureText({c:NaN,f:Infinity},'F'),'—');
});
test('weather wind uses corresponding units and converts a missing counterpart',()=>{
  assert.equal(windText({wind_kph:0,wind_mph:0,wind_direction:'N'},'F'),'0 mph N');
  assert.equal(windText({wind_kph:16.09344,wind_mph:null,wind_direction:''},'F'),'10 mph');
  assert.equal(windText({wind_kph:null,wind_mph:10,wind_direction:'NE'},'C'),'16 km/h NE');
  assert.equal(windText({wind_kph:null,wind_mph:null,wind_direction:''},'C'),'—');
});
test('weather symbols distinguish clear nights, forecasts, precipitation, and unknown codes',()=>{
  assert.equal(weatherKind('113','Clear'),'moon');assert.equal(weatherKind('113','Clear',true),'sun');
  assert.equal(weatherKind('116'),'partly');assert.equal(weatherKind('248'),'fog');assert.equal(weatherKind('296'),'rain');
  assert.equal(weatherKind('371'),'snow');assert.equal(weatherKind('389'),'storm');assert.equal(weatherKind('unrecognized'),'cloud');
});
test('forecast calendar dates retain their day rather than shifting with UTC',()=>{
  assert.match(forecastDate('2026-11-13'),/Fri.*Nov.*13/);assert.equal(forecastDate('invalid'),'invalid');
});

test('provider links retain explicit locations and match selected units',()=>{
  assert.equal(weatherUrl('22206','F'),'https://wttr.in/22206%2C%20United%20States?u');
  assert.equal(weatherUrl('London, UK','C'),'https://wttr.in/London%2C%20UK?m');
});
