/* ============================================================
   Lexify · 看板页顶部「招标雷达」下拉分组（nav-dropdown.js）
   ------------------------------------------------------------
   btabs 是一颗胶囊导航，招标雷达作为其中一项，下面挂三个子项
   （招标看板 / 招标列表 / 数据来源）。

   交互约定：
     · 悬停或键盘聚焦 → 展开（由 CSS 负责，这里不用管）
     · 点击「招标雷达」→ 直接进列表页，不做「点两次才跳转」那种反直觉的处理
     · 点菜单外面、按 Esc → 收起（清掉移动端手动展开的状态）
   ============================================================ */
(function () {
  "use strict";

  function boot() {
    var groups = document.querySelectorAll(".nav-drp");
    if (!groups.length) return;

    function closeAll(except) {
      for (var i = 0; i < groups.length; i++) {
        if (groups[i] !== except) groups[i].classList.remove("is-open");
      }
    }

    // 触屏没有 hover：点一下触发器把菜单钉住，再点一次收起
    for (var i = 0; i < groups.length; i++) {
      (function (grp) {
        var trigger = grp.querySelector("[data-drp]");
        if (!trigger) return;
        trigger.addEventListener("click", function (e) {
          var touch = window.matchMedia && window.matchMedia("(hover: none)").matches;
          if (!touch) {
            closeAll(null);       // 鼠标环境：直接跳转
            return;
          }
          if (!grp.classList.contains("is-open")) {
            e.preventDefault();   // 触屏第一次点击只展开
            closeAll(grp);
            grp.classList.add("is-open");
          }
        });
      })(groups[i]);
    }

    document.addEventListener("click", function (e) {
      if (!e.target.closest(".nav-drp")) closeAll(null);
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
