/* ============================================================
   Lexify — 首页星场（field-universe.js）
   ------------------------------------------------------------
   同一块画布，换成「宇宙」：
   - 三层景深的星点，指针移动时按景深做视差
   - 指针是一个引力源：附近星点被拉着打旋、拉出短拖尾
   - 指针附近彼此靠近的星点会连成极淡的线（星座）
   - 指针离开后，星点按弹簧回到原位

   与 flow 版一致的底线：reduced-motion 只画一帧；出视口停 rAF；
   没有 JS 时回落到 CSS 里的静态 field.svg。
   ============================================================ */
(function () {
  "use strict";

  var hero = document.querySelector(".hero");
  if (!hero || !window.requestAnimationFrame) return;

  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  var canvas = document.createElement("canvas");
  canvas.className = "field-canvas";
  canvas.setAttribute("aria-hidden", "true");
  hero.insertBefore(canvas, hero.firstChild);
  hero.classList.add("has-field");

  var ctx = canvas.getContext && canvas.getContext("2d");
  if (!ctx) { hero.classList.remove("has-field"); return; }

  var BLUE = "20, 52, 203";
  var R = 340;          // 引力半径
  var stars = [];
  var W = 0, H = 0, dpr = 1;

  function rnd(a, b) { return a + Math.random() * (b - a); }

  function build() {
    var rect = hero.getBoundingClientRect();
    W = Math.max(320, Math.round(rect.width));
    H = Math.max(240, Math.round(rect.height));
    dpr = Math.min(window.devicePixelRatio || 1, 2);

    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(H * dpr);
    canvas.style.width = W + "px";
    canvas.style.height = H + "px";
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    // 三层：远景密而淡，近景疏而实
    var layers = [
      { n: Math.round((W * H) / 2100), r: [0.6, 1.0], a: [0.22, 0.40], depth: 0.35 },
      { n: Math.round((W * H) / 5200), r: [1.0, 1.5], a: [0.36, 0.58], depth: 1.0 },
      { n: Math.round((W * H) / 15000), r: [1.5, 2.2], a: [0.50, 0.78], depth: 1.8 }
    ];

    stars = [];
    layers.forEach(function (L) {
      for (var i = 0; i < L.n; i++) {
        var x = rnd(0, W), y = rnd(0, H);
        stars.push({
          hx: x, hy: y,
          x: x, y: y,
          vx: 0, vy: 0,
          r: rnd(L.r[0], L.r[1]),
          a: rnd(L.a[0], L.a[1]),
          depth: L.depth,
          ph: rnd(0, Math.PI * 2),
          sp: rnd(0.6, 1.5)
        });
      }
    });
  }

  // 指针与视差
  var px = -9999, py = -9999, tx = -9999, ty = -9999;
  var parX = 0, parY = 0;

  hero.addEventListener("pointermove", function (e) {
    var r = hero.getBoundingClientRect();
    tx = e.clientX - r.left;
    ty = e.clientY - r.top;
    // 指针相对中心的偏移，用来做视差（-1..1）
    parX = (tx / W - 0.5) * 2;
    parY = (ty / H - 0.5) * 2;
  }, { passive: true });

  hero.addEventListener("pointerleave", function () {
    tx = -9999; ty = -9999; parX = 0; parY = 0;
  });

  function leftFalloff(x) {
    var a = W * 0.12, b = W * 0.46;
    if (x <= a) return 0.46;
    if (x >= b) return 1;
    var t = (x - a) / (b - a);
    t = t * t * (3 - 2 * t);
    return 0.46 + 0.54 * t;
  }

  var curX = 0, curY = 0;

  function step(dt) {
    // 视差缓动
    curX += (parX - curX) * 0.05;
    curY += (parY - curY) * 0.05;

    var hasPointer = px > -9000;

    for (var i = 0; i < stars.length; i++) {
      var s = stars[i];

      // 视差位移量（近景动得多）
      var ox = -curX * 14 * s.depth;
      var oy = -curY * 10 * s.depth;
      var gx = s.hx + ox;
      var gy = s.hy + oy;

      var ax = (gx - s.x) * 0.014;
      var ay = (gy - s.y) * 0.014;

      if (hasPointer) {
        var dx = s.x - px, dy = s.y - py;
        var d = Math.sqrt(dx * dx + dy * dy);
        if (d < R && d > 0.6) {
          var f = 1 - d / R;
          f = f * f;
          // 切向：绕着指针转
          ax += (-dy / d) * f * 0.85 * s.depth;
          ay += (dx / d) * f * 0.85 * s.depth;
          // 一点向心，避免越转越远
          ax += (-dx / d) * f * 0.16;
          ay += (-dy / d) * f * 0.16;
        }
      }

      s.vx = (s.vx + ax) * 0.93;
      s.vy = (s.vy + ay) * 0.93;
      s.x += s.vx;
      s.y += s.vy;
    }
  }

  function draw(t) {
    ctx.clearRect(0, 0, W, H);

    var hasPointer = px > -9000;

    // 星点
    for (var i = 0; i < stars.length; i++) {
      var s = stars[i];
      var tw = 0.82 + 0.18 * Math.sin(t * 0.0012 * s.sp + s.ph);
      var a = s.a * tw * leftFalloff(s.x);

      // 指针附近更亮
      if (hasPointer) {
        var dx = s.x - px, dy = s.y - py;
        var d = Math.sqrt(dx * dx + dy * dy);
        if (d < R) {
          var f = 1 - d / R;
          a += f * f * 0.45;
        }
      }
      if (a > 0.95) a = 0.95;

      var r = s.r;
      ctx.fillStyle = "rgba(" + BLUE + "," + a.toFixed(2) + ")";

      var sp = Math.sqrt(s.vx * s.vx + s.vy * s.vy);
      if (sp > 0.25) {
        // 拖动时拉一小段尾迹
        ctx.strokeStyle = "rgba(" + BLUE + "," + (a * 0.55).toFixed(2) + ")";
        ctx.lineWidth = r * 1.4;
        ctx.beginPath();
        ctx.moveTo(s.x - s.vx * 5, s.y - s.vy * 5);
        ctx.lineTo(s.x, s.y);
        ctx.stroke();
      } else {
        ctx.fillRect(s.x - r / 2, s.y - r / 2, r, r);
      }
    }

    // 星座：只在指针附近连，且线极淡
    if (hasPointer) {
      var near = [];
      for (var k = 0; k < stars.length; k++) {
        var q = stars[k];
        var ddx = q.x - px, ddy = q.y - py;
        if (ddx * ddx + ddy * ddy < R * R) near.push(q);
      }
      var LIM = 52 * 52;
      ctx.lineWidth = 0.7;
      for (var m = 0; m < near.length; m++) {
        for (var n = m + 1; n < near.length; n++) {
          var a1 = near[m], a2 = near[n];
          var lx = a1.x - a2.x, ly = a1.y - a2.y;
          var dd = lx * lx + ly * ly;
          if (dd < LIM) {
            var f = 1 - Math.sqrt(dd) / 52;
            // 越靠近指针的连线越实，让星簇聚在光标周围
            var mx = (a1.x + a2.x) / 2 - px;
            var my = (a1.y + a2.y) / 2 - py;
            var dc = Math.sqrt(mx * mx + my * my) / R;
            if (dc > 1) continue;
            var near2 = 1 - dc;
            near2 = near2 * near2;
            ctx.strokeStyle = "rgba(" + BLUE + "," + (0.30 * f * near2).toFixed(3) + ")";
            ctx.beginPath();
            ctx.moveTo(a1.x, a1.y);
            ctx.lineTo(a2.x, a2.y);
            ctx.stroke();
          }
        }
      }
    }
  }

  var raf = 0, running = false, visible = true, last = 0;

  function loop(t) {
    if (!running) return;
    var dt = last ? Math.min(t - last, 40) : 16;
    last = t;

    if (tx < -9000) { px = -9999; py = -9999; }
    else if (px < -9000) { px = tx; py = ty; }
    else { px += (tx - px) * 0.16; py += (ty - py) * 0.16; }

    step(dt);
    draw(t);
    raf = requestAnimationFrame(loop);
  }

  function start() {
    if (running || reduce) return;
    running = true;
    last = 0;
    raf = requestAnimationFrame(loop);
  }
  function stop() {
    running = false;
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
  }

  build();
  draw(0);

  if (reduce) return;

  if ("IntersectionObserver" in window) {
    new IntersectionObserver(function (entries) {
      visible = entries[0].isIntersecting;
      if (visible && !document.hidden) start(); else stop();
    }, { threshold: 0 }).observe(hero);
  } else {
    start();
  }

  document.addEventListener("visibilitychange", function () {
    if (document.hidden) stop(); else if (visible) start();
  });

  var rt;
  window.addEventListener("resize", function () {
    clearTimeout(rt);
    rt = setTimeout(function () {
      var was = running;
      stop(); build(); draw(0);
      if (was) start();
    }, 160);
  });
})();
