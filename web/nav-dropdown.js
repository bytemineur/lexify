/* ============================================================
   Lexify · 顶部导航下拉（nav-dropdown.js）
   ------------------------------------------------------------
   桌面端：展开收起完全交给 CSS（:hover / :focus-within），这里不插手。
   触屏端：没有 hover，点「看板 / 文档」第一次只展开菜单，点第二次才收起，
          子项照常跳转；点空白处或按 Esc 收起。
   ============================================================ */
(function () {
  "use strict";

  function boot() {
    var groups = document.querySelectorAll(".nav-drop");
    if (!groups.length) return;

    var touch = window.matchMedia && window.matchMedia("(hover: none)").matches;
    if (!touch) return;              // 鼠标环境什么都不做，CSS 已经够用

    function closeAll(except) {
      for (var i = 0; i < groups.length; i++) {
        if (groups[i] !== except) groups[i].classList.remove("is-open");
      }
    }

    for (var i = 0; i < groups.length; i++) {
      (function (grp) {
        var trigger = grp.querySelector(":scope > a");
        if (!trigger) return;
        trigger.addEventListener("click", function (e) {
          if (grp.classList.contains("is-open")) {
            e.preventDefault();
            grp.classList.remove("is-open");
            return;
          }
          e.preventDefault();        // 第一次点开，不跳转
          closeAll(grp);
          grp.classList.add("is-open");
        });
      })(groups[i]);
    }

    document.addEventListener("click", function (e) {
      if (!e.target.closest(".nav-drop")) closeAll(null);
    });

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") closeAll(null);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
