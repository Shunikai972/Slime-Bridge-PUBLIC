/* Profiles & saves, and the recovery center (carried over from the retired Qt window). Read-only: nothing here edits a save or a game. */
(function (SB) {
  'use strict';
  const { esc } = SB.ui, { slime, icon } = SB.Art, t = (...a) => SB.t(...a);
  const L = SB.local = SB.local || {};
  const TONE = { LINKED: 'good', SR1_ONLY: 'info', SR2_ONLY: 'info', PENDING_COUNTERPART: 'busy', REVERIFY_REQUIRED: 'warn', CONFLICTED: 'bad' };
  const SEV = { error: 'bad', warning: 'warn', info: 'info' };
  const REALM = { SR1: 'Slime Rancher', SR2: 'Slime Rancher 2' };

  function profiles(S) {
    const P = L.profiles;
    if (!P) return `<div class="page"><div class="page-title"><h1>${t('nav.profiles')}</h1><p>${t('prof.sub')}</p></div><p class="muted">${t('prof.loading')}</p></div>`;
    const rows = P.pairs.length ? P.pairs.map(p => `<section class="panel stack">
      <div class="row between"><h2>${esc(p.label)}</h2><span class="pill ${TONE[p.status] || 'muted'}">${esc(t('prof.s_' + p.status))}</span></div>
      <p class="muted">${esc(p.status_text)}</p>
      ${['SR1', 'SR2'].map(r => `<div class="row"><b>${REALM[r]}</b>${p.endpoints[r] ? `<code>${esc(p.endpoints[r].save)}</code><span class="pill ${p.endpoints[r].compatibility === 'VALID' ? 'good' : p.endpoints[r].compatibility === 'INCOMPATIBLE' ? 'bad' : 'warn'}">${esc(p.endpoints[r].compatibility.replace(/_/g, ' ').toLowerCase())}</span>` : `<span class="faint">${t('prof.none')}</span>`}</div>`).join('')}
    </section>`).join('') : `<section class="panel stack">${slime('pink', { size: 64, cls: 'idle' })}<h2>${t('prof.empty')}</h2><p class="muted">${t('prof.empty_body')}</p></section>`;
    return `<div class="page"><div class="page-title"><h1>${t('nav.profiles')}</h1><p>${t('prof.sub')}</p></div>${rows}<p class="faint small">${t('prof.readonly')}</p></div>`;
  }

  function recovery(S) {
    const R = L.recovery;
    if (!R) return `<div class="page"><div class="page-title"><h1>${t('nav.recovery')}</h1><p>${t('rec.sub')}</p></div><p class="muted">${t('prof.loading')}</p></div>`;
    const status = R.summary.status;
    const groups = R.groups.map(g => `<section class="stack"><h2>${esc(g.category.charAt(0) + g.category.slice(1).toLowerCase())}</h2>${g.issues.map(i => `<div class="panel stack${i.minor ? ' minor' : ''}">
      <div class="row between"><b>${esc(i.title)}</b><span class="pill ${SEV[i.severity] || 'info'}">${esc(t('rec.sev_' + i.severity))}</span></div><p class="muted">${esc(i.summary)}</p></div>`).join('')}</section>`).join('');
    return `<div class="page"><div class="page-title"><h1>${t('nav.recovery')}</h1><p>${t('rec.sub')}</p></div>
      <section class="panel stack"><div class="row between"><h2>${t('rec.' + status)}</h2><button class="btn btn--sky" data-act="recovery-export">${icon('refresh')} ${t('rec.export')}</button></div>
      <p class="faint small">${t('rec.export_body')}</p>${L.exported ? `<p class="ok">${t('rec.exported', { name: esc(L.exported) })}</p>` : ''}</section>${groups}</div>`;
  }

  SB.pages = Object.assign(SB.pages || {}, { profiles: { render: profiles }, recovery: { render: recovery } });
})(window.SB = window.SB || {});
