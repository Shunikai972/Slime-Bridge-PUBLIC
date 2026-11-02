// Deterministic startup boundary tests use the real frontend modules with an inert DOM and backend.
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ROOT = path.resolve(process.argv[2]);
const CASE = process.argv[3] || 'all';
const results = [];
const watchdog=setTimeout(()=>{console.error('FIXTURE INCOMPLETE: no completed result summary');process.exitCode=1;},2000);
process.on('unhandledRejection', error => { console.error('PRODUCTION UNHANDLED REJECTION: '+(error.stack||error)); process.exitCode=1; });
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => { resolve=a; reject=b; }); return {promise,resolve,reject}; };
const flush = async () => { for(let i=0;i<35;i++) await Promise.resolve(); };
const never = () => new Promise(()=>{});

class Clock {
  constructor(){this.now=0;this.id=0;this.jobs=new Map();}
  set(fn,delay=0,repeat=0){const id=++this.id;this.jobs.set(id,{id,fn,at:this.now+delay,repeat});return id;}
  clear(id){this.jobs.delete(id);}
  async tick(ms){const end=this.now+ms;await flush();let turns=0;while(true){const job=[...this.jobs.values()].filter(j=>j.at<=end).sort((a,b)=>a.at-b.at||a.id-b.id)[0];if(!job)break;assert.ok(++turns<10000,'timer runaway');this.now=job.at;this.jobs.delete(job.id);if(job.repeat)this.jobs.set(job.id,{...job,at:job.at+job.repeat});job.fn();await flush();}this.now=end;await flush();}
}
const camel = s => s.replace(/-([a-z])/g,(_,c)=>c.toUpperCase());
function matchesSimple(el,sel){
  if(!el || el.tagName==='#DOCUMENT')return false;
  let good=true;
  sel=sel.replace(/:not\(([^)]+)\)/g,(_,inner)=>{good=good&&!matchesSimple(el,inner);return '';});
  if(sel.includes(':hover'))return false;
  const attrs=[...sel.matchAll(/\[([\w-]+)(?:([*^$]?=)["']?([^\]"']*)["']?)?\]/g)];
  sel=sel.replace(/\[[^\]]+\]/g,'');
  const tag=sel.match(/^[A-Za-z][\w-]*/);if(tag&&el.tagName!==tag[0].toUpperCase())return false;
  for(const m of sel.matchAll(/#([\w-]+)/g))if(el.id!==m[1])return false;
  for(const m of sel.matchAll(/\.([\w-]+)/g))if(!el.classList.contains(m[1]))return false;
  for(const m of attrs){
    const value=el.getAttribute(m[1]);if(value===null)return false;
    if(m[2]==='='&&value!==m[3])return false;
    if(m[2]==='*='&&!value.includes(m[3]))return false;
  }
  return good;
}
function matches(el,selector){return selector.split(',').some(part=>{const chain=part.trim().split(/\s+/);if(!matchesSimple(el,chain.pop()))return false;let parent=el.parentNode;while(chain.length){const next=chain.pop();while(parent&&!matchesSimple(parent,next))parent=parent.parentNode;if(!parent)return false;parent=parent.parentNode;}return true;});}
class Element {
  constructor(tag,doc){this.tagName=tag.toUpperCase();this.ownerDocument=doc;this.children=[];this.attrs={};this.dataset={};this.style={setProperty(k,v){this[k]=v;}};this.events=new Map();this.scrollTop=0;this.textContent='';this._html='';this.classList={contains:c=>(this.attrs.class||'').split(/\s+/).includes(c),add:(...cs)=>{this.attrs.class=[...new Set((this.attrs.class||'').split(/\s+/).filter(Boolean).concat(cs))].join(' ');},remove:(...cs)=>{this.attrs.class=(this.attrs.class||'').split(/\s+/).filter(c=>!cs.includes(c)).join(' ');},toggle:(c,v)=>{const on=v===undefined?!this.classList.contains(c):v;on?this.classList.add(c):this.classList.remove(c);return on;}};}
  get id(){return this.attrs.id||'';}set id(v){this.attrs.id=v;}
  get className(){return this.attrs.class||'';}set className(v){this.attrs.class=v;}
  get disabled(){return 'disabled' in this.attrs;}set disabled(v){if(v)this.attrs.disabled='';else delete this.attrs.disabled;}
  get firstChild(){return this.children[0]||null;}
  get isConnected(){let p=this;while(p){if(p===this.ownerDocument)return true;p=p.parentNode;}return false;}
  setAttribute(k,v){this.attrs[k]=String(v);if(k.startsWith('data-'))this.dataset[camel(k.slice(5))]=String(v);}
  getAttribute(k){return Object.hasOwn(this.attrs,k)?this.attrs[k]:null;}
  appendChild(el){el.parentNode=this;this.children.push(el);return el;}
  remove(){if(this.parentNode){this.parentNode.children=this.parentNode.children.filter(x=>x!==this);this.parentNode=null;}}
  set innerHTML(html){this._html=String(html);for(const c of this.children)c.parentNode=null;this.children=[];const stack=[this];const voids=new Set(['img','input','br','hr','meta','link']);for(const token of this._html.matchAll(/<[^>]+>|[^<]+/g)){const text=token[0];if(text.startsWith('</')){if(stack.length>1)stack.pop();continue;}if(text.startsWith('<!'))continue;if(text.startsWith('<')){const m=text.match(/^<([\w-]+)/);if(!m)continue;const el=new Element(m[1],this.ownerDocument);const attrs=text.slice(m[0].length).replace(/\/?\s*>$/,'');for(const a of attrs.matchAll(/([^\s=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s]+)))?/g))el.setAttribute(a[1],a[2]??a[3]??a[4]??'');stack.at(-1).appendChild(el);if(!voids.has(m[1])&&!text.endsWith('/>'))stack.push(el);}else{stack.at(-1).textContent+=text;}}}
  get innerHTML(){return this._html;}
  querySelectorAll(sel){const out=[];const walk=p=>{for(const c of p.children){if(matches(c,sel))out.push(c);walk(c);}};walk(this);return out;}
  querySelector(sel){return this.querySelectorAll(sel)[0]||null;}
  closest(sel){let p=this;while(p){if(matches(p,sel))return p;p=p.parentNode;}return null;}
  addEventListener(type,fn,opts={}){const list=this.events.get(type)||[];list.push({fn,once:!!opts.once});this.events.set(type,list);}
  removeEventListener(type,fn){this.events.set(type,(this.events.get(type)||[]).filter(e=>e.fn!==fn));}
  focus(){this.ownerDocument.activeElement=this;}
  async emit(type,event={},bubble=false){const e={target:this,preventDefault(){this.defaultPrevented=true;},...event};let p=this;const promises=[];while(p){for(const item of [...(p.events.get(type)||[])]){if(item.once)p.removeEventListener(type,item.fn);const value=item.fn(e);if(value&&typeof value.then==='function')promises.push(value);}if(!bubble)break;p=p.parentNode;}await flush();return promises;}
}
function state(legal=true){return {version:'startup-boundary-fixture',platform:'inert',installations:[],preferences:{language:'en',reduce_motion:true,play_target:'SR1',backup_ack:false,native_frame:false},games:{SR1:{primary:null,all:[],running:false},SR2:{primary:null,all:[],running:false}},host:{state:'STOPPED'},legal_understood:legal,paths:{models:'fixture',output:'fixture',cache:'fixture'},tool:{found:false,path:null},last_session:null};}
function create(options={}){
  const clock=new Clock();const doc=new Element('#document',null);doc.ownerDocument=doc;doc.hidden=false;doc.createElement=t=>new Element(t,doc);doc.getElementById=id=>doc.querySelector('#'+id);const html=doc.appendChild(new Element('html',doc));doc.documentElement=html;const body=html.appendChild(new Element('body',doc));body.innerHTML='<div id="bg"></div><div id="app"><header id="head"></header><aside id="side"></aside><main id="stage"><div id="view"></div><footer id="dock"></footer></main><footer id="ribbon"></footer></div><div id="chars-back"></div><div id="chars"></div><canvas id="fx"></canvas><div id="toasts"></div><div id="modal-root"></div><div id="loading-root"></div>';
  const calls=[];const unknown=[];const local=new Map();const backend=new Proxy({get_state:async()=>state(options.legal??true),autodetect:never,news_cached:async()=>null,news_refresh:async()=>null,image_data:async()=>null,update_check:async()=>({ok:false}),acknowledge_legal:async()=>({understood:true}),...options.api},{get(target,name){if(typeof target[name]!=='function'){unknown.push(String(name));return async()=>{throw Error('Unimplemented inert API: '+String(name));};}return (...args)=>{calls.push({name,args,at:clock.now});return target[name](...args);};}});
  const winEvents=new Map();const SB={local:{assets:{job:null}},Art:Object.fromEntries(['slime','plort','icon','glyph','largo','wordmark'].map(k=>[k,()=>'<svg></svg>'])),Scene:{background:()=>'<svg></svg>',hero:()=>'<svg></svg>',parallax(){}},has:()=>true};SB.Art.trackEyes=()=>{};
  const window={SB,pywebview:{api:backend},addEventListener:(type,fn)=>{const list=winEvents.get(type)||[];list.push(fn);winEvents.set(type,list);}};
  class FakeDate extends Date {constructor(...args){super(...(args.length?args:[clock.now]));}static now(){return clock.now;}}
  const ctx={SB,window,document:doc,location:{search:'',hash:''},navigator:{language:'en'},localStorage:{getItem:k=>local.get(k)??null,setItem:(k,v)=>local.set(k,v)},Date:FakeDate,Math,URLSearchParams,console,Map,Set,Promise,setTimeout:(fn,ms)=>clock.set(fn,ms),clearTimeout:id=>clock.clear(id),setInterval:(fn,ms)=>clock.set(fn,ms,ms),clearInterval:id=>clock.clear(id),innerWidth:1320,innerHeight:840,devicePixelRatio:1};window.window=window;vm.createContext(ctx);
  const evalFile=n=>vm.runInContext(fs.readFileSync(path.join(ROOT,'apps/launcher/ui/js',n),'utf8'),ctx,{filename:n});
  for(const n of ['i18n.js','ui.js','api.js','pages_home.js','pages_other.js','loading.js','app.js'])evalFile(n);
  return {clock,doc,SB,backend,calls,unknown,local,window,start:()=>doc.emit('DOMContentLoaded'),q:s=>doc.querySelector(s),click:el=>{assert.ok(el,'clicked element must exist');return el.emit('click',{},true);}};
}
function assertChrome(h){for(const id of ['head','side','ribbon','chars'])assert.ok(h.q('#'+id).innerHTML.length>0,id+' must render during boot');assert.match(h.q('#chars').innerHTML,/pink-cotton-artist\.png/);}
async function booted(h){const pending=await h.start();await h.clock.tick(4000);await Promise.all(pending);return h;}
// Isolate terminal-news-status behavior; production modules are unchanged.
const cases={
  async 'late-cache-after-empty-refresh'(){
    const cached=deferred();const old={items:[{title:'old-cache',url:'https://fixture.invalid/old',date:'old',summary:'old'}],slimes:[],scenes:[],backdrop:null,offline:true};
    const empty={...old,items:[],offline:false,error:'no news found on the page'};
    const h=create({api:{news_cached:()=>cached.promise,news_refresh:async()=>empty}});h.local.set('sb.detected','1');await booted(h);
    assert.equal(h.SB.newsLoading,false);assert.ok(h.q('.news-empty'));assert.equal(h.SB.NEWS,empty);
    cached.resolve(old);await flush();await h.clock.tick(10000);
    console.log('TERMINAL_NEWS_OBSERVATION '+JSON.stringify({source:ROOT,feed_items:h.SB.NEWS.items.length,title:h.SB.NEWS.items[0]?.title,newsLoading:h.SB.newsLoading,skeletons:h.doc.querySelectorAll('.newsbox .skel').length,empty_state:!!h.q('.news-empty'),cache_calls:h.calls.filter(x=>x.name==='news_cached').length,refresh_calls:h.calls.filter(x=>x.name==='news_refresh').length,unknown:h.unknown}));
    assert.equal(h.SB.newsLoading,false,'a completed empty refresh must not revert to indefinitely loading when startup cache arrives late');
    assert.ok(h.q('.news-empty'),'completed empty feed must expose its empty/retry UI');
  },
  async 'cache-before-empty-refresh-control'(){
    const refreshed=deferred();const old={items:[{title:'old-cache',url:'https://fixture.invalid/old',date:'old',summary:'old'}],slimes:[],scenes:[],backdrop:null,offline:true};
    const empty={...old,items:[],offline:false,error:'no news found on the page'};
    const h=create({api:{news_cached:async()=>old,news_refresh:()=>refreshed.promise}});h.local.set('sb.detected','1');await booted(h);
    assert.equal(h.SB.NEWS,old);refreshed.resolve(empty);await flush();await h.clock.tick(10000);
    assert.equal(h.SB.NEWS,empty);assert.equal(h.SB.newsLoading,false);assert.ok(h.q('.news-empty'));
  },
  async 'cache-rejection-after-fresh-control'(){
    const cached=deferred();const fresh={items:[{title:'fresh',url:'https://fixture.invalid/fresh',date:'new',summary:'new'}],slimes:[],scenes:[],backdrop:null,offline:false};
    const h=create({api:{news_cached:()=>cached.promise,news_refresh:async()=>fresh}});h.local.set('sb.detected','1');await booted(h);
    cached.reject(Error('cache unavailable'));await flush();assert.equal(h.SB.NEWS,fresh);assert.equal(h.SB.newsLoading,false);
  },
  async 'null-refresh-then-late-cache-control'(){
    const cached=deferred();const old={items:[{title:'cache',url:'https://fixture.invalid/cache',date:'old',summary:'old'}],slimes:[],scenes:[],backdrop:null,offline:true};
    const h=create({api:{news_cached:()=>cached.promise,news_refresh:async()=>null}});h.local.set('sb.detected','1');await booted(h);
    cached.resolve(old);await flush();assert.equal(h.SB.NEWS,old);assert.equal(h.SB.newsLoading,false);
  },
  async 'rejected-refresh-then-late-cache-control'(){
    const cached=deferred();const old={items:[{title:'cache',url:'https://fixture.invalid/cache',date:'old',summary:'old'}],slimes:[],scenes:[],backdrop:null,offline:true};
    const h=create({api:{news_cached:()=>cached.promise,news_refresh:async()=>{throw Error('offline');}}});h.local.set('sb.detected','1');await booted(h);
    cached.resolve(old);await flush();assert.equal(h.SB.NEWS,old);assert.equal(h.SB.newsLoading,false);
  }
};

(async()=>{for(const [name,fn] of Object.entries(cases)){if(CASE!=='all'&&CASE!==name)continue;try{await fn();results.push({name,passed:true});console.log('PASS '+name);}catch(error){results.push({name,passed:false,error:String(error),stack:error.stack});console.error('FAIL '+name+'\n'+error.stack);}}assert.ok(results.length,'known case required');const failures=results.filter(r=>!r.passed);clearTimeout(watchdog);console.log(JSON.stringify({synthetic_dom:true,visual_acceptance:false,source_root:ROOT,cases:results,passed:results.length-failures.length,failed:failures.length},null,2));process.exitCode=failures.length?1:0;})().catch(e=>{console.error(e);process.exitCode=1;});
