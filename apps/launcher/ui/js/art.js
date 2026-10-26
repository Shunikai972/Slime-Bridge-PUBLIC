/* Procedural fan art: slimes, largos, plorts, logo, icons. Everything is drawn here as SVG; no game art is used. */
(function (SB) {
  'use strict';
  let uid = 0;
  const nid = p => `${p}${++uid}`;
  const BODY = 'M10 92 C5 54 30 14 60 14 C90 14 115 54 110 92 C109 103 92 107 60 107 C28 107 11 103 10 92 Z';
  const BODY_L = 'M6 98 C-2 40 38 -4 88 -4 C138 -4 178 40 170 98 C169 110 140 114 88 114 C36 114 7 110 6 98 Z';

  /* palette: [highlight, base, shade]; f = features; mouth/eyes = face style; plort = gem colour */
  const SPECIES = {
    pink:     { c: ['#ffd0e2', '#ff82b8', '#df4f8f'], f: ['cheeks'], plort: '#ff6fa8' },
    tabby:    { c: ['#ffe2bd', '#ffa857', '#d9692a'], f: ['ears:#ff8fb0', 'stripes:#7a4bd1', 'tail'], plort: '#ff9a4d' },
    phosphor: { c: ['#d4fff6', '#58eed5', '#1fa59f'], f: ['glow:#7dffe6', 'dots'], mouth: 'o', plort: '#34e8cf' },
    honey:    { c: ['#fff3a8', '#ffcf3d', '#e0911c'], f: ['drips'], plort: '#ffc93c' },
    rock:     { c: ['#e3deef', '#aaa0c4', '#6f6690'], f: ['plates'], mouth: 'flat', plort: '#9a90b8' },
    boom:     { c: ['#ffb9a8', '#ff6e5c', '#c93d3f'], f: ['fuse'], mouth: 'o', plort: '#ff5b6e' },
    crystal:  { c: ['#eadcff', '#ad84ff', '#6a49d1'], f: ['crystals'], plort: '#a58bff' },
    hunter:   { c: ['#c0cdff', '#7487ff', '#3f45b5'], f: ['mask', 'stripes:#ffffff'], mouth: 'flat', plort: '#6b7cff' },
    cotton:   { c: ['#ffffff', '#f6effb', '#d3c7e8'], f: ['bunnyears:#ffc2da', 'fluff'], plort: '#e8dcff' },
    angler:   { c: ['#c8f5ff', '#4cbcec', '#1f6fb0'], f: ['lure'], plort: '#4db9ff' },
    rad:      { c: ['#f9ffb0', '#c9f046', '#78b51d'], f: ['glow:#d9ff5a', 'rings'], plort: '#c9f046' },
    quantum:  { c: ['#ece6ff', '#a08aff', '#5a47c8'], f: ['ghost'], plort: '#a08aff' },
    mosaic:   { c: ['#ffd9f2', '#ff82dc', '#a03fd4'], f: ['tiles'], plort: '#ff7ad9' },
    batty:    { c: ['#d9c9ff', '#8f6fe0', '#4a2f9a'], f: ['ears:#c8a8ff', 'wings:#6f4fc7'], plort: '#8f6fe0' },
    flutter:  { c: ['#ffe6f6', '#ff9fd4', '#d4569f'], f: ['butterfly:#8fe3ff'], plort: '#ff9fd4' },
    ringtail: { c: ['#f4e6cf', '#d9b27a', '#9a6b3a'], f: ['tail', 'ringmask'], plort: '#d9b27a' },
    sloomber: { c: ['#d6d9ff', '#8e93f5', '#4f54bf'], f: ['zzz'], eyes: 'sleepy', plort: '#8e93f5' },
    hyper:    { c: ['#fff5a8', '#ffe036', '#e0a010'], f: ['speed'], plort: '#ffe036' },
    twin:     { c: ['#cdfcea', '#52e0b0', '#1b9d78'], f: ['twin'], plort: '#52e0b0' },
    saber:    { c: ['#ffd1c2', '#ff8a6a', '#c9472e'], f: ['stripes:#6a1f12', 'fangs'], plort: '#ff8a6a' },
  };
  const ALIAS = { slumber: 'sloomber', rad: 'rad' };
  const get = k => SPECIES[ALIAS[k] || k] || SPECIES.pink;

  const split = spec => { const [n, a] = spec.split(':'); return { n, a }; };

  /* feature builders: (palette, accent) -> {back, over, front} (SVG strings, 120x118 space) */
  const F = {
    cheeks: () => ({ front: '' }),
    ears: (c, a) => ({ back: `<path d="M20 40 L16 8 Q34 12 48 24 Z" fill="${c[1]}" stroke="${c[2]}" stroke-width="2.5" stroke-linejoin="round"/><path d="M100 40 L104 8 Q86 12 72 24 Z" fill="${c[1]}" stroke="${c[2]}" stroke-width="2.5" stroke-linejoin="round"/><path d="M23 30 L21 15 Q31 18 38 25Z" fill="${a || '#ff9fbd'}"/><path d="M97 30 L99 15 Q89 18 82 25Z" fill="${a || '#ff9fbd'}"/>` }),
    bunnyears: (c, a) => ({ back: `<g stroke="${c[2]}" stroke-width="2.5"><path d="M34 30 C22 6 26 -14 38 -12 C50 -10 52 14 50 28Z" fill="${c[0]}"/><path d="M86 30 C98 6 94 -14 82 -12 C70 -10 68 14 70 28Z" fill="${c[0]}"/></g><path d="M37 22 C32 8 34 -4 38 -3 C43 -2 44 10 43 22Z" fill="${a || '#ffc2da'}"/><path d="M83 22 C88 8 86 -4 82 -3 C77 -2 76 10 77 22Z" fill="${a || '#ffc2da'}"/>` }),
    stripes: (c, a) => ({ over: `<g stroke="${a || c[2]}" stroke-width="7" stroke-linecap="round" fill="none" opacity=".5"><path d="M22 52 Q34 44 42 30"/><path d="M98 52 Q86 44 78 30"/><path d="M50 22 L52 36"/><path d="M70 22 L68 36"/></g>` }),
    tail: c => ({ back: `<path d="M104 88 C132 88 134 56 118 46" fill="none" stroke="${c[2]}" stroke-width="14" stroke-linecap="round"/><path d="M104 88 C132 88 134 56 118 46" fill="none" stroke="${c[1]}" stroke-width="9" stroke-linecap="round"/><circle cx="118" cy="46" r="6" fill="${c[0]}"/>` }),
    drips: c => ({ over: `<g fill="${c[0]}" opacity=".85"><path d="M24 40 C26 54 24 62 30 62 C36 62 34 52 34 40Z"/><path d="M62 20 C64 36 60 48 66 48 C72 48 70 34 72 20Z"/><path d="M92 36 C94 46 92 52 97 52 C102 52 100 44 100 36Z"/></g>`, front: `<g fill="${c[0]}"><ellipse cx="29" cy="64" rx="4" ry="5"/><ellipse cx="97" cy="54" rx="3.4" ry="4.4"/></g>` }),
    plates: c => ({ front: `<g fill="${c[2]}" stroke="#4d4668" stroke-width="2" stroke-linejoin="round"><path d="M40 24 L50 6 L62 22 Z"/><path d="M60 18 L74 2 L86 22 Z"/><path d="M82 28 L96 16 L102 38 Z"/></g><g fill="${c[0]}" opacity=".6"><path d="M48 14 L50 9 L54 15Z"/><path d="M72 11 L74 6 L78 12Z"/></g>` }),
    fuse: () => ({ front: `<path d="M60 16 Q64 6 70 2" fill="none" stroke="#6b4423" stroke-width="4" stroke-linecap="round"/><g class="glow"><circle cx="72" cy="0" r="9" fill="#ffe066" opacity=".55"/><path d="M72 -8 L74 -1 L81 0 L74 2 L72 9 L70 2 L63 0 L70 -1 Z" fill="#fff6b0" stroke="#ffb02e" stroke-width="1.5"/></g>` }),
    crystals: c => ({ back: `<g fill="${c[0]}" stroke="${c[2]}" stroke-width="2.4" stroke-linejoin="round"><path d="M22 36 L12 6 L38 22Z"/><path d="M98 36 L108 6 L82 22Z"/><path d="M48 20 L58 -10 L70 20Z"/></g>`, over: `<g fill="#fff" opacity=".28"><path d="M30 38 L52 28 L46 60Z"/><path d="M84 50 L96 40 L92 66Z"/></g>` }),
    mask: () => ({ over: `<path d="M12 54 Q60 38 108 54 L106 76 Q60 62 14 76Z" fill="#26264f" opacity=".62"/>` }),
    fluff: c => ({ front: `<g fill="${c[0]}" stroke="${c[2]}" stroke-width="1.6"><circle cx="16" cy="78" r="9"/><circle cx="104" cy="78" r="9"/><circle cx="26" cy="96" r="8"/><circle cx="94" cy="96" r="8"/><circle cx="60" cy="102" r="9"/><circle cx="60" cy="18" r="9"/><circle cx="40" cy="22" r="7"/><circle cx="80" cy="22" r="7"/></g>` }),
    lure: c => ({ back: `<path d="M64 18 C62 -6 92 -14 98 6" fill="none" stroke="${c[2]}" stroke-width="3.4" stroke-linecap="round"/>`, front: `<g class="glow"><circle cx="98" cy="8" r="13" fill="#fff6a0" opacity=".5"/></g><circle cx="98" cy="8" r="6.4" fill="#ffe55c" stroke="#e0a010" stroke-width="2"/><circle cx="96" cy="6" r="2" fill="#fff"/>` }),
    glow: (c, a) => ({ back: `<g class="glow"><circle cx="60" cy="62" r="68" fill="${a || c[0]}" opacity=".32"/><circle cx="60" cy="62" r="52" fill="${a || c[0]}" opacity=".28"/></g>` }),
    dots: () => ({ over: `<g fill="#fff" opacity=".7"><circle cx="30" cy="62" r="3"/><circle cx="92" cy="50" r="2.4"/><circle cx="84" cy="86" r="3.2"/><circle cx="38" cy="90" r="2.4"/><circle cx="70" cy="30" r="2.2"/></g>` }),
    rings: (c, a) => ({ front: `<g fill="none" stroke="${a || '#fff'}" stroke-width="3.2" opacity=".75"><ellipse cx="60" cy="58" rx="62" ry="15" transform="rotate(-16 60 58)" stroke-dasharray="14 8"/></g>` }),
    ghost: c => ({ back: `<g opacity=".3"><path d="${BODY}" fill="${c[1]}" transform="translate(-10 -3)"/></g><g opacity=".2"><path d="${BODY}" fill="${c[0]}" transform="translate(10 -6)"/></g>` }),
    tiles: () => ({ over: `<g opacity=".8"><path d="M14 70 L40 60 L46 84 L18 94Z" fill="#ffe27a"/><path d="M46 36 L74 30 L70 58 L44 56Z" fill="#7ad7ff"/><path d="M76 66 L106 62 L102 92 L78 90Z" fill="#9affb8"/><path d="M26 34 L44 26 L40 44Z" fill="#ff9ad0"/><path d="M80 32 L98 40 L84 52Z" fill="#c4a8ff"/></g>` }),
    wings: (c, a) => ({ back: `<g fill="${a || c[2]}" stroke="${c[2]}" stroke-width="2.4" stroke-linejoin="round"><path d="M16 58 Q-14 40 -8 74 Q2 70 6 80 Q12 72 20 82Z"/><path d="M104 58 Q134 40 128 74 Q118 70 114 80 Q108 72 100 82Z"/></g>` }),
    butterfly: (c, a) => ({ back: `<g stroke="#fff" stroke-width="2" opacity=".92"><path d="M14 56 Q-16 22 -8 62 Q-10 86 18 80Z" fill="${a || '#8fe3ff'}"/><path d="M106 56 Q136 22 128 62 Q130 86 102 80Z" fill="${a || '#8fe3ff'}"/></g>` }),
    ringmask: c => ({ over: `<g fill="${c[2]}" opacity=".7"><ellipse cx="42" cy="62" rx="14" ry="16"/><ellipse cx="78" cy="62" rx="14" ry="16"/></g><g stroke="${c[2]}" stroke-width="5" opacity=".5" fill="none"><path d="M16 84 Q60 98 104 84"/></g>` }),
    zzz: () => ({ front: `<g class="glow" font-family="Fredoka, sans-serif" font-weight="700" fill="#fff" stroke="#6f6bd6" stroke-width="1.4"><text x="92" y="12" font-size="18">Z</text><text x="104" y="-6" font-size="13">z</text></g>` }),
    speed: () => ({ back: `<g stroke="#fff" stroke-width="4" stroke-linecap="round" opacity=".75"><path d="M-14 50 H12"/><path d="M-18 66 H8"/><path d="M-10 82 H14"/></g>` }),
    twin: c => ({ front: `<g transform="translate(78 62) scale(.5)"><path d="${BODY}" fill="${c[1]}" stroke="${c[2]}" stroke-width="4"/><ellipse cx="44" cy="62" rx="8" ry="11" fill="#3b2650"/><ellipse cx="76" cy="62" rx="8" ry="11" fill="#3b2650"/><circle cx="42" cy="57" r="3.4" fill="#fff"/><circle cx="74" cy="57" r="3.4" fill="#fff"/></g>` }),
    fangs: () => ({ front: `<path d="M52 80 L55 91 L58 80Z M62 80 L65 91 L68 80Z" fill="#fff" stroke="#8b3a2a" stroke-width="1.4" stroke-linejoin="round"/>` }),
  };

  function features(list, c) {
    const out = { back: '', over: '', front: '' };
    for (const spec of list || []) {
      const { n, a } = split(spec);
      const r = (F[n] || (() => ({})))(c, a);
      out.back += r.back || ''; out.over += r.over || ''; out.front += r.front || '';
    }
    return out;
  }

  function face(sp, scale = 1) {
    const ey = sp.eyes || 'round';
    const eye = (x) => {
      if (ey === 'sleepy') return `<g transform="translate(${x} 64)"><path d="M-9 0 Q0 9 9 0" fill="none" stroke="#3b2650" stroke-width="3.6" stroke-linecap="round"/></g>`;
      return `<g transform="translate(${x} 62)"><ellipse rx="8.6" ry="11.4" fill="#3b2650"/><g class="pupil"><ellipse cx="-2.4" cy="-4.2" rx="3.6" ry="4.5" fill="#fff"/><circle cx="3.2" cy="4.4" r="1.9" fill="#fff" opacity=".9"/></g></g>`;
    };
    const m = sp.mouth || 'cat';
    const mouth = m === 'o' ? '<ellipse cx="60" cy="79" rx="3.6" ry="4.6" fill="#7a2b4d"/><ellipse cx="60" cy="81" rx="2" ry="2" fill="#ff7a9c"/>'
      : m === 'flat' ? '<path d="M53 78 H67" stroke="#3b2650" stroke-width="3" stroke-linecap="round"/>'
        : '<path d="M52 75 Q56 81 60 75 Q64 81 68 75" fill="none" stroke="#3b2650" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>';
    return `<g class="face"><g class="eyes">${eye(43)}${eye(77)}</g>${mouth}<ellipse cx="29" cy="76" rx="7" ry="4.2" fill="#ff6fa8" opacity=".38"/><ellipse cx="91" cy="76" rx="7" ry="4.2" fill="#ff6fa8" opacity=".38"/></g>`;
  }

  /** One slime as an SVG string. o: {size, cls ('hop'|'idle'|''), delay, flip} */
  function slime(key, o = {}) {
    const sp = get(key), c = sp.c, g = nid('g'), k = nid('k');
    const ft = features(sp.f, c);
    const cls = o.cls === undefined ? 'idle' : o.cls;
    const style = [o.delay ? `--hop-delay:${o.delay}s` : '', o.hopH ? `--hop-h:${o.hopH}px` : '', o.dur ? `--hop-d:${o.dur}s` : ''].filter(Boolean).join(';');
    return `<svg class="slime ${cls}" data-species="${key}" viewBox="-14 -16 148 138" ${o.size ? `width="${o.size}"` : ''} style="overflow:visible;${style}${o.flip ? ';transform:scaleX(-1)' : ''}" aria-hidden="true">
<defs><radialGradient id="${g}" cx="38%" cy="26%" r="85%"><stop offset="0" stop-color="${c[0]}"/><stop offset=".5" stop-color="${c[1]}"/><stop offset="1" stop-color="${c[2]}"/></radialGradient><clipPath id="${k}"><path d="${BODY}"/></clipPath></defs>
<ellipse class="shadow" cx="60" cy="109" rx="46" ry="6.5" fill="#1d1448" opacity=".2"/>
${ft.back}
<path d="${BODY}" fill="url(#${g})" stroke="${c[2]}" stroke-width="3" stroke-linejoin="round"/>
<g clip-path="url(#${k})">${ft.over}<path d="M14 88 Q60 112 106 88 L106 110 L14 110Z" fill="${c[2]}" opacity=".22"/><ellipse cx="60" cy="96" rx="34" ry="9" fill="${c[0]}" opacity=".22"/></g>
${face(sp)}
<ellipse cx="40" cy="31" rx="15" ry="8" fill="#fff" opacity=".6" transform="rotate(-24 40 31)"/><circle cx="26" cy="46" r="3.4" fill="#fff" opacity=".75"/><path d="M96 56 Q104 70 100 88" fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round" opacity=".28"/>
${ft.front}
</svg>`;
  }

  /** A Largo: two bodies merged, dominant face; colours blend from parent a to parent b. */
  function largo(a, b, o = {}) {
    const A = get(a), B = get(b), g = nid('lg'), k = nid('lk'), s = nid('ls');
    const fa = features(A.f, A.c), fb = features(B.f, B.c);
    const wrap = (ft, tx) => `<g transform="translate(${tx} -12) scale(.7 1)">${ft}</g>`;
    const cls = o.cls === undefined ? 'idle' : o.cls;
    const face2 = `<g transform="translate(28 0) scale(1)">${face(B)}</g>`;
    return `<svg class="slime ${cls}" viewBox="-20 -30 216 160" ${o.size ? `width="${o.size}"` : ''} style="overflow:visible;${o.delay ? `--hop-delay:${o.delay}s` : ''}" aria-hidden="true">
<defs><linearGradient id="${g}" x1="0" x2="1"><stop offset="0" stop-color="${A.c[1]}"/><stop offset=".42" stop-color="${A.c[1]}"/><stop offset=".58" stop-color="${B.c[1]}"/><stop offset="1" stop-color="${B.c[1]}"/></linearGradient>
<linearGradient id="${s}" x1="0" x2="1"><stop offset="0" stop-color="${A.c[2]}"/><stop offset="1" stop-color="${B.c[2]}"/></linearGradient>
<clipPath id="${k}"><path d="${BODY_L}"/></clipPath></defs>
<ellipse class="shadow" cx="88" cy="114" rx="76" ry="8" fill="#1d1448" opacity=".2"/>
${wrap(fa.back, -2)}${wrap(fb.back, 78)}
<path d="${BODY_L}" fill="url(#${g})" stroke="url(#${s})" stroke-width="3.4" stroke-linejoin="round"/>
<g clip-path="url(#${k})"><path d="M0 0 H176 V120 H0Z" fill="url(#${s})" opacity="0"/>${wrap(fa.over, -2)}${wrap(fb.over, 78)}
<path d="M10 92 Q88 124 166 92 L166 120 L10 120Z" fill="#000" opacity=".12"/>
<ellipse cx="88" cy="26" rx="62" ry="12" fill="#fff" opacity=".18"/></g>
${face2}
<ellipse cx="44" cy="30" rx="18" ry="9" fill="#fff" opacity=".6" transform="rotate(-22 44 30)"/><circle cx="26" cy="50" r="3.6" fill="#fff" opacity=".75"/>
${wrap(fa.front, -2)}${wrap(fb.front, 78)}
</svg>`;
  }

  function plort(key, o = {}) {
    const col = get(key).plort, g = nid('p');
    return `<svg class="plort ${o.cls || ''}" viewBox="0 0 60 70" ${o.size ? `width="${o.size}"` : ''} aria-hidden="true" style="overflow:visible">
<defs><radialGradient id="${g}" cx="38%" cy="28%" r="80%"><stop offset="0" stop-color="#fff" stop-opacity=".95"/><stop offset=".35" stop-color="${col}"/><stop offset="1" stop-color="${col}" stop-opacity=".78"/></radialGradient></defs>
<ellipse cx="30" cy="66" rx="20" ry="3.4" fill="#1d1448" opacity=".2"/>
<path d="M30 4 C37 18 56 28 56 45 C56 59 44 66 30 66 C16 66 4 59 4 45 C4 28 23 18 30 4Z" fill="url(#${g})" stroke="${col}" stroke-width="2.6" stroke-linejoin="round" style="filter:saturate(1.15) brightness(.92)"/>
<path d="M14 40 Q30 52 46 40" fill="none" stroke="#fff" stroke-width="2.4" opacity=".4" stroke-linecap="round"/>
<ellipse cx="21" cy="30" rx="5" ry="9" fill="#fff" opacity=".6" transform="rotate(24 21 30)"/></svg>`;
  }

  /* ----- wordmark ----- */
  function logo(o = {}) {
    const g = nid('lo'), tg = nid('lt'), deco = o.deco !== false;
    return `<svg class="logo" viewBox="0 0 520 ${deco ? 190 : 120}" ${o.size ? `width="${o.size}"` : ''} aria-label="SlimeBridge" role="img" style="overflow:visible">
<defs><linearGradient id="${g}" x1="0" x2="1"><stop offset="0" stop-color="#ff7ab0"/><stop offset=".5" stop-color="#ffb142"/><stop offset="1" stop-color="#34d8a4"/></linearGradient>
<linearGradient id="${tg}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ffffff"/><stop offset="1" stop-color="#ffe9c8"/></linearGradient></defs>
${deco ? `<g transform="translate(18 64)"><g class="float">${slime('pink', { size: 82, cls: 'idle' }).replace('<svg', '<svg x="0" y="0"')}</g></g>
<g transform="translate(414 52)"><g class="float" style="animation-delay:-2s">${slime('sloomber', { size: 90, cls: 'idle', flip: false }).replace('<svg', '<svg x="0" y="0"')}</g></g>
<path d="M92 164 Q260 70 428 164" fill="none" stroke="#7a4a1f" stroke-width="22" stroke-linecap="round" opacity=".28" transform="translate(0 6)"/>
<path d="M92 160 Q260 66 428 160" fill="none" stroke="url(#${g})" stroke-width="14" stroke-linecap="round"/>
<path d="M92 160 Q260 66 428 160" fill="none" stroke="#fff" stroke-width="4" stroke-linecap="round" stroke-dasharray="3 16" opacity=".85"/>` : ''}
<text x="260" y="${deco ? 104 : 92}" text-anchor="middle" font-family="Fredoka, sans-serif" font-weight="700" font-size="82" letter-spacing="1" fill="url(#${tg})" stroke="#5b3a8c" stroke-width="13" stroke-linejoin="round" paint-order="stroke" style="filter:drop-shadow(0 7px 0 rgba(60,30,120,.45))">SlimeBridge</text>
</svg>`;
  }

  function mark(o = {}) { /* compact rail logo: arch + two blobs */
    const g = nid('mk');
    return `<svg viewBox="0 0 120 84" ${o.size ? `width="${o.size}"` : ''} aria-label="SlimeBridge" role="img" style="overflow:visible">
<defs><linearGradient id="${g}" x1="0" x2="1"><stop offset="0" stop-color="#ff7ab0"/><stop offset=".5" stop-color="#ffb142"/><stop offset="1" stop-color="#34d8a4"/></linearGradient></defs>
<path d="M18 68 Q60 8 102 68" fill="none" stroke="#4a2c12" stroke-width="15" stroke-linecap="round" opacity=".5"/>
<path d="M18 66 Q60 6 102 66" fill="none" stroke="url(#${g})" stroke-width="11" stroke-linecap="round"/>
<g transform="translate(-2 20) scale(.26)">${slime('pink', { cls: '' }).replace(/<svg[^>]*>/, '').replace('</svg>', '')}</g>
<g transform="translate(70 20) scale(.26)">${slime('sloomber', { cls: '' }).replace(/<svg[^>]*>/, '').replace('</svg>', '')}</g>
</svg>`;
  }

  /* ----- navigation icons: chunky two-tone cartoon glyphs on a 48 grid ----- */
  const ST = 'stroke="#3a2a4d" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"';
  const ICONS = {
    home: `<path d="M6 24 L24 7 L42 24 V41 H6Z" fill="#ffd76a" ${ST}/><path d="M3 25 L24 5 L45 25" fill="none" stroke="#e8553d" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/><rect x="19" y="27" width="10" height="14" rx="2" fill="#a45f2a" ${ST}/><circle cx="35" cy="30" r="3.5" fill="#fff" ${ST}/>`,
    games: `<rect x="5" y="11" width="38" height="26" rx="9" fill="#9fe6c9" ${ST}/><circle cx="31" cy="22" r="3" fill="#ff6fa8" ${ST}/><circle cx="37" cy="27" r="3" fill="#ffd76a" ${ST}/><path d="M13 24 H21 M17 20 V28" ${ST} fill="none"/>`,
    assets: `<path d="M7 17 L24 8 L41 17 V33 L24 42 L7 33Z" fill="#ffcf7a" ${ST}/><path d="M7 17 L24 26 L41 17 M24 26 V42" fill="none" ${ST}/><path d="M24 2 V15 M19 10 L24 15 L29 10" fill="none" stroke="#e8553d" stroke-width="3.6" stroke-linecap="round" stroke-linejoin="round"/>`,
    models: `<path d="M5 38 C2 22 12 12 22 12 C33 12 41 22 39 38Z" fill="#ff9fd4" ${ST}/><path d="M18 40 C16 26 24 18 32 18 C41 18 47 26 45 40Z" fill="#9ae8ff" ${ST}/><circle cx="14" cy="28" r="2.2" fill="#3a2a4d"/><circle cx="24" cy="28" r="2.2" fill="#3a2a4d"/><circle cx="30" cy="31" r="2" fill="#3a2a4d"/><circle cx="39" cy="31" r="2" fill="#3a2a4d"/>`,
    profiles: `<rect x="6" y="9" width="36" height="30" rx="7" fill="#c9b8ff" ${ST}/><circle cx="18" cy="21" r="5" fill="#fff" ${ST}/><path d="M10 34 C10 28 26 28 26 34" fill="#fff" ${ST}/><path d="M30 20 H38 M30 27 H38" fill="none" ${ST}/>`,
    recovery: `<path d="M24 6 L40 12 V24 C40 33 33 39 24 43 C15 39 8 33 8 24 V12Z" fill="#9fe6c9" ${ST}/><path d="M16 24 L22 30 L33 18" fill="none" ${ST}/>`,
    multiplayer: `<circle cx="16" cy="16" r="7" fill="#ffc2da" ${ST}/><path d="M3 41 C3 28 29 28 29 41Z" fill="#ff6fa8" ${ST}/><circle cx="33" cy="19" r="6" fill="#bfe6ff" ${ST}/><path d="M22 41 C22 31 45 31 45 41Z" fill="#4db9ff" ${ST}/>`,
    options: `<circle cx="24" cy="24" r="9" fill="#fff3a8" ${ST}/><path d="M24 4 V10 M24 38 V44 M4 24 H10 M38 24 H44 M10 10 L14 14 M34 34 L38 38 M10 38 L14 34 M34 14 L38 10" ${ST} fill="none"/><circle cx="24" cy="24" r="3.4" fill="#e29a12" ${ST}/>`,
    about: `<path d="M24 42 C8 31 5 22 5 17 C5 10 10 6 15 6 C19 6 22 8 24 12 C26 8 29 6 33 6 C38 6 43 10 43 17 C43 22 40 31 24 42Z" fill="#ff7a8e" ${ST}/><circle cx="24" cy="20" r="2.4" fill="#fff"/><path d="M24 25 V33" stroke="#fff" stroke-width="3.6" stroke-linecap="round"/>`,
    sun: `<circle cx="24" cy="24" r="9" fill="#ffd54a" ${ST}/><path d="M24 5 V10 M24 38 V43 M5 24 H10 M38 24 H43 M11 11 L15 15 M33 33 L37 37 M11 37 L15 33 M33 15 L37 11" stroke="#e29a12" stroke-width="3.4" stroke-linecap="round"/>`,
    moon: `<path d="M34 6 C20 6 10 16 10 28 C10 38 18 44 28 44 C36 44 42 40 44 34 C34 36 24 28 24 18 C24 13 28 8 34 6Z" fill="#fff3a8" ${ST}/>`,
    play: `<path d="M10 6 L40 24 L10 42Z" fill="#fff" ${ST}/>`,
    folder: `<path d="M4 14 C4 10 6 8 10 8 H19 L24 14 H38 C42 14 44 16 44 20 V36 C44 40 42 42 38 42 H10 C6 42 4 40 4 36Z" fill="#ffd76a" ${ST}/>`,
    search: `<circle cx="20" cy="20" r="12" fill="#bfe6ff" ${ST}/><path d="M29 29 L42 42" stroke="#3a2a4d" stroke-width="6" stroke-linecap="round"/>`,
    download: `<path d="M24 4 V30 M13 20 L24 31 L35 20" fill="none" stroke="#fff" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/><path d="M6 36 V42 H42 V36" fill="none" stroke="#fff" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>`,
    check: `<path d="M8 25 L19 36 L40 12" fill="none" stroke="currentColor" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/>`,
    star: `<path d="M24 4 L30 17 L44 19 L34 29 L36 43 L24 36 L12 43 L14 29 L4 19 L18 17Z" fill="currentColor" stroke="#3a2a4d" stroke-width="2.6" stroke-linejoin="round"/>`,
    link: `<path d="M20 28 C14 28 10 24 10 19 C10 14 14 10 19 10 H25 M28 20 C34 20 38 24 38 29 C38 34 34 38 29 38 H23 M18 29 L30 19" fill="none" stroke="#fff" stroke-width="5" stroke-linecap="round"/>`,
    stop: `<rect x="10" y="10" width="28" height="28" rx="6" fill="#fff" ${ST}/>`,
    refresh: `<path d="M38 18 A15 15 0 1 0 39 29" fill="none" stroke="#fff" stroke-width="6" stroke-linecap="round"/><path d="M40 6 V19 H27" fill="none" stroke="#fff" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>`,
    plus: `<path d="M24 8 V40 M8 24 H40" stroke="#fff" stroke-width="7" stroke-linecap="round"/>`,
    trash: `<path d="M10 14 H38 L35 42 H13Z" fill="#fff" ${ST}/><path d="M6 14 H42 M18 14 V8 H30 V14" fill="none" ${ST}/>`,
  };
  function icon(name, cls = 'ico') {
    return `<svg class="${cls}" viewBox="0 0 48 48" aria-hidden="true">${ICONS[name] || ''}</svg>`;
  }



  /* ----- header wordmark: bubbly pink lettering with white outline (original, no game logo) ----- */
  function wordmark(o = {}) {
    const g = nid('wm');
    return `<svg viewBox="0 0 560 128" ${o.size ? `height="${o.size}"` : ''} role="img" aria-label="SlimeBridge Launcher" style="overflow:visible">
<defs><linearGradient id="${g}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ff8ab4"/><stop offset=".55" stop-color="#ff4f8f"/><stop offset="1" stop-color="#e8306f"/></linearGradient></defs>
<text x="8" y="84" font-family="Fredoka, sans-serif" font-weight="700" font-size="88" letter-spacing="1" fill="#a8195a" stroke="#a8195a" stroke-width="22" stroke-linejoin="round" transform="translate(0 6)">SlimeBridge</text>
<text x="8" y="84" font-family="Fredoka, sans-serif" font-weight="700" font-size="88" letter-spacing="1" fill="url(#${g})" stroke="#fff" stroke-width="15" stroke-linejoin="round" paint-order="stroke">SlimeBridge</text>
<path d="M30 40 q6 -14 22 -16" fill="none" stroke="#fff" stroke-width="5" stroke-linecap="round" opacity=".7"/>
<text x="286" y="118" text-anchor="middle" font-family="Fredoka, sans-serif" font-weight="600" font-size="21" letter-spacing="14" fill="#fff" stroke="#16486b" stroke-width="5" stroke-linejoin="round" paint-order="stroke">LAUNCHER</text>
<path d="M528 20 l4 10 10 4 -10 4 -4 10 -4 -10 -10 -4 10 -4Z" fill="#fff" opacity=".95"/>
</svg>`;
  }

  /* ----- line glyphs (viewBox 32): navigation + interface ----- */
  const GLYPHS = {
    home: 'M4 15 L16 5 L28 15 M7 13 V27 H25 V13 M13 27 V19 H19 V27',
    news: 'M6 5 H22 V25 a2 2 0 0 0 2 2 H8 a2 2 0 0 1 -2 -2Z M22 11 H27 V25 a2 2 0 0 1 -2 2 H24 M10 11 H18 M10 16 H18 M10 21 H15',
    games: 'M9 9 H23 a6 6 0 0 1 6 6 V19 a4 4 0 0 1 -7 2.6 L21 20 H11 L10 21.6 A4 4 0 0 1 3 19 V15 a6 6 0 0 1 6 -6Z M10 13 V17 M8 15 H12 M21 14.6 H21.1 M24 17.4 H24.1',
    extract: 'M6 12 L16 6 L26 12 V22 L16 28 L6 22Z M6 12 L16 18 L26 12 M16 18 V28',
    profiles: 'M5 7 H27 V25 H5Z M11 17 a3 3 0 1 0 0 -6 a3 3 0 0 0 0 6 M7 23 c0 -4 8 -4 8 0 M19 12 H24 M19 17 H24',
    recovery: 'M16 3 L27 7 V16 C27 22 22 26 16 29 C10 26 5 22 5 16 V7Z M11 16 L15 20 L22 12',
    models: 'M3 25 C2 16 8 9 13 9 C19 9 23 16 22 25Z M13 26 C12 20 16 15 21 15 C26 15 30 20 29 26Z',
    multiplayer: 'M11 14 a4 4 0 1 0 0 -8 a4 4 0 0 0 0 8 M3 26 c0 -5 4 -8 8 -8 s8 3 8 8 M22 13 a3.2 3.2 0 1 0 0 -6.4 M21 18.5 c4.5 -.4 8 2 8 7.5',
    options: 'M16 21 a5 5 0 1 0 0 -10 a5 5 0 0 0 0 10 M16 3 V7 M16 25 V29 M3 16 H7 M25 16 H29 M7 7 l3 3 M22 22 l3 3 M7 25 l3 -3 M22 10 l3 -3',
    chevR: 'M12 6 L21 16 L12 26', chevL: 'M20 6 L11 16 L20 26', chevD: 'M6 12 L16 21 L26 12',
    folder: 'M3 9 a2 2 0 0 1 2 -2 h7 l3 3 h12 a2 2 0 0 1 2 2 v12 a2 2 0 0 1 -2 2 H5 a2 2 0 0 1 -2 -2Z',
    min: 'M8 22 H24', max: 'M8 8 H24 V24 H8Z', close: 'M9 9 L23 23 M23 9 L9 23',
    check: 'M7 17 L14 24 L26 9', heart: 'M16 27 C6 20 3 14 3 10 C3 6 6 4 9 4 C12 4 14 6 16 9 C18 6 20 4 23 4 C26 4 29 6 29 10 C29 14 26 20 16 27Z',
    search: 'M14 24 a9 9 0 1 0 0 -18 a9 9 0 0 0 0 18 M21 21 L28 28', refresh: 'M26 12 A11 11 0 1 0 27 20 M27 4 V12 H19',
    star: 'M16 3 L20 12 L30 13 L22 20 L25 29 L16 24 L7 29 L10 20 L2 13 L12 12Z', link: 'M13 19 a5 5 0 0 1 0 -7 l3 -3 a5 5 0 0 1 7 7 l-2 2 M19 13 a5 5 0 0 1 0 7 l-3 3 a5 5 0 0 1 -7 -7 l2 -2',
    stop: 'M8 8 H24 V24 H8Z', plus: 'M16 6 V26 M6 16 H26', trash: 'M6 9 H26 M12 9 V5 H20 V9 M9 9 L10.5 27 H21.5 L23 9',
    download: 'M16 4 V20 M9 14 L16 21 L23 14 M5 26 H27',
  };
  function glyph(name, cls = 'gl') {
    if (name === 'play') return `<svg class="${cls}" viewBox="0 0 32 32" aria-hidden="true"><path d="M9 5 L27 16 L9 27Z" fill="currentColor" stroke="currentColor" stroke-width="3" stroke-linejoin="round"/></svg>`;
    return `<svg class="${cls}" viewBox="0 0 32 32" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round"><path d="${GLYPHS[name] || ''}"/></svg>`;
  }

  /* pupils follow the cursor (cheap, throttled by rAF) */
  function trackEyes() {
    let raf = 0, mx = 0, my = 0;
    window.addEventListener('pointermove', e => { mx = e.clientX; my = e.clientY; if (!raf) raf = requestAnimationFrame(apply); });
    function apply() {
      raf = 0;
      document.querySelectorAll('.slime .pupil').forEach(p => {
        const r = p.getBoundingClientRect(); if (!r.width) return;
        const dx = mx - (r.left + r.width / 2), dy = my - (r.top + r.height / 2), d = Math.hypot(dx, dy) || 1, m = Math.min(3.4, d / 60);
        p.style.transform = `translate(${(dx / d) * m}px, ${(dy / d) * m}px)`;
      });
    }
  }

  SB.Art = { slime, largo, plort, logo, mark, wordmark, glyph, icon, trackEyes, SPECIES, BODY };
})(window.SB = window.SB || {});
