/* Application core: boot + splash, shell (header / sidebar / dock), router, official news + artwork, actions, polling. */
(function (SB) {
  'use strict';
  const { $, $$, esc, toast, modal, sleep, confetti } = SB.ui, { slime, icon, glyph } = SB.Art, t = (...a) => SB.t(...a), api = SB.api;
  const L = SB.local = SB.local || {};
  SB.errors = [];
  window.addEventListener('error', e => SB.errors.push(String(e.message)));
  window.addEventListener('unhandledrejection', e => SB.errors.push(String(e.reason && e.reason.message || e.reason)));
  // Five tabs; the other pages open from a tab's sub-tabs or from the header (light bulb: tutorial, gear: settings).
  const NAV = ['home', 'news', 'games', 'mods', 'multiplayer'];   // 'recovery' lives in 'debug' now
  const SUBNAV = {
    games: ['games', 'profiles'], profiles: ['games', 'profiles'],
    mods: ['mods', 'models'], models: ['mods', 'models'],
    options: ['options', 'debug', 'about'], debug: ['options', 'debug', 'about'], about: ['options', 'debug', 'about'], recovery: ['options', 'debug', 'about'],
  };
  const SECTION = { profiles: 'games', models: 'mods' };
  const subLabel = k => t(k === 'games' ? 'sub.games' : k === 'debug' ? 'sub.debug' : 'nav.' + k);
  function subnav(name) {
    const tabs = SUBNAV[name];
    if (!tabs) return '';
    const here = name === 'recovery' ? 'debug' : name;
    return `<div class="subnav" role="tablist">${tabs.map(k => `<button role="tab" class="subtab" data-act="go" data-arg="${k}" aria-selected="${k === here}">${glyph(k === 'debug' ? 'recovery' : k === 'about' ? 'heart' : k, 'gl')} ${subLabel(k)}</button>`).join('')}</div>`;
  }
  SB.subnav = subnav;
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
      <button class="head-ico ${page === 'tutorial' ? 'on' : ''}" data-act="go" data-arg="tutorial" aria-label="${esc(t('head.tuto'))}" title="${esc(t('head.tuto'))}">${glyph('bulb')}</button>
      <button class="head-ico ${SUBNAV.options.includes(page) ? 'on' : ''}" data-act="go" data-arg="options" aria-label="${esc(t('nav.options'))}" title="${esc(t('nav.options'))}">${glyph('options')}</button>
      <div class="profile" data-act="profile-menu" role="button" tabindex="0" aria-haspopup="true">
        <span class="avatar"><img src="img/play-sr1.png" alt=""></span>
        <span class="who"><b>${t('profile.name')}</b><small><i class="dot ${on ? 'on' : ''}"></i>${on ? t('profile.on') : t('profile.off')}</small></span>${glyph('chevD', 'chev')}
        ${menu === 'profile' ? `<div class="menu" role="menu"><button data-act="${on ? 'host-stop' : 'host-start'}">${glyph(on ? 'stop' : 'play', 'gl mi')} ${on ? t('menu.host_stop') : t('menu.host_start')}</button><hr>
          <button data-act="lang-toggle">${glyph('news', 'gl mi')} ${SB.lang === 'fr' ? 'English' : 'Français'}</button><button data-act="go" data-arg="options">${glyph('options', 'gl mi')} ${t('nav.options')}</button><button data-act="go" data-arg="profiles">${glyph('profiles', 'gl mi')} ${t('nav.profiles')}</button><button data-act="go" data-arg="debug">${glyph('recovery', 'gl mi')} ${t('sub.debug')}</button><button data-act="go" data-arg="about">${glyph('heart', 'gl mi')} ${t('nav.about')}</button></div>` : ''}
      </div>
      ${frameless ? `<div class="winctl"><button data-act="win-min" aria-label="Minimize">${glyph('min')}</button><button data-act="win-max" aria-label="Maximize">${glyph('max')}</button><button class="x" data-act="win-close" aria-label="Close">${glyph('close')}</button></div>` : ''}`;
    SB.net.hydrate($('#head'));
  }
  function renderSide() {
    const side = $('#side'), top = side.querySelector('.nav')?.scrollTop || 0;
    side.innerHTML = `<nav class="nav" tabindex="0" aria-label="${t('nav.navigation')}">${NAV.map(k => `<button class="nav-item" data-act="go" data-arg="${k}" ${!S ? 'disabled aria-disabled="true"' : ''} ${k === (SECTION[page] || page) ? 'aria-current="page"' : ''}>${glyph(k === 'assets' ? 'extract' : k === 'debug' ? 'recovery' : k === 'credits' ? 'star' : k)}<span>${t('nav.' + k)}</span></button>`).join('')}</nav>
      <div class="side-art" aria-hidden="true"><img src="img/pink-cotton-artist.png" alt=""></div>`;
    side.querySelector('.nav').scrollTop = top;
  }
  function renderChars() {
    // Keep artist art inside the sidebar (.side-art); no floating procedural slimes behind the header controls.
    $('#chars').innerHTML = '';
    $('#chars-back').innerHTML = '<span class="ch-anchor" aria-hidden="true"></span>';
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
    const realms = isBoth(tg) ? ['SR1', 'SR2'] : [tg];
    const ready = st.tone !== 'bad', stop = realms.some(r => g[r].running);
    const closing = stop && realms.every(r => !g[r].running || g[r].closing);
    const unconfirmed = realms.some(r => g[r].running && g[r].close_unconfirmed);
    const label = t(unconfirmed ? 'games.close_unconfirmed' : closing ? 'games.stopping' : stop ? 'games.stop' : 'dock.play');
    $('#dock').innerHTML = `<div class="dock-row">
      <div class="dock-card"><label for="dock-game">${t('dock.game')}</label><select id="dock-game" class="dock-select" data-change="play-target">${[['SR1', NAMES.SR1], ['SR2', NAMES.SR2], ['BOTH_SR1', t('dock.both_sr1')], ['BOTH_SR2', t('dock.both_sr2')]].map(([v, label]) => `<option value="${v}" ${tg === v ? 'selected' : ''}>${label}</option>`).join('')}</select></div>

      <div class="play ${stop ? 'stop' : ready ? '' : 'off'}" ${unconfirmed ? `title="${esc(t('err.SESSION_CLOSE_HOST_LOST'))}"` : ''}><button class="go" data-act="${stop ? 'game-stop' : 'play'}" data-arg="${isBoth(tg) ? 'BOTH' : tg}" aria-label="${label}" ${closing || unconfirmed ? 'disabled' : ''}>${glyph(stop ? 'stop' : 'play')}<span>${label}</span></button><button class="more" data-act="play-menu" aria-label="More" aria-haspopup="true">${glyph('chevD')}</button>
        ${menu === 'play' ? `<div class="menu" role="menu"><button data-act="launch" data-arg="BOTH_SR1">${glyph('play', 'gl mi')} ${t('dock.both_sr1')}</button><button data-act="launch" data-arg="BOTH_SR2">${glyph('play', 'gl mi')} ${t('dock.both_sr2')}</button><button data-act="launch" data-arg="SR1">${glyph('play', 'gl mi')} ${NAMES.SR1}</button><button data-act="launch" data-arg="SR2">${glyph('play', 'gl mi')} ${NAMES.SR2}</button><hr><button data-act="go" data-arg="games">${glyph('folder', 'gl mi')} ${t('nav.games')}</button></div>` : ''}</div></div>
      <div class="dock-status"><span class="ok"><i class="tick ${st.tone === 'good' ? '' : st.tone}">${glyph(st.tone === 'good' ? 'check' : 'close')}</i><span class="t" title="${esc(st.text)}">${esc(st.text)}</span></span>
        ${running || dling ? `<div class="bar" style="--p:${(Math.max(0, Math.min(1, prog || 0)) * 100).toFixed(1)}%"><i></i></div><span>${work}</span>` : ''}<span>SlimeBridge ${esc(S.version)}</span></div>`;
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
    if (page === 'assets') page = 'games';                 // Extract is part of the Setup page now
    const root = $('#view'), top = root.scrollTop, def = SB.pages[page];
    const nameInput = keep && page === 'multiplayer' ? $('#mp-name') : null;
    const nameFocused = nameInput && nameInput === document.activeElement;
    if (nameInput) L.mpName = nameInput.value;
    let html = def.render(S);
    if (page === 'games' && SB.pages.assets) html += `<div id="extract">${SB.pages.assets.render(S)}</div>`;   // games + extraction, one page
    if (page === 'about' && SB.pages.credits) html += SB.pages.credits.render(S);      // the credits live in About
    const tabs = subnav(page);
    if (tabs) html = html.replace(/^(\s*<(?:div|section) class="page[^"]*">)/, '$1' + tabs);

    const codeInput = keep && page === 'multiplayer' ? $('#mp-join-code') : null;
    const codeFocused = codeInput && codeInput === document.activeElement;
    const codeValue = codeInput && codeInput.value;
    if (keep) redraw(root, html); else root.innerHTML = html;
    if (nameInput) {
      const replacement = $('#mp-name');
      replacement.value = L.mpName;
      if (nameFocused) {
        replacement.focus({ preventScroll: true });
        replacement.setSelectionRange?.(nameInput.selectionStart, nameInput.selectionEnd, nameInput.selectionDirection);
      }
    }
    if (codeInput) {
      const replacement = $('#mp-join-code');
      replacement.value = codeValue;
      if (codeFocused) {
        replacement.focus({ preventScroll: true });
        if (replacement.setSelectionRange) replacement.setSelectionRange(codeInput.selectionStart, codeInput.selectionEnd, codeInput.selectionDirection);
      }
    }
    root.scrollTop = keep ? top : 0;
    SB.net.hydrate(root);
    renderSide(); renderDock();
    if ((page === 'options' || page === 'tutorial') && !L.settings) loadSettings();
    if (page === 'options' && !L.links) loadLinks();
    if (page === 'models' && !L.models) loadModels();
    if (page === 'profiles' && !L.profiles) loadProfiles();
    if ((page === 'recovery' || page === 'debug') && !L.recovery) loadRecovery();
    if (page === 'mods' && !(L.mods && L.mods.started)) { L.mods = L.mods || { realm: 'SR2', query: '', results: null, installed: {}, busy: false }; L.mods.started = true; loadModsInstalled(); A['mods-search'](); }
    if (page === 'multiplayer' && !L.mp) loadMp();
    if (page === 'multiplayer' && !mpTimer && !SHOT) followSession();
    if (page === 'games' && !L.assets.job) pollJob(true);
    if (page === 'games' && !L.sanct) loadSanct();
    schedulePoll(); startCarousel();
  }
  function renderAll() { renderHead(); renderSide(); renderRibbon(); renderChars(); renderView(true); }
  function validState(value) {
    const object = x => !!x && typeof x === 'object' && !Array.isArray(x);
    return object(value) && object(value.preferences) && object(value.host) && typeof value.host.state === 'string' &&
      Array.isArray(value.installations) && object(value.games) && object(value.games.SR1) && object(value.games.SR2);
  }
  async function refresh(keep = true) {
    S = await safe(async () => { const state = await api.get_state(); if (!validState(state)) throw new Error('Launcher state unavailable'); return state; }, S);
    if (S) { applyPrefs(); renderHead(); renderRibbon(); renderView(keep); }
  }
  async function loadLinks() { const r = await safe(() => api.travel_links()); L.links = r && r.ok ? r.links : []; if (page === 'options') renderView(true); }
  async function loadSettings() { const r = await safe(() => api.get_settings()); if (r) { L.settings = r; if (page === 'options' || page === 'tutorial') renderView(true); } }
  async function loadProfiles() { const r = await safe(() => api.profiles_state()); if (r) { L.profiles = r; if (page === 'profiles') renderView(true); } }
  async function loadModsInstalled() { const M = L.mods; if (!M) return; const realm = M.realm; const r = await safe(() => api.mods_installed(realm)); if (r && L.mods === M) { M.installed[realm] = r.ok ? r : { mods: [], pending: null }; if (page === 'mods' && M.realm === realm) renderView(true); } }
  async function loadModThumbs() { const M = L.mods; if (!M || !M.results) return; const todo = M.results.filter(m => m.thumbnail && !m.thumb); if (!todo.length) return;
    const data = await api.mods_thumbs(todo.map(m => m.thumbnail)).catch(() => []); todo.forEach((m, i) => { m.thumb = data[i] || null; }); if (page === 'mods') renderView(true); }
  function watchModDownload() { const tick = async () => { await loadModsInstalled(); const p = L.mods.installed[L.mods.realm] && L.mods.installed[L.mods.realm].pending;
      if (p && p.state === 'INSTALLED') { toast(t('mods.done', { name: p.name || '' }), 'good'); return; } if (p && (p.state === 'FAILED' || p.state === 'TIMEOUT')) { toast(t('mods.err') + ' (' + (p.error || p.state) + ')', 'bad'); return; } setTimeout(tick, 2500); }; setTimeout(tick, 2500); }
  async function loadRecovery() { const r = await safe(() => api.recovery_state()); if (r) { L.recovery = r; if (page === 'recovery' || page === 'debug') renderView(true); } }
  async function loadModels() {
    let r = await safe(() => api.models_state());
    // the author's largos are published with SlimeBridge: read their list on the first visit, so "Download all" is one click
    if (r && !(r.manifest && r.manifest.loaded) && !L.modelsAuto) { L.modelsAuto = true; const f = await safe(() => api.models_refresh()); if (f && f.rows) r = f; }
    if (r) { L.models = r; if (page === 'models') renderView(true); }
  }
  async function loadSanct() { const r = await safe(() => api.sanctuary_state()); if (r) { L.sanct = r; if (page === 'games') renderView(true); } }
  async function loadMp() { const r = await safe(() => api.mp_state()); if (r) { L.mp = r; if (!L.mpSession && !L.mpSubmitting) L.mpSession = r.session; if (page === 'multiplayer') renderView(true); } }
  // The shared online code changes in the games (Auto Host, lobby): follow it while the Multi page is open.
  let mpTimer = 0, mpRevision = 0, mpInFlight = false;
  function followSession() {
    clearInterval(mpTimer);
    mpTimer = setInterval(async () => {
      if (page !== 'multiplayer') { clearInterval(mpTimer); mpTimer = 0; return; }
      if (L.mpSubmitting || mpInFlight) return;
      const revision = mpRevision;
      mpInFlight = true;
      try {
        const r = await api.mp_session().catch(() => null);
        if (page !== 'multiplayer' || revision !== mpRevision || !r || !r.ok) return;
        if (JSON.stringify(r) !== JSON.stringify(L.mpSession)) { L.mpSession = r; renderView(true); }
      } finally { mpInFlight = false; }
    }, 1500);
  }
  function go(name) { if (!S || !SB.pages[name]) return; menu = null; page = name; location.hash = '#/' + name; renderHead(); renderView(false); }

  /* ----- carousel ----- */
  const car = { idx: 0, timer: 0 };
  function setSlide(i) {
    const el = $('#carousel'); if (!el) return; const n = +el.dataset.count; car.idx = (i + n) % n;
    $$('.slide', el).forEach((s, k) => s.classList.toggle('on', k === car.idx)); $$('.dots button', el).forEach((d, k) => d.classList.toggle('on', k === car.idx));
  }
  function startCarousel() {
    clearInterval(car.timer);
    if (page !== 'home' || document.documentElement.classList.contains('reduce-motion')) return;
    const el = $('#carousel'); if (!el || !+el.dataset.count) return;
    setSlide(car.idx);
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
  let newsRefreshFinished = false, newsRefreshAttempt = 0;
  async function loadNews() {
    const cached = await api.news_cached().catch(() => null);
    if (cached && !SB.NEWS) {
      SB.NEWS = cached;
      SB.newsLoading = !newsRefreshFinished && !(cached.items && cached.items.length);
    }
    return cached;
  }
  async function refreshNews(manual) {
    const attempt = ++newsRefreshAttempt;
    newsRefreshFinished = false;
    SB.newsLoading = true; if (manual && (page === 'home' || page === 'news')) renderView(true);
    const fresh = await api.news_refresh().catch(() => null);
    if (attempt !== newsRefreshAttempt) return;
    newsRefreshFinished = true;
    SB.newsLoading = false;
    if (fresh) {
      SB.NEWS = fresh; await applyBackdrop(); renderChars();
      if (page === 'home' || page === 'news') renderView(true);
      if (fresh.offline) toast(t('news.offline'), 'warn'); else if (manual) toast(t('news.refreshed'), 'good');
    } else if (page === 'home' || page === 'news') renderView(true);
  }

  /* ----- polling for long jobs (extraction, downloads) ----- */
  /** The online name the player typed (kept for next time); SRMP only ever uses a name the player chose. */
  function mpName() {
    const name = (($('#mp-name') || {}).value || '').trim();
    L.mpName = name;
    try { localStorage.setItem('sb.mpName', name); } catch (e) { /* storage refused */ }
    return name;
  }
  function schedulePoll() {
    clearInterval(pollTimer);
    const job = L.assets.job, dl = L.models && L.models.download;
    const sj = L.sanct && L.sanct.job, need = (job && ['RUNNING', 'QUEUED'].includes(job.status)) || (dl && ['RUNNING', 'QUEUED'].includes(dl.status)) || (sj && ['RUNNING', 'QUEUED'].includes(sj.status));
    if (need) pollTimer = setInterval(pollTick, 650);
  }
  async function pollJob(silent) {
    const r = await api.extract_status().catch(() => null);
    if (r && r.job && (!L.assets.job || r.job.id !== L.assets.job.id || silent)) { L.assets.job = r.job; if (page === 'games') renderView(true); renderDock(); schedulePoll(); }
  }
  async function pollTick() {
    const job0 = L.assets.job;
    if (job0 && ['RUNNING', 'QUEUED'].includes(job0.status)) {
      const r = await api.extract_status().catch(() => null);
      if (r && r.job) {
        const was = job0.status; L.assets.job = r.job; renderDock();
        const box = $('#jobbox'); if (box) redraw(box, SB.jobBox(r.job));
        if (was !== r.job.status && !['RUNNING', 'QUEUED'].includes(r.job.status)) { if (page === 'games') renderView(true); if (r.job.status === 'DONE') { toast(t('assets.done'), 'good'); confetti($('.play') || document.body, 26); } }
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
        if (r.job && !['RUNNING', 'QUEUED'].includes(r.job.status)) { if (page === 'games') renderView(true); toast(r.job.status === 'DONE' ? t('sanct.done') : (r.job.error || t('sanct.failed')), r.job.status === 'DONE' ? 'good' : 'bad'); if (r.job.status === 'DONE') confetti($('.sanct') || document.body, 24); }
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
    slide: a => { setSlide(car.idx + +a); startCarousel(); },
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
      return runLaunch(tg, el, false);
    },
    resume: (a, el) => runLaunch(a, el, true),
    // Never read the 3rd arg of data-act click handlers as `resume`: the click dispatcher passes the DOM Event there.
    launch: (a, el) => runLaunch(a, el, false),
    'game-stop': async a => {
      if (busy.has('game-stop')) return;
      const game = a === 'BOTH' ? t('dock.two') : NAMES[a];
      if (!game || !window.confirm(t('games.stop_confirm', { game }))) return;
      busy.add('game-stop');
      try {
        const result = await safe(() => api.game_stop(a));
        if (result) {
          if (result.error) toast(t('err.' + result.error), 'warn');
          (result.errors || []).forEach(e => toast(`${NAMES[e.realm]}: ${t('err.' + e.error)}`, 'bad'));
          (result.games || []).filter(g => g.stopping).forEach(g => toast(`${NAMES[g.realm]}: ${t('games.stopping')}`, 'good'));
        }
        await refresh();
      } finally { busy.delete('game-stop'); }
    },
    'host-start': async () => { menu = null; const r = await safe(() => api.host_start()); if (r && !r.ok) toast(`BridgeHost: ${r.error}`, 'bad'); await refresh(); },
    'host-stop': async () => { menu = null; const r = await safe(() => api.host_stop()); if (r?.ok === false) toast(t('err.' + r.error), 'warn'); await refresh(); },

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

    'nexus-key-save': async () => { const v = ($('#nexus-key') || {}).value || ''; await safe(() => api.set_preference('nexus_api_key', v.trim() || null)); toast(t('opt.saved'), 'good'); await refresh(); },
    'manifest-save': async () => { const v = ($('#manifest-url') || {}).value || ''; try { await api.set_preference('largo_manifest_url', v.trim()); toast(t('models.saved'), 'good'); L.models = null; await refresh(); } catch (e) { toast(t('err.BAD_URL'), 'bad'); } },
    'recovery-export': async () => { const r = await safe(() => api.recovery_export()); if (r && r.ok) { L.exported = r.name; toast(t('rec.exported', { name: r.name }), 'good'); } else if (r) toast(r.error || 'error', 'bad'); renderView(true); },
    'models-refresh': async () => { const r = await safe(() => api.models_refresh()); if (r) { L.models = r; if (r.error === 'NO_SOURCE') toast(t('models.no_source'), 'warn'); else if (!r.ok) toast(r.manifest.error || 'error', 'bad'); else toast(t('models.refreshed'), 'good'); renderView(true); } },
    'model-dl': async a => { const r = await safe(() => api.models_download(a === 'all' ? 'all' : [a])); if (r && r.ok) { L.models = await api.models_state(); renderView(true); } else if (r) toast(t('err.' + r.error), 'warn'); },
    'models-cancel': async () => { await safe(() => api.models_cancel()); },
    'model-rm': async a => { const r = await safe(() => api.models_remove(a)); if (r) { L.models = r; renderView(true); } },
    'models-import': async () => { const r = await safe(() => api.models_import_folder()); if (r && r.imported) { L.models = r; toast(t('models.imported', { n: r.imported.length }), 'good'); renderView(true); } else if (r && r.ok === false && !r.cancelled) toast(t('models.import_none'), 'warn'); },

    'mp-online': async realm => { toast(t('mp.on_working'), 'info'); const r = await safe(() => api.mp_online(realm, 'install')); if (r && r.ok) { toast(t('mp.on_done'), 'good'); L.mp = await api.mp_state(); renderView(true); } else if (r) toast(t('mp.on_err', { e: r.error || '?' }), 'bad'); },

    pref: async (a, el) => { await safe(() => api.set_preference(a, el.dataset.v)); await refresh(); renderChars(); },
    'pref-bool': async (a, el) => { await safe(() => api.set_preference(a, el.getAttribute('aria-checked') !== 'true')); await refresh(); },
    'pref-bool-inv': async (a, el) => { await safe(() => api.set_preference(a, el.getAttribute('aria-checked') === 'true')); await refresh(); },
    'update-check': async () => { L.update = await safe(() => api.update_check()); renderView(true); if (L.update && L.update.ok && !L.update.update) toast(t('upd.uptodate'), 'good'); },
    'update-apply': async () => {
      if (L.updateBusy) return;
      if (L.update && L.update.frozen === false) { toast(t('upd.source'), 'warn'); return; }
      const attempt = L.updateAttempt = (L.updateAttempt || 0) + 1;
      L.updateBusy = true; L.updateProgress = { phase: 'checking', done: 0, total: 0 }; renderView(true);
      let polling = false;
      const timer = setInterval(async () => {
        if (polling || !L.updateBusy || L.updateAttempt !== attempt) return;
        polling = true;
        try {
          const status = await api.update_status();
          if (L.updateBusy && L.updateAttempt === attempt && status && status.ok) {
            L.updateProgress = status;
            // Preserve the page, focus and entry animation while only the byte counter changes.
            const label = $('#update-progress');
            if (label) label.textContent = t('upd.phase.' + status.phase) + (status.phase === 'download'
              ? ` · ${(status.done / 1048576).toFixed(1)} MiB${status.total > 0 ? ` / ${(status.total / 1048576).toFixed(1)} MiB` : ''}` : '');
          }
        } catch (_) { /* a closing backend is not a new update failure */ }
        finally { polling = false; }
      }, 400);
      try {
        const r = await safe(() => api.update_apply());
        if (r && r.ok) { toast(t('upd.restarting', { v: r.version }), 'good'); await safe(() => api.update_restart_ack()); }
        else toast(t('upd.err') + (r && r.error ? ' (' + r.error + ')' : ''), 'bad');
      } finally { clearInterval(timer); L.updateBusy = false; L.updateProgress = null; if (page === 'options') renderView(true); }
    },
    'mods-realm': a => { SB.local.mods.realm = a; SB.local.mods.results = null; renderView(true); loadModsInstalled(); A['mods-search'](); },
    'mods-search': async () => { const M = SB.local.mods, q = $('#mods-q'); if (q) M.query = q.value; const realm = M.realm, epoch = M.searchEpoch = (M.searchEpoch || 0) + 1; M.busy = true; renderView(true);
      const r = await safe(() => api.mods_search(realm, M.query, 0)); if (SB.local.mods !== M || M.realm !== realm || M.searchEpoch !== epoch) return;
      M.busy = false; M.results = r && r.ok ? r.mods : []; if (r && !r.ok) toast(t('mods.err') + ' (' + r.error + ')', 'bad'); renderView(true); loadModThumbs(); },
    'mod-install': async a => { const M = SB.local.mods, mod = (M.results || []).find(m => String(m.id) === String(a)); if (!mod) return;
      const r = await safe(() => api.mods_install(M.realm, mod)); if (!r) return;
      if (r.ok && r.installed) { toast(t('mods.done', { name: mod.name }), 'good'); loadModsInstalled(); }
      else if (r.ok && r.waiting) { toast(t('mods.waiting', { name: mod.name }), 'info'); loadModsInstalled(); watchModDownload(); }
      else toast(t('mods.err') + ' (' + r.error + ')', 'bad'); },
    'mod-remove': async a => { const M = SB.local.mods; const r = await safe(() => api.mods_remove(M.realm, a)); if (r && r.ok) toast(t('mods.removed'), 'good'); else if (r) toast(t('mods.err') + ' (' + r.error + ')', 'bad'); loadModsInstalled(); },
    'mod-file': async () => { const M = SB.local.mods; const r = await safe(() => api.mods_install_file(M.realm)); if (r && r.ok) { toast(t('mods.done', { name: r.installed.name }), 'good'); loadModsInstalled(); } else if (r && r.error !== 'CANCELLED') toast(t('mods.err') + ' (' + r.error + ')', 'bad'); },
    'console-set': a => { const [k, v] = a.split(':'); SB.console.state[k] = v; renderView(true); },
    'console-src': a => { SB.console.state.sources[a] = !SB.console.state.sources[a]; renderView(true); },
    'console-pause': () => { SB.console.state.paused = !SB.console.state.paused; renderView(true); },
    'console-clear': () => { SB.console.lines.length = 0; renderView(true); },
    'mp-mode': (a, el) => { L.mpMode = el.dataset.v; renderView(true); },
    'mp-host': a => submitOnline(() => api.mp_host(a || 'BOTH', mpName())),
    'mp-join': async () => {
      const code = (($('#mp-join-code') || {}).value || '').trim();
      await submitOnline(() => api.mp_join(code, mpName()));
    },
    'mp-copy': async a => { try { await navigator.clipboard.writeText(a); } catch (e) { const el = document.createElement('textarea'); el.value = a; document.body.appendChild(el); el.select(); document.execCommand('copy'); el.remove(); } toast(t('mp.copied'), 'good'); },
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

  async function runLaunch(a, el, resume) {
    menu = null; renderDock();
    if (busy.has(a)) return; busy.add(a);
    let screen = null;
    try {
      const remember = true;                         // the welcome card already said to keep copies: Play is one click
      const game = isBoth(a) ? t('launch.both') : NAMES[a];
      screen = SB.loading.open({ compact: true, title: t('launch.t', { game }), sub: t('launch.sub'), steps: [t('launch.s1')] });
      SB.net.hydrate(screen.el); screen.progress(12, t('launch.s1'));
      const steps = [setTimeout(() => screen.progress(40, a === 'SR1' || a === 'SR2' ? t('launch.s3', { game }) : t('launch.s2')), 600), setTimeout(() => screen.progress(68, t('launch.s3', { game })), 1400)];
      const r = await safe(() => api.launch(a, true, remember, resume === true)); steps.forEach(clearTimeout);
      const close = () => screen.actions(`<button class="btn btn--ghost" data-close>${t('launch.close')}</button>`).onclick = () => screen.close();
      if (!r) { screen.fail(t('launch.fail')); close(); return; }
      const errs = (r.errors || []).map(e => `${NAMES[e.realm] || e.realm}: ${t('err.' + e.error) !== 'err.' + e.error ? t('err.' + e.error) : e.error}`);
      (r.warnings || []).forEach(w => toast(w, 'warn'));
      if ((r.launched || []).length) { screen.done(t('launch.ok')); setTimeout(() => confetti(screen.el.querySelector('.ld-bar'), 200), 200); errs.forEach(e => toast(e, 'bad')); screen.close(900); }
      else { screen.fail(errs[0] || t('launch.fail')); close(); }
      await refresh();
    } finally { busy.delete(a); }
  }

  async function submitOnline(call) {
    const current = L.mpSession || (L.mp && L.mp.session);
    if (L.mpSubmitting || (current && current.request && !current.request.done)) return;
    L.mpSubmitting = true;
    ++mpRevision;
    try {
      const r = await safe(call);
      if (r && !r.ok) {
        const key = { BAD_CODE: 'mp.launcher_bad_code', BAD_NAME: 'mp.bad_name', SR2_JOIN_IN_GAME: 'mp.sr2_join_in_game' }[r.error];
        toast(key ? t(key) : (r.error || t('mp.step_failed', { error: '?' })), 'bad');
      }
      if (r && r.ok) {
        L.mpSession = { ...(L.mpSession || current || {}), request: r.request };
        if (page === 'multiplayer') { renderView(true); followSession(); }
        if (r.launched) await refresh();
      }
    } finally { L.mpSubmitting = false; }
  }

  document.addEventListener('click', e => {
    const el = e.target.closest('[data-act]');
    if (menu && !e.target.closest('.menu, [data-act="profile-menu"], [data-act="play-menu"]')) { menu = null; renderHead(); renderDock(); }
    if (!el || el.disabled) return;
    if (!S && !['win-min', 'win-max', 'win-close', 'open-url'].includes(el.dataset.act)) return;
    const fn = A[el.dataset.act]; if (fn) { e.preventDefault(); fn(el.dataset.arg, el, e); }
  });
  document.addEventListener('input', e => {
    if (e.target.id === 'ram-range' && SB.ramLive) SB.ramLive(Number(e.target.value), S);
  });
  document.addEventListener('change', async e => {
    const el = e.target;
    if (el.id === 'ram-range') { const v = Number(el.value); const r = await safe(() => api.set_preference('extract_ram_gb', v)); if (r && r.ok !== false) S.preferences.extract_ram_gb = v; return; }
    if (el.dataset.change === 'choose') { await safe(() => api.choose_install(el.dataset.realm, el.value)); await refresh(); }
    if (el.dataset.enter) { /* handled on keydown */ }
    if (el.dataset.change === 'play-target') { await safe(() => api.set_preference('play_target', el.value)); S.preferences.play_target = el.value; renderDock(); }
    if (el.classList.contains('opt-text')) { const r = await safe(() => api.set_setting(el.dataset.sec, el.dataset.key, el.value)); if (r && !r.ok) toast(r.error, 'bad'); }
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Enter' && e.target.id === 'mp-join-code' && S) { e.preventDefault(); A['mp-join'](); }
    if (e.key === 'Enter' && e.target.id === 'manifest-url') A['manifest-save']();
    if (e.key === 'Escape' && menu) { menu = null; renderHead(); renderDock(); }
  });
  window.addEventListener('hashchange', () => { const n = location.hash.replace('#/', ''); if (SB.pages[n] && n !== page && S) { page = n; renderHead(); renderView(false); } });

  /* ----- boot ----- */
  async function backendReady() {
    if ((window.pywebview && window.pywebview.api) || SHOT) return;
    await Promise.race([new Promise(r => window.addEventListener('pywebviewready', r, { once: true })), sleep(1800)]);
  }
  function startupCall(call, timeout = 12000) {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Launcher startup timed out')), timeout);
      Promise.resolve().then(call).then(resolve, reject).finally(() => clearTimeout(timer));
    });
  }
  async function boot() {
    if (SHOT) document.documentElement.classList.add('shot');
    SB.setLang((navigator.language || 'en').toLowerCase().startsWith('fr') ? 'fr' : 'en');
    $('#bg').innerHTML = SB.Scene.background(); SB.Scene.parallax($('#bg')); SB.Art.trackEyes();
    renderHead(); renderSide(); renderRibbon(); renderChars();
    const splash = SB.loading.open({ id: 'splash', steps: [t('boot.1')] }), started = Date.now();
    await backendReady(); splash.progress(18, t('boot.1'));
    try {
      const state = await startupCall(() => api.get_state());
      if (!validState(state)) throw new Error('Launcher state unavailable');
      S = state;
    } catch (error) {
      splash.fail(SB.lang === 'fr' ? 'Le launcher ne répond pas. Tu peux réessayer.' : 'The launcher is not responding. You can retry.');
      const actions = splash.actions(`<button class="btn btn--sky" data-retry>${SB.lang === 'fr' ? 'Réessayer' : 'Retry'}</button>`);
      actions.querySelector('[data-retry]').addEventListener('click', async () => { await splash.close(); boot(); }, { once: true });
      return;
    }
    splash.progress(38, t('boot.2')); applyPrefs();
    const first = !localStorage.getItem('sb.detected');
    if (!S.installations.length && first) {
      startupCall(() => api.autodetect(), 30000).then(() => { localStorage.setItem('sb.detected', '1'); return refresh(); }).catch(() => {});
    }
    safe(() => api.update_check()).then(u => { if (u && u.ok) { L.update = u; if (u.update) toast(t('upd.available', { v: u.latest }) + ' — ' + t('nav.options'), 'info'); } });
    splash.progress(62, t('boot.3'));
    const initial = location.hash.replace('#/', ''); if (SB.pages[initial]) page = initial;
    splash.progress(78, t('boot.4'));
    renderAll(); applyBackdrop();
    await Promise.race([sleep(2600), Promise.all([SB.net.hydrate($('#view')), SB.net.hydrate($('#chars'))])]);
    if (SHOT && new URLSearchParams(location.search).get('open') === 'splash') { splash.progress(64, t('boot.3')); return; }
    splash.done(t('boot.5'));
    await sleep(Math.max(0, (S.preferences.reduce_motion || SHOT ? 200 : 1800) - (Date.now() - started)));
    if (SHOT) splash.el.remove(); else await splash.close();
    loadNews().then(() => { applyBackdrop(); if (page === 'home' || page === 'news') renderView(true); }).catch(() => {});
    const openParam = new URLSearchParams(location.search).get('open');
    if (SHOT && openParam === 'legal') SB.showLegal();
    if (SHOT && openParam === 'launch') A.launch('BOTH_SR1');
    if (SHOT && openParam === 'launching') { const sc = SB.loading.open({ compact: true, title: t('launch.t', { game: NAMES.SR2 }), sub: t('launch.sub') }); sc.progress(68, t('launch.s3', { game: NAMES.SR2 })); sc.el.querySelector('.ld-tip').textContent = SB.tips()[1]; }
    if (SHOT && openParam === 'menu') { menu = 'profile'; renderHead(); }
    if (SHOT && openParam === 'playmenu') { menu = 'play'; renderDock(); }
    if (!S.legal_understood && !SHOT) { await SB.showLegal(); await api.acknowledge_legal(); S.legal_understood = true; go('tutorial'); }   // first start: the tutorial
    refreshNews(false);            // official news + artwork, at every start
  }
  /* ----- portal crossings: a plugin asked BridgeHost to cross, the launcher starts the other game ----- */
  const seenCrossings = new Set(), askedLink = new Set();
  /* The portal was entered with a save that is not linked to a save of the other game yet: the player chooses which one it is. */
  async function askLink(c) {
    const game = NAMES[c.to] || c.to || '?', rows = c.candidates || [];
    const list = rows.length ? rows.map((x, i) => `<label class="row link-row" style="gap:10px;cursor:${x.linked ? 'not-allowed' : 'pointer'};opacity:${x.linked ? .55 : 1}"><input type="radio" name="link-save" value="${esc(x.group)}" ${x.linked ? 'disabled' : ''} ${(x.open && !x.linked) || (!rows.some(y => y.open && !y.linked) && i === 0 && !x.linked) ? 'checked' : ''} style="width:20px;height:20px"><span><b>${x.new ? esc(t('cross.link_new', { game })) : esc(x.label || x.group)}</b>${x.open ? ` <span class="pill good">${t('cross.link_open')}</span>` : ''}${x.linked ? ` <span class="pill muted">${t('cross.link_taken')}</span>` : ''}</span></label>`).join('') : `<p class="err">${t('cross.link_none', { game })}</p>`;
    const res = await modal({ title: t('cross.link_t', { game }), art: `<div class="art-real"><img src="img/pink-cotton-artist.png" alt=""></div>`, body: `<p class="muted">${t('cross.link_b', { game })}</p><div class="stack small-gap" style="max-height:260px;overflow:auto">${list}</div><p class="faint small">${t('cross.link_note', { game })}</p>`, buttons: [{ label: t('common.cancel'), cls: 'btn--ghost', value: false }, { label: t('cross.link_go'), cls: 'btn--bridge', value: true }], collect: root => { const r = root.querySelector('input[name="link-save"]:checked'); return r ? r.value : null; } });
    const group = res && res.value ? res.data : null;
    await api.travel_link(c.id, group || null).catch(() => null);
  }
  async function watchCrossings() {
    if (!S || !S.host || S.host.state !== 'RUNNING') return;
    const r = await api.crossings_poll().catch(() => null); if (!r || !r.ok) return;
    for (const c of r.crossings) {
      if (c.state === 'NEEDS_LINK' && !askedLink.has(c.id)) { askedLink.add(c.id); askLink(c); continue; }
      const key = (c.request_id || c.id) + ':' + c.state;
      if (seenCrossings.has(key) || !['COMPLETE', 'ROLLED_BACK', 'FAILED', 'CANCELLED'].includes(c.state)) continue; seenCrossings.add(key);
      const game = NAMES[c.to] || c.to || '?';
      if (c.code === 'SESSION_CLOSED') toast(t('cross.session_closed', { game }), 'good');
      else if (c.state === 'COMPLETE') toast(t('cross.ok'), 'good');
      else if (c.code === 'NEEDS_BACKUP_CONFIRMATION') toast(t('cross.backup'), 'warn');
      else if (c.state === 'ROLLED_BACK' || c.state === 'FAILED') toast(t('cross.' + String(c.code || '').toLowerCase(), { game }) !== 'cross.' + String(c.code || '').toLowerCase() ? t('cross.' + String(c.code).toLowerCase(), { game }) : t('cross.failed', { game, code: c.code || '' }), 'bad');
    }
  }
  document.addEventListener('keydown', ev => { const el = ev.target; if (ev.key === 'Enter' && el && el.dataset && el.dataset.enter && A[el.dataset.enter]) A[el.dataset.enter](); });
  setInterval(watchCrossings, 2500);
  let checkingGames = false;
  setInterval(async () => {
    // An online error can leave one or both games running even if the last UI state was idle.
    if (!S || document.hidden || checkingGames || busy.has('game-stop')) return;
    const state = S;
    checkingGames = true;
    try {
      const result = await api.game_process_state().catch(() => null);
      if (S !== state || busy.has('game-stop') || !result?.SR1 || !result?.SR2) return;
      if (['SR1', 'SR2'].some(r => state.games[r].running !== result[r].running || !!state.games[r].closing !== result[r].closing ||
          !!state.games[r].close_unconfirmed !== !!result[r].close_unconfirmed)) {
        for (const r of ['SR1', 'SR2']) Object.assign(state.games[r], { close_unconfirmed: false }, result[r]);
        if (page === 'home' || page === 'games') renderView(true); else renderDock();
      }
    } finally { checkingGames = false; }
  }, 1500);

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
