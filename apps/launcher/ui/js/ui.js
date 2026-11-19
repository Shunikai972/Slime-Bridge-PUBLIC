/* Small UI helpers: escaping, toasts, modals, confetti of plorts, formatting. */
(function (SB) {
  'use strict';
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  function toast(message, tone = '') {
    const box = $('#toasts'), node = document.createElement('div');
    node.className = `toast ${tone}`; node.textContent = message; box.appendChild(node);
    setTimeout(() => { node.style.transition = 'opacity .4s, transform .4s'; node.style.opacity = '0'; node.style.transform = 'translateY(14px)'; setTimeout(() => node.remove(), 450); }, 4600);
  }

  /** Modal with a cream panel. buttons: [{label, cls, value}] -> resolves the clicked value (or null on backdrop/Escape). */
  function modal({ title, body, buttons = [{ label: 'OK', cls: 'btn--sky', value: true }], art = '', collect = null }) {
    return new Promise(resolve => {
      const back = document.createElement('div');
      back.className = 'modal-back';
      back.innerHTML = `<div class="panel modal" role="dialog" aria-modal="true" aria-label="${esc(title)}"><div class="stack">${art ? `<div class="row" style="justify-content:center">${art}</div>` : ''}<h2>${esc(title)}</h2><div class="stack small-gap">${body}</div><div class="row" style="justify-content:flex-end;margin-top:6px">${buttons.map((b, i) => `<button class="btn ${b.cls || 'btn--ghost'}" data-i="${i}">${esc(b.label)}</button>`).join('')}</div></div></div>`;
      const close = v => { const data = collect ? collect(back) : undefined; document.removeEventListener('keydown', onKey); back.remove(); resolve(collect ? { value: v, data } : v); };
      const onKey = e => { if (e.key === 'Escape') close(null); };
      back.addEventListener('click', e => { const b = e.target.closest('[data-i]'); if (b) close(buttons[+b.dataset.i].value); else if (e.target === back) close(null); });
      document.addEventListener('keydown', onKey);
      $('#modal-root').appendChild(back);
      (back.querySelector('.btn--sky, .btn--bridge, .btn') || back).focus();
    });
  }

  const fmtBytes = n => n == null ? '' : n > 1e9 ? (n / 1e9).toFixed(1) + ' GB' : n > 1e6 ? (n / 1e6).toFixed(0) + ' MB' : (n / 1e3).toFixed(0) + ' KB';
  const pct = v => `${Math.round(Math.max(0, Math.min(1, v || 0)) * 100)}%`;

  /* kept exported for callers; decorative confetti disabled */
  function confetti() {}

  SB.ui = { $, $$, esc, sleep, toast, modal, fmtBytes, pct, confetti };
})(window.SB = window.SB || {});
