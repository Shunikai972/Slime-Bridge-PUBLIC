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

  async function showLegal() {
    return SB.ui.modal({
      title: t('legal.modal_title'), art: slime('pink', { size: 84, cls: 'hop', dur: 1.8 }),
      body: `<p><b>${t('legal.notaff')}</b></p><p class="muted">${t('legal.body1')}</p><p class="muted">${t('legal.body2')}</p>`,
      buttons: [{ label: t('legal.accept'), cls: 'btn--bridge', value: true }],
    });
  }

  function debug(S) {
    const D = SB.local.diag || {};
    return `<section class="page">
  <div class="page-title"><h1>${t('nav.debug')}</h1><p>${t('diag.sub')}</p></div>
  <div class="card"><div class="row"><button class="btn btn--bridge" data-act="diag-run" ${D.busy ? 'disabled' : ''}>${D.busy ? t('diag.running') : t('diag.run')}</button>
  ${D.text ? `<button class="btn" data-act="diag-copy">${t('diag.copy')}</button>` : ''}</div>
  ${D.text ? `<textarea id="diag-text" class="diag" readonly spellcheck="false" style="width:100%;min-height:420px;font-family:monospace;font-size:12px;white-space:pre;margin-top:12px">${SB.ui.esc(D.text)}</textarea>` : `<p class="muted">${t('diag.hint')}</p>`}</div>
</section>`;
  }

  SB.pages = Object.assign(SB.pages || {}, { multiplayer: { render: multiplayer }, options: { render: options }, about: { render: about }, debug: { render: debug } });
  SB.showLegal = showLegal;
})(window.SB = window.SB || {});
