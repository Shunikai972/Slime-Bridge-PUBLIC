/* Living ranch scenery (SVG): sky, sun/moon, clouds, hills, barn, windmill, grass. Drawn in code, original. */
(function (SB) {
  'use strict';
  const rng = seed => { let s = seed % 2147483647; if (s <= 0) s += 2147483646; return () => (s = (s * 16807) % 2147483647) / 2147483647; };

  /** Smooth wavy hill path across [0,w], bottom closed at h. */
  function hill(w, h, base, amp, seed, freq = 0.0042) {
    const r = rng(seed), pts = [];
    for (let x = -120; x <= w + 120; x += 110) {
      const y = base - amp * (0.5 + 0.5 * Math.sin(x * freq + seed)) - amp * 0.35 * Math.sin(x * freq * 2.3 + seed * 1.7) + (r() - .5) * amp * 0.12;
      pts.push([x, y]);
    }
    let d = `M${pts[0][0]} ${h} L${pts[0][0]} ${pts[0][1].toFixed(1)}`;
    for (let i = 0; i < pts.length - 1; i++) {
      const p0 = pts[Math.max(0, i - 1)], p1 = pts[i], p2 = pts[i + 1], p3 = pts[Math.min(pts.length - 1, i + 2)];
      const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6], c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
      d += ` C${c1[0].toFixed(1)} ${c1[1].toFixed(1)} ${c2[0].toFixed(1)} ${c2[1].toFixed(1)} ${p2[0]} ${p2[1].toFixed(1)}`;
    }
    return d + ` L${pts[pts.length - 1][0]} ${h} Z`;
  }
  const yAt = (w, base, amp, seed, x, freq = 0.0042) => base - amp * (0.5 + 0.5 * Math.sin(x * freq + seed)) - amp * 0.35 * Math.sin(x * freq * 2.3 + seed * 1.7);

  function cloud(x, y, s, cls, dur, delay) {
    return `<g transform="translate(${x} ${y}) scale(${s})"><g class="cloud ${cls}" style="--dur:${dur}s;--delay:${delay}s"><ellipse cx="0" cy="18" rx="92" ry="22" fill="var(--cloud-shade)"/><ellipse cx="-40" cy="6" rx="42" ry="30" fill="var(--cloud)"/><ellipse cx="6" cy="-8" rx="52" ry="38" fill="var(--cloud)"/><ellipse cx="52" cy="6" rx="40" ry="28" fill="var(--cloud)"/><ellipse cx="0" cy="12" rx="90" ry="20" fill="var(--cloud)"/></g></g>`;
  }
  function grass(w, base, amp, seed, n, color, h) {
    const r = rng(seed + 77); let d = '';
    for (let i = 0; i < n; i++) {
      const x = r() * w, y = yAt(w, base, amp, seed, x) + 6, bh = h * (0.6 + r() * 0.8), lean = (r() - .5) * 16;
      d += `<path class="blade" style="--d:${(r() * 3).toFixed(2)}s" d="M${x.toFixed(0)} ${y.toFixed(0)} q${(lean / 2).toFixed(0)} ${(-bh * .6).toFixed(0)} ${lean.toFixed(0)} ${-bh.toFixed(0)} q${(2 + r() * 3).toFixed(0)} ${(bh * .5).toFixed(0)} ${(5 + r() * 2).toFixed(0)} ${bh.toFixed(0)}Z" fill="${color}"/>`;
    }
    return d;
  }
  function flowers(w, base, amp, seed, n) {
    const r = rng(seed + 5), cols = ['#ffffff', '#ffd54a', '#ff8fbf', '#b7a2ff', '#7fe0ff'];
    let d = '';
    for (let i = 0; i < n; i++) {
      const x = r() * w, y = yAt(w, base, amp, seed, x) + 14 + r() * 20, c = cols[Math.floor(r() * cols.length)], s = 2.6 + r() * 2.6;
      d += `<g transform="translate(${x.toFixed(0)} ${y.toFixed(0)})"><path d="M0 0 V10" stroke="#2a8c4a" stroke-width="1.6"/><circle r="${s.toFixed(1)}" fill="${c}"/><circle r="${(s * .4).toFixed(1)}" fill="#ffb12b"/></g>`;
    }
    return d;
  }
  function fence(x0, x1, w, base, amp, seed) {
    let d = '';
    for (let x = x0; x <= x1; x += 54) {
      const y = yAt(w, base, amp, seed, x) + 8;
      d += `<rect x="${x - 4}" y="${y - 34}" width="9" height="40" rx="3" fill="#b9814b"/><rect x="${x - 4}" y="${y - 34}" width="9" height="6" rx="3" fill="#d9a56d"/>`;
      const y2 = yAt(w, base, amp, seed, x + 54) + 8;
      d += `<path d="M${x} ${y - 24} L${x + 54} ${y2 - 24} M${x} ${y - 11} L${x + 54} ${y2 - 11}" stroke="#a36d3c" stroke-width="5" stroke-linecap="round"/>`;
    }
    return d;
  }
  function barn(x, y, s) {
    return `<g transform="translate(${x} ${y}) scale(${s})"><rect x="-60" y="-70" width="120" height="70" fill="#e5594c"/><path d="M-68 -70 L0 -118 L68 -70Z" fill="#b83a37"/><path d="M-68 -70 L0 -118 L68 -70" fill="none" stroke="#fff3df" stroke-width="5" stroke-linejoin="round"/><rect x="-24" y="-52" width="48" height="52" rx="3" fill="#fff3df"/><path d="M-24 -52 L24 0 M24 -52 L-24 0" stroke="#e5594c" stroke-width="5"/><circle cx="0" cy="-86" r="9" fill="#fff3df" stroke="#b83a37" stroke-width="3"/></g>`;
  }
  function windmill(x, y, s) {
    return `<g transform="translate(${x} ${y}) scale(${s})"><path d="M-14 0 L-8 -90 H8 L14 0Z" fill="#f4e8d3"/><path d="M-9 -90 L0 -108 L9 -90Z" fill="#b87a4a"/><g class="spin" style="transform-origin:0px -96px"><g stroke="#fff" stroke-width="7" stroke-linecap="round"><path d="M0 -96 V-150 M0 -96 V-42 M0 -96 H-54 M0 -96 H54"/></g><g fill="#ffc2da" stroke="#fff" stroke-width="2"><rect x="-6" y="-150" width="12" height="28" rx="3"/><rect x="-6" y="-70" width="12" height="28" rx="3"/><rect x="26" y="-102" width="28" height="12" rx="3"/><rect x="-54" y="-102" width="28" height="12" rx="3"/></g></g><circle cx="0" cy="-96" r="6" fill="#b87a4a"/></g>`;
  }
  function stars(n, seed) {
    const r = rng(seed); let d = '';
    for (let i = 0; i < n; i++) d += `<circle class="twinkle" style="--d:${(r() * 4).toFixed(1)}s" cx="${(r() * 1600).toFixed(0)}" cy="${(r() * 460).toFixed(0)}" r="${(0.8 + r() * 1.9).toFixed(1)}" fill="#fff"/>`;
    return d;
  }
  function motes(n, seed) {
    const r = rng(seed); let d = '';
    for (let i = 0; i < n; i++) d += `<circle class="mote" style="--x:${(r() * 80 - 40).toFixed(0)}px;--dur:${(7 + r() * 9).toFixed(1)}s;--d:${(-r() * 12).toFixed(1)}s" cx="${(r() * 1600).toFixed(0)}" cy="${(380 + r() * 460).toFixed(0)}" r="${(2 + r() * 3.4).toFixed(1)}" fill="${['#fff6a8', '#ffc2da', '#bfe6ff', '#c9ffe2'][i % 4]}"/>`;
    return d;
  }

  const SKY_STYLE = `
.cloud { animation: drift var(--dur) linear var(--delay) infinite; }
.cloud.c2 { opacity: .85; } .cloud.c3 { opacity: .7; }
@keyframes drift { from { transform: translate(var(--x0, -300px), 0); } to { transform: translate(var(--x1, 1900px), 0); } }
.spin { animation: spin 14s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.blade { transform-box: fill-box; transform-origin: 50% 100%; animation: sway 3.4s ease-in-out var(--d) infinite; }
@keyframes sway { 0%, 100% { transform: rotate(-3deg); } 50% { transform: rotate(4deg); } }
.twinkle { animation: tw 3.2s ease-in-out var(--d) infinite; }
@keyframes tw { 0%, 100% { opacity: .25; } 50% { opacity: 1; } }
.mote { animation: rise var(--dur) ease-in-out var(--d) infinite; opacity: .0; }
@keyframes rise { 0% { transform: translate(0, 0); opacity: 0; } 15% { opacity: .85; } 85% { opacity: .6; } 100% { transform: translate(var(--x), -300px); opacity: 0; } }
.sunrays { transform-origin: 1180px 190px; animation: spin 90s linear infinite; }
.layer { transition: transform .5s var(--ease-out); }`;

  /** Full-window background. */
  function background() {
    const W = 1600, H = 900;
    return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMax slice" xmlns="http://www.w3.org/2000/svg"><style>${SKY_STYLE}</style>
<defs>
<radialGradient id="sunG"><stop offset="0" stop-color="var(--sun-core)"/><stop offset=".28" stop-color="var(--sun-core)"/><stop offset=".3" stop-color="var(--sun-glow)"/><stop offset="1" stop-color="var(--sun-glow)" stop-opacity="0"/></radialGradient>
<linearGradient id="hF" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="var(--hill-far)"/><stop offset="1" stop-color="var(--hill-far-2)"/></linearGradient>
<linearGradient id="hM" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="var(--hill-mid)"/><stop offset="1" stop-color="var(--hill-mid-2)"/></linearGradient>
<linearGradient id="hN" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="var(--hill-near)"/><stop offset="1" stop-color="var(--hill-near-2)"/></linearGradient>
</defs>
<g class="layer" data-depth="0.01"><g opacity="var(--stars)">${stars(90, 11)}</g>
<g class="sunrays" opacity="var(--rays)"><path d="${[...Array(12)].map((_, i) => { const a = i * Math.PI / 6; return `M1180 190 L${(1180 + Math.cos(a) * 2400).toFixed(0)} ${(190 + Math.sin(a) * 2400).toFixed(0)} L${(1180 + Math.cos(a + .09) * 2400).toFixed(0)} ${(190 + Math.sin(a + .09) * 2400).toFixed(0)}Z`; }).join(' ')}" fill="var(--sun-core)"/></g>
<circle cx="1180" cy="190" r="190" fill="url(#sunG)"/><circle cx="1180" cy="190" r="58" fill="var(--sun-core)"/>
${cloud(0, 150, 1.5, 'c1', 150, -40)}${cloud(0, 300, 1.0, 'c2', 190, -120)}${cloud(0, 90, 0.9, 'c3', 230, -80)}${cloud(0, 420, 1.2, 'c2', 210, -10)}</g>
<g class="layer" data-depth="0.02"><path d="${hill(W, H, 560, 90, 3)}" fill="url(#hF)"/>${barn(380, 520, 0.8)}${windmill(1010, 520, 0.9)}</g>
<g class="layer" data-depth="0.035"><path d="${hill(W, H, 650, 80, 8)}" fill="url(#hM)"/>${fence(60, 520, W, 650, 80, 8)}${grass(W, 650, 80, 8, 70, 'var(--hill-mid-2)', 18)}</g>
<g class="layer" data-depth="0.055"><path d="${hill(W, H, 775, 70, 14)}" fill="url(#hN)"/>${grass(W, 775, 70, 14, 130, 'var(--hill-near-2)', 26)}${flowers(W, 775, 70, 14, 46)}</g>
${motes(26, 4)}</svg>`;
  }

  /** The hero diorama: two ranches joined by a glowing bridge. */
  function hero() {
    const W = 1100, H = 430;
    const bridge = (() => {
      const x0 = 330, x1 = 770, y = 330, up = 96;
      let planks = '';
      for (let i = 0; i <= 14; i++) {
        const t = i / 14, x = x0 + (x1 - x0) * t, yy = y - Math.sin(Math.PI * t) * up;
        planks += `<rect x="${(x - 11).toFixed(1)}" y="${(yy - 8).toFixed(1)}" width="22" height="16" rx="4" fill="${i % 2 ? '#d9a66a' : '#c8905a'}" stroke="#7a4a1f" stroke-width="2.4" transform="rotate(${((t - .5) * 56).toFixed(1)} ${x.toFixed(1)} ${yy.toFixed(1)})"/>`;
      }
      return `<path d="M${x0} ${y + 6} Q550 ${y - up * 2 + 6} ${x1} ${y + 6}" fill="none" stroke="url(#bridgeG)" stroke-width="34" stroke-linecap="round" opacity=".4" filter="url(#blur)"/>
<path d="M${x0} ${y + 16} Q550 ${y - up * 2 + 16} ${x1} ${y + 16}" fill="none" stroke="#6b3f18" stroke-width="12" stroke-linecap="round" opacity=".5"/>${planks}
<path d="M${x0 + 4} ${y - 22} Q550 ${y - up * 2 - 22} ${x1 - 4} ${y - 22}" fill="none" stroke="#fff3df" stroke-width="5" stroke-linecap="round"/>
<g fill="#fff3df" stroke="#9a6b3a" stroke-width="2">${[0.08, 0.28, 0.5, 0.72, 0.92].map(t => { const x = x0 + (x1 - x0) * t, yy = y - Math.sin(Math.PI * t) * up; return `<rect x="${(x - 3.5).toFixed(1)}" y="${(yy - 26).toFixed(1)}" width="7" height="20" rx="3"/>`; }).join('')}</g>`;
    })();
    return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMax slice" xmlns="http://www.w3.org/2000/svg"><style>${SKY_STYLE.replace(/1180px 190px/g, '900px 100px')}</style>
<defs>
<linearGradient id="hs" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="var(--sky-1)"/><stop offset=".55" stop-color="var(--sky-2)"/><stop offset=".85" stop-color="var(--sky-3)"/><stop offset="1" stop-color="var(--sky-4)"/></linearGradient>
<linearGradient id="hL" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="var(--hill-mid)"/><stop offset="1" stop-color="var(--hill-near-2)"/></linearGradient>
<linearGradient id="hR" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="var(--hill-sr2-mid)"/><stop offset="1" stop-color="var(--hill-sr2-far)"/></linearGradient>
<linearGradient id="bridgeG" x1="0" x2="1"><stop offset="0" stop-color="#34d8a4"/><stop offset=".5" stop-color="#ff7ab0"/><stop offset="1" stop-color="#8f8bff"/></linearGradient>
<radialGradient id="hsun"><stop offset="0" stop-color="var(--sun-core)"/><stop offset=".3" stop-color="var(--sun-core)"/><stop offset=".32" stop-color="var(--sun-glow)"/><stop offset="1" stop-color="var(--sun-glow)" stop-opacity="0"/></radialGradient>
<filter id="blur"><feGaussianBlur stdDeviation="9"/></filter>
</defs>
<rect width="${W}" height="${H}" fill="url(#hs)"/>
<g opacity="var(--stars)">${stars(50, 21).replace(/cy="(\d+)"/g, (m, y) => `cy="${Math.round(y * 0.7)}"`)}</g>
<circle cx="900" cy="100" r="150" fill="url(#hsun)"/><circle cx="900" cy="100" r="44" fill="var(--sun-core)"/>
<g transform="scale(.8)">${cloud(0, 120, 1.2, 'c1', 120, -30)}${cloud(0, 230, 0.8, 'c2', 150, -90)}${cloud(0, 60, 0.7, 'c3', 170, -50)}</g>
<path d="${hill(W, H, 300, 60, 31)}" fill="url(#hR)" opacity=".75" transform="translate(0 -30)"/>
<path d="M-40 ${H} L-40 330 Q120 200 340 ${H - 100} L420 ${H}Z" fill="url(#hL)"/>
<path d="M1140 ${H} L1140 330 Q980 200 760 ${H - 100} L680 ${H}Z" fill="url(#hR)"/>
<g>${grass(420, 360, 50, 5, 40, 'var(--hill-near-2)', 20).replace(/translate/g, 'translate')}</g>
${bridge}
<ellipse cx="170" cy="${H - 10}" rx="260" ry="46" fill="var(--hill-near-2)"/><ellipse cx="930" cy="${H - 10}" rx="260" ry="46" fill="var(--hill-sr2-mid)"/>
<g transform="translate(40 0)">${flowers(300, 360, 20, 9, 14)}</g>${sparkles()}</svg>`;
  }

  function sparkles() { /* little stars twinkling along the bridge */
    const r = rng(99); let d = '';
    for (let i = 0; i < 16; i++) {
      const t = r(), x = 330 + 440 * t, y = 330 - Math.sin(Math.PI * t) * 96 - 30 - r() * 70, s = 4 + r() * 6;
      d += `<path class="twinkle" style="--d:${(r() * 3).toFixed(1)}s" transform="translate(${x.toFixed(0)} ${y.toFixed(0)}) scale(${(s / 6).toFixed(2)})" d="M0 -9 L2.4 -2.4 L9 0 L2.4 2.4 L0 9 L-2.4 2.4 L-9 0 L-2.4 -2.4Z" fill="${['#fff', '#fff6a8', '#ffd1ea'][i % 3]}"/>`;
    }
    return d;
  }

  function parallax(root) {
    const layers = [...root.querySelectorAll('.layer')];
    window.addEventListener('pointermove', e => {
      const x = e.clientX / innerWidth - .5, y = e.clientY / innerHeight - .5;
      layers.forEach(l => { const d = parseFloat(l.dataset.depth || 0); l.style.transform = `translate(${(-x * d * 700).toFixed(1)}px, ${(-y * d * 260).toFixed(1)}px)`; });
    });
  }

  SB.Scene = { background, hero, parallax };
})(window.SB = window.SB || {});
