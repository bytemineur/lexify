/* ============================================================
   Lexify — 终端外壳（terminal.js）
   ------------------------------------------------------------
   只做「加壳」，不碰任何业务逻辑：
   - 顶部状态条：品牌 / 行情跑马灯 / 时钟
   - 底部快捷键条：F2–F4 切看板、F9 导出、Esc 关闭浮窗
   所有内容都从页面已有元素读取，取不到就退化成静态文案。
   ============================================================ */
(function () {
  "use strict";

  if (document.documentElement.getAttribute("data-terminal") === null) return;

  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ── 取行情文案 ─────────────────────────────── */
  function tapeItems() {
    var out = [];

    // 首页：底部 .ticker 的「数字 + 说明」
    var t = document.querySelectorAll(".ticker > div");
    for (var i = 0; i < t.length; i++) {
      var dt = t[i].querySelector("dt");
      var dd = t[i].querySelector("dd");
      if (dt && dd) out.push(dt.textContent.trim() + " " + dd.textContent.trim());
    }

    // 看板页：KPI 面板
    if (!out.length) {
      var k = document.querySelectorAll(".kpis .kpi");
      for (var j = 0; j < k.length; j++) {
        var b = k[j].querySelector("b");
        var s = k[j].querySelector("span");
        if (b && s) out.push(s.textContent.trim().replace(/^[\s·]+/, "") + " " + b.textContent.trim());
      }
    }

    if (!out.length) {
      out = ["数据源 34 采购平台", "法院公告网 每日增量", "口径可核 可回溯原文", "不卖案源 只做判断依据"];
    }
    return out;
  }

  /* ── 顶部状态条 ─────────────────────────────── */
  var bar = document.createElement("div");
  bar.className = "termbar";
  bar.setAttribute("role", "status");
  bar.setAttribute("aria-label", "终端状态");

  var mark = document.createElement("span");
  mark.className = "tb-mark";
  mark.innerHTML =
    '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
    '<path d="M7.8 4.4Q7 4.4 7 5.2v13.5h10v-2.2a1.1 1.1 0 0 0-1.1-1.1h-5.7V4.4z" fill="currentColor"/></svg>' +
    "<b>LXFY</b>";

  // 当前窗格（像终端里的 panel 名）
  var PAGE = { "": "总览", "index.html": "总览", "tender.html": "招标雷达", "hearing.html": "开庭公告", "bounty.html": "执行悬赏" };
  var leaf = (location.pathname.split("/").pop() || "");
  var page = document.createElement("span");
  page.className = "tb-page";
  page.textContent = PAGE[leaf] || leaf;
  mark.appendChild(page);

  var mid = document.createElement("div");
  mid.className = "tb-mid";

  var tape = document.createElement("div");
  tape.className = "tb-tape";
  var seq = tapeItems();
  var html = "";
  for (var r = 0; r < 2; r++) {
    for (var s = 0; s < seq.length; s++) html += "<span>" + esc(seq[s]) + "</span>";
  }
  tape.innerHTML = html;
  mid.appendChild(tape);

  var right = document.createElement("div");
  right.className = "tb-right";
  var live = document.createElement("span");
  live.className = "tb-live";
  live.innerHTML = "<i></i>LIVE";
  var clock = document.createElement("span");
  clock.className = "tb-clock";
  clock.textContent = "--:--:--";
  right.appendChild(live);
  right.appendChild(clock);

  bar.appendChild(mark);
  bar.appendChild(mid);
  bar.appendChild(right);
  document.body.insertBefore(bar, document.body.firstChild);

  function esc(str) {
    return String(str).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }

  /* ── 时钟（固定北京时区） ────────────────────── */
  function tick() {
    var d = new Date();
    // 用 toLocaleTimeString 拿到本地时分秒；终端里只需要稳定跳动
    var hh = String(d.getHours()).padStart(2, "0");
    var mi = String(d.getMinutes()).padStart(2, "0");
    var ss = String(d.getSeconds()).padStart(2, "0");
    clock.textContent = hh + ":" + mi + ":" + ss + " CST";
  }
  tick();
  setInterval(tick, 1000);

  /* ── 底部快捷键条 ───────────────────────────── */
  var foot = document.createElement("div");
  foot.className = "termfoot";
  foot.setAttribute("aria-hidden", "true");

  var keys = [
    { k: "F2", text: "招标", href: "tender.html" },
    { k: "F3", text: "开庭", href: "hearing.html" },
    { k: "F4", text: "悬赏", href: "bounty.html" },
    { k: "F9", text: "导出 CSV", act: "export" },
    { k: "ESC", text: "关闭", act: "close" }
  ];

  var parts = [];
  for (var q = 0; q < keys.length; q++) {
    var kk = keys[q];
    var inner = "<u>" + kk.k + "</u>" + kk.text;
    if (kk.href) {
      parts.push('<a class="fk" data-act="go" href="' + kk.href + '">' + inner + "</a>");
    } else {
      parts.push('<span class="fk" data-act="' + kk.act + '">' + inner + "</span>");
    }
  }
  foot.innerHTML =
    parts.join("") +
    '<span class="sp"></span>' +
    '<span class="stat" data-foot-stat>READY</span>';
  document.body.appendChild(foot);

  // 记录数：直接读表格的计数文案，2 秒同步一次
  var statEl = foot.querySelector("[data-foot-stat]");
  function syncStat() {
    var c = document.getElementById("tcount");
    if (c) {
      var txt = c.textContent.replace(/\s+/g, " ").trim();
      if (txt) { statEl.textContent = txt.toUpperCase(); return; }
    }
    var rows = document.querySelectorAll("table.list tbody tr").length;
    statEl.textContent = rows ? rows + " ROWS ON SCREEN" : "READY";
  }
  syncStat();
  setInterval(syncStat, 2000);

  /* ── 快捷键 ─────────────────────────────────── */
  function go(href) { window.location.href = href; }

  document.addEventListener("keydown", function (e) {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    var f = e.key;
    if (f === "F2") { e.preventDefault(); go("tender.html"); }
    else if (f === "F3") { e.preventDefault(); go("hearing.html"); }
    else if (f === "F4") { e.preventDefault(); go("bounty.html"); }
    else if (f === "F9") {
      e.preventDefault();
      var btn = document.getElementById("btnExport");
      if (btn) btn.click();
    } else if (f === "Escape") {
      var modal = document.getElementById("modal");
      if (modal && !modal.hidden) {
        var closer = modal.querySelector("[data-close]");
        if (closer) closer.click();
      }
    }
  });

  // 点快捷键条等同按键
  foot.addEventListener("click", function (e) {
    var el = e.target.closest("[data-act]");
    if (!el) return;
    var act = el.getAttribute("data-act");
    if (act === "go") return; // 交给 <a>
    if (act === "export") {
      var b = document.getElementById("btnExport");
      if (b) b.click();
    } else if (act === "close") {
      var m = document.getElementById("modal");
      if (m && !m.hidden) {
        var c = m.querySelector("[data-close]");
        if (c) c.click();
      }
    }
  });

  // 减少动态效果时把跑马灯停在开头，避免持续位移
  if (reduce) tape.style.animation = "none";

  /* ── 看板页：给表格加一列行号（纯视觉，不进数据） ── */
  function addRowIndex() {
    var body = document.querySelector("table.list tbody");
    var head = document.querySelector("table.list thead tr");
    if (!body || !head || head.querySelector(".th-idx")) return;

    var th = document.createElement("th");
    th.className = "th-idx";
    th.textContent = "#";
    th.style.width = "34px";
    head.insertBefore(th, head.firstChild);

    var rows = body.querySelectorAll("tr");
    for (var i = 0; i < rows.length; i++) {
      if (rows[i].querySelector(".td-idx")) continue;
      var td = document.createElement("td");
      td.className = "td-idx t-mono";
      td.style.width = "34px";
      td.textContent = String(i + 1).padStart(2, "0");
      rows[i].insertBefore(td, rows[i].firstChild);
    }
  }

  // board.js 是同步渲染的，DOMContentLoaded 之后表格已在；分页后补一次
  if (document.querySelector("table.list")) {
    addRowIndex();
    var tbody = document.querySelector("table.list tbody");
    if (tbody && window.MutationObserver) {
      new MutationObserver(function () { addRowIndex(); })
        .observe(tbody, { childList: true });
    }
  }
})();
