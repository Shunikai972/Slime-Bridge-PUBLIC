// Exercise unmodified production boot with inert backend boundaries and deterministic short timers.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const file = process.argv[2], scenario = process.argv[3];
const source = fs.readFileSync(file, 'utf8');
const marker = '  SB.app = { refresh, go, get state() { return S; } };';
assert.equal(source.split(marker).length, 2);
const nodes = new Map();
const node = key => {
  if (!nodes.has(key)) nodes.set(key, {innerHTML:'', textContent:'', scrollTop:0, dataset:{}, style:{setProperty(){}},
    classList:{contains:()=>false,toggle(){},add(){},remove(){}},querySelectorAll:()=>[],querySelector:s=>node(key+s),addEventListener(){},focus(){}});
  return nodes.get(key);
};
let opened = 0, failed = false, closed = false, actions = '', legal = 0, scans = 0, rendered = 0;
const state = {version:'test', installations:[], preferences:{language:'fr',reduce_motion:true,play_target:'SR1'},
  games:{SR1:{primary:null},SR2:{primary:null}},host:{state:'STOPPED'},legal_understood:true};
const never = () => new Promise(()=>{});
const api = {get_state:()=>scenario==='state-error'?Promise.reject(new Error('fixture unavailable')):Promise.resolve(state),
  news_cached:()=>scenario==='news-hang'?never():Promise.resolve(null),news_refresh:never, update_check:async()=>({ok:false}),
  autodetect:()=>{scans++;return scenario==='scan-hang'?never():Promise.resolve({ok:true});},image_data:async()=>null};
const SB = {local:{assets:{job:null}},api,net:{hydrate:async()=>{},img:async()=>null},pages:{home:{render:()=>{rendered++;return '<div class="page">home</div>';}}},
 ui:{$:node,$$:()=>[],esc:String,toast(){},modal(){},sleep:()=>Promise.resolve(),confetti(){}},
 Art:{slime:()=>'',icon:()=>'',glyph:()=>'',wordmark:()=>'',trackEyes(){}},Scene:{background:()=>'',parallax(){}},
 t:k=>k,setLang(){},lang:'fr',isDemo:()=>false,cutout:()=>'',has:()=>true,
 showLegal:async()=>{legal++;},loading:{open:()=>{
   opened++;
   return {el:{remove(){closed=true;},querySelector:()=>node('tip')},progress(){},refreshArt(){},done(){},
     fail(){failed=true;},close:async()=>{closed=true;},actions:html=>{actions=html;return node('actions');}};
 }}};
const context = {window:{SB,pywebview:{api},addEventListener(){}},SB,document:{addEventListener(){},documentElement:{classList:{contains:()=>false,toggle(){},add(){}}},hidden:false},
 location:{search:'',hash:''},navigator:{language:'fr'},localStorage:{getItem:()=>null,setItem(){}},URLSearchParams,Map,Set,Promise,console,
 setInterval:()=>1,clearInterval(){},setTimeout:(fn,ms)=>setTimeout(fn,Math.min(ms,30)),clearTimeout};
vm.runInNewContext(source.replace(marker,'  SB.__boot = boot;\n'+marker),context,{filename:file});
(async()=>{
  await Promise.race([SB.__boot(),new Promise((_,reject)=>setTimeout(()=>reject(new Error('Startup remained blocked by '+scenario)),180))]);
  assert.equal(opened,1);
  if(scenario==='state-error') {
    assert.equal(failed,true,'backend failure must be shown rather than leaving an endless spinner');
    assert.match(actions,/retry|Réessayer|boot.retry/,'failed startup needs an actionable retry');
  } else {
    assert.equal(closed,true,'startup overlay must leave the screen');
    assert.ok(SB.app.state,'real initial state must be available');
    assert.ok(rendered>0,'launcher must render without optional news or scan completion');
    assert.ok(node('#head').innerHTML!==undefined);
  }
  console.log('PASS real boot '+scenario);
})().catch(error=>{console.error(error);process.exitCode=1;});
