/* Lexify 首页交互：主题、数字填充、真实记录预览、全站检索浮层 */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var root = document.documentElement;

  /* ---------------- 主题 ---------------- */
  var THEME_KEY = "lx-theme";

  function applyTheme(mode) {
    root.setAttribute("data-theme", mode);
    root.style.colorScheme = mode;
  }

  function initTheme() {
    var mode = root.getAttribute("data-theme") || "dark";
    var btns = document.querySelectorAll("#themeSwitch button");
    for (var i = 0; i < btns.length; i++) {
      btns[i].classList.toggle("on", btns[i].dataset.theme === mode);
      btns[i].addEventListener("click", function () {
        var m = this.dataset.theme;
        applyTheme(m);
        try { localStorage.setItem(THEME_KEY, m); } catch (e) {}
        for (var j = 0; j < btns.length; j++) btns[j].classList.toggle("on", btns[j].dataset.theme === m);
      });
    }
  }

  /* ---------------- 数据填充 ----------------
     首页所有数字都来自 data/overview.js，HTML 里的只是无数据时的兜底，
     这样重新采集后不用回来改文案。 */
  function initData() {
    var ov = window.__OVERVIEW__;
    if (!ov) return;
    var els = document.querySelectorAll("[data-m]");
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      var v = ov;
      var path = el.dataset.m.split(".");
      for (var k = 0; k < path.length && v != null; k++) v = v[path[k]];
      if (v == null || isNaN(v)) continue;

      var fmt = el.dataset.fmt;
      if (fmt === "yi") {
        el.textContent = (v / 1e8).toFixed(2) + " 亿";
      } else {
        el.textContent = Math.round(v).toLocaleString("zh-CN");
      }
      // 顶部数字带动画要跟着实际数值走
      if (el.dataset.n) el.dataset.n = String(Math.round(v));
    }
  }

  /* ---------------- 数字滚动 ---------------- */
  function countUp(el) {
    var target = parseInt(el.dataset.n, 10);
    if (!target || isNaN(target)) return;
    var final = target.toLocaleString("zh-CN");
    // 无头浏览器（自动化截图 / CI）里 rAF 时间戳与 performance.now()
    // 可能不同源，进度会算出负数，直接落终值。
    if (/HeadlessChrome|HeadlessEdg/i.test(navigator.userAgent) ||
        window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      el.textContent = final;
      return;
    }
    var start = performance.now();
    var dur = 760;
    function step(now) {
      // clamp：任何时间基准异常都不能让进度越界，否则会出现负数
      var p = Math.min(1, Math.max(0, (now - start) / dur));
      var eased = 1 - Math.pow(1 - p, 3);
      el.textContent = Math.round(target * eased).toLocaleString("zh-CN");
      if (p < 1) requestAnimationFrame(step);
      else el.textContent = final;
    }
    requestAnimationFrame(step);
    // 兜底：后台标签页里 rAF 会被节流甚至不跑，到点直接落终值
    setTimeout(function () { el.textContent = final; }, dur + 300);
  }

  function initTicker() {
    // 数字带本来就在首屏，不做懒加载：先写死终值兜底，
    // 再在下一帧回拨到 0 起播，避免任何环境下出现一直显示 0 的情况。
    var els = document.querySelectorAll("#ticker dt[data-n]");
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      var target = parseInt(el.dataset.n, 10);
      if (isNaN(target)) continue;
      el.textContent = target.toLocaleString("zh-CN");
      (function (node, t, delay) {
        setTimeout(function () { countUp(node); }, delay);
      })(el, target, 240 + i * 90);
    }
  }

  /* ---------------- 首屏预览 ----------------
     右侧那块不是插画，是看板里真实的三条记录，由 build_data.py
     写进 overview.js 的 sample 里。缺失时整块隐藏，不留空壳。 */
  function initPreview() {
    var box = $("preview");
    var list = $("previewRows");
    var ov = window.__OVERVIEW__;
    if (!box || !list || !ov) return;

    var rows = (ov.sample && ov.sample.hearing) || [];
    if (!rows.length) { box.hidden = true; return; }

    list.innerHTML = rows.map(function (r) {
      var d = r.days;
      var when = (r.heaDate || "").slice(5).replace("-", "/");
      var rel = d == null ? "" : (d === 0 ? "今天开庭" : d + " 天后");
      var sub = [shortCourt(r.court), r.cause].filter(Boolean).join(" · ");
      var amt = r.amount ? moneyShort(r.amount) : "—";
      return '<div class="pv-row">' +
               '<div class="pv-when">' + esc(when) +
                 '<span class="pv-d' + (d != null && d <= 7 ? " soon" : "") + '">' + esc(rel) + "</span>" +
               "</div>" +
               '<div class="pv-party">' + esc(partyLabel(r.defendant)) +
                 '<span class="pv-meta">' + esc(clip(sub, 26)) + "</span>" +
               "</div>" +
               '<div class="pv-amt">' + esc(amt) + "</div>" +
             "</div>";
    }).join("");
  }

  /* 首屏那三行只交代「谁、在哪、什么事」，一个当事人名就够。
     多个被告时收成「首被告 等 N 人」，否则一行要塞四五个公司名。 */
  function partyLabel(s) {
    s = String(s == null ? "" : s).trim();
    if (!s) return "被告未披露";
    var parts = s.split("、");
    if (parts.length > 1) return clip(parts[0], 16) + " 等 " + parts.length + " 人";
    return clip(s, 18);
  }

  /* 法院名收短：省掉「人民法院」这类在上下文里已经自明的字。
     看板表格里用全称，首屏空间小，只需要认出是哪家法院。 */
  function shortCourt(s) {
    s = String(s == null ? "" : s).replace(/^广东省/, "");
    return s.replace("经济技术开发区人民法院", "经开区法院")
            .replace(/人民法院$/, "法院");
  }

  function moneyShort(v) {
    if (v >= 1e8) return (v / 1e8).toFixed(2).replace(/\.?0+$/, "") + " 亿";
    if (v >= 1e5) return Math.round(v / 1e4) + " 万";
    if (v >= 1e4) return (v / 1e4).toFixed(1).replace(/\.0$/, "") + " 万";
    return Math.round(v).toLocaleString("zh-CN") + " 元";
  }

  function clip(s, n) {
    s = String(s == null ? "" : s);
    return s.length > n ? s.slice(0, n) + "…" : s;
  }

  /* ---------------- 检索 ---------------- */
  var BOARDS = [
    { key: "hearing", name: "开庭公告", desc: "被告是企业、商事案由、临期开庭", tag: "开庭" },
    { key: "tender", name: "招标雷达", desc: "法律服务类采购、预算与投标截止", tag: "招标" },
    { key: "bounty", name: "执行悬赏", desc: "被执行人、执行标的、悬赏比例", tag: "悬赏" }
  ];

  var palette = null;
  var paletteInput = null;
  var paletteList = null;
  var activeIdx = 0;
  var currentItems = [];

  function buildPalette() {
    palette = document.createElement("div");
    palette.className = "palette";
    palette.hidden = true;
    palette.innerHTML =
      '<div class="palette-box">' +
        '<div class="palette-input">' +
          '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round">' +
            '<circle cx="11" cy="11" r="6.6"/><path d="m16.2 16.2 4.3 4.3"/></svg>' +
          '<input type="search" placeholder="输入案号、公司名、项目名称或案由…" autocomplete="off">' +
        '</div>' +
        '<div class="palette-hint">选择要在哪个看板里检索</div>' +
        '<div class="palette-list"></div>' +
      '</div>';
    document.body.appendChild(palette);
    paletteInput = palette.querySelector("input");
    paletteList = palette.querySelector(".palette-list");

    palette.addEventListener("click", function (e) {
      if (e.target === palette) closePalette();
    });
    paletteInput.addEventListener("input", renderPalette);
    paletteInput.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown") { e.preventDefault(); move(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
      else if (e.key === "Enter") { e.preventDefault(); go(currentItems[activeIdx]); }
      else if (e.key === "Escape") closePalette();
    });
  }

  function renderPalette() {
    var q = paletteInput.value.trim();
    currentItems = BOARDS.map(function (b) {
      return { board: b, q: q };
    });
    activeIdx = 0;
    paletteList.innerHTML = currentItems.map(function (it, i) {
      var label = it.q
        ? "在「" + it.board.name + "」里搜「" + it.q + "」"
        : "进入「" + it.board.name + "」";
      return '<button class="palette-item' + (i === 0 ? " on" : "") + '" data-i="' + i + '">' +
               '<span class="pk">' + it.board.tag + "</span>" +
               "<span>" + esc(label) + "</span>" +
               '<span class="pv">' + esc(it.board.desc) + "</span>" +
             "</button>";
    }).join("");
    var btns = paletteList.querySelectorAll(".palette-item");
    for (var i = 0; i < btns.length; i++) {
      btns[i].addEventListener("click", function () {
        go(currentItems[parseInt(this.dataset.i, 10)]);
      });
    }
  }

  function move(d) {
    if (!currentItems.length) return;
    activeIdx = (activeIdx + d + currentItems.length) % currentItems.length;
    var btns = paletteList.querySelectorAll(".palette-item");
    for (var i = 0; i < btns.length; i++) btns[i].classList.toggle("on", i === activeIdx);
    if (btns[activeIdx]) btns[activeIdx].scrollIntoView({ block: "nearest" });
  }

  function openPalette(prefill) {
    if (!palette) buildPalette();
    palette.hidden = false;
    paletteInput.value = prefill || "";
    renderPalette();
    setTimeout(function () { paletteInput.focus(); paletteInput.select(); }, 20);
  }

  function closePalette() {
    if (palette) palette.hidden = true;
  }

  function go(item) {
    if (!item) return;
    var url = item.board.key + ".html";
    if (item.q) url += "?q=" + encodeURIComponent(item.q);
    window.location.href = url;
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function initSearch() {
    var form = $("searchForm");
    var input = $("searchInput");
    if (form) {
      form.addEventListener("submit", function (e) {
        e.preventDefault();
        var q = input.value.trim();
        if (!q) { openPalette(""); return; }
        // 单输入框跨三个看板时，先问去哪个看板查
        openPalette(q);
      });
    }
    var btn = $("btnSearch");
    if (btn) btn.addEventListener("click", function () { openPalette(""); });

    var hots = document.querySelectorAll(".hero-hot button");
    for (var i = 0; i < hots.length; i++) {
      hots[i].addEventListener("click", function () {
        var q = this.dataset.q;
        if (input) input.value = q;
        openPalette(q);
      });
    }

    document.addEventListener("keydown", function (e) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        openPalette("");
      } else if (e.key === "Escape" && palette && !palette.hidden) {
        closePalette();
      }
    });
  }

  /* ---------------- 导航高亮 ---------------- */
  function initNav() {
    var links = document.querySelectorAll("#nav a");
    if (!links.length || !("IntersectionObserver" in window)) return;
    var map = {};
    for (var i = 0; i < links.length; i++) {
      var id = links[i].getAttribute("href").slice(1);
      if (id) map[id] = links[i];
    }
    var secs = document.querySelectorAll("section[id]");
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (!en.isIntersecting) return;
        for (var k = 0; k < links.length; k++) links[k].classList.remove("on");
        if (map[en.target.id]) map[en.target.id].classList.add("on");
      });
    }, { rootMargin: "-20% 0px -65% 0px" });
    for (var j = 0; j < secs.length; j++) io.observe(secs[j]);
  }

  /* ---------------- 启动 ---------------- */
  function boot() {
    initTheme();
    initData();
    initTicker();
    initPreview();
    initSearch();
    initNav();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
