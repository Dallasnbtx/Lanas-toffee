/* Living bezel: two gold chasers half a lap apart + riding glints, and a slight brighten on taps. */
(function () {
  var bz = document.getElementById('bezel'); if (!bz) return;
  var t; // tap glow
  document.addEventListener('pointerdown', function (e) {
    if (!e.target.closest('button,.btn,summary,input,textarea')) return;
    bz.classList.add('lit'); clearTimeout(t); t = setTimeout(function () { bz.classList.remove('lit'); }, 900);
  }, { passive: true });

  if (!window.matchMedia || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  var svg = document.getElementById('bz-trails'); if (!svg || !svg.animate) return;
  var LAP = 14000;                        // ms per full lap (each chaser)
  var TAIL = [150, 90, 44, 12];           // px dash lengths: long faint -> short bright head
  var LAGS = [0, 14, 30];                 // px behind the head where glints ride (0 = on the head)
  var rects = svg.querySelectorAll('.tl'), anims = [], riders = [], T0 = null;
  var canRide = window.CSS && CSS.supports && CSS.supports('offset-path', 'path("M0 0H1")');

  if (canRide) {
    [0, 1].forEach(function (c) {
      var h = document.createElement('i'); h.className = 'rider head'; h._c = c; h._lag = 0; bz.appendChild(h); riders.push(h);
      LAGS.forEach(function (lag, k) {
        var r = document.createElement('i'); r.className = 'rider'; r._c = c; r._lag = lag;
        var g = document.createElement('i'); g.className = 'gl';
        g.style.animationDuration = (1.5 + 0.45 * k + 0.2 * c) + 's'; g.style.animationDelay = (-0.55 * k - 0.9 * c) + 's';
        r.appendChild(g); bz.appendChild(r); riders.push(r);
      });
    });
  }

  function build() {
    anims.forEach(function (a) { a.cancel(); }); anims = [];
    var w = bz.clientWidth, hgt = bz.clientHeight; if (!w || !hgt) return;
    var R = parseFloat(getComputedStyle(bz).borderTopLeftRadius) || 0, i = 0.75;
    var W = w - 2 * i, H = hgt - 2 * i, r = Math.max(0, Math.min(R - i, W / 2, H / 2));
    var P = 2 * (W + H) - 8 * r + 2 * Math.PI * r;   // exact perimeter of the rounded rect
    if (T0 === null) T0 = document.timeline ? document.timeline.currentTime : performance.now();
    for (var n = 0; n < rects.length; n++) {
      var e = rects[n], L = TAIL[n];
      e.setAttribute('x', i); e.setAttribute('y', i); e.setAttribute('width', W); e.setAttribute('height', H);
      e.setAttribute('rx', r); e.setAttribute('ry', r);
      // pattern period = P/2, so there are exactly two dashes, 180deg apart. Head of each dash = progress*P (+P/2).
      e.style.strokeDasharray = L + ' ' + (P / 2 - L);
      var a = e.animate([{ strokeDashoffset: L }, { strokeDashoffset: L - P }], { duration: LAP, iterations: Infinity, easing: 'linear' });
      a.startTime = T0; anims.push(a);
    }
    // same path for riders; it starts where an SVG rect stroke starts (top edge, after the corner) and runs clockwise
    var x0 = i, y0 = i, x1 = i + W, y1 = i + H;
    var d = 'M' + (x0 + r) + ' ' + y0 + 'H' + (x1 - r) + 'A' + r + ' ' + r + ' 0 0 1 ' + x1 + ' ' + (y0 + r) +
            'V' + (y1 - r) + 'A' + r + ' ' + r + ' 0 0 1 ' + (x1 - r) + ' ' + y1 + 'H' + (x0 + r) +
            'A' + r + ' ' + r + ' 0 0 1 ' + x0 + ' ' + (y1 - r) + 'V' + (y0 + r) + 'A' + r + ' ' + r + ' 0 0 1 ' + (x0 + r) + ' ' + y0 + 'Z';
    riders.forEach(function (el) {
      el.style.offsetPath = 'path("' + d + '")';
      var phase = el._c * LAP / 2 - el._lag / P * LAP;            // where on the lap this rider sits
      var a = el.animate([{ offsetDistance: '0%' }, { offsetDistance: '100%' }], { duration: LAP, iterations: Infinity, easing: 'linear',
                         delay: -(((phase % LAP) + LAP) % LAP) });
      a.startTime = T0; anims.push(a);
    });
  }
  var rt; window.addEventListener('resize', function () { clearTimeout(rt); rt = setTimeout(build, 120); });
  build();
})();
