/* Loading screens: the start-up splash and the "launching the game" screen share one Slime-Rancher-2-style design
   (official key art behind a dark veil, the wordmark, a chunky green bar with a real slime hopping on it, rotating tips). */
(function (SB) {
  'use strict';
  const { $, esc, sleep } = SB.ui, t = (...a) => SB.t(...a);
  const feed = () => SB.NEWS || { scenes: [], slimes: [], backdrop: null };

  function slimeMarkup(size) {
    const url = (feed().slimes || []).find(u => /pink/i.test(u));
    if (url) return `<img data-src="${esc(url)}" data-w="${size * 2}" data-h="${size * 2}" data-fit="1" alt="">`;
    return SB.Art.slime('pink', { size, cls: 'idle' });
  }

  /**
   * Open a loading screen. opts: { id, title, sub, steps: [label…], tips: bool }.
   * Returns { progress(v, label), done(label), fail(label), close() }.
   */
  function open(opts = {}) {
    const root = document.createElement('div');
    root.className = 'ld' + (opts.compact ? ' ld--launch' : '');
    if (opts.id) root.id = opts.id;
    const art = feed().backdrop || (feed().scenes || [])[0];
    root.innerHTML = `
      <div class="ld-art" ${art ? `data-bg="${esc(art)}" data-w="1920" data-h="1080"` : ''}></div>
      <div class="ld-veil"></div>
      <div class="ld-center">
        <div class="ld-logo">${SB.Art.wordmark()}</div>
        ${opts.title ? `<h1 class="ld-title">${esc(opts.title)}</h1>` : ''}
        ${opts.sub ? `<p class="ld-sub">${esc(opts.sub)}</p>` : ''}
        <div class="ld-bar" style="--p:3%"><i></i><span class="ld-hop">${slimeMarkup(70)}</span></div>
        <div class="ld-line"><b class="ld-step">${esc((opts.steps || [''])[0] || t('splash.loading'))}</b><span class="ld-pct">3%</span></div>
        <div class="ld-tip"></div>
        ${opts.compact ? `<div class="ld-actions"></div>` : ''}
      </div>
      <div class="ld-notice">${esc(t('ribbon.a'))}${esc(t('ribbon.link'))}${esc(t('ribbon.b'))}</div>`;
    SB.ui.$('#loading-root').appendChild(root);
    if (SB.net) SB.net.hydrate(root);
    const bar = root.querySelector('.ld-bar'), stepEl = root.querySelector('.ld-step'), pctEl = root.querySelector('.ld-pct'), tipEl = root.querySelector('.ld-tip');
    const tips = SB.tips(); let ti = Math.floor(Math.random() * tips.length); tipEl.textContent = tips[ti];
    const timer = setInterval(() => { tipEl.classList.add('swap'); setTimeout(() => { tipEl.textContent = tips[++ti % tips.length]; tipEl.classList.remove('swap'); }, 220); }, 2400);
    let closed = false;
    const ctl = {
      el: root,
      progress(v, label) {
        const p = Math.max(0, Math.min(100, Math.round(v)));
        bar.style.setProperty('--p', Math.max(3, p) + '%'); pctEl.textContent = p + '%';
        if (label) stepEl.textContent = label;
      },
      done(label) { root.classList.add('ok'); ctl.progress(100, label); clearInterval(timer); tipEl.textContent = ''; },
      fail(label) { root.classList.add('bad'); stepEl.textContent = label; clearInterval(timer); tipEl.textContent = ''; },
      /** Pick up the official art once the cached feed is known (the screen opens before the backend answers). */
      refreshArt() {
        const a = root.querySelector('.ld-art'), url = feed().backdrop || (feed().scenes || [])[0];
        if (url && !a.dataset.bg) { a.dataset.bg = url; a.dataset.w = 1920; a.dataset.h = 1080; }
        const hop = root.querySelector('.ld-hop'); if (hop && feed().slimes && feed().slimes.length) hop.innerHTML = slimeMarkup(70);
        if (SB.net) SB.net.hydrate(root);
      },
      actions(html) { const a = root.querySelector('.ld-actions'); if (a) a.innerHTML = html; return a; },
      async close(delay = 0) {
        if (closed) return; closed = true; clearInterval(timer);
        if (delay) await sleep(delay);
        root.classList.add('gone'); await sleep(750); root.remove();
      },
    };
    return ctl;
  }

  SB.loading = { open };
})(window.SB = window.SB || {});
