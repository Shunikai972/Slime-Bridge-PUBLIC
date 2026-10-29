/* Application core: boot + splash, shell (header / sidebar / dock), router, official news + artwork, actions, polling. */
(function (SB) {
  'use strict';
  const { $, $$, esc, toast, modal, sleep, confetti } = SB.ui, { slime, icon, glyph } = SB.Art, t = (...a) => SB.t(...a), api = SB.api;
  const L = SB.local = SB.local || {};
  SB.errors = [];
  window.addEventListener('error', e => SB.errors.push(String(e.message)));
  window.addEventListener('unhandledrejection', e => SB.errors.push(String(e.reason && e.reason.message || e.reason)));
  const NAV = ['home', 'news', 'games', 'assets', 'models', 'multiplayer', 'profiles', 'debug', 'options'];   // 'recovery' lives in 'debug' now
  const NAMES = { SR1: 'Slime Rancher', SR2: 'Slime Rancher 2' }, LOADERS = { SR1: 'SRML', SR2: 'MelonLoader' };
  const FAN_POLICY = 'https://www.slimerancher.com/fan-content-policy';
  let S = null, page = 'home', pollTimer = 0, busy = new Set(), menu = null;
  SB.NEWS = null; SB.newsLoading = true;

  const safe = async (fn, fallback) => { try { return await fn(); } catch (e) { toast(String(e && e.message || e), 'bad'); return fallback; } };
  const prefLang = () => { const p = S && S.preferences.language; if (p && p !== 'auto') return p; return (navigator.language || 'en').toLowerCase().startsWith('fr') ? 'fr' : 'en'; };
  const SHOT = location.search.includes('shot');

  function applyPrefs() {
    document.documentElement.classList.toggle('reduce-motion', !!(S && S.preferences.reduce_motion));
    SB.setLang(prefLang()); document.documentElement.lang = SB.lang;
  }

  /* ----- official pictures: fetched at runtime through the backend (cached on disk), hydrated into the DOM ----- */
  const imgCache = new Map();
  SB.net = {
    img(url, w, h, fit = true) {
      const key = `${url}|${w}x${h}|${fit}`;
      if (!imgCache.has(key)) imgCache.set(key, api.image_data(url, w, h, fit).catch(() => null));
      return imgCache.get(key);
    },
    hydrate(root = document) {
      const jobs = [];
      $$('[data-bg]:not([data-done])', root).forEach(el => {
        el.dataset.done = '1';
        jobs.push(SB.net.img(el.dataset.bg, +el.dataset.w || 640, +el.dataset.h || 360).then(d => { if (d && el.isConnected) { el.style.backgroundImage = `url("${d}")`; el.classList.remove('loading'); } }));
      });
      $$('img[data-src]:not([data-done])', root).forEach(el => {
        el.dataset.done = '1';
        jobs.push(SB.net.img(el.dataset.src, +el.dataset.w || 320, +el.dataset.h || 320, el.dataset.fit !== '0').then(d => { if (d && el.isConnected) el.src = d; }));
      });
      return Promise.all(jobs);
    },
  };

  /* ----- shell rendering ----- */
  function renderHead() {
    const host = S ? S.host : { state: 'STOPPED' }, on = host.state === 'RUNNING', frameless = !(S && S.preferences.native_frame);
    $('#head').innerHTML = `<button class="logo-link" data-act="go" data-arg="home" aria-label="SlimeBridge">${SB.Art.wordmark()}</button><div class="spacer pywebview-drag-region"></div>
      ${SB.isDemo() ? `<span class="pill warn" data-tip="${esc(t('demo.tip'))}">DEMO</span>` : ''}
      <div class="profile" data-act="profile-menu" role="button" tabindex="0" aria-haspopup="true">
        <span class="avatar">${SB.cutout(/pink/i, 'pink', 44, { cls: '' })}</span>
        <span class="who"><b>${t('profile.name')}</b><small><i class="dot ${on ? 'on' : ''}"></i>${on ? t('profile.on') : t('profile.off')}</small></span>${glyph('chevD', 'chev')}
        ${menu === 'profile' ? `<div class="menu" role="menu"><button data-act="${on ? 'host-stop' : 'host-start'}">${glyph(on ? 'stop' : 'play', 'gl mi')} ${on ? t('menu.host_stop') : t('menu.host_start')}</button><hr>
          <button data-act="lang-toggle">${glyph('news', 'gl mi')} ${SB.lang === 'fr' ? 'English' : 'Français'}</button><button data-act="go" data-arg="options">${glyph('options', 'gl mi')} ${t('nav.options')}</button><button data-act="go" data-arg="about">${glyph('heart', 'gl mi')} ${t('nav.about')}</button></div>` : ''}
      </div>
      ${frameless ? `<div class="winctl"><button data-act="win-min" aria-label="Minimize">${glyph('min')}</button><button data-act="win-max" aria-label="Maximize">${glyph('max')}</button><button class="x" data-act="win-close" aria-label="Close">${glyph('close')}</button></div>` : ''}`;
    SB.net.hydrate($('#head'));
  }
  function renderSide() {
    $('#side').innerHTML = `<nav class="nav" aria-label="Main">${NAV.map(k => `<button class="nav-item" data-act="go" data-arg="${k}" ${k === page ? 'aria-current="page"' : ''}>${glyph(k === 'assets' ? 'extract' : k === 'debug' ? 'recovery' : k)}<span>${t('nav.' + k)}</span></button>`).join('')}</nav>`;
  }
  function renderChars() {
    const N = SB.NEWS || {}, pick = re => (N.slimes || []).find(u => re.test(u));
    const spot = (cls, style, re, fallback) => `<div class="ch ${cls}" style="${style}">${pick(re) ? `<img data-src="${esc(pick(re))}" data-w="480" data-h="480" data-fit="1" alt="">` : slime(fallback, { cls: 'idle' })}</div>`;
    // the pink + cotton pair is an artist's drawing that ships with the launcher (no generated art)
    $('#chars').innerHTML = `<div class="ch c1 artist" style="left:12px;bottom:34px;width:236px"><img src="img/pink-cotton-artist.png" alt=""></div>`;
    $('#chars-back').innerHTML = spot('c3', 'right:22px;top:46px;width:92px', /batty|flutter/i, 'batty');
    SB.net.hydrate($('#chars')); SB.net.hydrate($('#chars-back'));
  }
  // BOTH_SR1 / BOTH_SR2: both games, that one in front (the other waits minimized); old 'BOTH' means SR1 in front
  const isBoth = tg => String(tg).startsWith('BOTH');
  function target() {
    const pref = S.preferences.play_target;
    if (pref === 'BOTH') return 'BOTH_SR1';
    return pref || 'BOTH_SR1';
  }
  function dockStatus() {
    const tg = target(), realms = isBoth(tg) ? ['SR1', 'SR2'] : [tg], missing = realms.filter(r => !S.games[r].primary);
    if (missing.length) return { tone: 'bad', text: t('dock.need_game', { game: missing.map(r => NAMES[r]).join(' + ') }) };
    const noLoader = realms.filter(r => S.games[r].primary.loader === 'Not configured');
    if (noLoader.length) return { tone: 'warn', text: t('dock.no_loader', { loader: noLoader.map(r => LOADERS[r]).join(' + ') }) };
    return { tone: 'good', text: t('dock.ready') };
  }
  function renderDock() {
    if (!S) return;
    const tg = target(), st = dockStatus(), g = S.games, job = L.assets && L.assets.job, dl = L.models && L.models.download;
    const running = job && ['RUNNING', 'QUEUED'].includes(job.status), dling = dl && ['RUNNING', 'QUEUED'].includes(dl.status);
    const prog = running ? job.progress : dling ? dl.progress : 1;
    const work = running ? `${t('job.' + job.phase) !== 'job.' + job.phase ? t('job.' + job.phase) : t('job.RUNNING')} ${job.done || 0}/${job.total || '…'}` : dling ? t('models.downloading', { id: esc(dl.current || '') }) : t('dock.no_dl');
    const path = isBoth(tg) ? t('dock.two') : (g[tg].primary ? g[tg].primary.root : '—');
    const pathTitle = isBoth(tg) ? ['SR1', 'SR2'].map(r => `${NAMES[r]}: ${g[r].primary ? g[r].primary.root : '—'}`).join('\n') : path;
    const ready = st.tone !== 'bad';
    $('#dock').innerHTML = `<div class="dock-row">
      <div class="dock-card"><label for="dock-game">${t('dock.game')}</label><select id="dock-game" class="dock-select" data-change="play-target">${[['SR1', NAMES.SR1], ['SR2', NAMES.SR2], ['BOTH_SR1', t('dock.both_sr1')], ['BOTH_SR2', t('dock.both_sr2')]].map(([v, label]) => `<option value="${v}" ${tg === v ? 'selected' : ''}>${label}</option>`).join('')}</select></div>

      <div class="play ${ready ? '' : 'off'}"><button class="go" data-act="play" aria-label="Play">${glyph('play')}<span>${t('dock.play')}</span></button><button class="more" data-act="play-menu" aria-label="More" aria-haspopup="true">${glyph('chevD')}</button>
        ${menu === 'play' ? `<div class="menu" role="menu"><button data-act="launch" data-arg="BOTH_SR1">${glyph('play', 'gl mi')} ${t('dock.both_sr1')}</button><button data-act="launch" data-arg="BOTH_SR2">${glyph('play', 'gl mi')} ${t('dock.both_sr2')}</button><button data-act="launch" data-arg="SR1">${glyph('play', 'gl mi')} ${NAMES.SR1}</button><button data-act="launch" data-arg="SR2">${glyph('play', 'gl mi')} ${NAMES.SR2}</button><hr><button data-act="go" data-arg="games">${glyph('folder', 'gl mi')} ${t('nav.games')}</button></div>` : ''}</div></div>
      <div class="dock-status"><span class="ok"><i class="tick ${st.tone === 'good' ? '' : st.tone}">${glyph(st.tone === 'good' ? 'check' : 'close')}</i><span class="t" title="${esc(st.text)}">${esc(st.text)}</span></span>
        <div class="bar" style="--p:${(Math.max(0, Math.min(1, prog || 0)) * 100).toFixed(1)}%"><i></i></div><span>SlimeBridge ${esc(S.version)}</span><span>${work}</span>
        <span class="tag">${slime('pink', { size: 30, cls: 'idle' })}${t('dock.tag')} <span style="color:var(--pink)">♥</span></span></div>`;
  }
  function renderRibbon() {
    $('#ribbon').innerHTML = `<span class="txt">${t('ribbon.a')}<a data-act="open-url" data-arg="${FAN_POLICY}">${t('ribbon.link')}</a>${t('ribbon.b')}</span>`;
  }
  // polling redraws boxes several times a second: keep opened <details> (logs...) open and their text where it was
  function redraw(el, html) {
    const key = (d, i) => d.dataset.key || d.className + ':' + i, opened = new Map();
    $$('details', el).forEach((d, i) => { if (d.open) { const pre = d.querySelector('pre'); opened.set(key(d, i), pre ? { top: pre.scrollTop, end: pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 4 } : {}); } });
    el.innerHTML = html;
    if (opened.size) $$('details', el).forEach((d, i) => { const s = opened.get(key(d, i)); if (!s) return; d.open = true; const pre = d.querySelector('pre'); if (pre) pre.scrollTop = s.end ? pre.scrollHeight : s.top; });
  }
  function renderView(keep) {
    const root = $('#view'), top = root.scrollTop, def = SB.pages[page];
    if (keep) redraw(root, def.render(S)); else root.innerHTML = def.render(S);
    root.scrollTop = keep ? top : 0;
    SB.net.hydrate(root);
    renderSide(); renderDock();
    if (page === 'options' && !L.settings) loadSettings();
    if (page === 'options' && !L.links) loadLinks();
    if (page === 'models' && !L.models) loadModels();
    if (page === 'profiles' && !L.profiles) loadProfiles();
    if ((page === 'recovery' || page === 'debug') && !L.recovery) loadRecovery();
    if (page === 'multiplayer' && !L.mp) loadMp();
    if (page === 'assets' && !L.assets.job) pollJob(true);
    if (page === 'assets' && !L.sanct) loadSanct();
    schedulePoll(); startCarousel();
  }
  function renderAll() { renderHead(); renderSide(); renderRibbon(); renderChars(); renderView(true); }
  async function refresh(keep = true) {
    S = await safe(() => api.get_state(), S);
    if (S) { applyPrefs(); renderHead(); renderRibbon(); renderView(keep); }
  }
  async function loadLinks() { const r = await safe(() => api.travel_links()); L.links = r && r.ok ? r.links : []; if (page === 'options') renderView(true); }
  async function loadSettings() { const r = await safe(() => api.get_settings()); if (r) { L.settings = r; if (page === 'options') renderView(true); } }
  async function loadProfiles() { const r = await safe(() => api.profiles_state()); if (r) { L.profiles = r; if (page === 'profiles') renderView(true); } }
  async function loadRecovery() { const r = await safe(() => api.recovery_state()); if (r) { L.recovery = r; if (page === 'recovery' || page === 'debug') renderView(true); } }
  async function loadModels() { const r = await safe(() => api.models_state()); if (r) { L.models = r; if (page === 'models') renderView(true); } }
  async function loadSanct() { const r = await safe(() => api.sanctuary_state()); if (r) { L.sanct = r; if (page === 'assets') renderView(true); } }
  async function loadMp() { const r = await safe(() => api.mp_state()); if (r) { L.mp = r; if (page === 'multiplayer') renderView(true); } }
  function go(name) { if (!SB.pages[name]) return; menu = null; page = name; location.hash = '#/' + name; renderHead(); renderView(false); }

  /* ----- carousel ----- */
  const car = { idx: 0, timer: 0 };
  function setSlide(i) {
    const el = $('#carousel'); if (!el) return; const n = +el.dataset.count; car.idx = (i + n) % n;
    $$('.slide', el).forEach((s, k) => s.classList.toggle('on', k === car.idx)); $$('.dots button', el).forEach((d, k) => d.classList.toggle('on', k === car.idx));
  }
  function startCarousel() {
    clearInterval(car.timer); car.idx = 0;
    if (page !== 'home' || document.documentElement.classList.contains('reduce-motion')) return;
    car.timer = setInterval(() => { if (!document.hidden && !$('#carousel:hover')) setSlide(car.idx + 1); }, 6500);
  }

  /* ----- official news (every start) ----- */
  async function applyBackdrop() {
    const N = SB.NEWS || {}, bg = $('#bg');
    if (N.backdrop) {
      const d = await SB.net.img(N.backdrop, 1920, 1080);
      if (d) { bg.innerHTML = ''; bg.style.backgroundImage = `url("${d}")`; return; }
    }
    if (!bg.firstChild) bg.innerHTML = SB.Scene.background();
  }
  async function loadNews() {
    const cached = await api.news_cached().catch(() => null);
    if (cached) SB.NEWS = cached;
    SB.newsLoading = !(SB.NEWS && SB.NEWS.items && SB.NEWS.items.length);
    return cached;
  }
  async function refreshNews(manual) {
    SB.newsLoading = true; if (manual && (page === 'home' || page === 'news')) renderView(true);
    const fresh = await api.news_refresh().catch(() => null);
    SB.newsLoading = false;
    if (fresh) {
      SB.NEWS = fresh; await applyBackdrop(); renderChars();
      if (page === 'home' || page === 'news') renderView(true);
      if (fresh.offline) toast(t('news.offline'), 'warn'); else if (manual) toast(t('news.refreshed'), 'good');
    } else if (page === 'home' || page === 'news') renderView(true);
  }

  /* ----- polling for long jobs (extraction, downloads) ----- */
  function schedulePoll() {
    clearInterval(pollTimer);
    const job = L.assets.job, dl = L.models && L.models.download;
    const sj = L.sanct && L.sanct.job, need = (job && ['RUNNING', 'QUEUED'].includes(job.status)) || (dl && ['RUNNING', 'QUEUED'].includes(dl.status)) || (sj && ['RUNNING', 'QUEUED'].includes(sj.status));
    if (need) pollTimer = setInterval(pollTick, 650);
  }
  async function pollJob(silent) {
    const r = await api.extract_status().catch(() => null);
    if (r && r.job && (!L.assets.job || r.job.id !== L.assets.job.id || silent)) { L.assets.job = r.job; if (page === 'assets') renderView(true); renderDock(); schedulePoll(); }
  }
  async function pollTick() {
    const job0 = L.assets.job;
    if (job0 && ['RUNNING', 'QUEUED'].includes(job0.status)) {
      const r = await api.extract_status().catch(() => null);
      if (r && r.job) {
        const was = job0.status; L.assets.job = r.job; renderDock();
        const box = $('#jobbox'); if (box) redraw(box, SB.jobBox(r.job));
        if (was !== r.job.status && !['RUNNING', 'QUEUED'].includes(r.job.status)) { if (page === 'assets') renderView(true); if (r.job.status === 'DONE') { toast(t('assets.done'), 'good'); confetti($('.play') || document.body, 26); } }
      }
    }
    const dl0 = L.models && L.models.download;
    if (dl0 && ['RUNNING', 'QUEUED'].includes(dl0.status)) {
      const r = await api.models_state().catch(() => null);
      if (r) {
        L.models = r; renderDock(); const box = $('#dlbox'); if (box && r.download) redraw(box, SB.dlBox(r.download));
        if (r.download && dl0.status !== r.download.status && !['RUNNING', 'QUEUED'].includes(r.download.status)) { if (page === 'models') renderView(true); if (r.download.status === 'DONE') toast(t('models.done'), 'good'); }
      }
    }
    const sj0 = L.sanct && L.sanct.job;
    if (sj0 && ['RUNNING', 'QUEUED'].includes(sj0.status)) {
      const r = await api.sanctuary_state().catch(() => null);
      if (r) {
        L.sanct = r; const box = $('#sanctbox'); if (box) redraw(box, SB.sanctBox(r));
        if (r.job && !['RUNNING', 'QUEUED'].includes(r.job.status)) { if (page === 'assets') renderView(true); toast(r.job.status === 'DONE' ? t('sanct.done') : (r.job.error || t('sanct.failed')), r.job.status === 'DONE' ? 'good' : 'bad'); if (r.job.status === 'DONE') confetti($('.sanct') || document.body, 24); }
      }
    }
    schedulePoll();
  }

  /* ----- actions ----- */
  const A = {
    go: a => go(a),
    noop: () => {},
    'open-url': async a => { const r = await safe(() => api.open_url(a)); if (r && !r.ok) toast(t('err.URL_NOT_ALLOWED'), 'warn'); },
    'news-refresh': () => refreshNews(true),
    slide: a => { setSlide(car.idx + +a); startCarousel(); car.idx = Math.max(0, car.idx); },
    'slide-to': a => { setSlide(+a); },
    'profile-menu': (a, el, e) => { if (e && e.target.closest('.menu')) return; menu = menu === 'profile' ? null : 'profile'; renderHead(); },
    'play-menu': () => { menu = menu === 'play' ? null : 'play'; renderDock(); },
    'win-min': () => api.window_minimize(), 'win-max': () => api.window_toggle_maximize(), 'win-close': () => api.window_close(),
    'lang-toggle': async () => { menu = null; await api.set_preference('language', SB.lang === 'fr' ? 'en' : 'fr'); await refresh(); renderChars(); },
    boop: (a, el) => { const s = el.querySelector('.slime'); if (!s) return; s.classList.remove('boop'); void s.getBoundingClientRect(); s.classList.add('boop'); confetti(el, 9); setTimeout(() => s.classList.remove('boop'), 700); },
    detect: async (a, el) => { el.disabled = true; const r = await safe(() => api.autodetect()); el.disabled = false; if (r) { toast(r.found ? t('games.detected', { n: r.found }) : t('games.none_found'), r.found ? 'good' : 'warn'); await refresh(); } },
    pick: async a => { const r = await safe(() => api.select_manual(a)); if (r) { toast(t('games.added', { game: r.game }), 'good'); await refresh(); } },
    forget: async a => { await safe(() => api.forget_game(a)); await refresh(); },
    folder: async a => { const r = await safe(() => api.open_folder(a)); if (r && !r.ok) toast(t('err.folder'), 'warn'); },
    'dock-folder': async () => {
      const tg = target(), realm = isBoth(tg) ? ['SR1', 'SR2'].find(r => !S.games[r].primary) : tg;
      if (!realm || (isBoth(tg) && S.games.SR1.primary && S.games.SR2.primary)) return go('games');
      if (!S.games[realm].primary) return A.pick('folder'); A.folder(realm);
    },
    store: a => api.open_store_page(a),
    play: async (a, el) => {
      const tg = target(), realms = isBoth(tg) ? ['SR1', 'SR2'] : [tg];
      if (realms.some(r => !S.games[r].primary)) { toast(dockStatus().text, 'warn'); return go('games'); }
      return A.launch(tg, el);
    },
    resume: (a, el) => A.launch(a, el, true),
    launch: async (a, el, resume) => {
      menu = null; renderDock();
      if (busy.has(a)) return; busy.add(a);
      let screen = null;
      try {
        const remember = true;                       // the welcome card already said to keep copies: Play is one click
        const game = isBoth(a) ? t('launch.both') : NAMES[a];
        screen = SB.loading.open({ compact: true, title: t('launch.t', { game }), sub: t('launch.sub'), steps: [t('launch.s1')] });
        SB.net.hydrate(screen.el); screen.progress(12, t('launch.s1'));
        const steps = [setTimeout(() => screen.progress(40, a === 'SR1' || a === 'SR2' ? t('launch.s3', { game }) : t('launch.s2')), 600), setTimeout(() => screen.progress(68, t('launch.s3', { game })), 1400)];
        const r = await safe(() => api.launch(a, true, remember, !!resume)); steps.forEach(clearTimeout);
        const close = () => screen.actions(`<button class="btn btn--ghost" data-close>${t('launch.close')}</button>`).onclick = () => screen.close();
        if (!r) { screen.fail(t('launch.fail')); close(); return; }
        const errs = (r.errors || []).map(e => `${NAMES[e.realm] || e.realm}: ${t('err.' + e.error) !== 'err.' + e.error ? t('err.' + e.error) : e.error}`);
        (r.warnings || []).forEach(w => toast(w, 'warn'));
        if ((r.launched || []).length) { screen.done(t('launch.ok')); setTimeout(() => confetti(screen.el.querySelector('.ld-bar'), 26), 200); errs.forEach(e => toast(e, 'bad')); screen.close(2600); }
        else { screen.fail(errs[0] || t('launch.fail')); close(); }
        await refresh();
      } finally { busy.delete(a); }
    },
    'host-start': async () => { menu = null; const r = await safe(() => api.host_start()); if (r && !r.ok) toast(`BridgeHost: ${r.error}`, 'bad'); await refresh(); },
    'host-stop': async () => { menu = null; await safe(() => api.host_stop()); await refresh(); },

    opt: (a, el) => { const k = el.dataset.k, v = el.dataset.v; L.assets[k] = k === 'only_missing' ? v === 'true' : v; renderView(true); },
    tool: async () => { const r = await safe(() => api.choose_tool_folder()); if (r && r.ok) { toast(t('assets.tool_ok'), 'good'); await refresh(); } else if (r && r.error) toast(t('err.' + r.error), 'bad'); },
    'out-choose': async () => { const r = await safe(() => api.choose_output_folder()); if (r && !r.ok && r.error) toast(t('err.' + r.error), 'bad'); await refresh(); },
    extract: async () => {
      const o = L.assets, ram = Number((S.preferences || {}).extract_ram_gb) || 8;
      const r = await safe(() => api.extract_start({ direction: o.direction, only_missing: o.only_missing, kind: o.kind, ram_gb: ram }));
      if (!r) return; if (!r.ok) { toast(t('err.' + r.error) !== 'err.' + r.error ? t('err.' + r.error) : (r.detail || r.error), 'bad'); return; }
      L.assets.job = r.job; renderView(true);
    },
    'extract-cancel': async () => { await safe(() => api.extract_cancel()); },
    'sanct-build': async () => { const r = await safe(() => api.sanctuary_build()); if (!r) return; if (!r.ok) { toast(r.error === 'MISSING_DEPENDENCIES' ? `${t('sanct.need_deps')} ${r.detail || ''}` : (t('err.' + r.error) !== 'err.' + r.error ? t('err.' + r.error) : r.error), 'bad'); return; } L.sanct = await api.sanctuary_state(); renderView(true); },
    'sanct-install': async () => { const r = await safe(() => api.sanctuary_install()); if (!r) return; if (!r.ok) { toast(r.detail || r.error, 'bad'); return; } toast(t('sanct.install_ok'), 'good'); L.sanct = await api.sanctuary_state(); renderView(true); },
    'sanct-remove': async () => { const r = await safe(() => api.sanctuary_remove()); if (r && r.ok) { toast(t('sanct.remove_ok'), 'good'); L.sanct = await api.sanctuary_state(); renderView(true); } },
    'install-cache': async () => { const r = await safe(() => api.extract_install_cache()); if (r) toast(t('assets.installed', { n: r.copied }), r.copied ? 'good' : 'warn'); },

    'manifest-save': async () => { const v = ($('#manifest-url') || {}).value || ''; try { await api.set_preference('largo_manifest_url', v.trim()); toast(t('models.saved'), 'good'); L.models = null; await refresh(); } catch (e) { toast(t('err.BAD_URL'), 'bad'); } },
    'recovery-export': async () => { const r = await safe(() => api.recovery_export()); if (r && r.ok) { L.exported = r.name; toast(t('rec.exported', { name: r.name }), 'good'); } else if (r) toast(r.error || 'error', 'bad'); renderView(true); },
    'models-refresh': async () => { const r = await safe(() => api.models_refresh()); if (r) { L.models = r; if (r.error === 'NO_SOURCE') toast(t('models.no_source'), 'warn'); else if (!r.ok) toast(r.manifest.error || 'error', 'bad'); else toast(t('models.refreshed'), 'good'); renderView(true); } },
    'model-dl': async a => { const r = await safe(() => api.models_download(a === 'all' ? 'all' : [a])); if (r && r.ok) { L.models = await api.models_state(); renderView(true); } else if (r) toast(t('err.' + r.error), 'warn'); },
    'models-cancel': async () => { await safe(() => api.models_cancel()); },
    'model-rm': async a => { const r = await safe(() => api.models_remove(a)); if (r) { L.models = r; renderView(true); } },
    'models-import': async () => { const r = await safe(() => api.models_import_folder()); if (r && r.imported) { L.models = r; toast(t('models.imported', { n: r.imported.length }), 'good'); renderView(true); } else if (r && r.ok === false && !r.cancelled) toast(t('models.import_none'), 'warn'); },

    'mp-fill': a => { const i = $('#mp-addr'); if (i) i.value = a; },
    'mp-star': async a => { const fav = L.mp.servers.favourites.includes(a); await api.mp_remember(a, !fav); L.mp = await api.mp_state(); renderView(true); },
    'mp-join': async () => { const i = $('#mp-addr'), r = await safe(() => api.mp_remember(i.value.trim(), null)); if (r && !r.ok) { L.mp.error = r.error; renderView(true); } else if (r) { toast(t('mp.saved', { a: r.address }), 'good'); L.mp = await api.mp_state(); renderView(true); } },
    'mp-mode': async (a, el) => { L.mp.host.mode = el.dataset.v; renderView(true); },
    'mp-online': async realm => { toast(t('mp.on_working'), 'info'); const r = await safe(() => api.mp_online(realm, 'install')); if (r && r.ok) { toast(t('mp.on_done'), 'good'); L.mp = await api.mp_state(); renderView(true); } else if (r) toast(t('mp.on_err', { e: r.error || '?' }), 'bad'); },
    'mp-save': async () => { const r = await safe(() => api.mp_host_save({ name: $('#mp-name').value, port: $('#mp-port').value, mode: L.mp.host.mode })); if (r) { if (r.ok) { L.mp.host = r.host; toast(t('mp.saved_host'), 'good'); } else toast(r.error, 'bad'); } },

    pref: async (a, el) => { await safe(() => api.set_preference(a, el.dataset.v)); await refresh(); renderChars(); },
    'pref-bool': async (a, el) => { await safe(() => api.set_preference(a, el.getAttribute('aria-checked') !== 'true')); await refresh(); },
    'pref-bool-inv': async (a, el) => { await safe(() => api.set_preference(a, el.getAttribute('aria-checked') === 'true')); await refresh(); },
    'update-check': async () => { L.update = await safe(() => api.update_check()); renderView(true); if (L.update && L.update.ok && !L.update.update) toast(t('upd.uptodate'), 'good'); },
    'update-apply': async () => { if (L.update && L.update.frozen === false) { toast(t('upd.source'), 'warn'); return; } toast(t('upd.busy'), 'info'); const r = await safe(() => api.update_apply()); if (r && r.ok) toast(t('upd.restarting', { v: r.version }), 'good'); else toast(t('upd.err') + (r && r.error ? ' (' + r.error + ')' : ''), 'bad'); },
    'console-set': a => { const [k, v] = a.split(':'); SB.console.state[k] = v; renderView(true); },
    'console-src': a => { SB.console.state.sources[a] = !SB.console.state.sources[a]; renderView(true); },
    'console-pause': () => { SB.console.state.paused = !SB.console.state.paused; renderView(true); },
    'console-clear': () => { SB.console.lines.length = 0; renderView(true); },
    'console-copy': async () => { const text = SB.console.visible().map(l => `[${l.source}] ${l.text}`).join('\n'); try { await navigator.clipboard.writeText(text); } catch (e) { /* clipboard refused */ } toast(t('diag.copied'), 'good'); },
    'diag-run': async () => { L.diag = { busy: true, text: '' }; renderView(true); const r = await safe(() => api.diagnostics()); L.diag = { busy: false, text: r && r.text ? r.text : t('diag.failed') }; renderView(true); },
    'diag-copy': async () => { const text = (L.diag && L.diag.text) || ''; try { await navigator.clipboard.writeText(text); toast(t('diag.copied'), 'good'); } catch (e) { const ta = $('#diag-text'); if (ta) { ta.select(); document.execCommand('copy'); toast(t('diag.copied'), 'good'); } } },
    'game-repair': async a => { toast(t('games.repair_busy'), 'info'); const r = await safe(() => api.game_repair(a)); if (r && r.ok) { toast(t('games.repair_done'), 'good'); await refresh(); } else toast(t('games.repair_err') + (r && r.error ? ' (' + r.error + ')' : ''), 'bad'); },
    'loader-install': async a => { toast(t('games.loader_busy'), 'info'); const r = await safe(() => api.loader_install(a)); if (r && r.ok) { toast(t('games.loader_done'), 'good'); await refresh(); } else toast(t('games.loader_err') + (r && r.error ? ' (' + r.error + ')' : ''), 'bad'); },
    'plugin-install': async a => { const r = await safe(() => api.plugin_install(a)); if (r && r.ok) { toast(t('games.plugin_done'), 'good'); await refresh(); } else toast(t('games.plugin_err.' + (r && r.error ? r.error : 'x')) !== 'games.plugin_err.' + (r && r.error ? r.error : 'x') ? t('games.plugin_err.' + r.error) : t('games.plugin_err.x'), 'bad'); },
    unlink: async a => { const r = await safe(() => api.travel_unlink('SR1', a)); if (r && r.ok) toast(t('opt.links_done'), 'good'); L.links = null; loadLinks(); },
    'opt-bool': async (a, el) => { const [s, k] = a.split('.'); const r = await safe(() => api.set_setting(s, k, el.getAttribute('aria-checked') !== 'true')); if (r && !r.ok) toast(r.error, 'bad'); L.settings = await api.get_settings(); renderView(true); },
    'opt-choice': async (a, el) => { const [s, k] = a.split('.'); const r = await safe(() => api.set_setting(s, k, el.dataset.v)); if (r && !r.ok) toast(r.error, 'bad'); L.settings = await api.get_settings(); renderView(true); },
  };

  document.addEventListener('click', e => {
    const el = e.target.closest('[data-act]');
    if (menu && !e.target.closest('.menu, [data-act="profile-menu"], [data-act="play-menu"]')) { menu = null; renderHead(); renderDock(); }
    if (!el || el.disabled) return;
    const fn = A[el.dataset.act]; if (fn) { e.preventDefault(); fn(el.dataset.arg, el, e); }
  });
  document.addEventListener('input', e => {
    if (e.target.id === 'ram-range' && SB.ramLive) SB.ramLive(Number(e.target.value), S);
  });
  document.addEventListener('change', async e => {
    const el = e.target;
    if (el.id === 'ram-range') { const v = Number(el.value); const r = await safe(() => api.set_preference('extract_ram_gb', v)); if (r && r.ok !== false) S.preferences.extract_ram_gb = v; return; }
    if (el.dataset.change === 'choose') { await safe(() => api.choose_install(el.dataset.realm, el.value)); await refresh(); }
    if (el.dataset.change === 'play-target') { await safe(() => api.set_preference('play_target', el.value)); S.preferences.play_target = el.value; renderDock(); }
    if (el.classList.contains('opt-text')) { const r = await safe(() => api.set_setting(el.dataset.sec, el.dataset.key, el.value)); if (r && !r.ok) toast(r.error, 'bad'); }
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Enter' && e.target.id === 'mp-addr') A['mp-join'](); if (e.key === 'Enter' && e.target.id === 'manifest-url') A['manifest-save']();
    if (e.key === 'Escape' && menu) { menu = null; renderHead(); renderDock(); }
  });
  window.addEventListener('hashchange', () => { const n = location.hash.replace('#/', ''); if (SB.pages[n] && n !== page && S) { page = n; renderHead(); renderView(false); } });

  /* ----- boot ----- */
  async function backendReady() {
    if ((window.pywebview && window.pywebview.api) || SHOT) return;
    await Promise.race([new Promise(r => window.addEventListener('pywebviewready', r, { once: true })), sleep(1800)]);
  }
  async function boot() {
    if (SHOT) document.documentElement.classList.add('shot');
    SB.setLang((navigator.language || 'en').toLowerCase().startsWith('fr') ? 'fr' : 'en');
    $('#bg').innerHTML = SB.Scene.background(); SB.Scene.parallax($('#bg')); SB.Art.trackEyes();
    const splash = SB.loading.open({ id: 'splash', steps: [t('boot.1')] }), started = Date.now();
    await backendReady(); splash.progress(18, t('boot.1'));
    await loadNews(); splash.refreshArt();
    S = await safe(() => api.get_state());
    if (!S) return; splash.progress(38, t('boot.2')); applyPrefs();
    const first = !localStorage.getItem('sb.detected');
    if (!S.installations.length && first) { try { await api.autodetect(); } catch (e) { /* shown on the Games page */ } localStorage.setItem('sb.detected', '1'); S = await api.get_state(); }
    safe(() => api.update_check()).then(u => { if (u && u.ok) { L.update = u; if (u.update) toast(t('upd.available', { v: u.latest }) + ' — ' + t('nav.options'), 'info'); } });
    splash.progress(62, t('boot.3'));
    const initial = location.hash.replace('#/', ''); if (SB.pages[initial]) page = initial;
    splash.progress(78, t('boot.4'));
    renderAll(); applyBackdrop();
    await Promise.race([sleep(2600), Promise.all([SB.net.hydrate($('#view')), SB.net.hydrate($('#chars'))])]);
    if (SHOT && new URLSearchParams(location.search).get('open') === 'splash') { splash.progress(64, t('boot.3')); return; }
    splash.done(t('boot.5'));
    await sleep(Math.max(0, (S.preferences.reduce_motion || SHOT ? 200 : 1800) - (Date.now() - started)));
    if (SHOT) splash.el.remove(); else splash.close();
    const openParam = new URLSearchParams(location.search).get('open');
    if (SHOT && openParam === 'legal') SB.showLegal();
    if (SHOT && openParam === 'launch') A.launch('BOTH_SR1');
    if (SHOT && openParam === 'launching') { const sc = SB.loading.open({ compact: true, title: t('launch.t', { game: NAMES.SR2 }), sub: t('launch.sub') }); sc.progress(68, t('launch.s3', { game: NAMES.SR2 })); sc.el.querySelector('.ld-tip').textContent = SB.tips()[1]; }
    if (SHOT && openParam === 'menu') { menu = 'profile'; renderHead(); }
    if (SHOT && openParam === 'playmenu') { menu = 'play'; renderDock(); }
    if (!S.legal_understood && !SHOT) { await SB.showLegal(); await api.acknowledge_legal(); S.legal_understood = true; }
    refreshNews(false);            // official news + artwork, at every start
  }
  /* ----- portal crossings: a plugin asked BridgeHost to cross, the launcher starts the other game ----- */
  const seenCrossings = new Set(), askedLink = new Set();
  /* The portal was entered with a save that is not linked to a save of the other game yet: the player chooses which one it is. */
  async function askLink(c) {
    const game = NAMES[c.to] || c.to || '?', rows = c.candidates || [];
    const list = rows.length ? rows.map((x, i) => `<label class="row link-row" style="gap:10px;cursor:${x.linked ? 'not-allowed' : 'pointer'};opacity:${x.linked ? .55 : 1}"><input type="radio" name="link-save" value="${esc(x.group)}" ${x.linked ? 'disabled' : ''} ${(x.open && !x.linked) || (!rows.some(y => y.open && !y.linked) && i === 0 && !x.linked) ? 'checked' : ''} style="width:20px;height:20px"><span><b>${x.new ? esc(t('cross.link_new', { game })) : esc(x.label || x.group)}</b>${x.open ? ` <span class="pill good">${t('cross.link_open')}</span>` : ''}${x.linked ? ` <span class="pill muted">${t('cross.link_taken')}</span>` : ''}</span></label>`).join('') : `<p class="err">${t('cross.link_none', { game })}</p>`;
    const res = await modal({ title: t('cross.link_t', { game }), art: `<div class="art-real">${SB.cutout(/pink/i, 'pink', 92, { cls: 'hop' })}</div>`, body: `<p class="muted">${t('cross.link_b', { game })}</p><div class="stack small-gap" style="max-height:260px;overflow:auto">${list}</div><p class="faint small">${t('cross.link_note', { game })}</p>`, buttons: [{ label: t('common.cancel'), cls: 'btn--ghost', value: false }, { label: t('cross.link_go'), cls: 'btn--bridge', value: true }], collect: root => { const r = root.querySelector('input[name="link-save"]:checked'); return r ? r.value : null; } });
    const group = res && res.value ? res.data : null;
    await api.travel_link(c.id, group || null).catch(() => null);
  }
  async function watchCrossings() {
    if (!S || !S.host || S.host.state !== 'RUNNING') return;
    const r = await api.crossings_poll().catch(() => null); if (!r || !r.ok) return;
    for (const c of r.crossings) {
      if (c.state === 'NEEDS_LINK' && !askedLink.has(c.id)) { askedLink.add(c.id); askLink(c); continue; }
      const key = c.request_id + ':' + c.state;
      if (seenCrossings.has(key) || !['COMPLETE', 'ROLLED_BACK', 'FAILED', 'CANCELLED'].includes(c.state)) continue; seenCrossings.add(key);
      const game = NAMES[c.to] || c.to || '?';
      if (c.state === 'COMPLETE') toast(t('cross.ok'), 'good');
      else if (c.code === 'NEEDS_BACKUP_CONFIRMATION') toast(t('cross.backup'), 'warn');
      else if (c.state === 'ROLLED_BACK' || c.state === 'FAILED') toast(t('cross.' + String(c.code || '').toLowerCase(), { game }) !== 'cross.' + String(c.code || '').toLowerCase() ? t('cross.' + String(c.code).toLowerCase(), { game }) : t('cross.failed', { game, code: c.code || '' }), 'bad');
    }
  }
  setInterval(watchCrossings, 2500);

  /* ----- live console of both games (Console page) ----- */
  SB.console = {
    lines: [], state: { filter: 'all', paused: false, sources: { SR1: true, SR2: true, LAUNCHER: true } },
    visible() { const s = this.state; return this.lines.filter(l => s.sources[l.source] && (s.filter === 'all' || (s.filter === 'error' ? l.kind === 'error' : l.kind !== 'game'))); },
    row(l) { return `<div class="cl ${l.kind} src-${l.source.toLowerCase()}"><b>${l.source}</b>${esc(l.text)}</div>`; },
    html() { const v = this.visible(); return v.length ? v.slice(-1500).map(l => this.row(l)).join('') : `<p class="muted console-empty">${t('console.empty')}</p>`; },
  };
  async function pollConsole() {
    if (page !== 'debug' || SB.console.state.paused) return;
    const r = await api.console_poll().catch(() => null);
    if (!r || !r.ok || !r.lines.length) return;
    SB.console.lines.push(...r.lines); if (SB.console.lines.length > 4000) SB.console.lines.splice(0, SB.console.lines.length - 4000);
    const box = $('#console-log'); if (!box) return;
    const atEnd = box.scrollTop + box.clientHeight >= box.scrollHeight - 8;
    box.innerHTML = SB.console.html();
    if (atEnd) box.scrollTop = box.scrollHeight;
  }
  setInterval(pollConsole, 1000);
  document.addEventListener('DOMContentLoaded', boot);
  SB.app = { refresh, go, get state() { return S; } };
})(window.SB = window.SB || {});
