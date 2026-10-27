/* Backend bridge: pywebview's Python API when running inside the launcher, a clearly-labelled demo otherwise. */
(function (SB) {
  'use strict';
  const real = () => (window.pywebview && window.pywebview.api) ? window.pywebview.api : null;
  SB.isDemo = () => !real();
  SB.api = new Proxy({}, {
    get: (_, name) => async (...args) => {
      const backend = real() || SB.mock;
      if (typeof backend[name] !== 'function') throw new Error(`unknown API method ${String(name)}`);
      return backend[name](...args);
    },
  });

  /* ---------- demo backend: plausible data, nothing is read, launched or written ---------- */
  const S = {
    target: 'BOTH', legal: false, ack: false, lang: 'auto', theme: (new URLSearchParams(location.search).get('theme') || 'day'), reduce: false, manifestUrl: null,
    games: {
      SR1: { found: true, store: 'steam', root: 'C:\\Program Files (x86)\\Steam\\steamapps\\common\\Slime Rancher', loader: 'SRML', plugin: false },
      SR2: { found: true, store: 'epic', root: 'D:\\Epic Games\\SlimeRancher2', loader: 'Not configured', plugin: false },
    },
    host: { state: 'STOPPED', url: null, pid: null, heartbeats: {} },
    sanct: { kit: new URLSearchParams(location.search).get('sanct') === '1', layout: new URLSearchParams(location.search).get('sanct') === '1', installed: false, job: null, plugin: false, nodeps: false },
    job: null, dl: null, installed: {}, models: null, tool: true,
    mp: { servers: { default_mode: 'HYBRID', favourites: ['ranch.example:7777'], recent: ['127.0.0.1:7777'], directory_enabled: true }, host: { name: 'My Ranch', port: 7777, mode: 'HYBRID' }, online: { SR1: { state: 'current', loader: true }, SR2: { state: 'missing', loader: true } } },
    settings: {
      transitions: { mode: 'WARM', overlay: true, overlay_style: 'aurora', background_audio: 'DUCK' },
      servers: { default_mode: 'HYBRID', directory_enabled: true },
      content: { auto_build: false, generated_path: 'Lab/GeneratedContent', auto_reverify_on_update: true },
      updates: { channel: 'stable', check_on_start: true },
      diagnostics: { level: 'NORMAL', share_anonymous: false },
    },
  };
  const CHOICES = { transitions: { mode: ['WARM', 'COLD'], overlay_style: ['aurora', 'minimal'], background_audio: ['DUCK', 'MUTE', 'KEEP'] }, servers: { default_mode: ['HYBRID', 'SR1_ONLY', 'SR2_ONLY'] }, updates: { channel: ['stable', 'beta', 'dev'] }, diagnostics: { level: ['NORMAL', 'VERBOSE'] } };
  const SR_NAMES = ['rad', 'quantum', 'mosaic'], SR2_NAMES = ['slumber', 'cotton', 'flutter', 'angler', 'batty', 'ringtail', 'hyper', 'twin'];
  const DISPLAY = { rad: 'Rad', quantum: 'Quantum', mosaic: 'Mosaic', slumber: 'Sloomber', cotton: 'Cotton', flutter: 'Flutter', angler: 'Angler', batty: 'Batty', ringtail: 'Ringtail', hyper: 'Hyper', twin: 'Twin' };
  const catalogue = () => SR_NAMES.flatMap(a => SR2_NAMES.map(b => ({ id: `${a}_${b}`, parents: [a, b], name: `${DISPLAY[a]} × ${DISPLAY[b]}`, family: a, partner: b })));
  const install = realm => { const g = S.games[realm]; return g.found ? { game: realm, root: g.root, store: g.store, runtime: realm === 'SR1' ? 'mono' : 'il2cpp', loader: g.loader, bridge_plugin: g.plugin, role: 'source', version: 'Not fingerprinted' } : null; };
  const wait = ms => new Promise(r => setTimeout(r, ms));
  { /* URL switches for screenshots/tests of otherwise transient states: ?job=running|done|failed &models=1 */
    const q = new URLSearchParams(location.search), j = q.get('job');
    if (j) S.job = { id: 'demo', status: j === 'running' ? 'RUNNING' : j === 'done' ? 'DONE' : 'FAILED', phase: 'convert', phase_label: 'Extracting and converting', progress: j === 'running' ? .46 : 1, total: 122, done: j === 'running' ? 61 : 122, error: j === 'failed' ? 'RuntimeError: the converter refused this game build' : null, logs: ['[SR2:slime.rad] extracting...', '[SR2:largo.rad.cotton] extracting...', '[SR2:slime.hunter] up to date (cache hit)'], options: {}, output: 'D:\SlimeBridge\Lab\Converter', result: j === 'done' ? { convert: { ok: true, count: 122, by_status: { CONVERTED: 122 } }, verify: { ok: true, count: 122, by_status: { OFFLINE_VERIFIED: 119, INCOMPLETE: 3 } }, export: { ok: true, count: 121, by_status: { OK: 121 } }, ledger: {} } : null };
    if (q.get('models')) { S.manifestUrl = 'https://example.org/slimebridge-largos.json'; S.models = catalogue().filter((_, i) => i % 2 === 0).map(m => m.id); ['rad_cotton', 'quantum_batty', 'mosaic_twin'].forEach(id => S.installed[id] = true); }
    if (q.get('games') === 'none') { S.games.SR1.found = false; S.games.SR2.found = false; }
    if (q.get('host')) S.host = { state: 'RUNNING', url: 'http://127.0.0.1:41873', pid: 4242, heartbeats: {} };
  }
  const state = () => ({
    version: 'demo', installations: ['SR1', 'SR2'].map(install).filter(Boolean),
    games: Object.fromEntries(['SR1', 'SR2'].map(r => [r, { primary: install(r), all: [install(r)].filter(Boolean), running: false, store_page: '#' }])),
    legal_understood: S.legal, preferences: { language: S.lang, theme: S.theme, reduce_motion: S.reduce, backup_ack: S.ack, prefer_lab: false, converter_path: S.tool ? 'D:\\Tools\\SlimeBridge-AssetConverter' : null, converter_output: null, cache_dir: null, models_dir: null, largo_manifest_url: S.manifestUrl, native_frame: false, play_target: S.target, extract_ram_gb: S.ram || null },
    host: S.host, tool: { found: S.tool, path: S.tool ? 'D:\\Tools\\SlimeBridge-AssetConverter' : null, python: 'python', own_environment: true, reason: S.tool ? null : 'SlimeBridge-AssetConverter was not found next to the launcher: choose its folder.' },
    paths: { models: 'D:\\SlimeBridge\\Lab\\Largos\\models', output: 'D:\\SlimeBridge\\Lab\\Converter', cache: 'D:\\SlimeBridge\\Lab\\AssetCache' }, platform: 'demo',
  });
  function modelRows() {
    return catalogue().map(m => ({ ...m, state: S.installed[m.id] ? 'INSTALLED' : (S.models && S.models.includes(m.id) ? 'AVAILABLE' : 'NOT_PUBLISHED'), version: S.models && S.models.includes(m.id) ? '1.0.0' : (S.installed[m.id] ? 'local' : null), size: S.models && S.models.includes(m.id) ? 58e6 : null }));
  }
  const sanctState = () => ({
    ok: true, sr1_root: S.games.SR1.root, dependencies: { ok: !S.sanct.nodeps, missing: S.sanct.nodeps ? ['UnityPy==1.25.4'] : [], hint: S.sanct.nodeps ? 'pip install UnityPy==1.25.4' : null },
    kit: S.sanct.kit ? { name: 'sanctuary-kit-demo', statues: { native: 11, synthesized: 6, excluded: 4, decorative: 1 }, native_constructs: 18, walkway_end: 'woodPlat09' } : null,
    layout: S.sanct.layout ? { name: 'layout-demo', statues: 17, required: 16, pieces: 30, access_mode: 'ENDGAME', gold_cost: 10 } : null,
    installed: S.sanct.installed ? { present: true, matches_latest: true } : { present: false }, plugin: S.sanct.plugin,
    settings: { sr1_bridge_access_mode: 'ENDGAME', sr1_gold_plort_gate_cost: 10 }, job: S.sanct.job ? { ...S.sanct.job } : null });
  const modelsState = () => ({ ok: true, rows: modelRows(), dir: 'D:\\SlimeBridge\\Lab\\Largos\\models', manifest: { url: S.manifestUrl, loaded: !!S.models, name: S.models ? 'Demo manifest' : null, updated: S.models ? '2026-10-14' : null, error: null }, download: S.dl });


  /* demo news: titles are placeholders, pictures are generated gradients (the real launcher loads the official ones) */
  const DEMO_ITEMS = [
    ['9/22/2026', 'Ranching in Style — Update 1.3.0 Notes', 'More control over how you decorate your ranch and more!'],
    ['9/17/2026', 'The Monomi Park Shop is Live!', 'Check out the apparel, accessories and decor!'],
    ['5/11/2026', 'Patch 1.2.2 Notes', 'Some important fixes to improve your experience!'],
    ['4/16/2026', 'Radiant Slime Sanctuary — Update 1.2.0', 'Our most radiant update yet!'],
  ].map(([date, title, summary], i) => ({ date, title, summary, url: 'https://www.slimerancher.com/news/demo-' + i, image: 'https://a.storyblok.com/f/164147/3840x2160/demo' + i + '/demo-' + i + '.png' }));
  const DEMO_NEWS = { items: DEMO_ITEMS, scenes: ['https://a.storyblok.com/f/1/3840x2160/a1/keyart-demo.png', 'https://a.storyblok.com/f/1/3840x2160/a2/announce-demo.png', 'https://a.storyblok.com/f/1/3840x2160/a3/screen-demo.png', 'https://a.storyblok.com/f/1/3840x2160/a4/screen2-demo.png'], slimes: [], backdrop: null, offline: false, from_cache: false, error: null, fetched_at: 0 };
  const hue = s => { let h = 0; for (const c of s) h = (h * 31 + c.charCodeAt(0)) % 360; return h; };
  const placeholder = (url, w, h) => { const a = hue(url), b = (a + 60) % 360; return 'data:image/svg+xml;utf8,' + encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="hsl(${a},80%,62%)"/><stop offset="1" stop-color="hsl(${b},85%,70%)"/></linearGradient></defs><rect width="100%" height="100%" fill="url(#g)"/><circle cx="${w * .3}" cy="${h * .7}" r="${h * .28}" fill="rgba(255,255,255,.35)"/><circle cx="${w * .7}" cy="${h * .4}" r="${h * .2}" fill="rgba(255,255,255,.25)"/></svg>`); };

  SB.mock = {
    async get_state() { await wait(60); return state(); },
    async autodetect() { await wait(900); return { ok: true, found: 2, added: [], installations: state().installations }; },
    async forget_game(realm) { S.games[realm].found = false; return { ok: true }; },
    async select_manual() { await wait(300); S.games.SR1.found = true; return install('SR1'); },
    async choose_install() { return { ok: true }; },
    async acknowledge_legal() { S.legal = true; return { understood: true }; },
    async set_preference(k, v) { if (k === 'extract_ram_gb') S.ram = v; if (k === 'play_target') S.target = v; if (k === 'language') S.lang = v; if (k === 'theme') S.theme = v; if (k === 'reduce_motion') S.reduce = v; if (k === 'backup_ack') S.ack = v; if (k === 'largo_manifest_url') { S.manifestUrl = v || null; S.models = null; } return { ok: true, key: k, value: v }; },
    async launch(target, confirmed, remember) {
      if (!S.ack && !confirmed) return { ok: false, needs_confirmation: true };
      if (confirmed && remember) S.ack = true;
      if (target === 'BOTH') S.host = { state: 'RUNNING', url: 'http://127.0.0.1:41873', pid: 4242, heartbeats: {} };
      await wait(500);
      return { ok: true, launched: (target === 'BOTH' ? ['SR1', 'SR2'] : [target]).map(r => ({ realm: r, pid: 1000, already_running: false })), errors: [], warnings: [], host: S.host };
    },
    async host_start() { await wait(400); S.host = { state: 'RUNNING', url: 'http://127.0.0.1:41873', pid: 4242, heartbeats: {} }; return { ok: true, host: S.host }; },
    async host_stop() { S.host = { state: 'STOPPED', url: null, pid: null, heartbeats: {} }; return { ok: true, host: S.host }; },
    async choose_tool_folder() { S.tool = true; return { ok: true }; },
    async choose_output_folder() { return { ok: true, output: 'D:\\SlimeBridge\\Lab\\Converter' }; },
    async extract_start(opt) {
      const total = opt.only_missing ? 122 : 347; let done = 0, phase = 0; const phases = ['list', 'convert', 'verify', 'export'];
      const labels = { list: 'Counting species', convert: 'Extracting and converting', verify: 'Verifying', export: 'Preparing the SlimeBridge caches' };
      S.job = { id: 'demo', status: 'RUNNING', phase: 'list', phase_label: labels.list, progress: 0, total, done: 0, logs: [], error: null, result: null, options: opt, output: 'D:\\SlimeBridge\\Lab\\Converter' };
      const species = ['slime.pink', 'slime.tabby', 'slime.rad', 'largo.rad.cotton', 'slime.hunter', 'largo.quantum.batty', 'slime.mosaic', 'largo.honey.saber'];
      const timer = setInterval(() => {
        const j = S.job; if (!j || j.status !== 'RUNNING') return clearInterval(timer);
        done += Math.ceil(total / 60); j.done = Math.min(total, done);
        if (j.done >= total) { phase++; done = 0; if (phase >= phases.length) { j.status = 'DONE'; j.progress = 1; j.result = { convert: { ok: true, count: total, by_status: { CONVERTED: total } }, verify: { ok: true, count: total, by_status: { OFFLINE_VERIFIED: total - 3, INCOMPLETE: 3 } }, export: { ok: true, count: total - 1, by_status: { OK: total - 1 } }, ledger: {} }; return clearInterval(timer); } j.phase = phases[phase]; j.phase_label = labels[j.phase]; j.done = 0; }
        j.progress = Math.min(.99, (phase + (j.done / total)) / phases.length);
        j.logs.push(`[SR2:${species[Math.floor(Math.random() * species.length)]}] extracting...`); if (j.logs.length > 60) j.logs.shift();
      }, 160);
      return { ok: true, job: S.job };
    },
    async crossings_poll() { return { ok: true, crossings: S.crossings || [] }; },
    async travel_link(id, group) { S.crossings = (S.crossings || []).map(c => c.id === id ? { ...c, state: group ? 'PREPARING' : 'CANCELLED' } : c); return { ok: true }; },
    async travel_links() { return { ok: true, links: [{ sr1: '20261015041707_Ranch', sr2: '20250619134130_1' }] }; },
    async travel_unlink() { return { ok: true }; },
    async plugin_install() { return { ok: true, state: 'current' }; },
    async loader_install() { return { ok: true, state: 'installed' }; },
    async game_repair() { return { ok: true }; },
    async diagnostics() { return { ok: true, text: 'SlimeBridge diagnostic (demo)' }; },
    async sanctuary_state() { return sanctState(); },
    async sanctuary_status() { return { ok: true, job: S.sanct.job ? { ...S.sanct.job } : null }; },
    async sanctuary_build() {
      S.sanct.job = { status: 'RUNNING', phase: 'kit', phase_label: 'Reading your Slime Rancher', progress: .05, error: null, result: null };
      const timer = setInterval(() => {
        const j = S.sanct.job; if (!j || j.status !== 'RUNNING') return clearInterval(timer);
        j.progress = Math.min(1, j.progress + .09);
        if (j.progress > .72) { j.phase = 'layout'; j.phase_label = 'Placing the island'; }
        if (j.progress >= 1) { j.status = 'DONE'; S.sanct.kit = true; S.sanct.layout = true; }
      }, 250);
      return { ok: true, job: S.sanct.job };
    },
    async sanctuary_install() { if (!S.sanct.layout) return { ok: false, error: 'NOT_BUILT' }; S.sanct.installed = true; return { ok: true, installed: sanctState().installed }; },
    async sanctuary_remove() { S.sanct.installed = false; return { ok: true, removed: ['sanctuary-layout.json', 'install-receipt.json'] }; },
    async extract_status() { return { ok: true, job: S.job ? { ...S.job } : null }; },
    async extract_cancel() { if (S.job) S.job.status = 'CANCELLED'; return { ok: true }; },
    async extract_install_cache() { return { ok: true, copied: 118, skipped: 0, destination: 'D:\\SlimeBridge\\Lab\\AssetCache' }; },
    async open_folder() { return { ok: true }; },
    async open_store_page() { return { ok: true }; },
    async models_state() { return modelsState(); },
    async models_refresh() { await wait(600); if (!S.manifestUrl) return { ok: false, error: 'NO_SOURCE', ...modelsState() }; S.models = catalogue().filter((_, i) => i % 3 === 0).map(m => m.id); return { ok: true, ...modelsState() }; },
    async models_download(ids) {
      const list = (ids && ids !== 'all') ? ids : (S.models || []); let i = 0;
      S.dl = { id: 'dl', status: 'RUNNING', index: 0, total: list.length, current: list[0], progress: 0, done: [], errors: [] };
      const timer = setInterval(() => { S.dl.progress = Math.min(1, S.dl.progress + .05); S.dl.index = Math.min(list.length - 1, Math.floor(S.dl.progress * list.length)); S.dl.current = list[S.dl.index]; if (S.dl.progress >= 1) { list.forEach(id => S.installed[id] = true); S.dl.status = 'DONE'; S.dl.done = list; clearInterval(timer); } }, 140);
      return { ok: true, download: S.dl };
    },
    async models_cancel() { if (S.dl) S.dl.status = 'CANCELLED'; return { ok: true }; },
    async models_remove(id) { delete S.installed[id]; return { ok: true, ...modelsState() }; },
    async models_import_folder() { await wait(300); ['rad_cotton', 'quantum_batty', 'mosaic_twin'].forEach(id => S.installed[id] = true); return { ok: true, imported: ['rad_cotton', 'quantum_batty', 'mosaic_twin'], ...modelsState() }; },
    async mp_state() { return { ok: true, servers: S.mp.servers, host: S.mp.host, network_adapter: S.mp.online.SR1.state === 'current', online: S.mp.online, content: { SR1: '2babb6dac1c59104d203496de9773ac280f40901473d94633bea5dc6fd140251', SR2: 'f7b481715ba6bdc79012bcd75c73778a3078162257ab6929081f92f87b2c3d0d' } }; },
    async mp_online(realm) { S.mp.online[realm] = { state: 'current', loader: true }; return { ok: true, online: S.mp.online[realm] }; },
    async mp_remember(address, fav) { if (/[ \/@]/.test(address) || !address) return { ok: false, error: 'address must be host[:port] without scheme, path or credentials' }; const a = address.includes(':') ? address : address + ':7777'; S.mp.servers.recent = [a, ...S.mp.servers.recent.filter(x => x !== a)].slice(0, 20); if (fav === true) S.mp.servers.favourites = [a, ...S.mp.servers.favourites.filter(x => x !== a)]; if (fav === false) S.mp.servers.favourites = S.mp.servers.favourites.filter(x => x !== a); return { ok: true, address: a }; },
    async mp_host_save(o) { S.mp.host = { name: o.name || 'My Ranch', port: +o.port || 7777, mode: o.mode || 'HYBRID' }; return { ok: true, host: S.mp.host }; },
    async get_settings() { return { ok: true, sections: Object.entries(S.settings).map(([section, v]) => ({ section, fields: Object.entries(v).map(([key, value]) => ({ key, value, kind: typeof value === 'boolean' ? 'bool' : 'text', choices: (CHOICES[section] || {})[key] || null })) })) }; },
    async set_setting(section, key, value) { S.settings[section][key] = value; return { ok: true }; },
    async news_cached() { await wait(30); return { ...DEMO_NEWS }; },
    async news_refresh() { await wait(400); return { ...DEMO_NEWS }; },
    async image_data(url, w, h) { return placeholder(url, w || 640, h || 360); },
    async images_data(list) { return list.map(([u, w, h]) => placeholder(u, w, h)); },
    async open_url() { return { ok: true }; },
    async window_minimize() { return { ok: true }; }, async window_toggle_maximize() { return { ok: true }; }, async window_close() { return { ok: true }; },
    async profiles_state() { return { ok: true, pairs: [{ profile_id: 'demo', label: 'Sunny Meadow Ranch', status: 'LINKED', status_text: 'Both saves are linked and verified.', endpoints: { SR1: { save: 'ranch-1', compatibility: 'VALID' }, SR2: { save: 'ranch-2', compatibility: 'VALID' } } }] }; },
    async recovery_state() { return { ok: true, count: 2, summary: { status: 'warning', by_category: {} }, groups: [{ category: 'LOADERS', issues: [{ id: 'a', severity: 'warning', minor: false, title: 'A mod loader is missing', summary: 'Slime Rancher has no mod loader yet (SRML): install it from the Games page' }, { id: 'b', severity: 'info', minor: true, title: 'Something needs attention', summary: 'process adapter is not connected yet (local integration step)' }] }] }; },
    async recovery_export() { return { ok: true, name: 'slimebridge-diagnostics.json' }; },
    async about() { return { ok: true, version: 'demo', python: '3.13', repo: 'demo', config: 'demo' }; },
  };
})(window.SB = window.SB || {});
