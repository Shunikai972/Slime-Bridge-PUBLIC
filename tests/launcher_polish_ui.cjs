const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const root = process.argv[2] || 'apps/launcher/ui/js/';
const SB = {ui:{esc:s=>String(s ?? ''),pct:x=>x,fmtBytes:x=>String(x)},t:k=>k,local:{},NEWS:{items:[],scenes:[],slimes:[]},Scene:{hero:()=>''}};
const ctx = vm.createContext({window:{SB,addEventListener(){}},console});
for (const file of ['art.js','pages_home.js','pages_tools.js','pages_other.js']) vm.runInContext(fs.readFileSync(root+file,'utf8'),ctx,{filename:file});
const primary = realm => ({game:realm,root:'C:/fixture/'+realm,loader:realm==='SR1'?'SRML':'MelonLoader',store:'manual',plugin_state:'current'});
const S={version:'fixture',preferences:{},host:{state:'STOPPED'},installations:[primary('SR1'),primary('SR2')],
 games:Object.fromEntries(['SR1','SR2'].map(r=>[r,{primary:primary(r),all:[primary(r)],running:true}])),
 paths:{output:'C:/fixture/output',models:'C:/fixture/models'},tool:{found:true},last_session:null};
let fails=0,passed=0;
function check(name,fn){try{fn();passed++;console.log('PASS',name);}catch(e){fails++;console.error('FAIL',name,e.message);}}
check('Games detection uses Victor; ownership card uses arcade',()=>{
 const html=SB.pages.games.render(S), detect=html.split('panel detect')[1].split('id="games"')[0], own=html.split('panel own')[1];
 assert(detect.includes('img/need-games.gif'));assert(own.includes('img/find-games.png'));
});
check('Both actual running cards offer Stop instead of disabled Play',()=>{
 const html=SB.pages.home.render(S);assert.equal((html.match(/data-act="game-stop"/g)||[]).length,3);
 assert(html.includes('data-arg="BOTH"'));assert(!html.includes('data-act="launch"'));
 const games=SB.pages.games.render(S);assert.equal((games.match(/data-act="game-stop"/g)||[]).length,2);
});
check('All nonfinal largo icons are honest placeholder, not procedural art',()=>{
 const html=SB.Art.largo('mosaic','cotton');assert(html.includes('img/largos/unknown.png'));assert(!html.includes('<svg'));
});
check('Explicit artist mappings and alias canonicalization',()=>{
 for(const [a,b,file] of [['rad','angler','rad_angler.png'],['cotton','rad','rad_cotton.png'],['quantum','sloomber','quantum_slumber.png'],['quantum','cotton','quantum_cotton.png']]){
  assert(SB.Art.largo(a,b).includes('img/largos/'+file),`${a}/${b}`);
 }
});
check('Tutorial is a real FR/EN-keyed renderer with actionable setup sections',()=>{
 assert(SB.pages.tutorial);const html=SB.pages.tutorial.render(S);
 for(const key of ['tuto.setup','tuto.island','tuto.settings','tuto.travel','tuto.models','tuto.safety'])assert(html.includes(key),key);
 assert(html.includes('data-arg="assets"'));assert(html.includes('data-arg="options"'));
});
check('Extract next actions have a dedicated wrapping flow container',()=>{
 const html=SB.pages.assets.render(S);assert(html.includes('extract-next-actions'));
});
console.log(`UI_POLISH_CHECKS=${passed+fails} PASSED=${passed} FAILED=${fails}`);process.exitCode=fails?1:0;
