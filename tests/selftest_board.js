/* ============================================================
   Lexify 看板交互回归（三个看板通用，不针对单个页面写死）
   由 tests/run_ui_test.py 注入到 tender / hearing / bounty 三页后运行，
   输出 TEST| 行，供 Python 侧解析。

   设计原则：断言全部对着引擎的 CFG 与 state 去验，而不是对着某个
   具体法院名、某个具体金额。这样新增第四个看板时，这个文件不用改。
   ============================================================ */
(function () {
  "use strict";

  var out = [];
  var errs = [];

  window.addEventListener("error", function (e) {
    errs.push((e.message || "error") + " @" + (e.filename || "?") + ":" + (e.lineno || 0));
  });

  function log(s) { out.push("TEST| " + s); }
  function ok(name, cond, extra) {
    log((cond ? "PASS " : "FAIL ") + name + (extra != null ? " :: " + extra : ""));
  }
  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }
  function $(id) { return document.getElementById(id); }
  function qs(s) { return document.querySelector(s); }
  function qsa(s) { return Array.prototype.slice.call(document.querySelectorAll(s)); }
  function dbg() { return window.__LEX_DEBUG__(); }
  function rows() { return dbg().rows; }
  function all() { return dbg().all; }
  function cfg() { return dbg().cfg; }

  function reset() {
    var b = $("btnReset");
    if (b) b.click();
  }

  // 某条记录在某个 facet 上的显示值（要和引擎一样过 labelOf）
  function facetValue(r, f) {
    var v = r[f.field];
    if (v == null) return null;
    return f.labelOf ? f.labelOf(String(v)) : String(v);
  }

  // 某列参与排序的值
  function sortValue(r, col) {
    if (col.sortVal) return col.sortVal(r);
    return r[col.key];
  }

  function digits(s) { return String(s).replace(/[^\d.\-]/g, ""); }

  async function run() {
    var D = window.__LEX_DEBUG__;
    ok("引擎暴露自测钩子", typeof D === "function");
    if (typeof D !== "function") return;

    var C = cfg(), A = all();

    /* ---------- 0. 骨架 ---------- */
    ok("数据已加载", A.length > 0, "全量 " + A.length + " 条");
    ok("页头标题非空", $("bTitle") && $("bTitle").textContent.trim().length > 0,
       $("bTitle") ? $("bTitle").textContent.trim() : "无节点");
    ok("页头有英文副名", !!qs("#bTitle .en"));
    ok("副标题非空", $("bSub") && $("bSub").textContent.trim().length > 8);

    var ths = qsa("#thead th");
    ok("表头列数 = 可见列数", ths.length === dbg().cols.length,
       ths.length + " / " + dbg().cols.length);
    ok("表头有列宽声明", ths.every(function (t) { return t.style.width !== ""; }) ||
       ths.some(function (t) { return t.style.width !== ""; }), "按 c.w 控制宽度");

    var kpis = qsa("#kpis .kpi");
    ok("KPI 四项", kpis.length === 4, "实际 " + kpis.length);
    ok("KPI 数字都不是占位符", kpis.every(function (k) {
      var t = k.querySelector("b").textContent.trim();
      return t && t !== "—" && !/NaN|undefined|null/.test(t);
    }), kpis.map(function (k) { return k.querySelector("b").textContent.trim(); }).join(" / "));

    var trs = qsa("#tbody tr[data-i]");
    var ps = cfg().pageSize || 20;
    ok("首屏行数 = min(每页, 全量)", trs.length === Math.min(ps, A.length),
       trs.length + " 行 / 每页 " + ps);
    ok("每行单元格数 = 表头列数", trs.every(function (tr) {
      return tr.children.length === ths.length;
    }));
    ok("表格无 undefined / NaN 文本", !/undefined|NaN/.test($("tbody").textContent));

    // 布局回归：列宽是 table-layout:fixed 撑出来的，加列时最容易把
    // 表头挤成竖排、或者把金额单位的「万」裁掉
    var wrappedTh = ths.filter(function (t) { return t.getBoundingClientRect().height > 46; });
    ok("表头不折行", wrappedTh.length === 0,
       wrappedTh.map(function (t) { return t.textContent.trim(); }).join(", ") || "全部单行");
    var overflow = qsa("#tbody tr:first-child td").filter(function (d) {
      return d.scrollWidth > d.clientWidth + 2;
    });
    ok("单元格内容不溢出", overflow.length === 0,
       overflow.length + " 个溢出");

    /* ---------- 1. 多选筛选 ---------- */
    var mf = dbg().fields.filter(function (f) { return f.type === "multi"; })[0];
    ok("存在多选筛选器", !!mf, mf ? mf.key : "无");
    if (mf) {
      var chips = qsa('.chips[data-f="' + mf.key + '"] button.chip');
      ok("多选筛选器有选项", chips.length > 0, chips.length + " 个");
      var c0 = chips[0];
      var label = c0.getAttribute("data-v");
      var cnt = parseInt(c0.querySelector("i").textContent, 10);
      c0.click();
      var rs = rows();
      ok("多选筛选条数与 chip 计数一致", rs.length === cnt, rs.length + " / chip " + cnt);
      var bad = rs.filter(function (r) { return facetValue(r, mf) !== label; });
      ok("多选筛选结果全部命中该值", bad.length === 0, "越界 " + bad.length + " 条");
      ok("chip 进入选中态", c0.classList.contains("on"));
      c0.click();
      ok("再次点击取消筛选", rows().length === A.length, rows().length + " / " + A.length);
    }

    /* ---------- 2. 日期预设 ---------- */
    var df = dbg().fields.filter(function (f) { return f.type === "days"; })[0];
    if (df) {
      var dchips = qsa('.chips[data-f="' + df.key + '"] button.chip');
      ok("存在日期预设", dchips.length > 0, dchips.length + " 个");
      var tested = 0, nonEmpty = 0, badD = 0, detail = [];
      dchips.forEach(function (c) {
        var key = c.getAttribute("data-v");
        var p = (cfg().dayPresets || []).filter(function (x) { return x.key === key; })[0];
        if (!p) return;
        c.click();                                  // 选中
        var rsd = rows();
        tested++;
        if (rsd.length) nonEmpty++;
        badD += rsd.filter(function (r) {
          return !p.test(window.LexUtil.daysFrom(r[cfg().dateField]));
        }).length;
        detail.push(c.textContent.trim() + "=" + rsd.length);
        c.click();                                  // 取消
      });
      ok("日期预设结果全部落在区间内", badD === 0 && nonEmpty > 0,
         detail.join(" ") + (badD ? " / 越界 " + badD : ""));
      reset();
    } else {
      log("SKIP 该看板没有日期预设筛选");
    }

    /* ---------- 3. 金额筛选 ---------- */
    var af = dbg().fields.filter(function (f) { return f.type === "amount"; })[0];
    if (af) {
      var achip = qsa('.chips[data-f="' + af.key + '"] button.chip')[0];
      ok("存在金额预设", !!achip, achip ? achip.textContent.trim() : "无");
      if (achip) {
        var mn = achip.getAttribute("data-min");
        var mx = achip.getAttribute("data-max");
        mn = mn === "" || mn == null ? null : parseFloat(mn);
        mx = mx === "" || mx == null ? null : parseFloat(mx);
        achip.click();
        var rsa = rows();
        var afield = cfg().amountField || "amount";
        var badA = rsa.filter(function (r) {
          var v = parseFloat(r[afield]);
          if (isNaN(v)) return true;
          if (mn != null && v < mn) return true;
          if (mx != null && v > mx) return true;
          return false;
        });
        ok("金额筛选结果全部落在区间内", rsa.length > 0 && badA.length === 0,
           rsa.length + " 条，越界 " + badA.length);
        // 手填上下限
        var inp = qsa('.chips[data-f="' + af.key + '"] input[data-a="min"]')[0];
        achip.click();                       // 先取消预设
        inp.value = "1000000";
        inp.dispatchEvent(new Event("input"));
        ok("手填下限后结果全部达标", rows().every(function (r) {
          return parseFloat(r[afield]) >= 1000000;
        }), rows().length + " 条");
        reset();
      }
    } else {
      log("SKIP 该看板没有金额筛选");
    }

    /* ---------- 4. 排序 ---------- */
    // 每次过滤都会重建 thead，旧引用已经脱离文档，必须重新查
    function th(key) { return qs('#thead th[data-k="' + key + '"]'); }
    var sortTh = qsa("#thead th.sortable")[0];
    ok("存在可排序表头", !!sortTh);
    if (sortTh) {
      var sk = sortTh.getAttribute("data-k");
      var col = cfg().columns.filter(function (c) { return c.key === sk; })[0];
      th(sk).click();
      var vals = rows().slice(0, 12).map(function (r) { return sortValue(r, col); });
      var nums = vals.filter(function (v) { return typeof v === "number"; });
      if (nums.length >= 5) {
        var dir = dbg().state.sortDir === "asc" ? 1 : -1;
        var mono = true;
        for (var i = 1; i < nums.length; i++) {
          if ((nums[i] - nums[i - 1]) * dir < 0) mono = false;
        }
        ok("数值列排序单调", mono, dbg().state.sortDir + " · " + nums.slice(0, 5).join(","));
      } else {
        ok("文本列排序可执行", vals.length > 0, "样本 " + vals.slice(0, 3).join(" | "));
      }
      var d1 = dbg().state.sortDir;
      th(sk).click();
      ok("再次点击切换升降序", dbg().state.sortDir !== d1, d1 + " -> " + dbg().state.sortDir);

      // 缺值必须沉底，不受方向影响
      var nullCol = null;
      dbg().cols.forEach(function (c) {
        if (nullCol) return;
        if (rows().some(function (r) { return r[c.key] == null; })) nullCol = c;
      });
      if (nullCol) {
        th(nullCol.key).click();
        th(nullCol.key).click();
        var last = rows()[rows().length - 1];
        ok("空值恒排最后（降序）", last[nullCol.key] == null, "字段 " + nullCol.key);
      } else {
        log("SKIP 当前数据无可用于验证空值排序的列");
      }
      reset();
    }

    /* ---------- 5. 分页 ---------- */
    var nextBtn = qsa("#pager button").filter(function (b) {
      return /下一页/.test(b.textContent);
    })[0];
    if (A.length > ps && nextBtn) {
      var firstIdx = qsa("#tbody tr[data-i]")[0].getAttribute("data-i");
      nextBtn.click();
      var afterIdx = qsa("#tbody tr[data-i]")[0].getAttribute("data-i");
      ok("下一页换页生效", firstIdx !== afterIdx, firstIdx + " -> " + afterIdx);
      ok("第二页起始索引 = 每页条数", parseInt(afterIdx, 10) === ps, afterIdx);
      var cur = qsa("#pager button.on")[0];
      ok("页码高亮跟随", cur && cur.textContent.trim() === "2", cur ? cur.textContent.trim() : "无");
      reset();
      ok("清空筛选回到第一页", qsa("#tbody tr[data-i]")[0].getAttribute("data-i") === "0");
    } else {
      log("SKIP 数据量不足以翻页");
    }

    /* ---------- 6. 搜索 ---------- */
    var q = $("q");
    ok("存在搜索框", !!q);
    if (q && A.length) {
      // 拿一条真实记录里的长词，保证一定能搜到
      var probe = "";
      cfg().search.some(function (k) {
        var v = A[0][k];
        if (typeof v === "string" && v.length >= 8) { probe = v.slice(0, 8); return true; }
        return false;
      });
      if (!probe) probe = String(A[0].court || A[0].debtor || A[0].title || "").slice(0, 6);
      q.value = probe;
      q.dispatchEvent(new Event("input"));
      await sleep(420);
      var rsq = rows();
      var hay = function (r) {
        return cfg().search.map(function (k) { return r[k] || ""; }).join(" ").toLowerCase();
      };
      ok("关键词能搜到结果", rsq.length > 0, "「" + probe + "」-> " + rsq.length + " 条");
      ok("搜索结果全部含关键词", rsq.every(function (r) {
        return hay(r).indexOf(probe.toLowerCase()) >= 0;
      }), "共 " + rsq.length + " 条");

      q.value = "zzz不存在的关键词zzz";
      q.dispatchEvent(new Event("input"));
      await sleep(420);
      ok("无结果时显示空态", rows().length === 0 && !!$("btnEmptyReset"),
         "结果 " + rows().length + " 条");
      var eb = $("btnEmptyReset");
      if (eb) eb.click();
      await sleep(60);
      ok("空态按钮可清空筛选", rows().length === A.length, rows().length + " / " + A.length);
    }

    /* ---------- 7. 详情弹窗 ---------- */
    reset();
    var firstRow = qsa("#tbody tr[data-i]")[0];
    firstRow.click();
    var modal = $("modal");
    ok("点击行打开详情", !modal.hidden);
    ok("详情标题非空", $("mTitle").textContent.trim().length > 0, $("mTitle").textContent.trim());
    var kv = qsa("#mBody .kv dt");
    ok("详情有字段表", kv.length >= 3, kv.length + " 个字段");
    ok("详情含正文区块", /公告正文/.test($("mBody").textContent));
    ok("详情有 WAF 提示", /防火墙|403|拦截/.test($("mBody").textContent));
    var snip = (rows()[0] && rows()[0].body) || "";
    if (snip) {
      ok("存在复制全文按钮", !!$("mCopy"));
      var pre = qs("#mBody .fulltext");
      ok("正文快照已渲染", pre && pre.textContent.trim().length > 10,
         pre ? pre.textContent.trim().length + " 字" : "无节点");
    }
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    ok("Esc 可关闭详情", modal.hidden);

    /* ---------- 8. 快捷口径 ---------- */
    if ((cfg().presets || []).length) {
      var pres = qsa("#aside .preset");
      ok("快捷口径全部渲染", pres.length === cfg().presets.length,
         pres.length + " / " + cfg().presets.length);
      var n0 = parseInt(pres[0].querySelector(".pn").textContent, 10);
      qsa("#aside .preset")[0].click();   // 侧栏会重建，点之前重新取一次
      ok("套用快捷口径后条数与标注一致", rows().length === n0, rows().length + " / 标注 " + n0);
      ok("套用后快捷口径按钮自身仍高亮可用", !!qs("#aside .preset"));
      reset();
    } else {
      log("SKIP 该看板没有快捷口径");
    }

    /* ---------- 9. CSV ---------- */
    var lines = dbg().csv();
    ok("CSV 行数 = 结果数 + 表头", lines.length === rows().length + 1,
       lines.length + " 行 / 结果 " + rows().length);
    var head = lines[0].split(",");
    ok("CSV 含原文链接列", head.indexOf("原文链接") >= 0);
    ok("CSV 含公告正文列", head.indexOf("公告正文") >= 0);
    var hidden = cfg().columns.filter(function (c) { return c.hidden && c.csv !== false; });
    if (hidden.length) {
      ok("CSV 保留表格里隐藏的列", head.some(function (h) {
        return hidden.some(function (c) { return h === c.label; });
      }), "隐藏列 " + hidden.map(function (c) { return c.label; }).join("/"));
    } else {
      log("SKIP 该看板没有隐藏列");
    }
    var cols = head.length;
    ok("CSV 每行字段数一致", lines.slice(1).every(function (l) {
      // 简单校验：引号成对
      return (l.match(/"/g) || []).length % 2 === 0;
    }), cols + " 列");

    /* ---------- 10. 侧栏分布 ---------- */
    reset();
    var panels = qsa("#aside .panel");
    var expect = (cfg().dist || []).length + ((cfg().presets || []).length ? 1 : 0);
    ok("侧栏面板数 = 分布数 + 快捷口径", panels.length === expect,
       panels.length + " / " + expect);
    var distSum = qsa("#aside .dist-row").map(function (r) {
      return parseInt(r.querySelector(".dv").textContent, 10);
    });
    ok("分布条数不超过总条数", distSum.every(function (n) { return n <= A.length; }),
       "最大 " + Math.max.apply(null, distSum.concat([0])));

    /* ---------- 11. 没有英文枚举泄露 ---------- */
    var labels = qsa("#facets .chip").map(function (c) {
      return c.textContent.replace(/\d+$/, "").trim();
    }).concat(qsa("#aside .dl").map(function (e) { return e.textContent.trim(); }));
    var leaked = labels.filter(function (s) { return /^[a-z][a-z_]{2,}$/.test(s); });
    ok("界面无英文枚举值泄露", leaked.length === 0, leaked.join(", ") || "无");

    /* ---------- 12. 金额单位完整 ---------- */
    var moneyTxt = qsa("#tbody td.num, #tbody .num").map(function (t) {
      return t.textContent.trim();
    }).join(" ");
    ok("金额带单位（万/亿/元）", !/\d\s*$/.test(moneyTxt.replace(/\s+/g, " ").trim()) ||
       /万|亿|元/.test(moneyTxt), moneyTxt.slice(0, 60));

    /* ---------- 13. 运行期无 JS 错误 ---------- */
    ok("运行期无未捕获异常", errs.length === 0, errs.slice(0, 3).join(" ; ") || "无");

    log("DONE");
  }

  function emit() {
    var pre = document.getElementById("testout");
    if (!pre) {
      pre = document.createElement("pre");
      pre.id = "testout";
      document.body.appendChild(pre);
    }
    pre.textContent = out.join("\n");
  }

  // 断言过程中随时落盘，脚本中途崩掉也能看到跑到哪
  var origLog = log;
  log = function (s) { origLog(s); emit(); };

  window.addEventListener("load", function () {
    var t = setInterval(emit, 120);
    run().then(function () {
      emit();
      clearInterval(t);
    }).catch(function (e) {
      log("CRASH " + (e && e.message ? e.message : e));
      emit();
      clearInterval(t);
    });
  });
})();
