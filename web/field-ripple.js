/* ============================================================
   Lexify — 首页涟漪（field-ripple.js）
   ------------------------------------------------------------
   一片极细的点阵当水面：
   - 指针移动时在身后留下涟漪，波纹一圈圈向外推
   - 点阵被波纹推着径向位移、并随位移变亮
   - 波纹自己画一圈 1px 的极淡波前
   - 静止时就是一张安静的网点纸

   与前几版一致的底线：reduced-motion 只画一帧；出视口停 rAF；
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
  var GAP = 22;          // 点阵间距
  var SPEED = 0.50;      // 波纹扩散速度 px/ms
  var WIDTH = 54;        // 波front 宽度
  var AMP = 14;          // 最大位移
  var LIFE = 1700;       // 一圈涟漪活多久
  var MAXR = 10;         // 同时存在的涟漪数

  var W = 0, H = 0, dpr = 1, pts = [], ripples = [];

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

    pts = [];
    for (var y = GAP / 2; y < H; y += GAP) {
      for (var x = GAP / 2; x < W; x += GAP) {
        pts.push({ x: x, y: y });
      }
    }
    ripples = [];
  }

  var lastX = -9999, lastY = -9999;
  // 统一用 rAF 的时间基准：performance.now() 在某些渲染模式下与它不同源，
  // 混用会让 age 变成负数，涟漪就永远画不出来
  var frameT = 0;

  function addRipple(x, y) {
    ripples.push({ x: x, y: y, t: frameT });
    if (ripples.length > MAXR) ripples.shift();
  }

  hero.addEventListener("pointermove", function (e) {
    var r = hero.getBoundingClientRect();
    var x = e.clientX - r.left, y = e.clientY - r.top;
    // 走出一段距离才落一圈，不然波纹会糊成一片
    if (lastX < -9000 || Math.hypot(x - lastX, y - lastY) > 34) {
      addRipple(x, y);
      lastX = x; lastY = y;
    }
  }, { passive: true });

  function leftFalloff(x) {
    var a = W * 0.12, b = W * 0.46;
    if (x <= a) return 0.34;
    if (x >= b) return 1;
    var t = (x - a) / (b - a);
    t = t * t * (3 - 2 * t);
    return 0.34 + 0.66 * t;
  }

  function draw(t) {
    frameT = t;
    ctx.clearRect(0, 0, W, H);

    // 清掉过期涟漪
    ripples = ripples.filter(function (r) { return t - r.t < LIFE; });

    // 点阵
    for (var i = 0; i < pts.length; i++) {
      var p = pts[i];
      var dx = 0, dy = 0, hit = 0;

      for (var k = 0; k < ripples.length; k++) {
        var rp = ripples[k];
        var age = t - rp.t;
        var wave = age * SPEED;
        var vx = p.x - rp.x, vy = p.y - rp.y;
        var d = Math.sqrt(vx * vx + vy * vy) || 1;
        var off = Math.abs(d - wave);
        if (off < WIDTH) {
          var f = 1 - off / WIDTH;
          f = f * f;
          var decay = 1 - age / LIFE;
          var force = f * AMP * decay;
          dx += (vx / d) * force;
          dy += (vy / d) * force;
          if (f * decay > hit) hit = f * decay;
        }
      }

      var a = (0.14 + hit * 0.70) * leftFalloff(p.x);
      if (a > 0.92) a = 0.92;
      var s = 1.3 + hit * 2.6;

      ctx.fillStyle = "rgba(" + BLUE + "," + a.toFixed(2) + ")";
      ctx.fillRect(p.x + dx - s / 2, p.y + dy - s / 2, s, s);
    }

    // 波前：一圈极淡的线，让涟漪有边界
    for (var m = 0; m < ripples.length; m++) {
      var r2 = ripples[m];
      var age2 = t - r2.t;
      var rad = age2 * SPEED;
      if (rad < 4) continue;
      var alpha = 0.15 * (1 - age2 / LIFE);
      if (alpha <= 0.004) continue;
      ctx.strokeStyle = "rgba(" + BLUE + "," + alpha.toFixed(3) + ")";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.arc(r2.x, r2.y, rad, 0, Math.PI * 2);
      ctx.stroke();
    }

    // 底部渐隐
    var g = ctx.createLinearGradient(0, H * 0.72, 0, H);
    g.addColorStop(0, "rgba(0,0,0,0)");
    g.addColorStop(1, "rgba(0,0,0,1)");
    ctx.globalCompositeOperation = "destination-out";
    ctx.fillStyle = g;
    ctx.fillRect(0, H * 0.72, W, H * 0.28);
    ctx.globalCompositeOperation = "source-over";
  }

  var raf = 0, running = false, visible = true;

  function loop(t) {
    if (!running) return;
    draw(t);
    raf = requestAnimationFrame(loop);
  }

  function start() { if (running || reduce) return; running = true; raf = requestAnimationFrame(loop); }
  function stop() { running = false; if (raf) cancelAnimationFrame(raf); raf = 0; }

  build();
  draw(performance.now());

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
      stop(); build(); draw(performance.now());
      if (was) start();
    }, 160);
  });
})();
