/* ============================================================
   Lexify 看板引擎
   配置驱动：kpis / facets / columns / presets / detail 全部由页面声明，
   引擎只负责筛选、排序、分页、渲染、导出。
   新增一个看板 = 一份数据 + 一份配置，不用改这里。
   ============================================================ */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function num(v) {
    var n = parseFloat(v);
    return isNaN(n) ? null : n;
  }

  /* 金额展示：按量级切换单位，避免长串零 */
  function money(v) {
    var n = num(v);
    if (n == null) return null;
    if (n >= 1e8) return trimZero((n / 1e8).toFixed(2)) + " 亿";
    if (n >= 1e4) return trimZero((n / 1e4).toFixed(n >= 1e6 ? 0 : 1)) + " 万";
    return Math.round(n).toLocaleString("zh-CN") + " 元";
  }

  // 25.0 万 -> 25 万
  function trimZero(s) { return s.replace(/\.0+$/, ""); }

  function byPath(obj, path) {
    if (!path) return obj;
    var p = path.split("."), v = obj;
    for (var i = 0; i < p.length && v != null; i++) v = v[p[i]];
    return v;
  }

  function daysFrom(dateStr) {
    if (!dateStr) return null;
    var m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(dateStr));
    if (!m) return null;
    var t = new Date(+m[1], +m[2] - 1, +m[3]);
    var now = new Date();
    var t0 = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    return Math.round((t - t0) / 86400000);
  }

  // 页面配置里也要用这几个工具（渲染列、拼金额），统一下发
  window.LexUtil = { esc: esc, num: num, money: money, daysFrom: daysFrom };

  /* ============================================================ */
  window.LexBoard = function (CFG) {
    var DATA = window.__BOARD__;
    if (!DATA) return;

    var ALL = DATA.rows || [];
    var META = DATA.meta || {};

    var state = {
      multi: {},        // facetKey -> {value: true}
      days: "",         // 日期预设 key
      amountMin: null,
      amountMax: null,
      flags: {},        // bool facet
      q: "",
      sortKey: CFG.sort.key,
      sortDir: CFG.sort.dir,
      page: 1,
      pageSize: CFG.pageSize || 20
    };

    var ROWS = [];      // 当前筛选结果
    var COLS = CFG.columns.filter(function (c) { return !c.hidden; });
    var CSV_COLS = CFG.columns.filter(function (c) { return c.csv !== false; });

    /* ---------------- 页头 ---------------- */
    function renderHead() {
      $("bTitle").innerHTML = esc(CFG.title) + '<span class="en">' + esc(CFG.en) + "</span>";
      $("bSub").textContent = CFG.tagline;
      $("bMeta").innerHTML =
        "更新于 <b>" + esc(META.generatedAt || "—") + "</b>" +
        (CFG.metaExtra ? CFG.metaExtra(META) : "");
    }

    function renderKpis() {
      var box = $("kpis");
      box.innerHTML = CFG.kpis.map(function (k) {
        var v = k.value(ALL, META);
        var lb = typeof k.label === "function" ? k.label(META, ALL) : k.label;
        var txt;
        if (k.fmt === "money") txt = money(v) || "—";
        else if (k.fmt === "yi") txt = v ? (v / 1e8).toFixed(2) + " 亿" : "—";
        else txt = v == null ? "—" : Math.round(v).toLocaleString("zh-CN");
        return '<div class="kpi ' + (k.tone || "") + '"><b>' + esc(txt) +
               "</b><span>" + esc(lb) + "</span></div>";
      }).join("");
    }

    /* ---------------- 筛选器 ---------------- */
    function facetCounts(f) {
      var m = {};
      ALL.forEach(function (r) {
        var v = byPath(r, f.field);
        if (v == null || v === "") return;
        var k = f.labelOf ? f.labelOf(String(v)) : String(v);
        m[k] = (m[k] || 0) + 1;
      });
      var arr = Object.keys(m).map(function (k) { return { k: k, n: m[k] }; });
      arr.sort(function (a, b) { return b.n - a.n; });
      if (f.top && arr.length > f.top) arr = arr.slice(0, f.top);
      return arr;
    }

    function renderFacets() {
      var box = $("facets");
      box.innerHTML = "";

      CFG.facets.forEach(function (f) {
        if (!f.label) return;
        var row = document.createElement("div");
        row.className = "frow";
        var html = "<label>" + esc(f.label) + "</label><div class=\"chips\" data-f=\"" + f.key + "\">";

        if (f.type === "multi") {
          var items = facetCounts(f);
          html += items.map(function (it) {
            var on = state.multi[f.key] && state.multi[f.key][it.k];
            var lab = f.labelOf ? f.labelOf(it.k) : it.k;
            return '<button class="chip' + (on ? " on" : "") + '" data-v="' + esc(it.k) + '">' +
                   esc(lab) + "<i>" + it.n + "</i></button>";
          }).join("");
        } else if (f.type === "days") {
          var dps = f.presets || CFG.dayPresets || [];
          html += dps.map(function (p) {
            return '<button class="chip' + (state.days === p.key ? " on" : "") +
                   '" data-v="' + p.key + '">' + esc(p.label) + "</button>";
          }).join("");
        } else if (f.type === "amount") {
          if (f.presets) {
            html += f.presets.map(function (p) {
              var on = state.amountMin === p.min && state.amountMax === (p.max == null ? null : p.max);
              return '<button class="chip' + (on ? " on" : "") + '" data-min="' +
                (p.min == null ? "" : p.min) + '" data-max="' +
                (p.max == null ? "" : p.max) + '">' + esc(p.label) + "</button>";
            }).join("");
          }
          html += '<div class="nrange" style="margin-left:4px">' +
                  '<input type="number" min="0" placeholder="下限" data-a="min" value="' +
                  (state.amountMin == null ? "" : state.amountMin) + '">' +
                  "<span>—</span>" +
                  '<input type="number" min="0" placeholder="上限" data-a="max" value="' +
                  (state.amountMax == null ? "" : state.amountMax) + '">' +
                  "<span>" + esc(f.unit || "元") + "</span></div>";
        } else if (f.type === "bool") {
          html += f.options.map(function (o) {
            return '<button class="chip' + (state.flags[f.key] === o.value ? " on" : "") +
                   '" data-v="' + esc(o.value) + '">' + esc(o.label) + "</button>";
          }).join("");
        }

        html += "</div>";
        row.innerHTML = html;
        box.appendChild(row);
      });

      // 事件
      box.querySelectorAll(".chips").forEach(function (chips) {
        var key = chips.dataset.f;
        var f = CFG.facets.filter(function (x) { return x.key === key; })[0];
        if (!f) return;

        if (f.type === "multi") {
          chips.addEventListener("click", function (e) {
            var b = e.target.closest("button.chip");
            if (!b) return;
            var v = b.dataset.v;
            state.multi[key] = state.multi[key] || {};
            if (state.multi[key][v]) delete state.multi[key][v];
            else state.multi[key][v] = true;
            b.classList.toggle("on");
            state.page = 1;
            apply();
          });
        } else if (f.type === "days" || f.type === "bool") {
          chips.addEventListener("click", function (e) {
            var b = e.target.closest("button.chip");
            if (!b) return;
            var v = b.dataset.v;
            var cur = f.type === "days" ? state.days : state.flags[key];
            var next = cur === v ? "" : v;
            if (f.type === "days") state.days = next;
            else state.flags[key] = next;
            chips.querySelectorAll(".chip").forEach(function (x) {
              x.classList.toggle("on", x.dataset.v === next);
            });
            state.page = 1;
            apply();
          });
        } else if (f.type === "amount") {
          chips.querySelectorAll("button.chip").forEach(function (b) {
            b.addEventListener("click", function () {
              var mn = b.dataset.min === "" ? null : parseFloat(b.dataset.min);
              var mx = b.dataset.max === "" ? null : parseFloat(b.dataset.max);
              var same = state.amountMin === mn && state.amountMax === mx;
              state.amountMin = same ? null : mn;
              state.amountMax = same ? null : mx;
              chips.querySelectorAll("button.chip").forEach(function (x) {
                x.classList.toggle("on", !same && x === b);
              });
              chips.querySelectorAll("input").forEach(function (inp) {
                inp.value = inp.dataset.a === "min"
                  ? (state.amountMin == null ? "" : state.amountMin)
                  : (state.amountMax == null ? "" : state.amountMax);
              });
              state.page = 1;
              apply();
            });
          });
          chips.querySelectorAll("input").forEach(function (inp) {
            inp.addEventListener("input", function () {
              var v = inp.value.trim();
              var n = v === "" ? null : parseFloat(v);
              if (inp.dataset.a === "min") state.amountMin = n;
              else state.amountMax = n;
              state.page = 1;
              apply();
            });
          });
        }
      });
    }

    /* ---------------- 筛选逻辑 ---------------- */
    function passes(r) {
      var i, f;

      for (i = 0; i < CFG.facets.length; i++) {
        f = CFG.facets[i];
        if (f.type === "multi") {
          var sel = state.multi[f.key];
          if (sel && Object.keys(sel).length) {
            var v = byPath(r, f.field);
            if (v == null) return false;
            var lv = f.labelOf ? f.labelOf(String(v)) : String(v);
            if (!sel[lv]) return false;
          }
        }
      }

      if (state.days) {
        var d = CFG.dateField ? daysFrom(r[CFG.dateField]) : r.days;
        var p = (CFG.dayPresets || []).filter(function (x) { return x.key === state.days; })[0];
        if (p && !p.test(d)) return false;
      }

      if (state.amountMin != null || state.amountMax != null) {
        var a = num(r[CFG.amountField || "amount"]);
        if (a == null) return false;
        if (state.amountMin != null && a < state.amountMin) return false;
        if (state.amountMax != null && a > state.amountMax) return false;
      }

      for (i = 0; i < CFG.facets.length; i++) {
        f = CFG.facets[i];
        if (f.type !== "bool" || !state.flags[f.key]) continue;
        if (!f.test(r, state.flags[f.key])) return false;
      }

      if (state.q) {
        var q = state.q.toLowerCase();
        var hay = CFG.search.map(function (k) { return byPath(r, k) || ""; })
                     .join(" ").toLowerCase();
        if (hay.indexOf(q) === -1) return false;
      }
      return true;
    }

    /* ---------------- 排序 ---------------- */
    function sortRows(rows) {
      var col = CFG.columns.filter(function (c) { return c.key === state.sortKey; })[0];
      var dir = state.sortDir === "asc" ? 1 : -1;
      var val = col && col.sortVal ? col.sortVal : function (r) { return r[state.sortKey]; };

      return rows.slice().sort(function (a, b) {
        var x = val(a), y = val(b);
        // 缺值恒排最后，不受升降序影响
        var xn = x == null || x === "", yn = y == null || y === "";
        if (xn && yn) return 0;
        if (xn) return 1;
        if (yn) return -1;
        if (typeof x === "number" && typeof y === "number") return (x - y) * dir;
        return String(x).localeCompare(String(y), "zh-CN") * dir;
      });
    }

    /* ---------------- 表格 ---------------- */
    function renderTable() {
      var thead = $("thead");
      thead.innerHTML = "<tr>" + COLS.map(function (c) {
        var sorted = c.key === state.sortKey;
        var arrow = sorted ? (state.sortDir === "asc" ? "▲" : "▼") : "";
        var w = c.w ? ' style="width:' + c.w + '"' : "";
        return '<th class="' + (c.sortable === false ? "" : "sortable ") +
               (sorted ? "sorted " : "") + (c.cls || "") + '"' + w +
               (c.sortable === false ? "" : ' data-k="' + c.key + '"') + ">" +
               esc(c.label) + '<span class="ar">' + arrow + "</span></th>";
      }).join("") + "</tr>";

      var start = (state.page - 1) * state.pageSize;
      var pageRows = ROWS.slice(start, start + state.pageSize);

      var tbody = $("tbody");
      if (!pageRows.length) {
        tbody.innerHTML = '<tr><td colspan="' + COLS.length + '">' +
          '<div class="empty"><p>没有符合条件的记录</p>' +
          '<button class="btn-sm" id="btnEmptyReset">清空筛选条件</button></div></td></tr>';
        var rb = $("btnEmptyReset");
        if (rb) rb.addEventListener("click", function () { resetAll(); });
      } else {
        tbody.innerHTML = pageRows.map(function (r, i) {
          return '<tr data-i="' + (start + i) + '">' + COLS.map(function (c) {
            var content = c.render ? c.render(r) : esc(r[c.key] == null ? "—" : r[c.key]);
            return "<td" + (c.cls ? ' class="' + c.cls + '"' : "") + ">" + content + "</td>";
          }).join("") + "</tr>";
        }).join("");
      }

      renderPager();
    }

    function renderPager() {
      var total = ROWS.length;
      var pages = Math.max(1, Math.ceil(total / state.pageSize));
      if (state.page > pages) state.page = pages;
      var pager = $("pager");

      if (total <= state.pageSize) {
        pager.innerHTML = '<span class="pinfo">共 ' + total + " 条</span>";
        return;
      }

      var html = '<button data-p="' + (state.page - 1) + '"' +
                 (state.page === 1 ? " disabled" : "") + ">上一页</button>";

      var list = [];
      for (var i = 1; i <= pages; i++) {
        if (i === 1 || i === pages || Math.abs(i - state.page) <= 2) list.push(i);
        else if (list[list.length - 1] !== "…") list.push("…");
      }
      list.forEach(function (p) {
        if (p === "…") html += '<span class="pinfo">…</span>';
        else html += '<button data-p="' + p + '"' + (p === state.page ? ' class="on"' : "") + ">" + p + "</button>";
      });

      html += '<button data-p="' + (state.page + 1) + '"' +
              (state.page === pages ? " disabled" : "") + ">下一页</button>";
      html += '<span class="pinfo">共 ' + total + " 条 / " + pages + " 页</span>";
      pager.innerHTML = html;

      pager.querySelectorAll("button[data-p]").forEach(function (b) {
        b.addEventListener("click", function () {
          var p = parseInt(b.dataset.p, 10);
          if (p < 1 || p > pages) return;
          state.page = p;
          renderTable();
          document.querySelector(".twrap").scrollIntoView({ behavior: "smooth", block: "start" });
        });
      });
    }

    /* ---------------- 侧栏分布 ---------------- */
    function renderAside() {
      var aside = $("aside");
      var html = "";

      (CFG.dist || []).forEach(function (d) {
        var m = {};
        ROWS.forEach(function (r) {
          var v = byPath(r, d.field);
          if (v == null || v === "") v = d.emptyLabel || "未识别";
          v = String(v);
          // 和筛选器一样按显示名归一，不然侧栏会冒出 property / both 这类枚举值
          if (d.labelOf) v = d.labelOf(v);
          m[v] = (m[v] || 0) + 1;
        });
        var arr = Object.keys(m).map(function (k) { return { k: k, n: m[k] }; })
          .sort(function (a, b) { return b.n - a.n; })
          .slice(0, d.top || 8);
        var max = arr.length ? arr[0].n : 1;

        html += '<section class="panel"><div class="panel-h">' + esc(d.label) +
                "<span>共 " + ROWS.length + " 条</span></div><div class=\"panel-b\"><div class=\"dist\">" +
                (arr.length ? arr.map(function (it, idx) {
                  return '<div class="dist-row' + (idx === 0 ? " top" : "") + '"><span class="dl" title="' + esc(it.k) + '">' +
                    esc(it.k) + '</span><span class="dv">' + it.n + "</span>" +
                    '<div class="dist-bar"><i style="width:' +
                    Math.max(3, Math.round(it.n / max * 100)) + '%"></i></div></div>';
                }).join("") : '<div style="font-size:11.5px;color:var(--text-3)">当前筛选无数据</div>') +
                "</div></div></section>";
      });

      if (CFG.presets && CFG.presets.length) {
        html += '<section class="panel"><div class="panel-h">快捷口径<span>点一下套用</span></div>' +
                '<div class="panel-b"><div class="presets">' +
                CFG.presets.map(function (p, i) {
                  var n = ALL.filter(p.test).length;
                  return '<button class="preset" data-p="' + i + '">' +
                         esc(p.label) + '<span class="pn">' + n + "</span></button>";
                }).join("") + "</div></div></section>";
      }

      if (CFG.asideNote) {
        html += '<div class="aside-note">' + CFG.asideNote + "</div>";
      }

      aside.innerHTML = html;

      aside.querySelectorAll(".preset").forEach(function (b) {
        b.addEventListener("click", function () {
          var p = CFG.presets[parseInt(b.dataset.p, 10)];
          resetAll(true);
          p.apply(state);
          syncFacetUI();
          apply();
        });
      });
    }

    function syncFacetUI() {
      // 把 state 反向同步到控件（套用快捷口径后需要）
      $("facets").querySelectorAll(".chips").forEach(function (chips) {
        var key = chips.dataset.f;
        var f = CFG.facets.filter(function (x) { return x.key === key; })[0];
        if (!f) return;
        chips.querySelectorAll("button.chip").forEach(function (b) {
          var v = b.dataset.v;
          var on = false;
          if (f.type === "multi") on = !!(state.multi[key] && state.multi[key][v]);
          else if (f.type === "days") on = state.days === v;
          else if (f.type === "bool") on = state.flags[key] === v;
          b.classList.toggle("on", on);
        });
        if (f.type === "amount") {
          chips.querySelectorAll("button.chip").forEach(function (b) {
            var mn = b.dataset.min === "" ? null : parseFloat(b.dataset.min);
            var mx = b.dataset.max === "" ? null : parseFloat(b.dataset.max);
            b.classList.toggle("on", state.amountMin === mn && state.amountMax === mx);
          });
          chips.querySelectorAll("input").forEach(function (inp) {
            inp.value = inp.dataset.a === "min"
              ? (state.amountMin == null ? "" : state.amountMin)
              : (state.amountMax == null ? "" : state.amountMax);
          });
        }
      });
      var si = $("q");
      if (si) si.value = state.q;
    }

    /* 把多选状态统一成显示名。

       chip 点击写进来的本来就是显示名，但快捷口径（presets[].apply）里
       习惯写原始枚举值（company / property 这种），两边不归一的话
       passes() 查不到、syncFacetUI() 也点不亮。这里在每次过滤前统一。 */
    function normalizeSelection() {
      CFG.facets.forEach(function (f) {
        if (f.type !== "multi" || !f.labelOf) return;
        var sel = state.multi[f.key];
        if (!sel) return;
        var out = {};
        Object.keys(sel).forEach(function (k) {
          if (sel[k]) out[f.labelOf(k)] = true;
        });
        state.multi[f.key] = out;
      });
    }

    /* ---------------- 主流程 ---------------- */
    function apply() {
      normalizeSelection();
      ROWS = ALL.filter(passes);
      ROWS = sortRows(ROWS);
      if (state.page > Math.max(1, Math.ceil(ROWS.length / state.pageSize))) state.page = 1;
      renderTable();
      var c = $("tcount");
      if (c) c.innerHTML = "筛选出 <b>" + ROWS.length + "</b> / " + ALL.length + " 条";
      renderAside();
    }

    function resetAll(silent) {
      state.multi = {};
      state.days = "";
      state.amountMin = null;
      state.amountMax = null;
      state.flags = {};
      state.q = "";
      state.page = 1;
      state.sortKey = CFG.sort.key;
      state.sortDir = CFG.sort.dir;
      if (!silent) {
        syncFacetUI();
        apply();
      }
    }

    /* ---------------- 详情 ---------------- */
    function openDetail(r) {
      var d = CFG.detail(r);
      $("mTitle").textContent = d.title || "";
      $("mSub").textContent = d.sub || "";

      var html = '<dl class="kv">' + d.kv.map(function (x) {
        return "<dt>" + esc(x[0]) + "</dt><dd>" + x[1] + "</dd>";
      }).join("") + "</dl>";

      var body = r.body || "";
      html += '<div class="body-head"><span>公告正文 · 本地留存快照</span>' +
        (body ? '<button class="btn-sm" id="mCopy">复制全文</button>' : "") + "</div>";
      html += '<div class="fulltext">' +
        esc(body || "（该条没有正文：法院排期表类数据只提供案号 / 案由 / 开庭时间 / 法庭）") + "</div>";
      html += '<p class="aside-note" style="margin-top:10px">原文链接指向源站详情页，' +
        "部分法院站点有 Web 应用防火墙，短时间多次访问会返回 403 拦截页。" +
        "上面的正文是采集时的快照，不受影响。</p>";

      $("mBody").innerHTML = html;

      var cp = $("mCopy");
      if (cp) {
        cp.addEventListener("click", function () {
          copyText(body);
          cp.textContent = "已复制";
          setTimeout(function () { cp.textContent = "复制全文"; }, 1400);
        });
      }

      var link = $("mLink");
      if (r.url) { link.href = r.url; link.style.display = ""; }
      else link.style.display = "none";

      $("modal").hidden = false;
    }

    function copyText(s) {
      try {
        if (navigator.clipboard && window.isSecureContext) {
          navigator.clipboard.writeText(s);
          return;
        }
      } catch (e) { /* 降级 */ }
      var ta = document.createElement("textarea");
      ta.value = s;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand("copy"); } catch (e) { /* 忽略 */ }
      ta.remove();
    }

    /* ---------------- CSV ---------------- */
    // 拆成「拼内容」与「触发下载」两步，自测里不弹下载也能校验内容
    function buildCSV() {
      var head = CSV_COLS.map(function (c) { return c.label; });
      head.push("原文链接", "公告正文");

      var lines = [head.join(",")];
      ROWS.forEach(function (r) {
        var cells = CSV_COLS.map(function (c) {
          return c.csvVal ? c.csvVal(r) : (r[c.key] == null ? "" : r[c.key]);
        });
        cells.push(r.url || "", r.body || "");
        lines.push(cells.map(function (c) {
          var s = c == null ? "" : String(c);
          return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
        }).join(","));
      });
      return lines;
    }

    function exportCSV() {
      var lines = buildCSV();
      var blob = new Blob(["\ufeff" + lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
      var a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = CFG.title + "_" + new Date().toISOString().slice(0, 10) + ".csv";
      document.body.appendChild(a);
      a.click();
      setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 400);
    }

    /* ---------------- 事件 ---------------- */
    function bind() {
      $("thead").addEventListener("click", function (e) {
        var th = e.target.closest("th.sortable");
        if (!th) return;
        var k = th.dataset.k;
        if (state.sortKey === k) state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
        else {
          state.sortKey = k;
          var col = CFG.columns.filter(function (c) { return c.key === k; })[0];
          state.sortDir = (col && col.firstDir) || "desc";
        }
        state.page = 1;
        apply();
      });

      $("tbody").addEventListener("click", function (e) {
        var tr = e.target.closest("tr[data-i]");
        if (!tr) return;
        var r = ROWS[parseInt(tr.dataset.i, 10)];
        if (r) openDetail(r);
      });

      var q = $("q");
      if (q) {
        var timer = null;
        q.addEventListener("input", function () {
          clearTimeout(timer);
          timer = setTimeout(function () {
            state.q = q.value.trim();
            state.page = 1;
            apply();
          }, 220);
        });
      }

      var reset = $("btnReset");
      if (reset) reset.addEventListener("click", function () { resetAll(); });

      var exp = $("btnExport");
      if (exp) exp.addEventListener("click", exportCSV);

      $("modal").addEventListener("click", function (e) {
        if (e.target.hasAttribute("data-close") || e.target.classList.contains("modal-mask")) {
          $("modal").hidden = true;
        }
      });
      document.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && !$("modal").hidden) $("modal").hidden = true;
      });
    }

    /* ---------------- 启动 ---------------- */
    renderHead();
    renderKpis();
    renderFacets();
    bind();

    // 首页带过来的关键词
    var m = /[?&]q=([^&]*)/.exec(location.search);
    if (m) {
      state.q = decodeURIComponent(m[1].replace(/\+/g, " "));
      var qi = $("q");
      if (qi) qi.value = state.q;
    }

    apply();

    /* 自测钩子：只在显式注入 tests/ 脚本时被读取，正常访问没有副作用 */
    window.__LEX_DEBUG__ = function () {
      return {
        all: ALL, rows: ROWS, state: state, cols: COLS, cfg: CFG, meta: META,
        csv: buildCSV,
        fields: CFG.facets.map(function (f) { return { key: f.key, type: f.type, field: f.field, labelOf: f.labelOf }; })
      };
    };
  };
})();
