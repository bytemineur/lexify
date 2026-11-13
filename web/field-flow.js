/* ============================================================
   Lexify — 首页流场（field.js）
   ------------------------------------------------------------
   把 millennium.css 里那片静态 field.svg 换成 canvas 实时演算：
   - 平时：整片缓缓流动（时间项让角度持续演化）
   - 鼠标进入：指针 240px 范围内的短线顺着指针打旋、拉长、变亮
   - 左侧标题区保底压暗，指针走到那里依然能看到涟漪

   守住的底线：
   - prefers-reduced-motion：只画一帧，不跑动画
   - 首屏滚出视口 / 标签页隐藏：停掉 rAF
   - 没有 JS：CSS 里的静态 field.svg 照常显示
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
  // 有 canvas 之后，静态 SVG 与白纱让位
  hero.classList.add("has-field");

  var ctx = canvas.getContext && canvas.getContext("2d");
  if (!ctx) {
    hero.classList.remove("has-field");
    return;
  }

  var BLUE = "20, 52, 203";
  var GAP = 26;
  var R = 285;          // 指针影响半径
  var dots = [];
  var W = 0, H = 0, dpr = 1;

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

    var cx = W * 0.78, cy = H * 0.40;
    dots = [];
    for (var y = GAP / 2; y < H; y += GAP) {
      for (var x = GAP / 2; x < W; x += GAP) {
        var dx = x - cx, dy = y - cy;
        var d = Math.sqrt(dx * dx + dy * dy);
        dots.push({
          x: x, y: y,
          // 离漩涡中心的权重
          w: Math.exp(-d / 520),
          // 每根线的固定抖动，避免机械感
          seed: (Math.sin(x * 127.1 + y * 311.7) * 43758.5453) % 1
        });
      }
    }
  }

  // 指针（画布坐标），带阻尼
  var px = -9999, py = -9999, tx = -9999, ty = -9999;

  hero.addEventListener("pointermove", function (e) {
    var r = hero.getBoundingClientRect();
    tx = e.clientX - r.left;
    ty = e.clientY - r.top;
  }, { passive: true });

  hero.addEventListener("pointerleave", function () {
    tx = -9999; ty = -9999;
  });

  // 左侧标题区的压暗曲线：0.18 保底，到 44% 宽度恢复正常
  function leftFalloff(x) {
    var a = W * 0.10, b = W * 0.44;
    if (x <= a) return 0.18;
    if (x >= b) return 1;
    var t = (x - a) / (b - a);
    t = t * t * (3 - 2 * t);
    return 0.18 + 0.82 * t;
  }

  function draw(t) {
    ctx.clearRect(0, 0, W, H);

    // 指针阻尼跟随
    if (tx < -9000) { px = -9999; py = -9999; }
    else {
      if (px < -9000) { px = tx; py = ty; }
      px += (tx - px) * 0.12;
      py += (ty - py) * 0.12;
    }
    var hasPointer = px > -9000;
    var time = t * 0.00012;

    // 指针处一层极淡的光晕，像水面被照亮
    if (hasPointer) {
      var g = ctx.createRadialGradient(px, py, 0, px, py, R);
      g.addColorStop(0, "rgba(" + BLUE + ",0.055)");
      g.addColorStop(0.55, "rgba(" + BLUE + ",0.022)");
      g.addColorStop(1, "rgba(" + BLUE + ",0)");
      ctx.fillStyle = g;
      ctx.fillRect(px - R, py - R, R * 2, R * 2);
    }

    // 透明度分档，批量成 path，避免每根线改一次样式
    var buckets = {};
    for (var i = 0; i < dots.length; i++) {
      var o = dots[i];
      var w = o.w;

      // 基础流场 + 随时间缓慢演化
      var base = Math.sin(o.x * 0.0042 + time) * 0.9 + Math.cos(o.y * 0.0038 - time * 0.8) * 0.7;
      var swirl = Math.atan2(o.y - H * 0.4, o.x - W * 0.78) + Math.PI / 2;
      var angle = base * (1 - w) + swirl * w;

      var len = 5 + 7 * (0.35 + 0.65 * w) + o.seed * 2;
      var alpha = 0.06 + 0.36 * w + o.seed * 0.10;

      if (hasPointer) {
        var dx = o.x - px, dy = o.y - py;
        var d = Math.sqrt(dx * dx + dy * dy);
        if (d < R) {
          var k = 1 - d / R;
          k = k * k;
          // 顺着指针打旋
          var tang = Math.atan2(dy, dx) + Math.PI / 2;
          var mix = Math.min(1, k * 1.5);
          angle = angle * (1 - mix) + tang * mix;
          // 拉长、变亮
          len *= 1 + k * 2.3;
          alpha += k * 0.72;
        }
      }

      alpha *= leftFalloff(o.x);
      if (alpha > 0.92) alpha = 0.92;

      var h = len / 2;
      var ca = Math.cos(angle), sa = Math.sin(angle);
      var key = (Math.round(alpha * 12) / 12).toFixed(2);
      if (!buckets[key]) buckets[key] = new Path2D();
      var p = buckets[key];
      p.moveTo(o.x - ca * h, o.y - sa * h);
      p.lineTo(o.x + ca * h, o.y + sa * h);
    }

    ctx.lineWidth = 1.1;
    ctx.lineCap = "round";
    for (var key in buckets) {
      ctx.strokeStyle = "rgba(" + BLUE + "," + key + ")";
      ctx.stroke(buckets[key]);
    }
  }

  var raf = 0, running = false, visible = true;

  function loop(t) {
    if (!running) return;
    draw(t);
    raf = requestAnimationFrame(loop);
  }

  function start() {
    if (running || reduce) return;
    running = true;
    raf = requestAnimationFrame(loop);
  }

  function stop() {
    running = false;
    if (raf) cancelAnimationFrame(raf);
    raf = 0;
  }

  build();
  draw(0);

  if (reduce) return; // 减少动态：只留一帧静态场

  if ("IntersectionObserver" in window) {
    new IntersectionObserver(function (entries) {
      visible = entries[0].isIntersecting;
      if (visible && !document.hidden) start();
      else stop();
    }, { threshold: 0 }).observe(hero);
  } else {
    start();
  }

  document.addEventListener("visibilitychange", function () {
    if (document.hidden) stop();
    else if (visible) start();
  });

  var resizeTimer;
  window.addEventListener("resize", function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () {
      var wasRunning = running;
      stop();
      build();
      draw(0);
      if (wasRunning) start();
    }, 160);
  });
})();
