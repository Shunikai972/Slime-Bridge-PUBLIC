/* Multiplayer, Options and About / Legal. */
(function (SB) {
  'use strict';
  const { esc } = SB.ui, { slime, plort, icon } = SB.Art, t = (...a) => SB.t(...a);
  const L = SB.local = SB.local || {};

  const toggle = (act, arg, on, label, desc) => `<div class="setting"><div><div class="name">${label}</div>${desc ? `<div class="desc">${desc}</div>` : ''}</div><button class="switch" role="switch" aria-checked="${!!on}" aria-label="${esc(label)}" data-act="${act}" data-arg="${arg}"></button></div>`;
  const segment = (act, arg, value, options) => `<div class="seg" role="group">${options.map(([v, label]) => `<button data-act="${act}" data-arg="${arg}" data-v="${esc(v)}" aria-pressed="${String(value) === String(v)}">${label}</button>`).join('')}</div>`;

  /* ------------------------------------------------------------------ multiplayer */
  function addrChips(list, fav) {
    if (!list.length) return `<p class="faint small">${t('mp.none')}</p>`;
    return `<div class="row">${list.slice(0, 10).map(a => `<span class="chip addr"><button data-act="mp-fill" data-arg="${esc(a)}" class="plain">${esc(a)}</button><button class="star ${fav.includes(a) ? 'on' : ''}" data-act="mp-star" data-arg="${esc(a)}" aria-label="${esc(t('mp.favourite'))}">${icon('star')}</button></span>`).join('')}</div>`;
  }
  function multiplayer(S) {
    const M = L.mp || { servers: { favourites: [], recent: [], directory_enabled: true }, host: { name: 'My Ranch', port: 7777, mode: 'HYBRID' } };
    const host = S.host, running = host.state === 'RUNNING';
    const online = M.online || {}, content = M.content || {};
    const card = (realm, mascot, flip) => {
      const o = online[realm] || { state: 'no_game', loader: false }, s = o.state || 'no_game';
      const pill = s === 'current' ? 'good' : (s === 'outdated' || s === 'other') ? 'warn' : 'muted';
      return `<section class="panel stack">
      <div class="row between"><h2>${slime(mascot, { size: 40, cls: 'idle', flip: !!flip })} ${t('mp.online_' + realm)}</h2><span class="pill ${pill}">${t('mp.on_s_' + s)}</span></div>
      <p class="muted">${t('mp.how_' + realm)}</p>
      ${o.loader === false && s !== 'no_game' ? `<p class="err">${t('mp.noloader_' + realm)}</p>` : ''}
      <div class="row">
        ${s !== 'current' && s !== 'no_game' && o.loader ? `<button class="btn btn--sky" data-act="mp-online" data-arg="${realm}">${icon('download')} ${t(s === 'missing' ? 'mp.on_install' : 'mp.on_update')}</button>` : ''}
      </div>
    </section>`;
    };
    return `<div class="page">
  <div class="page-title"><h1>${t('nav.multiplayer')}</h1><p>${t('mp.sub')}</p></div>
  <div class="grid cols-2">
    ${card('SR1', 'tabby')}
    ${card('SR2', 'hunter', true)}
  </div>
  <section class="panel stack">
    <h2>${slime('cotton', { size: 40, cls: 'idle' })} ${t('mp.content')}</h2>
    <p class="muted">${t('mp.content_body')}</p>
    ${Object.keys(content).length ? `<div class="row">${Object.entries(content).map(([realm, hash]) => `<div class="field"><label>${esc(realm)}</label><div class="mono">${esc(String(hash).slice(0, 12))}</div></div>`).join('')}</div>` : `<p class="faint small">${t('mp.content_none')}</p>`}
  </section>
  <section class="panel stack">
    <div class="row between"><h2>${t('mp.bridgehost')}</h2><span class="pill ${running ? 'good live' : 'muted'}">${running ? t('mp.hostrun') : t('mp.hoststop')}</span></div>
    <p class="muted">${t('mp.bridgehost_body')}</p>
    ${running ? `<div class="mono">${esc(host.url || '')}</div>` : ''}
    <div class="row"><button class="btn ${running ? 'btn--danger' : 'btn--sky'}" data-act="${running ? 'host-stop' : 'host-start'}">${running ? icon('stop') + ' ' + t('mp.stop_host') : icon('play') + ' ' + t('mp.start_host')}</button></div>
  </section></div>`;
  }

  /* ------------------------------------------------------------------ options */
  const pretty = k => k.replace(/_/g, ' ').replace(/^./, c => c.toUpperCase());
  const optLabel = (s, k) => SB.has(`opt.${s}.${k}`) ? t(`opt.${s}.${k}`) : pretty(k);
  const optDesc = (s, k) => SB.has(`optd.${s}.${k}`) ? t(`optd.${s}.${k}`) : '';
  const valLabel = (s, k, v) => SB.has(`optv.${v}`) ? t(`optv.${v}`) : String(v);
  function settingRow(sec, f) {
    const label = optLabel(sec, f.key), desc = optDesc(sec, f.key);
    if (f.kind === 'bool') return toggle('opt-bool', `${sec}.${f.key}`, f.value, label, desc);
    if (f.kind === 'list') return '';
    if (f.choices) return `<div class="setting"><div><div class="name">${label}</div>${desc ? `<div class="desc">${desc}</div>` : ''}</div>${segment('opt-choice', `${sec}.${f.key}`, f.value, f.choices.map(c => [c, valLabel(sec, f.key, c)]))}</div>`;
    return `<div class="setting"><div><div class="name">${label}</div>${desc ? `<div class="desc">${desc}</div>` : ''}</div><input class="input opt-text" style="max-width:300px" data-sec="${sec}" data-key="${f.key}" value="${esc(f.value ?? '')}"></div>`;
  }
  function linksList() {
    const rows = L.links;
    if (!rows) return `<p class="muted">${t('common.loading')}</p>`;
    if (!rows.length) return `<p class="faint">${t('opt.links_none')}</p>`;
    return rows.map(r => `<div class="setting"><div style="min-width:0"><div class="name">Slime Rancher <span class="mono">${esc(r.sr1)}</span></div><div class="desc">Slime Rancher 2 <span class="mono">${esc(r.sr2)}</span></div></div><button class="btn btn--ghost btn--sm" data-act="unlink" data-arg="${esc(r.sr1)}">${t('opt.links_unlink')}</button></div>`).join('');
  }
  function options(S) {
    const P = S.preferences, O = L.settings || { sections: [] };
    const folder = (name, path, btn) => `<div class="setting"><div style="min-width:0"><div class="name">${name}</div><div class="mono ellipsis muted" title="${esc(path || '')}">${esc(path || '—')}</div></div>${btn}</div>`;
    return `<div class="page">
  <div class="page-title"><h1>${t('nav.options')}</h1><p>${t('opt.sub')}</p></div>
  <div class="grid cols-2 opt-cols">
    <div class="stack">
      <section class="panel"><h2>${t('upd.title')}</h2>
        <div class="setting"><div><div class="name">${t('upd.current', { v: (L.update && L.update.current) || '…' })}</div><div class="desc">${L.update ? (L.update.ok === false ? t('upd.offline') : (L.update.update ? t('upd.available', { v: L.update.latest }) : t('upd.uptodate'))) : t('upd.d')}</div></div>
        ${L.update && L.update.update ? `<button class="btn btn--bridge" data-act="update-apply">${t('upd.apply', { v: L.update.latest })}</button>` : `<button class="btn" data-act="update-check">${t('upd.check')}</button>`}</div>
      </section>
      <section class="panel"><h2>${t('opt.launcher')}</h2>
        <div class="setting"><div class="name">${t('opt.language')}</div>${segment('pref', 'language', P.language, [['auto', 'Auto'], ['en', 'English'], ['fr', 'Français']])}</div>
        <div class="setting"><div style="min-width:0"><div class="name">${t('opt.nexus')}</div><div class="desc">${t('opt.nexus_d')}</div></div>
          <span class="row" style="gap:6px"><input id="nexus-key" type="password" class="field mods-q" style="min-width:160px" placeholder="${P.nexus_api_key ? '••••••••' : t('opt.nexus_ph')}"><button class="btn btn--sm btn--ghost" data-act="nexus-key-save">${t('opt.save')}</button></span></div>
        <div class="setting"><div><div class="name">${t('opt.perf')}</div><div class="desc">${t('opt.perf_d')}</div></div>${segment('pref', 'performance', P.performance || 'off', [['off', t('opt.perf_off')], ['balanced', t('opt.perf_balanced')], ['fast', t('opt.perf_fast')]])}</div>
        ${toggle('pref-bool-inv', 'native_frame', !P.native_frame, t('opt.custom_frame'), t('opt.frame_d'))}
        ${toggle('pref-bool', 'reduce_motion', P.reduce_motion, t('opt.reduce'), t('opt.reduce_d'))}
        ${toggle('pref-bool-inv', 'backup_ack', !P.backup_ack, t('opt.remind'), t('opt.remind_d'))}
      </section>
      <section class="panel"><h2>${t('opt.folders')}</h2>
        ${folder(t('opt.f_tool'), S.tool.path, `<button class="btn btn--ghost btn--sm" data-act="tool">${t('opt.change')}</button>`)}
        ${folder(t('opt.f_out'), S.paths.output, `<button class="btn btn--ghost btn--sm" data-act="out-choose">${t('opt.change')}</button>`)}
        ${folder(t('opt.f_models'), S.paths.models, `<button class="btn btn--ghost btn--sm" data-act="folder" data-arg="models">${t('games.open')}</button>`)}
        ${folder(t('opt.f_cache'), S.paths.cache, `<button class="btn btn--ghost btn--sm" data-act="folder" data-arg="cache">${t('games.open')}</button>`)}
      </section>
      <section class="panel stack"><h2>${t('opt.links_t')}</h2><p class="muted">${t('opt.links_b')}</p>${linksList()}</section>
    </div>
    <div class="stack">${O.sections.map(sec => `<section class="panel"><h2>${SB.has('sec.' + sec.section) ? t('sec.' + sec.section) : pretty(sec.section)}</h2>${sec.fields.map(f => settingRow(sec.section, f)).join('')}</section>`).join('') || `<section class="panel"><p class="muted">${t('opt.loading')}</p></section>`}</div>
  </div></div>`;
  }

  /* ------------------------------------------------------------------ about / legal */
  function about(S) {
    const row = (title, body) => `<li><b>${title}</b><span class="muted"> — ${body}</span></li>`;
    return `<div class="page">
  <div class="page-title"><h1>${t('nav.about')}</h1><p>${t('about.sub')}</p></div>
  <section class="panel legal-hero">
    <div class="lh-art">${slime('pink', { size: 90, cls: 'hop', dur: 2.2 })}${plort('honey', { size: 40 })}</div>
    <div class="stack" style="flex:1"><h2>${t('legal.title')}</h2>
      <p class="big-note"><b>${t('legal.notaff')}</b></p>
      <p class="muted">${t('legal.body1')}</p>
      <div class="row"><button class="btn btn--ghost btn--sm" data-act="store" data-arg="SR1">${t('games.buy', { game: 'Slime Rancher' })}</button><button class="btn btn--ghost btn--sm" data-act="store" data-arg="SR2">${t('games.buy', { game: 'Slime Rancher 2' })}</button></div></div>
  </section>
  <div class="grid cols-2">
    <section class="panel stack"><h2>${t('about.files')}</h2><ul class="clean">
      ${row(t('about.f1t'), t('about.f1'))}${row(t('about.f2t'), t('about.f2'))}${row(t('about.f3t'), t('about.f3'))}${row(t('about.f4t'), t('about.f4'))}</ul></section>
    <section class="panel stack"><h2>${t('about.fan')}</h2><ul class="clean">
      ${row(t('about.p1t'), t('about.p1'))}${row(t('about.p2t'), t('about.p2'))}${row(t('about.p3t'), t('about.p3'))}${row(t('about.p4t'), t('about.p4'))}</ul></section>
  </div>
  <section class="panel stack"><h2>${t('about.policy_t')}</h2><p class="muted">${t('about.policy')}</p><div class="row"><button class="btn btn--ghost btn--sm" data-act="open-url" data-arg="https://www.slimerancher.com/fan-content-policy">${t('ribbon.link')}</button></div></section>
  <section class="panel stack"><h2>${t('about.third')}</h2><ul class="clean">
    ${row('Fredoka · Nunito', t('about.fonts'))}${row('SRML · MelonLoader', t('about.loaders'))}${row('UnityPy · AssetRipper', t('about.tools'))}</ul>
    <p class="faint small">${t('about.version')}: ${esc(S.version)} · ${esc(S.platform)}</p></section></div>`;
  }

  /** First start: a welcome card in the launcher's own look (artist picture, three short points, one button). */
  async function showLegal() {
    return new Promise(resolve => {
      const back = document.createElement('div');
      back.className = 'welcome-back';
      const point = (glyphName, title, body) => `<li><span class="wi">${icon(glyphName)}</span><span><b>${title}</b><small>${body}</small></span></li>`;
      back.innerHTML = `<section class="welcome" role="dialog" aria-modal="true" aria-label="${esc(t('welcome.title'))}">
        <img class="welcome-art" src="img/pink-cotton-artist.png" alt="">
        <h1>${t('welcome.title')}</h1>
        <p class="welcome-sub">${t('welcome.sub')}</p>
        <ul class="welcome-points">
          ${point('games', t('welcome.p1_t'), t('welcome.p1'))}
          ${point('folder', t('welcome.p2_t'), t('welcome.p2'))}
          ${point('profiles', t('welcome.p3_t'), t('welcome.p3'))}
        </ul>
        <p class="welcome-legal">${t('legal.notaff')}</p>
        <button class="btn btn--sr1 welcome-go" data-go>${SB.Art.icon('play')} ${t('welcome.go')}</button>
      </section>`;
      back.querySelector('[data-go]').addEventListener('click', () => { back.remove(); resolve(true); });
      document.getElementById('modal-root').appendChild(back);
      back.querySelector('[data-go]').focus();
    });
  }

  /** What needs attention (the former Recovery page), short: one line per issue. */
  function health() {
    const R = SB.local.recovery;
    if (!R) return '';
    const sev = { error: 'bad', warning: 'warn', info: 'info' };
    const issues = R.groups.flatMap(g => g.issues).filter(i => !i.minor);
    const games = { SR1: 'Slime Rancher', SR2: 'Slime Rancher 2' }, loaders = { SR1: 'SRML', SR2: 'MelonLoader' };
    const text = (i, part, fallback) => { const key = `rec.code.${i.code}_${part}`; return SB.has(key) ? t(key, { game: games[i.realm] || i.subject || '', loader: loaders[i.realm] || '' }) : fallback; };
    return `<section class="panel stack health"><h2>${t('rec.' + R.summary.status)}</h2>${issues.map(i => `<div class="row between health-row"><span><b>${esc(text(i, 't', i.title))}</b><small class="muted"> — ${esc(text(i, 's', i.summary))}</small></span><span class="pill ${sev[i.severity] || 'info'}">${esc(t('rec.sev_' + i.severity))}</span></div>`).join('')}</section>`;
  }

  function debug(S) {
    const D = SB.local.diag || {}, C = SB.console.state;
    const seg = (key, value, label) => `<button class="seg ${C[key] === value ? 'on' : ''}" data-act="console-set" data-arg="${key}:${value}">${label}</button>`;
    const src = (name, label) => `<button class="seg src-${name.toLowerCase()} ${C.sources[name] ? 'on' : ''}" data-act="console-src" data-arg="${name}">${label}</button>`;
    return `<section class="page">
  <div class="page-title"><h1>${t('debug.title')}</h1><p>${t('console.sub')}</p></div>
  ${health()}
  <div class="card console-card">
    <div class="console-bar">
      <span class="segs">${src('SR1', 'SR1')}${src('SR2', 'SR2')}${src('LAUNCHER', 'Launcher')}</span>
      <span class="segs">${seg('filter', 'all', t('console.all'))}${seg('filter', 'bridge', 'SlimeBridge')}${seg('filter', 'error', t('console.errors'))}</span>
      <span style="flex:1"></span>
      <button class="btn btn--sm ${C.paused ? 'btn--bridge' : 'btn--ghost'}" data-act="console-pause">${C.paused ? t('console.resume') : t('console.pause')}</button>
      <button class="btn btn--sm btn--ghost" data-act="console-copy">${t('console.copy')}</button>
      <button class="btn btn--sm btn--ghost" data-act="console-clear">${t('console.clear')}</button>
    </div>
    <div id="console-log" class="console-log" role="log" aria-live="off">${SB.console.html()}</div>
  </div>
  <h2 class="console-h2">${t('diag.run')}</h2>
  <p class="muted">${t('diag.sub')}</p>
  <div class="card"><div class="row"><button class="btn btn--bridge" data-act="diag-run" ${D.busy ? 'disabled' : ''}>${D.busy ? t('diag.running') : t('diag.run')}</button>
  ${D.text ? `<button class="btn" data-act="diag-copy">${t('diag.copy')}</button>` : ''}</div>
  ${D.text ? `<textarea id="diag-text" class="diag" readonly spellcheck="false" style="width:100%;min-height:420px;font-family:monospace;font-size:12px;white-space:pre;margin-top:12px">${SB.ui.esc(D.text)}</textarea>` : `<p class="muted">${t('diag.hint')}</p>`}</div>
</section>`;
  }

  /** Mod manager: search the game's Nexus catalogue, install with one click, remove (kept aside, never deleted). */
  function mods(S) {
    const M = SB.local.mods = SB.local.mods || { realm: 'SR2', query: '', results: null, installed: {}, busy: false };
    const realm = M.realm, inst = M.installed[realm], game = realm === 'SR1' ? 'Slime Rancher' : 'Slime Rancher 2';
    const fmt = n => n >= 1000 ? (n / 1000).toFixed(n >= 10000 ? 0 : 1) + 'k' : String(n || 0);
    const installedIds = new Set(((inst && inst.mods) || []).map(m => String(m.nexus_id)));
    const card = m => `<article class="mod-card">
        <span class="mod-thumb" data-thumb="${esc(m.thumbnail || '')}">${m.thumb ? `<img src="${m.thumb}" alt="">` : ''}</span>
        <span class="mod-body"><b>${esc(m.name)}</b><small class="muted">${esc(m.author || '')} · v${esc(m.version || '?')} · ${fmt(m.downloads)} ${t('mods.downloads')}</small><span class="mod-sum">${esc(m.summary || '')}</span></span>
        <span class="mod-actions">${installedIds.has(String(m.id)) ? `<span class="pill good">${t('mods.installed_pill')}</span>` : `<button class="btn btn--sm btn--sr${realm === 'SR1' ? '1' : '2'}" data-act="mod-install" data-arg="${m.id}">${t('mods.install')}</button>`}
          <button class="btn btn--sm btn--ghost" data-act="open-url" data-arg="${esc(m.url)}">Nexus</button></span>
      </article>`;
    const pending = inst && inst.pending && inst.pending.realm === realm ? inst.pending : null;
    const rows = inst ? (inst.mods.length ? inst.mods.map(m => `<div class="row between mod-row"><span><b>${esc(m.name)}</b>${m.version ? ` <small class="muted">v${esc(m.version)}</small>` : ''}${m.protected ? ` <span class="pill muted">${t('mods.protected')}</span>` : ''}</span>
        ${m.protected ? '' : `<button class="btn btn--sm btn--ghost" data-act="mod-remove" data-arg="${esc(m.key)}">${t('mods.remove')}</button>`}</div>`).join('') : `<p class="muted">${t('mods.none')}</p>`) : `<p class="muted">${t('prof.loading')}</p>`;
    return `<section class="page">
  <div class="page-title"><h1>${t('nav.mods')}</h1><p>${t('mods.sub')}</p></div>
  <div class="row mods-top">
    <span class="segs game-switch">${['SR1', 'SR2'].map(r => `<button class="seg ${r === realm ? 'on' : ''}" data-act="mods-realm" data-arg="${r}">${r === 'SR1' ? 'Slime Rancher' : 'Slime Rancher 2'}</button>`).join('')}</span>
    <input id="mods-q" class="field mods-q" placeholder="${esc(t('mods.search_ph', { game }))}" value="${esc(M.query)}" data-enter="mods-search">
    <button class="btn btn--sky" data-act="mods-search">${t('mods.search')}</button>
    <button class="btn btn--ghost" data-act="mod-file">${t('mods.from_file')}</button>
  </div>
  ${pending && pending.state === 'WAITING' ? `<p class="pill warn mod-wait">${t('mods.waiting', { name: esc(pending.name || '') })}</p>` : ''}
  <div class="grid cols-2 mods-cols">
    <section class="panel stack"><h2>${t('mods.results')}</h2>${M.busy ? `<p class="muted">${t('mods.searching')}</p>` : M.results ? (M.results.length ? `<div class="mod-list">${M.results.map(card).join('')}</div>` : `<p class="muted">${t('mods.no_result')}</p>`) : `<p class="muted">${t('mods.hint')}</p>`}</section>
    <section class="panel stack"><h2>${t('mods.in_game', { game })}</h2>${rows}<p class="faint small">${t('mods.removed_note')}</p></section>
  </div>
  <p class="faint small">${t('mods.source')}</p>
</section>`;
  }

  /** Credits: the people who made the launcher with me, then every project SlimeBridge uses (role, licence, link). */
  const CREDITS = [
    ['credits.g_loaders', [
      ['SRML (Slime Rancher Mod Loader)', 'credits.r_srml', 'GPL-3.0', 'https://github.com/SlimeRancherModding/SRML'],
      ['MelonLoader', 'credits.r_melon', 'Apache-2.0', 'https://github.com/LavaGang/MelonLoader'],
      ['MelonSRML', 'credits.r_melonsrml', 'GPL-3.0', 'https://github.com/SlimeRancherModding/MelonSRML'],
      ['Harmony', 'credits.r_harmony', 'MIT', 'https://github.com/pardeike/Harmony'],
      ['Il2CppInterop', 'credits.r_interop', 'LGPL-3.0', 'https://github.com/BepInEx/Il2CppInterop'],
    ]],
    ['credits.g_online', [
      ['SRMP Revival', 'credits.r_srmp', 'GPL-3.0', 'https://github.com/Shunikai972/Slime-rancher-multiplayer-public-mode'],
      ['SRMP (srmp-public)', 'credits.r_srmp_up', 'GPL-3.0', 'https://github.com/cachenetworks/srmp-public'],
      ['Ranching Together (SR2MP)', 'credits.r_sr2mp', 'GPL-3.0', 'https://github.com/pyeight/SlimeRancher2Multiplayer'],
      ['Starlight', 'credits.r_starlight', 'Starlight-SAL', 'https://github.com/ThatFinn/Starlight'],
    ]],
    ['credits.g_tools', [
      ['Cpp2IL', 'credits.r_cpp2il', 'MIT', 'https://github.com/SamboyCoding/Cpp2IL'],
      ['UnityPy', 'credits.r_unitypy', 'MIT', 'https://github.com/K0lb3/UnityPy'],
      ['TypeTreeGeneratorAPI', 'credits.r_ttg', 'MIT', 'https://github.com/K0lb3/TypeTreeGenerator'],
      ['AssetRipper', 'credits.r_assetripper', 'GPL-3.0', 'https://github.com/AssetRipper/AssetRipper'],
      ['Blender', 'credits.r_blender', 'GPL-2.0+', 'https://www.blender.org/'],
      ['pywebview', 'credits.r_pywebview', 'BSD-3-Clause', 'https://github.com/r0x0r/pywebview'],
      ['PyInstaller', 'credits.r_pyinstaller', 'GPL-2.0 + exception', 'https://pyinstaller.org/'],
      ['dnfile · pygltflib · texture2ddecoder', 'credits.r_libs', 'MIT', 'https://pypi.org/project/dnfile/'],
      ['NumPy · Pillow · PyYAML', 'credits.r_libs2', 'BSD / MIT-CMU / MIT', 'https://pypi.org/project/Pillow/'],
      ['Nexus Mods', 'credits.r_nexus', '—', 'https://www.nexusmods.com/slimerancher2'],
      ['Fredoka · Nunito', 'credits.r_fonts', 'SIL OFL 1.1', 'https://fonts.google.com/specimen/Fredoka'],
    ]],
  ];
  function credits(S) {
    const row = ([name, role, licence, url]) => `<div class="credit-row"><span><b>${esc(name)}</b><small class="muted">${t(role)}</small></span><span class="pill muted">${esc(licence)}</span><button class="btn btn--sm btn--ghost" data-act="open-url" data-arg="${esc(url)}">${t('credits.open')}</button></div>`;
    return `<section class="page">
  <div class="page-title"><h1>${t('nav.credits')}</h1><p>${t('credits.sub')}</p></div>
  <section class="panel thanks">
    <img src="img/pink-cotton-artist.png" alt="">
    <div><h2>${t('credits.thanks_t')}</h2><p class="thanks-text">${t('credits.thanks')}</p></div>
  </section>
  ${CREDITS.map(([title, rows]) => `<section class="panel stack credits-group"><h2>${t(title)}</h2>${rows.map(row).join('')}</section>`).join('')}
  <section class="panel stack credits-group"><h2>Monomi Park</h2><p class="muted">${t('credits.monomi')}</p>
    <div class="row"><button class="btn btn--sm btn--ghost" data-act="open-url" data-arg="https://www.slimerancher.com/fan-content-policy">${t('nav.about')} · Fan Content Policy</button></div></section>
</section>`;
  }

  SB.pages = Object.assign(SB.pages || {}, { credits: { render: credits }, mods: { render: mods }, multiplayer: { render: multiplayer }, options: { render: options }, about: { render: about }, debug: { render: debug } });
  SB.showLegal = showLegal;
})(window.SB = window.SB || {});
