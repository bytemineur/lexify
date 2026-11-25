/* ============================================================
   Lexify · 页首检索（search.js）
   ------------------------------------------------------------
   一个放大镜按钮 + 一个跨看板检索浮层，给所有非首页使用（首页
   的检索浮层由 app.js 负责，所以首页不引这个文件）。

   交互：点按钮 / ⌘K（Ctrl+K）打开；输入后回车去第一个看板；
   上下键选择，点菜单项直接跳；Esc 或点空白关闭。
   ============================================================ */
(function () {
  "use strict";

  var BOARDS = [
    { key: "tender", name: "招标雷达", desc: "法律服务类采购、预算与投标截止", tag: "招标" },
    { key: "hearing", name: "开庭公告", desc: "被告是企业、商事案由、临期开庭", tag: "开庭" },
    { key: "bounty", name: "执行悬赏", desc: "被执行人、执行标的、悬赏比例", tag: "悬赏" }
  ];

  var box = null;
  var input = null;
  var list = null;
  var items = [];
  var active = 0;

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function build() {
    box = document.createElement("div");
    box.className = "palette";
    box.hidden = true;
    box.innerHTML =
      '<div class="palette-box" role="dialog" aria-label="全站检索">' +
        '<div class="palette-input">' +
          '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round">' +
            '<circle cx="11" cy="11" r="6.6"/><path d="m16.2 16.2 4.3 4.3"/></svg>' +
          '<input type="search" placeholder="输入案号、公司名、项目名称或案由…" autocomplete="off">' +
        "</div>" +
        '<div class="palette-hint">选择要在哪个看板里检索</div>' +
        '<div class="palette-list"></div>' +
      "</div>";
    document.body.appendChild(box);
    input = box.querySelector("input");
    list = box.querySelector(".palette-list");

    box.addEventListener("click", function (e) {
      if (e.target === box) close();
    });
    input.addEventListener("input", render);
    input.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown") { e.preventDefault(); move(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
      else if (e.key === "Enter") { e.preventDefault(); go(items[active]); }
      else if (e.key === "Escape") close();
    });
  }

  function render() {
    var q = input.value.trim();
    items = BOARDS.map(function (b) { return { board: b, q: q }; });
    active = 0;
    list.innerHTML = items.map(function (it, i) {
      var label = it.q
        ? "在「" + it.board.name + "」里搜「" + it.q + "」"
        : "进入「" + it.board.name + "」";
      return '<button type="button" class="palette-item' + (i === 0 ? " on" : "") + '" data-i="' + i + '">' +
               '<span class="pk">' + it.board.tag + "</span>" +
               "<span>" + esc(label) + "</span>" +
               '<span class="pv">' + esc(it.board.desc) + "</span>" +
             "</button>";
    }).join("");
    var btns = list.querySelectorAll(".palette-item");
    for (var i = 0; i < btns.length; i++) {
      btns[i].addEventListener("click", function () {
        go(items[parseInt(this.dataset.i, 10)]);
      });
    }
  }

  function move(d) {
    if (!items.length) return;
    active = (active + d + items.length) % items.length;
    var btns = list.querySelectorAll(".palette-item");
    for (var i = 0; i < btns.length; i++) btns[i].classList.toggle("on", i === active);
    if (btns[active]) btns[active].scrollIntoView({ block: "nearest" });
  }

  function open(prefill) {
    if (!box) build();
    box.hidden = false;
    input.value = prefill || "";
    render();
    setTimeout(function () { input.focus(); input.select(); }, 20);
  }

  function close() {
    if (box) box.hidden = true;
  }

  function go(item) {
    if (!item) return;
    var url = item.board.key + ".html";
    if (item.q) url += "?q=" + encodeURIComponent(item.q);
    window.location.href = url;
  }

  function boot() {
    var btn = document.getElementById("btnSearch");
    var form = document.getElementById("searchForm");
    if (!btn && !form) return;

    if (btn) {
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        open("");
      });
    }
    if (form) {
      form.addEventListener("submit", function (e) {
        e.preventDefault();
        var el = document.getElementById("searchInput");
        open(el ? el.value.trim() : "");
      });
    }
    document.addEventListener("keydown", function (e) {
      if ((e.metaKey || e.ctrlKey) && (e.key === "k" || e.key === "K")) {
        e.preventDefault();
        open("");
      } else if (e.key === "Escape" && box && !box.hidden) {
        close();
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
