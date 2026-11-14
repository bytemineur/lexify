/* ============================================================
   Lexify — 首页动效调度器（field-picker.js）
   ------------------------------------------------------------
   首屏划线场有 5 种互斥实现，同一时刻只能跑一个（都往 .hero
   插一块 .field-canvas，5 个同时跑会叠在一起看不清）：

     field-ripple.js    点阵涟漪
     field-curtain.js   竖线帘子（被指针拨开）
     field-slice.js     横向视差切片
     field-flow.js      流场（指针处打旋）
     field-universe.js  星场（景深 + 引力源）

   本脚本按「每次刷新轮换一种」挑选，动态注入对应脚本，
   保证同一时刻只跑一个。无 JS / 加载失败时回落到 CSS 里的
   静态 field.svg（.hero::after 会照常显示）。
   ============================================================ */
(function () {
  "use strict";

  var hero = document.querySelector(".hero");
  if (!hero || !window.requestAnimationFrame) return;

  // 动效清单：顺序即轮换顺序
  var EFFECTS = [
    "field-ripple.js",
    "field-curtain.js",
    "field-slice.js",
    "field-flow.js",
    "field-universe.js"
  ];

  var KEY = "lx-field-effect";

  function pick() {
    // 尊重「减少动态」：不注入任何 canvas，退回静态 field.svg
    if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      return null;
    }
    // 每次页面加载都轮换到下一个效果：连刷几次就能看到 5 种。
    // 用 localStorage 记住上一次用的是哪个，刷新后接着往下走。
    var last = -1;
    try { last = parseInt(localStorage.getItem(KEY), 10); } catch (e) {}
    var idx = isNaN(last) || last < 0
      ? Math.floor(Math.random() * EFFECTS.length)   // 首次随机起点
      : (last + 1) % EFFECTS.length;                 // 之后每次刷新下一个
    try { localStorage.setItem(KEY, String(idx)); } catch (e) {}
    return EFFECTS[idx];
  }

  var file = pick();
  if (!file) return;   // reduced-motion：静态兜底

  var s = document.createElement("script");
  s.src = file;
  s.async = false;
  s.onerror = function () {
    // 某个效果脚本加载失败：退回静态 SVG，不影响页面
    hero.classList.remove("has-field");
  };
  (document.body || document.documentElement).appendChild(s);
})();
