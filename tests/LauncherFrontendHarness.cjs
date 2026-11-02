// Execute the real app script; the only injection exports existing lexical functions for boundary tests.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[2], 'utf8');
const marker = '  SB.app = { refresh, go, get state() { return S; } };';
assert.equal(source.split(marker).length, 2, 'production export marker drifted');
const elements = new Map();
const element = selector => {
  if (!elements.has(selector)) elements.set(selector, { innerHTML:'',scrollTop:0,dataset:{count:'3'},querySelectorAll:()=>[],classList:{contains:()=>false,toggle:()=>{}},value:'' });
  return elements.get(selector);
};
const SB = { local:{assets:{job:null},mods:{realm:'SR2',installed:{},results:null}}, ui:{$:element,$$:()=>[],esc:String,toast:()=>{},modal:()=>{},sleep:()=>Promise.resolve(),confetti:()=>{}},Art:{slime:()=>'',icon:()=>'',glyph:()=>''},t:x=>x,pages:{home:{render:()=>''}} };
const document = { addEventListener:()=>{},querySelector:element,querySelectorAll:()=>[],documentElement:{classList:{contains:()=>false}},hidden:false };
const context = { window:{SB,addEventListener:()=>{}}, document,location:{search:'',hash:''},navigator:{language:'en'},setInterval:()=>1,clearInterval:()=>{},setTimeout:()=>1,clearTimeout:()=>{},console,Map,Set,Promise,URLSearchParams };
context.window.window=context.window;
context.SB=SB;
const waiting=[];
SB.api={mods_installed:realm=>new Promise(resolve=>waiting.push({realm,resolve})),mods_search:(realm,query)=>new Promise(resolve=>waiting.push({realm,query,resolve})),mods_thumbs:async()=>[]};
vm.runInNewContext(source.replace(marker,'  SB.__audit = { loadModsInstalled, actions: A, car, setSlide };\n'+marker),context,{filename:process.argv[2]});
(async()=>{
  if(process.argv[3]==='installed') {
    const pending=SB.__audit.loadModsInstalled();
    assert.equal(waiting[0].realm,'SR2');
    SB.local.mods.realm='SR1';
    waiting[0].resolve({ok:true,mods:[{name:'SR2 fixture'}],pending:null});
    await pending;
    assert.equal(SB.local.mods.installed.SR1,undefined,'late SR2 installed rows must not become SR1 rows');
    assert.equal(SB.local.mods.installed.SR2.mods[0].name,'SR2 fixture');
  } else if(process.argv[3]==='search') {
    const first=SB.__audit.actions['mods-search']();
    SB.local.mods.realm='SR1';
    const second=SB.__audit.actions['mods-search']();
    waiting[1].resolve({ok:true,mods:[{id:1,name:'SR1 result'}]});await second;
    waiting[0].resolve({ok:true,mods:[{id:2,name:'SR2 stale result'}]});await first;
    assert.equal(SB.local.mods.results[0].name,'SR1 result','late search result must not be installed into the newly selected game');
  } else if(process.argv[3]==='carousel') {
    SB.__audit.setSlide(0);
    SB.__audit.actions.slide('1');
    assert.equal(SB.__audit.car.idx,1,'restarting autoplay must not reset the selected slide');
    SB.__audit.actions.slide('-1');
    assert.equal(SB.__audit.car.idx,0);
    SB.__audit.actions.slide('-1');
    assert.equal(SB.__audit.car.idx,2,'previous from first slide wraps to last');
  } else throw Error('unknown scenario');
  console.log('PASS actual app.js '+process.argv[3]);
})().catch(error=>{console.error(error);process.exitCode=1;});
