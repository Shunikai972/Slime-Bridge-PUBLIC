/* Home (carousel + latest news + promo tiles), News and Games pages. Pictures are the official ones, loaded at runtime. */
(function (SB) {
  'use strict';
  const { esc } = SB.ui, { slime, plort, icon, glyph, largo } = SB.Art, t = (...a) => SB.t(...a);
  const NAMES = { SR1: 'Slime Rancher', SR2: 'Slime Rancher 2' };
  const STORES = { steam: 'Steam', epic: 'Epic Games', gog: 'GOG', manual: 'manual', lab: 'LAB' };
  const LOADERS = { SR1: 'SRML', SR2: 'MelonLoader' };
  const storeName = s => STORES[s] || s;
  const feed = () => SB.NEWS || { items: [], scenes: [], slimes: [] };

  /** An official slime cut-out (<img>, hydrated at runtime) or, when offline, a drawn slime. */
  function cutout(pattern, fallbackSpecies, size, o = {}) {
    const url = (feed().slimes || []).find(u => pattern.test(u));
    if (url) return `<img class="cut" data-src="${esc(url)}" data-w="${size * 2}" data-h="${size * 2}" data-fit="1" alt="" ${o.style ? `style="${o.style}"` : ''}>`;
    return slime(fallbackSpecies, { size, cls: o.cls === undefined ? 'idle' : o.cls });
  }
  const scene = i => (feed().scenes || [])[i] || null;

  function buildSlides() {
    const N = feed(), slides = [];
    if (scene(0)) slides.push({ url: scene(0), title: t('home.hero1_t'), sub: t('home.hero1_s') });
    if (scene(1)) slides.push({ url: scene(1), title: t('home.hero2_t'), sub: t('home.hero2_s'), go: 'models' });
    (N.items || []).filter(i => i.image).slice(0, 3).forEach(i => slides.push({ url: i.image, title: i.title, sub: i.date, link: i.url }));
    if (!slides.length) slides.push({ art: true, title: t('home.hero1_t'), sub: t('home.hero1_s') });
    return slides;
  }

  function carousel() {
    const slides = buildSlides();
    return `<section class="carousel" id="carousel" aria-roledescription="carousel" data-count="${slides.length}">
      ${slides.map((s, i) => `<div class="slide ${i === 0 ? 'on' : ''}" ${s.url ? `data-bg="${esc(s.url)}" data-w="1280" data-h="720"` : ''}>
        ${s.art ? `<div class="art">${SB.Scene.hero()}</div>` : ''}
        <div class="shade"></div>
        <div class="caption"><h2>${esc(s.title)}</h2><p>${esc(s.sub)}</p>${s.link ? `<button class="btn btn--bridge btn--sm" data-act="open-url" data-arg="${esc(s.link)}">${t('news.read')}</button>` : s.go ? `<button class="btn btn--bridge btn--sm" data-act="go" data-arg="${s.go}">${t('home.discover')}</button>` : ''}</div></div>`).join('')}
      ${slides.length > 1 ? `<button class="arrow prev" data-act="slide" data-arg="-1" aria-label="Previous">${glyph('chevL')}</button><button class="arrow next" data-act="slide" data-arg="1" aria-label="Next">${glyph('chevR')}</button>
      <div class="dots">${slides.map((_, i) => `<button data-act="slide-to" data-arg="${i}" class="${i === 0 ? 'on' : ''}" aria-label="${i + 1}"></button>`).join('')}</div>` : ''}
    </section>`;
  }

  function newsItem(i) {
    return `<li><button class="news-item" data-act="open-url" data-arg="${esc(i.url)}"><span class="thumb ${i.image ? 'loading' : ''}" ${i.image ? `data-bg="${esc(i.image)}" data-w="240" data-h="160"` : ''}></span>
      <span class="nt"><small>${esc(i.date)}</small><b>${esc(i.title)}</b><span class="s">${esc(i.summary)}</span></span>${glyph('chevR', 'go')}</button></li>`;
  }
  function newsBox() {
    const N = feed(), items = (N.items || []).slice(0, 3);
    let body;
    if (SB.newsLoading && !items.length) body = [1, 2, 3].map(() => '<li class="skel shimmer"></li>').join('');
    else if (!items.length) body = `<li class="news-empty"><div>${slime('cotton', { size: 56, cls: 'idle' })}</div><p>${t('news.none')}</p><button class="btn btn--ghost btn--sm" data-act="news-refresh">${icon('refresh')} ${t('news.retry')}</button></li>`;
    else body = items.map(newsItem).join('');
    return `<aside class="newsbox panel" aria-label="${t('news.title')}"><header><h2>${t('news.title')}</h2><a data-act="go" data-arg="news">${t('news.all')} ›</a></header><ul class="news-list">${body}</ul></aside>`;
  }

  function promo(cls, go, title, sub, art) {
    return `<button class="promo ${cls}" data-act="go" data-arg="${go}"><span class="pimg" ${art.bg ? `data-bg="${esc(art.bg)}" data-w="480" data-h="300"` : ''}>${art.html || ''}</span><span class="pt">${title}<small>${sub}</small></span><span class="pgo">${glyph('chevR')}</span></button>`;
  }

  function home(S) {
    return `<div class="page home">
  <div class="home-top">${carousel()}${newsBox()}</div>
  <div class="promos">
    ${promo('', 'assets', t('promo.p1'), t('promo.p1s'), { html: cutout(/pink/i, 'pink', 80) })}
    ${promo('orange', 'models', t('promo.p2'), t('promo.p2s'), scene(2) ? { bg: scene(2) } : { html: largo('rad', 'cotton', { cls: 'idle' }) })}
    ${promo('blue', 'multiplayer', t('promo.p3'), t('promo.p3s'), scene(3) ? { bg: scene(3) } : { html: slime('tabby', { size: 70 }) })}
  </div></div>`;
  }

  function news(S) {
    const N = feed(), items = N.items || [];
    return `<div class="page">
  <div class="page-title"><h1>${t('nav.news')}</h1><p>${t('news.sub')}</p><span style="flex:1"></span>
    <button class="btn btn--ghost btn--sm" data-act="news-refresh">${icon('refresh')} ${t('news.retry')}</button><button class="btn btn--sky btn--sm" data-act="open-url" data-arg="https://www.slimerancher.com/news/">${t('news.open_site')}</button></div>
  ${N.offline && items.length ? `<p class="pill warn">${t('news.offline')}</p>` : ''}
  ${items.length ? `<div class="news-grid">${items.map(i => `<button class="news-card" data-act="open-url" data-arg="${esc(i.url)}"><span class="nimg" ${i.image ? `data-bg="${esc(i.image)}" data-w="750" data-h="420"` : ''}></span><span class="nbody"><small>${esc(i.date)}</small><b>${esc(i.title)}</b><span class="s">${esc(i.summary)}</span></span></button>`).join('')}</div>`
      : `<section class="panel"><div class="empty">${slime('cotton', { size: 70, cls: 'hop', dur: 2.4 })}<div><b>${t('news.none')}</b><p class="muted">${esc(N.error || '')}</p></div></div></section>`}
  <p class="credit">${t('news.source')}</p></div>`;
  }

  /* ---------------------------------------------------------------- games */
  function gameCard(S, realm) {
    const g = S.games[realm], p = g.primary, key = realm.toLowerCase();
    const art = cutout(realm === 'SR1' ? /pink/i : /cotton/i, realm === 'SR1' ? 'pink' : 'sloomber', 64, { cls: '' });
    const head = `<div class="gc-head"><div class="gc-art ${key}">${art}</div>
      <div class="gc-title"><h3>${NAMES[realm]}</h3><span class="muted small">${realm === 'SR1' ? t('games.engine1') : t('games.engine2')}</span></div>
      <span class="pill ${p ? 'good live' : 'warn'}">${p ? t('games.found') : t('games.notfound')}</span></div>`;
    if (!p) {
      return `<article class="panel game-card ${key}">${head}<p class="muted">${t('games.empty')}</p>
        <div class="row"><button class="btn btn--sky btn--sm" data-act="detect">${icon('search')} ${t('games.autodetect')}</button><button class="btn btn--ghost btn--sm" data-act="pick" data-arg="folder">${icon('folder')} ${t('games.choose_folder')}</button><button class="btn btn--ghost btn--sm" data-act="pick" data-arg="exe">${t('games.choose_exe')}</button></div></article>`;
    }
    const loaderOk = p.loader !== 'Not configured';
    const chips = `<div class="row"><span class="pill ${loaderOk ? 'good' : 'warn'}" data-tip="${esc(loaderOk ? t('games.loader_ok_tip') : t('games.loader_tip', { loader: LOADERS[realm] }))}">${LOADERS[realm]}: ${loaderOk ? t('games.ready') : t('games.missing')}</span>
      <span class="pill ${p.bridge_plugin ? (p.plugin_state === 'outdated' ? 'warn' : 'good') : 'muted'}" data-tip="${esc(t('games.plugin_tip'))}">${t('games.plugin')}: ${p.bridge_plugin ? (p.plugin_state === 'outdated' ? t('games.outdated') : t('games.ready')) : t('games.missing')}</span>${loaderOk && p.plugin_state !== 'current' && p.plugin_state !== 'unavailable' ? `<button class="btn btn--bridge btn--sm" data-act="plugin-install" data-arg="${realm}">${p.bridge_plugin ? t('games.plugin_update') : t('games.plugin_install')}</button>` : ''}
      <span class="pill info">${storeName(p.store)}</span>${g.running ? `<span class="pill good live">${t('games.running')}</span>` : ''}</div>`;
    const others = g.all.length > 1 ? `<div class="field"><label>${t('games.which')}</label><select class="input" data-change="choose" data-realm="${realm}">${g.all.map(x => `<option value="${esc(x.root)}" ${x.root === p.root ? 'selected' : ''}>${esc(x.role === 'lab' ? 'LAB · ' : '')}${esc(storeName(x.store))} · ${esc(x.root)}</option>`).join('')}</select></div>` : '';
    return `<article class="panel game-card ${key}">${head}<div class="mono ellipsis path" title="${esc(p.root)}">${esc(p.root)}</div>${chips}${others}
      <div class="row"><button class="btn btn--sm ${realm === 'SR1' ? 'btn--sr1' : 'btn--sr2'}" data-act="launch" data-arg="${realm}">${icon('play')} ${t('games.launch')}</button><button class="btn btn--ghost btn--sm" data-act="folder" data-arg="${realm}">${icon('folder')} ${t('games.open')}</button><button class="btn btn--ghost btn--sm" data-act="pick" data-arg="folder">${t('games.change')}</button><button class="btn btn--ghost btn--sm" data-act="forget" data-arg="${realm}">${t('games.forget')}</button></div></article>`;
  }

  function games(S) {
    const n = S.installations.length;
    return `<div class="page">
  <div class="page-title"><h1>${t('nav.games')}</h1><p>${t('games.sub')}</p></div>
  <section class="panel detect">
    <div class="detect-art">${slime('phosphor', { size: 78, cls: 'hop', dur: 1.7 })}</div>
    <div class="stack" style="flex:1"><h2>${t('games.detect_title')}</h2><p class="muted">${t('games.detect_body')}</p>
      <div class="row"><button class="btn btn--sky" data-act="detect">${icon('search')} ${t('games.autodetect')}</button><button class="btn btn--ghost" data-act="pick" data-arg="folder">${icon('folder')} ${t('games.choose_folder')}</button><button class="btn btn--ghost" data-act="pick" data-arg="exe">${t('games.choose_exe')}</button><span class="pill ${n ? 'good' : 'muted'}">${t('games.count', { n })}</span></div></div>
  </section>
  <div class="grid cols-2" id="games">${['SR1', 'SR2'].map(r => gameCard(S, r)).join('')}</div>
  <section class="panel own">
    <div class="own-art">${plort('honey', { size: 54 })}</div>
    <div class="stack" style="flex:1"><h2>${t('legal.own_title')}</h2><p class="muted">${t('legal.own_body')}</p>
      <div class="row"><button class="btn btn--ghost btn--sm" data-act="store" data-arg="SR1">${t('games.buy', { game: NAMES.SR1 })}</button><button class="btn btn--ghost btn--sm" data-act="store" data-arg="SR2">${t('games.buy', { game: NAMES.SR2 })}</button></div></div>
  </section></div>`;
  }

  SB.pages = Object.assign(SB.pages || {}, { home: { render: home }, news: { render: news }, games: { render: games } });
  SB.cutout = cutout; SB.newsBox = newsBox; SB.carouselHtml = carousel; SB.scene = scene;
})(window.SB = window.SB || {});
