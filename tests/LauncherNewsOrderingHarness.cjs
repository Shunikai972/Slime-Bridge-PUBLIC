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
// Response-order matrix against the real production modules.
const cases = {};
const makeFeed = (kind) => kind === 'fresh' || kind === 'nonempty' ? {items:[{title:kind,url:'https://fixture.invalid/'+kind,date:'fixture',summary:kind}],slimes:[],scenes:[],backdrop:null,offline:kind!=='fresh'} : kind === 'empty' ? {items:[],slimes:[],scenes:[],backdrop:null,offline:true,error:'fixture-empty'} : null;
const settle = (d, kind, value) => kind === 'reject' ? d.reject(new Error('fixture rejection')) : d.resolve(value);
function homeContract(h, expected, loading) {
  assert.equal(h.SB.NEWS,expected,'feed identity must follow refresh authority, falling back to cache only when no refresh data');
  assert.equal(h.SB.newsLoading,loading,'loading must match current request terminal/pending state');
  const populated=!!expected?.items?.length;
  assert.equal(h.doc.querySelectorAll('.newsbox .skel').length,loading&&!populated?3:0,'home skeleton count');
  assert.equal(!!h.q('.news-empty'),!loading&&!populated,'home completed empty state');
  assert.equal(h.doc.querySelectorAll('.newsbox .news-item').length,populated?expected.items.length:0,'rendered home items');
  assert.equal(h.unknown.length,0,'no unexpected backend operations');
  assert.equal(h.SB.errors.length,0,'no production window errors');
}
for (const order of ['cache-first','refresh-first']) {
  for (const cacheKind of ['nonempty','empty','null','reject']) {
    for (const refreshKind of ['fresh','empty','null','reject']) {
      cases[`matrix-${order}-${cacheKind}-${refreshKind}`] = async () => {
        const cache=deferred(),refresh=deferred();
        const cached=makeFeed(cacheKind),fresh=makeFeed(refreshKind);
        const h=create({api:{news_cached:()=>cache.promise,news_refresh:()=>refresh.promise}});
        h.local.set('sb.detected','1');await booted(h);
        homeContract(h,null,true);
        if(order==='cache-first') {
          settle(cache,cacheKind,cached);await flush();
          homeContract(h,cached,!(cached?.items?.length));
          settle(refresh,refreshKind,fresh);await flush();
        } else {
          settle(refresh,refreshKind,fresh);await flush();
          homeContract(h,fresh,false);
          settle(cache,cacheKind,cached);await flush();
        }
        await h.clock.tick(10000);
        const expected=fresh||cached;
        homeContract(h,expected,false);
        assert.equal(h.calls.filter(x=>x.name==='news_cached').length,1);
        assert.equal(h.calls.filter(x=>x.name==='news_refresh').length,1);
        await h.click(h.q('#side [data-act="go"][data-arg="news"]'));await flush();
        assert.equal(h.doc.querySelectorAll('.news-card').length,expected?.items?.length||0,'news page agrees with adopted feed');
        assert.equal(!!h.q('#view .empty'),!expected?.items?.length,'news page completed empty state');
      };
    }
  }
}
for (const firstKind of ['null','reject']) {
  cases[`retry-pending-empty-cache-after-${firstKind}`]=async()=>{
    const cache=deferred(),retry=deferred();let refreshCalls=0;
    const empty=makeFeed('empty');
    const h=create({api:{news_cached:()=>cache.promise,news_refresh:()=>++refreshCalls===1?(firstKind==='reject'?Promise.reject(Error('first refresh failed')):Promise.resolve(null)):retry.promise}});
    h.local.set('sb.detected','1');await booted(h);
    homeContract(h,null,false);
    await h.click(h.q('.news-empty [data-act="news-refresh"]'));await flush();
    homeContract(h,null,true);
    cache.resolve(empty);await flush();
    console.log('RETRY_PENDING_OBSERVATION '+JSON.stringify({first:firstKind,newsLoading:h.SB.newsLoading,skeletons:h.doc.querySelectorAll('.newsbox .skel').length,emptyState:!!h.q('.news-empty'),cacheCalls:h.calls.filter(x=>x.name==='news_cached').length,refreshCalls:h.calls.filter(x=>x.name==='news_refresh').length,retrySettled:false,unknown:h.unknown}));
    homeContract(h,empty,true);
    retry.resolve(empty);await flush();await h.clock.tick(10000);
    homeContract(h,empty,false);
  };
}
for (const order of ['older-first','newer-first']) {
  cases['overlapping-refresh-'+order] = async()=>{
    const older=deferred(),newer=deferred();let calls=0;
    const first=makeFeed('fresh'),last={...first,items:[{...first.items[0],title:'latest-refresh'}]};
    const h=create({api:{news_cached:async()=>null,news_refresh:()=>++calls===1?older.promise:newer.promise}});
    h.local.set('sb.detected','1');await booted(h);
    await h.click(h.q('#side [data-act="go"][data-arg="news"]'));await flush();
    await h.click(h.q('[data-act="news-refresh"]'));await flush();
    h.SB.app.go('home');await flush();homeContract(h,null,true);
    if(order==='older-first') {
      older.resolve(first);await flush();homeContract(h,null,true);
      newer.resolve(last);await flush();
    } else {
      newer.resolve(last);await flush();homeContract(h,last,false);
      older.resolve(first);await flush();
    }
    homeContract(h,last,false);await h.clock.tick(10000);homeContract(h,last,false);
    assert.equal(calls,2);
  };
}
(async()=>{for(const [name,fn] of Object.entries(cases)){if(CASE!=='all'&&CASE!==name)continue;try{await fn();results.push({name,passed:true});console.log('PASS '+name);}catch(error){results.push({name,passed:false,error:String(error),stack:error.stack});console.error('FAIL '+name+'\n'+error.stack);}}assert.ok(results.length,'known case required');const failures=results.filter(r=>!r.passed);clearTimeout(watchdog);console.log(JSON.stringify({synthetic_dom:true,visual_acceptance:false,source_root:ROOT,cases:results,passed:results.length-failures.length,failed:failures.length},null,2));process.exitCode=failures.length?1:0;})().catch(e=>{console.error(e);process.exitCode=1;});
