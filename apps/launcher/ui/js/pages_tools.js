/* Asset extraction (drives the local SlimeBridge-AssetConverter) and cross-game Largo models. */
(function (SB) {
  'use strict';
  const { esc, pct, fmtBytes } = SB.ui, { slime, plort, icon, largo } = SB.Art, t = (...a) => SB.t(...a);
  const L = SB.local = SB.local || {};
  L.assets = L.assets || { direction: 'both', only_missing: true, kind: 'all', job: null };

  /* ------------------------------------------------------------------ assets */
  const seg = (key, value, options) => `<div class="seg" role="group">${options.map(([v, label]) => `<button data-act="opt" data-k="${key}" data-v="${v}" aria-pressed="${value === v}">${label}</button>`).join('')}</div>`;
  const scopeCard = (v, active, title, body, rec) => `<button class="scope ${active ? 'on' : ''}" data-act="opt" data-k="only_missing" data-v="${v}" aria-pressed="${active}"><span class="scope-art">${v === 'true' ? plort('rad', { size: 38 }) : plort('pink', { size: 30 }) + plort('honey', { size: 30 }) + plort('sloomber', { size: 30 })}</span><span><b>${title}</b>${rec ? `<span class="pill good">${t('assets.recommended')}</span>` : ''}<br><small>${body}</small></span></button>`;

  /* ----- memory limit: a pink slime hops along the bar; the extraction runs in its own process under this ceiling ----- */
  const RAM_MIN = 2, RAM_MAX = 32, RAM_TIERS = [[2, 'assets.ram_t0'], [5.5, 'assets.ram_t1'], [10.5, 'assets.ram_t2']];
  const ramValue = S => Math.max(RAM_MIN, Math.min(RAM_MAX, Number((S.preferences || {}).extract_ram_gb) || 8));
  const ramFill = v => ((v - RAM_MIN) / (RAM_MAX - RAM_MIN) * 100).toFixed(1) + '%';
  const ramTiers = v => RAM_TIERS.map(([gb, key]) => `<li class="${v >= gb ? 'on' : ''}"><b>${v >= gb ? '✓' : '·'}</b> <span>${t(key)}</span> <small class="faint">≥ ${gb} GB</small></li>`).join('');
  function ramPanel(S) {
    const v = ramValue(S);
    return `<section class="panel ram-panel">
    <div class="row between"><h2>${t('assets.ram')}</h2><b class="pill info" id="ram-val">${v} GB</b></div>
    <p class="muted">${t('assets.ram_body')}</p>
    <div class="bar ram" id="ram-bar" style="--p:${ramFill(v)}"><i></i><span class="hopper">${slime('pink', { size: 54, cls: 'idle', dur: .9, hopH: -10 })}</span>
      <input type="range" id="ram-range" min="${RAM_MIN}" max="${RAM_MAX}" step="1" value="${v}" aria-label="${esc(t('assets.ram'))}"></div>
    <div class="row between ram-scale"><span class="faint small">${RAM_MIN} GB</span><span class="faint small">${RAM_MAX} GB</span></div>
    <ul class="ram-tiers" id="ram-tiers">${ramTiers(v)}</ul></section>`;
  }
  SB.ramLive = (value, S) => {                      // while dragging: move the slime and the text, nothing is saved yet
    const bar = document.getElementById('ram-bar'), val = document.getElementById('ram-val'), tiers = document.getElementById('ram-tiers');
    if (bar) bar.style.setProperty('--p', ramFill(value));
    if (val) val.textContent = value + ' GB';
    if (tiers) tiers.innerHTML = ramTiers(value);
  };

  function jobBox(job) {
    if (!job) return '';
    const run = job.status === 'RUNNING' || job.status === 'QUEUED', p = Math.max(0, Math.min(1, job.progress || 0));
    const bar = `<div class="job-head"><div class="phase"><b>${esc(phaseLabel(job))}</b><span class="muted">${job.total ? `${job.done || 0} / ${job.total}` : ''}</span></div><span class="pill ${run ? 'info live' : job.status === 'DONE' ? 'good' : job.status === 'FAILED' ? 'bad' : 'warn'}">${t('job.' + job.status)}</span></div>
      <div class="bar" style="--p:${(p * 100).toFixed(1)}%"><i></i><span class="hopper">${slime('pink', { size: 54, cls: run ? 'hop' : 'idle', dur: .9, hopH: -10 })}</span></div>`;
    let out = '';
    if (job.error) out += `<p class="err">${esc(job.error)}</p>`;
    if (job.status === 'DONE' && job.result) {
      const c = job.result;
      const fmt = r => r ? Object.entries(r.by_status || {}).map(([k, v]) => `<span class="pill ${/INCOMPLETE|FAIL/.test(k) ? 'warn' : 'good'}">${esc(k)} ${v}</span>`).join(' ') : '';
      out += `<div class="grid cols-3 results"><div class="res"><h4>${t('assets.r_convert')}</h4><div class="big">${(c.convert || {}).count ?? 0}</div><div class="row">${fmt(c.convert)}</div></div><div class="res"><h4>${t('assets.r_verify')}</h4><div class="big">${(c.verify || {}).count ?? 0}</div><div class="row">${fmt(c.verify)}</div></div><div class="res"><h4>${t('assets.r_export')}</h4><div class="big">${(c.export || {}).count ?? 0}</div><div class="row">${fmt(c.export)}</div></div></div><p class="muted small">${t('assets.honest')}</p>`;
    }
    if (job.status === 'DONE' && job.result) {
      const c = job.result, specs = [c.largo_spec, c.sr2_spec].filter(x => x && x.ok);
      if (c.skipped_ram) out += `<p class="warn">${t('assets.skipped_ram', { n: c.skipped_ram.count })}</p>`;
      if (specs.length) out += `<p class="faint small">${t('assets.spec_ok', { n: specs.reduce((a, x) => a + (x.largos || 0), 0) })}</p>`;
    }
    const logs = (job.logs || []).slice(-40);
    out += `<details class="logs"><summary>${t('assets.log')} (${logs.length})</summary><pre class="mono">${esc(logs.join('\n'))}</pre></details>`;
    return bar + out;
  }
  const phaseLabel = job => (['DONE', 'FAILED', 'CANCELLED'].includes(job.status) ? t('job.' + job.status) : null) || ({ list: t('job.list'), convert: t('job.convert'), verify: t('job.verify'), export: t('job.export') }[job.phase] || t('job.starting'));

  /* ----- SR1 sanctuary: statues recovered from the player's own game, island layout, install next to SR1 ----- */
  function sanctBox(Z) {
    const job = Z.job, run = job && (job.status === 'RUNNING' || job.status === 'QUEUED'), p = Math.max(0, Math.min(1, job ? job.progress || 0 : 0));
    if (!job) return '';
    return `<div class="job-head"><div class="phase"><b>${esc(job.phase_label || t('sanct.idle'))}</b></div><span class="pill ${run ? 'info live' : job.status === 'DONE' ? 'good' : 'bad'}">${esc(job.status)}</span></div>
      <div class="bar" style="--p:${(p * 100).toFixed(1)}%"><i></i><span class="hopper">${slime('pink', { size: 54, cls: run ? 'hop' : 'idle', dur: .9, hopH: -10 })}</span></div>${job.error ? `<p class="err">${esc(job.error)}</p>` : ''}`;
  }
  function sanctuary(S) {
    const Z = L.sanct;
    if (!Z) return `<section class="panel"><h2>${t('sanct.title')}</h2><p class="muted">${t('common.loading')}</p></section>`;
    const run = Z.job && (Z.job.status === 'RUNNING' || Z.job.status === 'QUEUED'), kit = Z.kit, lay = Z.layout, ins = Z.installed || {}, cfg = Z.settings;
    const counts = kit ? kit.statues : {};
    const step = (n, title, body, pill) => `<div class="setting"><div style="min-width:0"><div class="name">${n}. ${title}</div><div class="desc">${body}</div></div>${pill}</div>`;
    const ready = !!(Z.sr1_root && Z.dependencies.ok);
    const mode = cfg.sr1_bridge_access_mode === 'ALWAYS' ? t('sanct.m_always') : t('sanct.m_end', { n: cfg.sr1_gold_plort_gate_cost });
    return `<section class="panel stack sanct">
    <div class="row between"><h2>${t('sanct.title')}</h2><span class="pill ${ins.present ? 'good' : lay ? 'info' : 'muted'}">${ins.present ? t('sanct.installed') : lay ? t('sanct.built') : t('sanct.none')}</span></div>
    <p class="muted">${t('sanct.body')}</p>
    ${!Z.sr1_root ? `<p class="err">${t('sanct.need_game')}</p>` : ''}${!Z.dependencies.ok ? `<p class="err">${t('sanct.need_deps')} <span class="mono">${esc(Z.dependencies.hint || '')}</span></p>` : ''}
    ${step(1, t('sanct.s1'), kit ? t('sanct.s1_done', { total: (counts.native || 0) + (counts.synthesized || 0), native: counts.native || 0, synth: counts.synthesized || 0, skip: (counts.excluded || 0) + (counts.decorative || 0) }) : t('sanct.s1_body'), `<span class="pill ${kit ? 'good' : 'muted'}">${kit ? t('sanct.ready') : t('sanct.todo')}</span>`)}
    ${step(2, t('sanct.s2'), lay ? t('sanct.s2_done', { req: lay.required, pieces: lay.pieces, mode }) : t('sanct.s2_body', { mode }), `<span class="pill ${lay ? 'good' : 'muted'}">${lay ? t('sanct.ready') : t('sanct.todo')}</span>`)}
    ${step(3, t('sanct.s3'), ins.present ? t('sanct.s3_done', { path: esc(Z.sr1_root || '') }) : t('sanct.s3_body'), `<span class="pill ${Z.plugin ? 'good' : 'warn'}">${Z.plugin ? t('sanct.plugin_ok') : t('sanct.plugin_missing')}</span>`)}
    <div id="sanctbox" class="stack">${sanctBox(Z)}</div>
    <div class="row">
      <button class="btn btn--bridge" data-act="sanct-build" ${ready && !run ? '' : 'disabled'}>${icon('extract')} ${kit ? t('sanct.rebuild') : t('sanct.build')}</button>
      <button class="btn btn--sky" data-act="sanct-install" ${lay && Z.sr1_root && !run ? '' : 'disabled'}>${icon('download')} ${ins.present && ins.matches_latest ? t('sanct.reinstall') : t('sanct.install')}</button>
      <button class="btn btn--ghost" data-act="sanct-remove" ${ins.present ? '' : 'disabled'}>${icon('trash')} ${t('sanct.remove')}</button>
      <button class="btn btn--ghost" data-act="go" data-arg="options">${icon('options')} ${t('sanct.settings')}</button>
    </div>
    <p class="faint small">${t('sanct.privacy')}</p></section>`;
  }

  function assets(S) {
    const a = L.assets, tool = S.tool, g = S.games;
    const needs = a.direction === 'sr1-to-sr2' ? ['SR1'] : a.direction === 'sr2-to-sr1' ? ['SR2'] : ['SR1', 'SR2'];
    const missing = needs.filter(r => !g[r].primary);
    const running = a.job && (a.job.status === 'RUNNING' || a.job.status === 'QUEUED');
    const can = tool.found && !missing.length && !running;
    const why = !tool.found ? t('assets.need_tool') : missing.length ? t('assets.need_game', { game: missing.map(r => r === 'SR1' ? 'Slime Rancher' : 'Slime Rancher 2').join(' + ') }) : '';
    return `<div class="page">
  <div class="page-title"><h1>${t('nav.assets')}</h1><p>${t('assets.sub')}</p></div>
  <section class="panel tool">
    <div class="tool-art">${plort('phosphor', { size: 54 })}</div>
    <div class="stack" style="flex:1"><div class="row between"><h2>${t('assets.tool_title')}</h2><span class="pill ${tool.found ? 'good' : 'bad'}">${tool.found ? t('assets.tool_ok') : t('assets.tool_missing')}</span></div>
      <p class="muted">${t('assets.tool_body')}</p>
      <div class="mono ellipsis" title="${esc(tool.path || '')}">${esc(tool.path || tool.reason || '')}</div>
      <div class="row"><button class="btn btn--ghost btn--sm" data-act="tool">${icon('folder')} ${t('assets.tool_choose')}</button></div></div>
  </section>
  <section class="panel">
    <h2>${t('assets.what')}</h2>
    <div class="stack">
      <div class="field"><label>${t('assets.direction')}</label>${seg('direction', a.direction, [['both', t('assets.d_both')], ['sr1-to-sr2', t('assets.d_12')], ['sr2-to-sr1', t('assets.d_21')]])}</div>
      <div class="field"><label>${t('assets.scope')}</label><div class="grid cols-2">${scopeCard('true', a.only_missing, t('assets.s_missing'), t('assets.s_missing_body'), true)}${scopeCard('false', !a.only_missing, t('assets.s_all'), t('assets.s_all_body'), false)}</div></div>
      <div class="field"><label>${t('assets.kind')}</label>${seg('kind', a.kind, [['all', t('assets.k_all')], ['base', t('assets.k_base')], ['largo', t('assets.k_largo')]])}</div>
      <div class="row">${['SR1', 'SR2'].map(r => `<span class="pill ${g[r].primary ? 'good' : needs.includes(r) ? 'bad' : 'muted'}">${r === 'SR1' ? 'Slime Rancher' : 'Slime Rancher 2'}: ${g[r].primary ? t('games.found') : t('games.notfound')}</span>`).join('')}</div>
    </div>
  </section>
  ${ramPanel(S)}
  <section class="panel run">
    <div class="row between"><h2>${t('assets.run')}</h2>
      <div class="row">${running ? `<button class="btn btn--danger" data-act="extract-cancel">${icon('stop')} ${t('assets.cancel')}</button>` : `<button class="btn btn--bridge btn--lg" data-act="extract" ${can ? '' : 'disabled'} ${why ? `data-tip="${esc(why)}"` : ''}>${icon('assets')} ${t('assets.go')}</button>`}</div></div>
    <div id="jobbox" class="stack">${jobBox(a.job)}</div>
  </section>
  ${sanctuary(S)}
  <section class="panel">
    <h2>${t('assets.after')}</h2><p class="muted">${t('assets.after_body')}</p>
    <div class="mono ellipsis" title="${esc(S.paths.output)}">${t('assets.output')}: ${esc(S.paths.output)}</div>
    <div class="row"><button class="btn btn--sky" data-act="install-cache" ${a.job && a.job.status === 'DONE' ? '' : 'disabled'}>${icon('download')} ${t('assets.install')}</button><button class="btn btn--ghost" data-act="folder" data-arg="output">${icon('folder')} ${t('assets.open_out')}</button><button class="btn btn--ghost" data-act="out-choose">${t('assets.out_change')}</button></div>
    <p class="faint small">${t('assets.privacy')}</p>
  </section></div>`;
  }

  /* ------------------------------------------------------------------ models */
  const FAMILIES = [['rad', 'Rad'], ['quantum', 'Quantum'], ['mosaic', 'Mosaic']];
  function modelCard(m, busy) {
    const st = m.state, pill = { INSTALLED: 'good', AVAILABLE: 'info', UPDATE: 'warn', NOT_PUBLISHED: 'muted' }[st];
    const action = st === 'AVAILABLE' || st === 'UPDATE' ? `<button class="btn btn--sky btn--sm" data-act="model-dl" data-arg="${m.id}" ${busy ? 'disabled' : ''}>${icon('download')} ${st === 'UPDATE' ? t('models.update') : t('models.download')}${m.size ? ` · ${fmtBytes(m.size)}` : ''}</button>`
      : st === 'INSTALLED' ? `<button class="btn btn--ghost btn--sm" data-act="model-rm" data-arg="${m.id}">${icon('trash')} ${t('models.remove')}</button>` : `<span class="muted small">${t('models.soon')}</span>`;
    return `<article class="model panel ${st.toLowerCase()}"><div class="model-art ${st === 'NOT_PUBLISHED' ? 'locked' : ''}">${largo(m.parents[0], m.parents[1] === 'slumber' ? 'sloomber' : m.parents[1], { cls: st === 'INSTALLED' ? 'idle' : '' })}</div><h3>${esc(m.name)}</h3><div class="row"><span class="pill ${pill}">${t('models.' + st)}</span>${m.version ? `<span class="faint small">v${esc(m.version)}</span>` : ''}</div><div class="row" style="margin-top:auto">${action}</div></article>`;
  }
  function models(S) {
    const M = L.models || { rows: [], manifest: {}, dir: S.paths.models, download: null };
    const dl = M.download, busy = dl && (dl.status === 'RUNNING' || dl.status === 'QUEUED');
    const have = M.rows.filter(r => r.state === 'INSTALLED').length, avail = M.rows.filter(r => r.state === 'AVAILABLE' || r.state === 'UPDATE').length;
    const url = (S.preferences.largo_manifest_url || '');
    return `<div class="page">
  <div class="page-title"><h1>${t('nav.models')}</h1><p>${t('models.sub')}</p></div>
  <section class="panel models-head">
    <div class="mh-art">${largo('quantum', 'batty', { cls: 'idle' })}</div>
    <div class="stack" style="flex:1"><h2>${t('models.title')}</h2><p class="muted">${t('models.body')}</p>
      <div class="field"><label>${t('models.source')}</label><div class="row" style="flex-wrap:nowrap"><input class="input" id="manifest-url" value="${esc(url)}" placeholder="${esc(t('models.source_ph'))}" spellcheck="false"><button class="btn btn--ghost btn--sm" data-act="manifest-save">${t('models.save')}</button></div></div>
      <div class="row"><button class="btn btn--sky" data-act="models-refresh">${icon('refresh')} ${t('models.check')}</button><button class="btn btn--bridge" data-act="model-dl" data-arg="all" ${avail && !busy ? '' : 'disabled'}>${icon('download')} ${t('models.download_all', { n: avail })}</button><button class="btn btn--ghost" data-act="models-import">${icon('folder')} ${t('models.import')}</button><button class="btn btn--ghost" data-act="folder" data-arg="models">${t('models.open')}</button>
        <span class="pill ${have ? 'good' : 'muted'}">${t('models.have', { n: have })}</span>${M.manifest && M.manifest.loaded ? `<span class="pill info">${esc(M.manifest.name || 'manifest')}${M.manifest.updated ? ' · ' + esc(M.manifest.updated) : ''}</span>` : ''}</div>
      ${M.manifest && M.manifest.error ? `<p class="err">${esc(M.manifest.error)}</p>` : ''}
      ${dl && dl.status !== 'QUEUED' ? `<div id="dlbox" class="stack">${dlBox(dl)}</div>` : '<div id="dlbox"></div>'}
      <p class="faint small">${t('models.safety')}</p></div>
  </section>
  ${FAMILIES.map(([fam, label]) => `<section class="stack"><h2 class="fam-title"><span class="fam-dot ${fam}"></span>${label} × ${t('models.sr2')}</h2><div class="grid cols-4 models-grid">${M.rows.filter(r => r.family === fam).map(m => modelCard(m, busy)).join('')}</div></section>`).join('')}
  </div>`;
  }
  function dlBox(dl) {
    const run = dl.status === 'RUNNING';
    return `<div class="job-head"><div class="phase"><b>${run ? t('models.downloading', { id: esc(dl.current || '') }) : t('job.' + dl.status)}</b><span class="muted">${dl.index != null ? Math.min(dl.total, (dl.done || []).length + (run ? 1 : 0)) : 0} / ${dl.total}</span></div>${run ? `<button class="btn btn--danger btn--sm" data-act="models-cancel">${icon('stop')} ${t('assets.cancel')}</button>` : ''}</div>
      <div class="bar" style="--p:${(Math.max(0, dl.progress || 0) * 100).toFixed(1)}%"><i></i><span class="hopper">${slime('honey', { size: 50, cls: run ? 'hop' : 'idle', dur: .9, hopH: -10 })}</span></div>
      ${(dl.errors || []).map(e => `<p class="err">${esc(e.id)}: ${esc(e.error)}</p>`).join('')}`;
  }

  SB.pages = Object.assign(SB.pages || {}, { assets: { render: assets }, models: { render: models } });
  SB.jobBox = jobBox; SB.dlBox = dlBox; SB.sanctBox = sanctBox;
})(window.SB = window.SB || {});
