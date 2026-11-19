/* Minimal offline fallback backdrop and hero banner (used only when official artwork is unavailable). */
(function (SB) {
  'use strict';

  function background() {
    return `<svg viewBox="0 0 1600 900" preserveAspectRatio="xMidYMax slice" xmlns="http://www.w3.org/2000/svg">
<defs>
  <linearGradient id="sbSky" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#1c5d8a"/>
    <stop offset=".48" stop-color="#2c7dad"/>
    <stop offset="1" stop-color="#195273"/>
  </linearGradient>
</defs>
<rect width="1600" height="900" fill="url(#sbSky)"/>
<path d="M0 640 Q420 560 880 620 T1600 590 L1600 900 L0 900 Z" fill="#184e6f" opacity=".55"/>
<path d="M0 730 Q520 660 1040 725 T1600 700 L1600 900 L0 900 Z" fill="#13415e" opacity=".75"/>
</svg>`;
  }

  function hero() {
    return `<svg viewBox="0 0 1100 430" preserveAspectRatio="xMidYMax slice" xmlns="http://www.w3.org/2000/svg">
<defs>
  <linearGradient id="sbHeroSky" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#1d6594"/>
    <stop offset=".55" stop-color="#28809f"/>
    <stop offset="1" stop-color="#1c6c6f"/>
  </linearGradient>
</defs>
<rect width="1100" height="430" fill="url(#sbHeroSky)"/>
<path d="M0 320 Q300 250 620 300 T1100 280 L1100 430 L0 430 Z" fill="#165768" opacity=".5"/>
<path d="M0 365 Q360 315 760 355 T1100 340 L1100 430 L0 430 Z" fill="#104252" opacity=".7"/>
</svg>`;
  }

  function parallax() {}

  SB.Scene = { background, hero, parallax };
})(window.SB = window.SB || {});
