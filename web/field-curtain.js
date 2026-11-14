/* ============================================================
   Lexify — 首页帘子（field-curtain.js）
   ------------------------------------------------------------
   一挂竖线构成的帘子：
   - 静止时每根线带极慢的正弦摆动，看起来是布，不是网格
   - 指针靠近：周围的线被「拨开」——向指针两侧分开、并被轻微带起
   - 离开后随指针阻尼自然回位（没有额外物理，回位是渐进的）
   - 底部渐隐，避免在区块交界处被硬切

   与其他两版一致的底线：reduced-motion 只画一帧；出视口停 rAF；
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
  var GAP_X = 15;        // 线间距
  var GAP_Y = 26;        // 取样步长
  var R = 300;           // 拨开半径
  var PUSH = 62;         // 最大拨开幅度

  var W = 0, H = 0, dpr = 1, cols = [];

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

    cols = [];
    var i = 0;
    for (var x = GAP_X; x < W; x += GAP_X) {
      i++;
      // 每隔几根给一点粗细/深浅差异，像织物的经线
      var heavy = i % 5 === 0;
      cols.push({
        x: x,
        ph: rnd(0, Math.PI * 2),
        amp: rnd(1.4, 3.0),
        a: heavy ? rnd(0.16, 0.24) : rnd(0.07, 0.14),
        w: heavy ? 1.15 : 0.8
      });
    }
  }

  var px = -9999, py = -9999, tx = -9999, ty = -9999;

  hero.addEventListener("pointermove", function (e) {
    var r = hero.getBoundingClientRect();
    tx = e.clientX - r.left;
    ty = e.clientY - r.top;
  }, { passive: true });

  hero.addEventListener("pointerleave", function () {
    tx = -9999; ty = -9999;
  });

  function leftFalloff(x) {
    var a = W * 0.12, b = W * 0.50;
    if (x <= a) return 0.30;
    if (x >= b) return 1;
    var t = (x - a) / (b - a);
    t = t * t * (3 - 2 * t);
    return 0.30 + 0.70 * t;
  }

  function draw(t) {
    ctx.clearRect(0, 0, W, H);

    var has = px > -9000;
    var time = t * 0.00035;

    for (var c = 0; c < cols.length; c++) {
      var col = cols[c];
      var pts = [];
      var maxF = 0;

      for (var y = -GAP_Y; y <= H + GAP_Y; y += GAP_Y) {
        // 静止时的轻微摆动：像挂着的布
        var x = col.x + Math.sin(y * 0.0075 + col.ph + time) * col.amp;
        var yy = y;

        if (has) {
          var dx = x - px, dy = y - py;
          var d = Math.sqrt(dx * dx + dy * dy);
          if (d < R) {
            var f = 1 - d / R;
            f = f * f;                       // 只影响近处
            if (f > maxF) maxF = f;
            // 用 tanh 让「过中点」的位移连续，避免在指针处折出尖角
            var s = Math.tanh(dx / 46);
            x += s * f * PUSH;
            yy = y + (dy / (d || 1)) * f * PUSH * 0.16;
          }
        }
        pts.push(x, yy);
      }

      var alpha = col.a * leftFalloff(col.x) * (1 + maxF * 1.9);
      if (alpha > 0.72) alpha = 0.72;

      ctx.strokeStyle = "rgba(" + BLUE + "," + alpha.toFixed(3) + ")";
      ctx.lineWidth = col.w;
      ctx.beginPath();
      ctx.moveTo(pts[0], pts[1]);
      for (var k = 2; k < pts.length - 2; k += 2) {
        var mx = (pts[k] + pts[k + 2]) / 2;
        var my = (pts[k + 1] + pts[k + 3]) / 2;
        ctx.quadraticCurveTo(pts[k], pts[k + 1], mx, my);
      }
      ctx.lineTo(pts[pts.length - 2], pts[pts.length - 1]);
      ctx.stroke();
    }

    // 底部渐隐：帘子下摆融进背景
    var g = ctx.createLinearGradient(0, H * 0.68, 0, H);
    g.addColorStop(0, "rgba(0,0,0,0)");
    g.addColorStop(1, "rgba(0,0,0,1)");
    ctx.globalCompositeOperation = "destination-out";
    ctx.fillStyle = g;
    ctx.fillRect(0, H * 0.68, W, H * 0.32);
    ctx.globalCompositeOperation = "source-over";
  }

  var raf = 0, running = false, visible = true;

  function loop(t) {
    if (!running) return;
    if (tx < -9000) { px += (-9999 - px) * 0.10; py += (-9999 - py) * 0.10; if (px < -8000) { px = -9999; py = -9999; } }
    else if (px < -9000) { px = tx; py = ty; }
    else { px += (tx - px) * 0.13; py += (ty - py) * 0.13; }

    draw(t);
    raf = requestAnimationFrame(loop);
  }

  function start() { if (running || reduce) return; running = true; raf = requestAnimationFrame(loop); }
  function stop() { running = false; if (raf) cancelAnimationFrame(raf); raf = 0; }

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
