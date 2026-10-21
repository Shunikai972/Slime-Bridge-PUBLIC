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

  /** Burst of little plorts from an element (decorative; skipped with reduced motion). */
  function confetti(from, n = 14) {
    if (document.documentElement.classList.contains('reduce-motion')) return;
    const cv = $('#fx'), ctx = cv.getContext('2d'), dpr = devicePixelRatio || 1;
    if (cv.width !== innerWidth * dpr) { cv.width = innerWidth * dpr; cv.height = innerHeight * dpr; }
    const r = from.getBoundingClientRect(), cols = ['#ff6fa8', '#ffc93c', '#34d8a4', '#4db9ff', '#a58bff', '#ff9a4d'];
    const ps = [...Array(n)].map(() => ({ x: r.left + r.width / 2, y: r.top + r.height / 2, vx: (Math.random() - .5) * 11, vy: -5 - Math.random() * 8, s: 5 + Math.random() * 6, c: cols[Math.floor(Math.random() * cols.length)], life: 1, rot: Math.random() * 6 }));
    (function frame() {
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, innerWidth, innerHeight);
      let alive = false;
      for (const p of ps) {
        p.vy += .38; p.x += p.vx; p.y += p.vy; p.life -= .014; p.rot += .15; if (p.life <= 0) continue; alive = true;
        ctx.save(); ctx.globalAlpha = Math.max(0, p.life); ctx.translate(p.x, p.y); ctx.rotate(p.rot); ctx.fillStyle = p.c;
        ctx.beginPath(); ctx.moveTo(0, -p.s); ctx.bezierCurveTo(p.s * .9, -p.s * .2, p.s, p.s * .9, 0, p.s); ctx.bezierCurveTo(-p.s, p.s * .9, -p.s * .9, -p.s * .2, 0, -p.s); ctx.fill();
        ctx.fillStyle = 'rgba(255,255,255,.7)'; ctx.beginPath(); ctx.ellipse(-p.s * .3, -p.s * .2, p.s * .18, p.s * .35, .4, 0, 7); ctx.fill(); ctx.restore();
      }
      if (alive) requestAnimationFrame(frame); else ctx.clearRect(0, 0, innerWidth, innerHeight);
    })();
  }

  SB.ui = { $, $$, esc, sleep, toast, modal, fmtBytes, pct, confetti };
})(window.SB = window.SB || {});
