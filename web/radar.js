/* ============================================================
   Lexify · 招标雷达（radar.js）
   ------------------------------------------------------------
   筛选与分页都交给后端 /api/data/tenders，前端不做全量过滤。
   这样页面看到的条数就是库里真实命中的条数，不会出现
   「前端筛 800 条、库里有 1000 条」的口径差。
   ============================================================ */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };

  var state = { page: 1, size: 20, maxPage: 1, items: [], total: 0, loaded: false };

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function num(n) {
    return (n == null || isNaN(n)) ? "—" : Number(n).toLocaleString("zh-CN");
  }

  function money(v) {
    if (v == null) return "未披露";
    if (v >= 1e8) return (v / 1e8).toFixed(2).replace(/\.?0+$/, "") + " 亿";
    if (v >= 1e4) return (v / 1e4).toFixed(1).replace(/\.0$/, "") + " 万";
    return num(v) + " 元";
  }

  function setLive(ok, text) {
    var pill = $("livePill");
    pill.className = "live-pill" + (ok ? "" : " live-pill--bad");
    $("liveText").textContent = text;
  }

  function params(extra) {
    var p = new URLSearchParams();
    var q = $("fq").value.trim();
    if (q) p.set("q", q);
    if ($("fprov").value) p.set("prov", $("fprov").value);
    if ($("ftype").value) p.set("type", $("ftype").value);
    if ($("fmin").value) p.set("min", $("fmin").value);
    if ($("fmax").value) p.set("max", $("fmax").value);
    p.set("active", $("factive").checked ? "1" : "0");
    p.set("result", $("fresult").checked ? "1" : "0");
    p.set("sort", $("fsort").value);
    p.set("page", String(state.page));
    p.set("size", String(state.size));
    if (extra) Object.keys(extra).forEach(function (k) { p.set(k, extra[k]); });
    return p.toString();
  }

  function itemHtml(it) {
    var d = it.daysLeft;
    var dcls = d == null ? "" : (d < 0 ? " is-past" : (d <= 3 ? " is-danger" : (d <= 7 ? " is-warn" : "")));
    var dtxt = it.due ? (d == null ? it.due : (d < 0 ? "已截止" : (d === 0 ? "今天截止" : d + " 天后截止"))) : "无截止时间";
    return '<article class="ri">' +
      '<div class="ri-main">' +
        '<h3 class="ri-t"><a href="' + esc(it.url || "#") + '" target="_blank" rel="noopener noreferrer">' +
          esc(it.title) + "</a></h3>" +
        '<p class="ri-meta">' + esc(it.purchaser || "采购人未披露") +
          (it.province ? " · " + esc(it.province) : "") +
          (it.type ? ' · <span class="ri-type">' + esc(it.type) + "</span>" : "") +
          (it.industry ? " · " + esc(it.industry) : "") + "</p>" +
      "</div>" +
      '<div class="ri-side">' +
        '<span class="ri-money">' + esc(money(it.budget)) + "</span>" +
        '<span class="ri-due' + dcls + '">' + esc(dtxt) + "</span>" +
      "</div>" +
      "</article>";
  }

  function render(data) {
    state.items = data.items || [];
    state.total = data.total || 0;
    state.maxPage = Math.max(1, Math.ceil(state.total / state.size));
    $("rCount").textContent = "共 " + num(state.total) + " 条";
    $("rList").innerHTML = state.items.length
      ? state.items.map(itemHtml).join("")
      : '<div class="dempty">没有符合条件的公告。换个关键词或放宽筛选试试。</div>';
    var pager = $("rPager");
    pager.hidden = state.maxPage <= 1;
    $("pInfo").textContent = "第 " + state.page + " / " + state.maxPage + " 页";
    $("pPrev").disabled = state.page <= 1;
    $("pNext").disabled = state.page >= state.maxPage;
  }

  function load(keepPage) {
    if (!keepPage) state.page = 1;
    var qs = params();
    history.replaceState(null, "", "?" + qs);
    fetch("api/data/tenders?" + qs, { cache: "no-store" })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (data) {
        render(data);
        setLive(true, "实时 · 数据库");
        if (!state.loaded) { state.loaded = true; fillSelects(data); }
      })
      .catch(function (e) {
        setLive(false, "接口不可用");
        $("rList").innerHTML = '<div class="dempty dempty--err">加载失败：' + esc(e.message) +
          "。确认后端已启动（python main.py）。</div>";
        $("rCount").textContent = "—";
      });
  }

  /* 地区 / 类型下拉：用看板接口里真实出现过的值，不写死。 */
  function fillSelects(data) {
    fetch("api/data/board/tender", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (b) {
        var rows = b.rows || [];
        var provs = {}, types = {};
        rows.forEach(function (r) {
          if (r.prov) provs[r.prov] = (provs[r.prov] || 0) + 1;
          if (r.type) types[r.type] = (types[r.type] || 0) + 1;
        });
        fill($("fprov"), provs, "全部地区");
        fill($("ftype"), types, "全部类型");
      })
      .catch(function () { /* 下拉拿不到不影响主列表 */ });
  }

  function fill(sel, map, first) {
    var keys = Object.keys(map).sort(function (a, b) { return map[b] - map[a]; });
    sel.innerHTML = '<option value="">' + first + "</option>" +
      keys.map(function (k) {
        return '<option value="' + esc(k) + '">' + esc(k) + " (" + map[k] + ")</option>";
      }).join("");
  }

  function exportCsv() {
    var qs = params({ size: "5000", page: "1" });
    fetch("api/data/tenders?" + qs, { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var head = ["标题", "采购人", "预算", "地区", "城市", "类型", "行业", "发布日期", "截止日期", "来源", "链接"];
        var lines = [head.join(",")];
        (d.items || []).forEach(function (it) {
          lines.push([it.title, it.purchaser, it.budget, it.province, it.city,
                      it.type, it.industry, it.pub, it.due, it.src, it.url]
            .map(function (v) { return '"' + String(v == null ? "" : v).replace(/"/g, '""') + '"'; })
            .join(","));
        });
        var blob = new Blob(["\ufeff" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
        var a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = "lexify-tender-" + new Date().toISOString().slice(0, 10) + ".csv";
        a.click();
        URL.revokeObjectURL(a.href);
      });
  }

  function boot() {
    var timer = null;
    function debounced() {
      clearTimeout(timer);
      timer = setTimeout(function () { load(false); }, 300);
    }
    ["fq", "fmin", "fmax"].forEach(function (id) {
      $(id).addEventListener("input", debounced);
    });
    ["fprov", "ftype", "fsort", "factive", "fresult"].forEach(function (id) {
      $(id).addEventListener("change", function () { load(false); });
    });
    $("freset").addEventListener("click", function () {
      $("fq").value = "";
      $("fprov").value = "";
      $("ftype").value = "";
      $("fmin").value = "";
      $("fmax").value = "";
      $("factive").checked = true;
      $("fresult").checked = false;
      $("fsort").value = "pub";
      load(false);
    });
    $("pPrev").addEventListener("click", function () {
      if (state.page > 1) { state.page--; load(true); }
    });
    $("pNext").addEventListener("click", function () {
      if (state.page < state.maxPage) { state.page++; load(true); }
    });
    $("btnCsv").addEventListener("click", exportCsv);

    // 从别的页面带 ?q= 过来时先回填关键词
    var q0 = new URLSearchParams(location.search).get("q");
    if (q0) $("fq").value = q0;
    load(false);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
