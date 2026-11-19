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

  /** Supplied final icon art only; unfinished designs use the artist's question mark. */
  function largo(a, b, o = {}) {
    const alias = k => k === 'sloomber' ? 'slumber' : k;
    const supplied = new Set(['quantum_angler', 'quantum_batty', 'quantum_cotton', 'quantum_flutter', 'quantum_ringtail',
      'quantum_slumber', 'quantum_twin', 'rad_angler', 'rad_batty', 'rad_cotton', 'rad_flutter', 'rad_hyper', 'rad_ringtail',
      'rad_slumber', 'rad_twin']);
    const direct = `${alias(a)}_${alias(b)}`, reverse = `${alias(b)}_${alias(a)}`;
    const key = supplied.has(direct) ? direct : supplied.has(reverse) ? reverse : 'unknown';
    const size = Number(o.size), width = Number.isFinite(size) && size > 0 ? ` width="${size}"` : '';
    return `<img class="largo-icon" src="img/largos/${key}.png"${width} alt="" draggable="false">`;
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

  /* ----- button icons: clean monochrome vector strokes matching the sidebar glyphs ----- */
  const ICON_PATHS = {
    home: 'M4 15 L16 5 L28 15 M7 13 V27 H25 V13 M13 27 V19 H19 V27',
    games: 'M9 9 H23 a6 6 0 0 1 6 6 V19 a4 4 0 0 1 -7 2.6 L21 20 H11 L10 21.6 A4 4 0 0 1 3 19 V15 a6 6 0 0 1 6 -6Z M10 13 V17 M8 15 H12 M21 14.6 H21.1 M24 17.4 H24.1',
    assets: 'M6 12 L16 6 L26 12 V22 L16 28 L6 22Z M6 12 L16 18 L26 12 M16 18 V28',
    extract: 'M6 12 L16 6 L26 12 V22 L16 28 L6 22Z M6 12 L16 18 L26 12 M16 18 V28',
    models: 'M3 25 C2 16 8 9 13 9 C19 9 23 16 22 25Z M13 26 C12 20 16 15 21 15 C26 15 30 20 29 26Z',
    profiles: 'M5 7 H27 V25 H5Z M11 17 a3 3 0 1 0 0 -6 a3 3 0 0 0 0 6 M7 23 c0 -4 8 -4 8 0 M19 12 H24 M19 17 H24',
    recovery: 'M16 3 L27 7 V16 C27 22 22 26 16 29 C10 26 5 22 5 16 V7Z M11 16 L15 20 L22 12',
    multiplayer: 'M11 14 a4 4 0 1 0 0 -8 a4 4 0 0 0 0 8 M3 26 c0 -5 4 -8 8 -8 s8 3 8 8 M22 13 a3.2 3.2 0 1 0 0 -6.4 M21 18.5 c4.5 -.4 8 2 8 7.5',
    options: 'M16 20.5 a4.5 4.5 0 1 0 0 -9 a4.5 4.5 0 0 0 0 9 M13.5 4 h5 l.9 3.1 a9.5 9.5 0 0 1 2.4 1 l2.9 -1.5 l3.5 3.5 l-1.5 2.9 a9.5 9.5 0 0 1 1 2.4 l3.1 .9 v5 l-3.1 .9 a9.5 9.5 0 0 1 -1 2.4 l1.5 2.9 l-3.5 3.5 l-2.9 -1.5 a9.5 9.5 0 0 1 -2.4 1 l-.9 3.1 h-5 l-.9 -3.1 a9.5 9.5 0 0 1 -2.4 -1 l-2.9 1.5 l-3.5 -3.5 l1.5 -2.9 a9.5 9.5 0 0 1 -1 -2.4 l-3.1 -.9 v-5 l3.1 -.9 a9.5 9.5 0 0 1 1 -2.4 l-1.5 -2.9 l3.5 -3.5 l2.9 1.5 a9.5 9.5 0 0 1 2.4 -1 Z',
    about: 'M16 27 C6 20 3 14 3 10 C3 6 6 4 9 4 C12 4 14 6 16 9 C18 6 20 4 23 4 C26 4 29 6 29 10 C29 14 26 20 16 27Z',
    folder: 'M3 9 a2 2 0 0 1 2 -2 h7 l3 3 h12 a2 2 0 0 1 2 2 v12 a2 2 0 0 1 -2 2 H5 a2 2 0 0 1 -2 -2Z',
    search: 'M14 24 a9 9 0 1 0 0 -18 a9 9 0 0 0 0 18 M21 21 L28 28',
    download: 'M16 4 V20 M9 14 L16 21 L23 14 M5 26 H27',
    check: 'M7 17 L14 24 L26 9',
    star: 'M16 3 L20 12 L30 13 L22 20 L25 29 L16 24 L7 29 L10 20 L2 13 L12 12Z',
    link: 'M13 19 a5 5 0 0 1 0 -7 l3 -3 a5 5 0 0 1 7 7 l-2 2 M19 13 a5 5 0 0 1 0 7 l-3 3 a5 5 0 0 1 -7 -7 l2 -2',
    refresh: 'M26 12 A11 11 0 1 0 27 20 M27 4 V12 H19',
    plus: 'M16 6 V26 M6 16 H26',
    trash: 'M6 9 H26 M12 9 V5 H20 V9 M9 9 L10.5 27 H21.5 L23 9',
  };
  function icon(name, cls = 'ico') {
    if (name === 'play') return `<svg class="${cls}" viewBox="0 0 32 32" aria-hidden="true"><path d="M9 6 L26 16 L9 26Z" fill="currentColor" stroke="currentColor" stroke-width="2.4" stroke-linejoin="round"/></svg>`;
    if (name === 'stop') return `<svg class="${cls}" viewBox="0 0 32 32" aria-hidden="true"><rect x="7" y="7" width="18" height="18" rx="3" fill="currentColor"/></svg>`;
    return `<svg class="${cls}" viewBox="0 0 32 32" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round"><path d="${ICON_PATHS[name] || ''}"/></svg>`;
  }

  /* ----- header wordmark: clean, crisp launcher branding (no sparkle icon or bubble chrome) ----- */
  function wordmark(o = {}) {
    return `<svg viewBox="0 0 360 72" ${o.size ? `height="${o.size}"` : ''} role="img" aria-label="SlimeBridge Launcher" style="overflow:visible">
<text x="2" y="45" font-family="Fredoka, sans-serif" font-weight="700" font-size="46" letter-spacing=".2" fill="#ffffff" stroke="#103652" stroke-width="7" stroke-linejoin="round" paint-order="stroke">Slime<tspan fill="#ff8db5">Bridge</tspan></text>
<text x="4" y="65" font-family="Nunito, sans-serif" font-weight="800" font-size="11.5" letter-spacing="4.2" fill="rgba(255,255,255,.88)">LAUNCHER</text>
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
    options: 'M16 20.5 a4.5 4.5 0 1 0 0 -9 a4.5 4.5 0 0 0 0 9 M13.5 4 h5 l.9 3.1 a9.5 9.5 0 0 1 2.4 1 l2.9 -1.5 l3.5 3.5 l-1.5 2.9 a9.5 9.5 0 0 1 1 2.4 l3.1 .9 v5 l-3.1 .9 a9.5 9.5 0 0 1 -1 2.4 l1.5 2.9 l-3.5 3.5 l-2.9 -1.5 a9.5 9.5 0 0 1 -2.4 1 l-.9 3.1 h-5 l-.9 -3.1 a9.5 9.5 0 0 1 -2.4 -1 l-2.9 1.5 l-3.5 -3.5 l1.5 -2.9 a9.5 9.5 0 0 1 -1 -2.4 l-3.1 -.9 v-5 l3.1 -.9 a9.5 9.5 0 0 1 1 -2.4 l-1.5 -2.9 l3.5 -3.5 l2.9 1.5 a9.5 9.5 0 0 1 2.4 -1 Z',
    chevR: 'M12 6 L21 16 L12 26', chevL: 'M20 6 L11 16 L20 26', chevD: 'M6 12 L16 21 L26 12',
    folder: 'M3 9 a2 2 0 0 1 2 -2 h7 l3 3 h12 a2 2 0 0 1 2 2 v12 a2 2 0 0 1 -2 2 H5 a2 2 0 0 1 -2 -2Z',
    min: 'M8 22 H24', max: 'M8 8 H24 V24 H8Z', close: 'M9 9 L23 23 M23 9 L9 23',
    check: 'M7 17 L14 24 L26 9', heart: 'M16 27 C6 20 3 14 3 10 C3 6 6 4 9 4 C12 4 14 6 16 9 C18 6 20 4 23 4 C26 4 29 6 29 10 C29 14 26 20 16 27Z',
    search: 'M14 24 a9 9 0 1 0 0 -18 a9 9 0 0 0 0 18 M21 21 L28 28', refresh: 'M26 12 A11 11 0 1 0 27 20 M27 4 V12 H19',
    star: 'M16 3 L20 12 L30 13 L22 20 L25 29 L16 24 L7 29 L10 20 L2 13 L12 12Z', link: 'M13 19 a5 5 0 0 1 0 -7 l3 -3 a5 5 0 0 1 7 7 l-2 2 M19 13 a5 5 0 0 1 0 7 l-3 3 a5 5 0 0 1 -7 -7 l2 -2',
    stop: 'M8 8 H24 V24 H8Z', plus: 'M16 6 V26 M6 16 H26', trash: 'M6 9 H26 M12 9 V5 H20 V9 M9 9 L10.5 27 H21.5 L23 9',
    mods: 'M9 4 h4 a2 2 0 1 1 4 0 h4 v6 a2 2 0 1 0 0 4 v6 h-6 a2 2 0 1 0 -4 0 h-6 v-6 a2 2 0 1 1 0 -4 Z',
    bulb: 'M12 21 C9 18.5 7 15.6 7 12 a9 9 0 0 1 18 0 c0 3.6 -2 6.5 -5 9 Z M12 25 H20 M13.5 29 H18.5',
    tutorial: 'M4 6 Q10 3 16 7 Q22 3 28 6 V26 Q22 23 16 27 Q10 23 4 26Z M16 7 V27 M8 11 H12 M20 11 H24 M8 16 H12 M20 16 H24',
    download: 'M16 4 V20 M9 14 L16 21 L23 14 M5 26 H27',
  };
  function glyph(name, cls = 'gl') {
    if (name === 'play') return `<svg class="${cls}" viewBox="0 0 32 32" aria-hidden="true"><path d="M9 5 L27 16 L9 27Z" fill="currentColor" stroke="currentColor" stroke-width="3" stroke-linejoin="round"/></svg>`;
    return `<svg class="${cls}" viewBox="0 0 32 32" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2.8" stroke-linecap="round" stroke-linejoin="round"><path d="${GLYPHS[name] || ''}"/></svg>`;
  }

  /* kept exported for startup compatibility; no cursor-chasing eyes */
  function trackEyes() {}

  SB.Art = { slime, largo, plort, logo, mark, wordmark, glyph, icon, trackEyes, SPECIES, BODY };
})(window.SB = window.SB || {});
