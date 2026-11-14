/* ============================================================
   Lexify — 首页视差切片（field-slice.js）
   ------------------------------------------------------------
   一整片横向"切片"（每条 68px 高，内含两行短划线）：
   - 相邻切片朝相反方向滑动，幅度由指针的横向位置决定
   - 切片离视线中心越远，位移越大（近景快、远景慢）
   - 指针纵向位置再给一点整体上下的错动，做出层叠的深度
   - 静止时切片对齐，是一片规整的划线纸

   与其他几版一致的底线：reduced-motion 只画一帧；出视口停 rAF；
   没有 JS 时回落到 CSS 里的静态 field.svg。
   ============================================================ */
(function () {
  "use strict";

  var hero = document.querySelector(".hero");
  if (!hero || !window.requestAnimationFrame) return;

  var reduce = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;

  var canvas = document.createElement("canvas");
  canvas.className = "field-canvas";
  canvas.setAttribute("aria-hidden", "true");
  hero.insertBefore(canvas, hero.firstChild);
  hero.classList.add("has-field");

  var ctx = canvas.getContext && canvas.getContext("2d");
  if (!ctx) { hero.classList.remove("has-field"); return; }

  var BLUE = "20, 52, 203";
  var BAND = 74;      // 切片高度
  var DASH = 34;      // 每段长度
  var GAP = 20;       // 段间空隙
  var SHIFT = 62;     // 最大横移
  var W = 0, H = 0, dpr = 1, bands = [];

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

    bands = [];
    var i = 0;
    for (var y = 0; y < H; y += BAND) {
      bands.push({
        top: y,
        dir: i % 2 === 0 ? 1 : -1,      // 相邻切片反向
        depth: 0.55 + 0.55 * (i % 3),   // 三种景深轮换
        phase: (i * 17) % (DASH + GAP), // 静止时也有细微错位
        alpha: i % 2 === 0 ? 0.150 : 0.090
      });
      i++;
    }
  }

  var px = -9999, py = -9999, tx = -9999, ty = -9999;

  hero.addEventListener("pointermove", function (e) {
    var r = hero.getBoundingClientRect();
    tx = e.clientX - r.left;
    ty = e.clientY - r.top;
  }, { passive: true });

  hero.addEventListener("pointerleave", function () { tx = -9999; ty = -9999; });

  function leftFalloff(x) {
    var a = W * 0.10, b = W * 0.44;
    if (x <= a) return 0.34;
    if (x >= b) return 1;
    var t = (x - a) / (b - a);
    t = t * t * (3 - 2 * t);
    return 0.34 + 0.66 * t;
  }

  function draw(t) {
    ctx.clearRect(0, 0, W, H);

    var has = px > -9000;
    var nx = has ? (px / W - 0.5) * 2 : 0;   // -1..1
    var ny = has ? (py / H - 0.5) * 2 : 0;

    for (var b = 0; b < bands.length; b++) {
      var band = bands[b];
      var offX = band.dir * nx * SHIFT * band.depth;
      var offY = -ny * 11 * band.depth;

      // 每条切片内两行线
      for (var r = 0; r < 2; r++) {
        var y = band.top + (r === 0 ? 18 : 50) + offY;
        if (y < -20 || y > H + 20) continue;

        // 静止时给一点缓慢漂移，避免死板
        var drift = Math.sin(t * 0.00018 + b * 1.3 + r) * 6;
        var start = -80 + offX + drift + band.phase;

        ctx.lineWidth = 1;
        for (var x = start; x < W + 80; x += DASH + GAP) {
          var x1 = Math.max(-2, x);
          var x2 = Math.min(W + 2, x + DASH);
          if (x2 <= 0 || x1 >= W) continue;

          var a = band.alpha * leftFalloff((x1 + x2) / 2);
          if (a <= 0.004) continue;

          ctx.strokeStyle = "rgba(" + BLUE + "," + a.toFixed(3) + ")";
          ctx.beginPath();
          ctx.moveTo(x1, y);
          ctx.lineTo(x2, y);
          ctx.stroke();
        }
      }
    }

    // 底部渐隐
    var g = ctx.createLinearGradient(0, H * 0.70, 0, H);
    g.addColorStop(0, "rgba(0,0,0,0)");
    g.addColorStop(1, "rgba(0,0,0,1)");
    ctx.globalCompositeOperation = "destination-out";
    ctx.fillStyle = g;
    ctx.fillRect(0, H * 0.70, W, H * 0.30);
    ctx.globalCompositeOperation = "source-over";
  }

  var raf = 0, running = false, visible = true;

  function loop(t) {
    if (!running) return;
    if (tx < -9000) {
      px += (-9999 - px) * 0.08;
      py += (-9999 - py) * 0.08;
      if (px < -8000) { px = -9999; py = -9999; }
    } else if (px < -9000) { px = tx; py = ty; }
    else { px += (tx - px) * 0.10; py += (ty - py) * 0.10; }

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
