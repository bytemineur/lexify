# -*- coding: utf-8 -*-
"""后台管理页渲染。

和前台同一套皮肤：直接引 style.css + millennium.css（`html[data-mlp]`），
只额外加载一小份 admin.css 补布局件。所以这里不写颜色、不写圆角、不写阴影 ——
凡是「长什么样」都交给前台那套设计语言，后台只管结构。

四个页签：概览、访问分析、数据与采集、系统与配置。
全部服务端渲染（?tab= / ?days= 走 GET 参数），不依赖前端框架 —— 后台要的是
在手机上、在 SSH 隧道里、在没装任何 JS 库的机器上都能打开。

访问统计的数据来自 server/analytics.py；这一层只负责排版和取数。
"""
import html
import os
import sqlite3

from . import analytics
from . import config
from .db import db_stats, db_tables_ok, db_writable
from .jobs import JOB, SCHEDULER, job_html

TABS = (("overview", "概览"), ("traffic", "访问分析"), ("data", "数据与采集"), ("system", "系统与配置"))

TAB_META = {
    "overview": ("控制台", "CONTROL PANEL", "站点访问、数据体量、后台入口一览。"),
    "traffic": ("访问分析", "TRAFFIC", "谁来过、看了什么、从哪来、留多久。"),
    "data": ("数据与采集", "DATA & INGEST", "更新、导入、重建，以及采集失败对账。"),
    "system": ("系统与配置", "SYSTEM", "配置文件、AI Key、库与口令状态。"),
}


def clamp_days(value, default=7):
    try:
        days = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    if days < 1:
        return default
    return min(days, 365)


# ── 小组件 ─────────────────────────────────────────────────────────
def _esc(v):
    return html.escape(str(v if v is not None else ""))


def _fmt(n):
    if isinstance(n, float):
        n = round(n, 1)
    if isinstance(n, float) and n == int(n):
        n = int(n)
    return "{:,}".format(n) if isinstance(n, int) else str(n)


def _kpi(label, value, sub="", hot=False):
    sub_html = '<i class="d">%s</i>' % _esc(sub) if sub else ""
    return ('<div class="kpi%s"><b>%s</b><span>%s</span>%s</div>'
            % (" hot" if hot else "", _fmt(value), _esc(label), sub_html))


def _kpis(items, cols=0):
    cls = "kpis" + (" k%d" % cols if cols else "")
    return '<div class="%s">%s</div>' % (cls, "".join(items))


def _sec(title, hint, body, tag="", key=None):
    """页面分区：上一条发丝线 + 标题 + 说明 + 内容。"""
    tag_html = '<span class="tag">%s</span>' % _esc(tag) if tag else ""
    hint_html = '<p class="hint">%s</p>' % _esc(hint) if hint else ""
    anchor = ' id="%s"' % key if key else ""
    return ('<section class="asec"%s><div class="asec-h"><div><h2>%s</h2>%s</div>%s</div>%s</section>'
            % (anchor, _esc(title), hint_html, tag_html, body))


def _bars(rows, empty="还没有浏览记录。"):
    """rows: [(名称, 数值, 百分比)]。"""
    if not rows:
        return '<p class="aempty">%s</p>' % _esc(empty)
    out = []
    for name, val, pct in rows:
        out.append('<div class="abrow"><span class="abname" title="%s">%s</span>'
                   '<span class="abtrack"><i class="abfill" style="width:%s%%"></i></span>'
                   '<span class="abval">%s</span></div>'
                   % (_esc(name), _esc(name), max(1.0, min(100.0, float(pct or 0))), _fmt(val)))
    return '<div class="abars">%s</div>' % "".join(out)


def _table(headers, rows, empty="没有数据。", mono=(), cls="", num=()):
    """rows 里的值一律当纯文本转义；num 里的列下标右对齐（数字列）。"""
    if not rows:
        return '<p class="aempty">%s</p>' % _esc(empty)
    head = "".join('<th%s>%s</th>' % (' class="n"' if i in num else "", _esc(h))
                   for i, h in enumerate(headers))
    body = []
    for row in rows:
        cells = []
        for i, cell in enumerate(row):
            classes = [c for c in (("mono" if i in mono else ""), ("n" if i in num else "")) if c]
            cells.append('<td%s>%s</td>' % (' class="%s"' % " ".join(classes) if classes else "",
                                            _esc(cell)))
        body.append("<tr>%s</tr>" % "".join(cells))
    return ('<div class="atbl-wrap"><table class="atbl%s"><thead><tr>%s</tr></thead>'
            '<tbody>%s</tbody></table></div>'
            % ((" " + cls) if cls else "", head, "".join(body)))


def _nice_max(v):
    """把纵轴上界收成整数档位（0/5/10/…），免得出现 3.8、1.2 这种刻度。"""
    v = max(1, int(v))
    step = max(1, int(-(-v // 4)))
    for m in (1, 2, 5, 10, 15, 20, 25, 30, 50, 100, 150, 200, 250, 300, 500, 1000):
        if m >= step:
            step = m
            break
    while step * 4 < v:
        step *= 2
    return step * 4


def _chart(trend, width=1000, height=260):
    """内联 SVG：实线 = PV，虚线 = UV。不引第三方图表库。"""
    if not trend:
        return '<p class="aempty">这个区间还没有浏览记录。</p>'
    pad_l, pad_r, pad_t, pad_b = 34, 8, 14, 26
    top = _nice_max(max(max(d["pv"], d["uv"]) for d in trend))
    n = len(trend)
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    def x(i):
        return pad_l + (plot_w * i / (n - 1) if n > 1 else plot_w / 2)

    def y(v):
        return pad_t + plot_h * (1 - float(v) / top)

    parts = ['<svg class="achart" viewBox="0 0 %d %d" role="img" aria-label="访问趋势">' % (width, height)]
    for k in range(5):
        val = top * k / 4.0
        yy = y(val)
        parts.append('<line class="g" x1="%d" y1="%.1f" x2="%d" y2="%.1f"/>'
                     % (pad_l, yy, width - pad_r, yy))
        parts.append('<text class="axis" x="%d" y="%.1f" text-anchor="end">%s</text>'
                     % (pad_l - 7, yy + 3.5, _fmt(val)))

    pv_pts = " ".join("%.1f,%.1f" % (x(i), y(d["pv"])) for i, d in enumerate(trend))
    uv_pts = " ".join("%.1f,%.1f" % (x(i), y(d["uv"])) for i, d in enumerate(trend))
    parts.append('<polyline class="pv" points="%s"/>' % pv_pts)
    parts.append('<polyline class="uv" points="%s"/>' % uv_pts)
    if n <= 14:
        for i, d in enumerate(trend):
            parts.append('<circle class="dot" cx="%.1f" cy="%.1f" r="2.4"/>' % (x(i), y(d["pv"])))

    # 横轴标签：最多 7 个，首尾必留；两个标签中心距离不足就跳过，避免糊成一团
    if n <= 7:
        idx = list(range(n))
    else:
        gap = plot_w / 8.0
        idx = [0]
        i = max(1, int(round((n - 1) / 7.0)))
        while i < n - 1:
            if x(i) - x(idx[-1]) >= gap:
                idx.append(i)
            i += max(1, int(round((n - 1) / 7.0)))
        if x(n - 1) - x(idx[-1]) >= gap or len(idx) == 1:
            idx.append(n - 1)
        else:
            idx[-1] = n - 1
    for i in idx:
        anchor = "middle" if 0 < i < n - 1 else ("start" if i == 0 else "end")
        parts.append('<text class="axis" x="%.1f" y="%d" text-anchor="%s">%s</text>'
                     % (x(i), height - 8, anchor, _esc(trend[i]["label"])))
    parts.append("</svg>")
    return "".join(parts)


def _hours(buckets):
    if not any(buckets):
        return '<p class="aempty">还没有足够的数据。</p>'
    peak = max(buckets) or 1
    cells = "".join('<i class="%s" style="height:%d%%" title="%02d 时 · %d 次"></i>'
                    % ("p" if b == peak else "", max(2, round(100.0 * b / peak)), h, b)
                    for h, b in enumerate(buckets))
    return ('<div class="ahours">%s</div><div class="ahours-x">'
            '<span>00</span><span>06</span><span>12</span><span>18</span><span>23</span></div>' % cells)


def _legend():
    return ('<div class="alegend"><span><i></i>浏览量 PV</span>'
            '<span><i class="u"></i>访客 UV</span></div>')


def _seg(days, tab="traffic"):
    out = []
    for d, label in ((7, "7 天"), (30, "30 天"), (90, "90 天")):
        out.append('<a href="/admin?tab=%s&amp;days=%d"%s>%s</a>'
                   % (tab, d, ' class="on"' if d == days else "", label))
    return '<span class="seg">%s</span>' % "".join(out)


def _topbar_html(active):
    """顶栏和前台完全同构：brand + .nav + .topbar-right。"""
    links = "".join('<a href="/admin?tab=%s"%s>%s</a>'
                    % (key, ' class="on"' if key == active else "", label)
                    for key, label in TABS)
    right = ['<a href="/">回到站点</a>', '<a href="/docs">接口文档</a>']
    if config.ADMIN_OPEN:
        right.append('<a class="flag" href="#">后台未设防</a>')
    else:
        right.append('<a href="/admin/logout">退出登录</a>')
    return ('<header class="topbar"><a class="brand" href="/">'
            '<span class="brand-text"><strong>lexify</strong><em>控制台</em></span></a>'
            '<nav class="nav">%s</nav>'
            '<div class="topbar-right">%s</div></header>' % (links, "".join(right)))


# ── 各页签 ─────────────────────────────────────────────────────────
def _tile(delta):
    if delta == 0:
        return "与昨日持平"
    return ("较昨日 +%s" % _fmt(delta)) if delta > 0 else ("较昨日 %s" % _fmt(delta))


def _tab_overview(st, counts, runs, output):
    ov = st["overview"]
    kpis = _kpis([
        _kpi("今日浏览量 PV", ov["todayPv"], _tile(ov["todayPv"] - ov["yesterdayPv"])),
        _kpi("今日访客 UV", ov["todayUv"], _tile(ov["todayUv"] - ov["yesterdayUv"])),
        _kpi("近 7 天 PV", ov["pv"], "人均 %.2f 页" % ov["pagesPerVisit"]),
        _kpi("近 7 天 UV", ov["uv"], "平均停留 %s" % _duration(ov["avgDwell"])),
        _kpi("实时在线", ov["live"], "最近 5 分钟", hot=True),
    ], cols=5)

    pages = [(p["path"], p["pv"], p["share"]) for p in st["pages"][:8]]
    kinds = [(k["name"], k["n"], k["share"]) for k in st["refKinds"]]
    recent_rows = [(r["at"], _page_cell(r), _ref(r), _dev(r["device"]), _duration(r["dwell"]))
                   for r in st["recent"][:10]]
    data_rows = [(k, "—" if (v is None or v < 0) else _fmt(v))
                 for k, v in (counts or {}).items()]

    body = [kpis]
    body.append(_sec("访问趋势", "每天有多少人打开页面、开了多少次。",
                     _chart(st["trend"]) + _legend(), tag="近 7 天"))
    body.append('<div class="agrid">')
    body.append(_sec("被访问页面", "浏览量最高的页面。", _bars(pages)))
    body.append(_sec("浏览来源", "「直接访问」= 没有来源页（收藏、扫码、直接输网址）。",
                     _bars(kinds)))
    body.append("</div>")
    body.append(_sec("最近访问", "最新的 10 条浏览记录，不含机器人；「停留」由离页时的浏览器上报。",
                     _table(("时间", "页面", "来源", "设备", "停留"), recent_rows,
                            empty="还没有访客。打开站点任意页面就会出现在这里。", cls="wide")))
    if counts:
        body.append(_sec("数据概况", "三块看板当前的库内条数。导入 / 采集 / 重建在「数据与采集」。",
                         _table(("表", "条数"), data_rows, num=(1,)),
                         tag="共 %s 条" % _fmt(sum(v for v in (counts or {}).values()
                                                  if v and v > 0) if counts else 0)))
    if output:
        body.append(_sec("执行结果", "", "<pre>%s</pre>" % _esc(output)))
    return "".join(body)


def _tab_traffic(st, days):
    ov = st["overview"]
    kpis = _kpis([
        _kpi("区间浏览量 PV", ov["pv"], "近 %d 天" % days),
        _kpi("区间访客 UV", ov["uv"], "人均 %.2f 页" % ov["pagesPerVisit"]),
        _kpi("会话数", ov["sessions"], "同一标签页算一次"),
        _kpi("平均停留", _duration(ov["avgDwell"]), "每次浏览"),
        _kpi("跳出率", "%.0f%%" % ov["bounce"], "只看一页就走"),
        _kpi("实时在线", ov["live"], "最近 5 分钟", hot=True),
    ], cols=6)

    page_rows = []
    for p in st["pages"]:
        page_rows.append((p["path"], _fmt(p["pv"]), _fmt(p["uv"]),
                          _duration(p["dwell"]), "%.1f%%" % p["pct"]))
    dev_rows = [(d["name"], d["n"], d["share"]) for d in st["devices"]]
    brw_rows = [(b["name"], b["n"], b["share"]) for b in st["browsers"]]
    recent_rows = [(r["at"], _page_cell(r), _ref(r), "%s · %s" % (_dev(r["device"]), r["browser"]),
                    _duration(r["dwell"])) for r in st["recent"]]

    body = [kpis]
    body.append(_sec("访问趋势", "实线是浏览量，虚线是访客数（同一会话同一人只算一次）。",
                     _chart(st["trend"]) + _legend(), tag="近 %d 天" % days))
    body.append('<div class="agrid">')
    body.append(_sec("浏览来源", "访客从哪些渠道进来。",
                     _bars([(k["name"], k["n"], k["share"]) for k in st["refKinds"]])))
    body.append(_sec("来源域名 Top", "外部链接具体来自哪个域名。",
                     _bars([(h["host"], h["n"], h["share"]) for h in st["refHosts"]])))
    body.append("</div>")
    body.append('<div class="agrid">')
    body.append(_sec("页面排行", "各页面的浏览量与平均停留。",
                     _table(("页面", "PV", "UV", "平均停留", "占比"), page_rows,
                            mono=(0,), num=(1, 2, 4))))
    body.append(_sec("搜索词 Top", "从雷达页搜索框带过来的关键词。",
                     _bars([(k["kw"], k["n"], k["share"]) for k in st["keywords"]])))
    body.append("</div>")
    body.append(_sec("时段分布", "按小时看访客什么时候来（0-23 点）。", _hours(st["hourly"])))
    body.append('<div class="agrid">')
    body.append(_sec("设备", "桌面端 / 手机 / 平板。", _bars(dev_rows)))
    body.append(_sec("浏览器", "访客用什么浏览器。", _bars(brw_rows)))
    body.append("</div>")
    body.append(_sec("最近访问", "最新 20 条；IP 只存加盐哈希，看不到原始地址。",
                     _table(("时间", "页面", "来源", "设备 / 浏览器", "停留"), recent_rows,
                            cls="wide")))
    body.append('<section class="asec"><p class="aempty">统计口径：机器人（搜索引擎、爬虫、脚本）'
                '默认排除；保留 %d 天原始记录；访客浏览器开 DNT 时不记录。</p></section>'
                % int(st["retentionDays"]))
    return "".join(body)


def _tab_data(counts, runs, fails, output):
    if counts is None:
        counts_html = ('<p class="aempty">还没有库，先跑 <code>python scripts/manage.py init</code>。</p>')
    else:
        rows = [(_esc(k), "—" if v is None or v < 0 else _fmt(v)) for k, v in counts.items()]
        counts_html = _table(("表", "条数"), rows)

    crawl_form = ('<form class="arow" method="post" action="/admin/crawl">'
                  '<button class="abtn p" name="mode" value="all">采集全部并更新</button>'
                  '<button class="abtn" name="mode" value="tender">只采招标</button>'
                  '<button class="abtn" name="mode" value="hearing">只采开庭</button>'
                  '<button class="abtn" name="mode" value="bounty">只采悬赏</button></form>')
    upload_form = ('<form class="arow" method="post" action="/admin/upload" '
                   'enctype="multipart/form-data"><input class="afield" type="file" name="file" required>'
                   '<input class="afield" type="text" name="source" placeholder="来源标识（可选）" size="16">'
                   '<button class="abtn p" type="submit">上传并入库</button></form>')
    rebuild_form = ('<form class="arow" method="post" action="/admin/rebuild">'
                    '<button class="abtn" type="submit" name="publish" value="0">只重建页面数据</button>'
                    '<button class="abtn p" type="submit" name="publish" value="1">'
                    '重建并推送到 GitHub</button></form>')

    if fails:
        agg = {}
        for f in fails:
            agg[f["source"]] = agg.get(f["source"], 0) + 1
        pills = "".join('<span class="pill">%s ×%d</span>' % (_esc(k), v)
                        for k, v in sorted(agg.items(), key=lambda kv: -kv[1])[:12])
        rows = [((f["started_at"] or "")[5:16], f["board"] or "", f["source"] or "",
                 f["stage"] or "", (f["reason"] or "")[:70]) for f in fails]
        fails_html = ('<div class="arow">%s</div>' % pills + _table(("时间", "看板", "来源", "阶段", "原因"), rows))
    else:
        fails_html = '<p class="aempty">没有未处理的采集失败。</p>'

    if runs:
        rows = [(r["started_at"] or "", r["kind"] or "", r["mode"] or "",
                 _fmt(r["total_seen"]), "+" + _fmt(r["inserted"]), _fmt(r["updated"]),
                 "skip " + _fmt(r["skipped"]), r["status"] or "") for r in runs]
        runs_html = _table(("时间", "类型", "方式", "读到", "新增", "更新", "跳过", "状态"), rows)
    else:
        runs_html = '<p class="aempty">还没有导入记录。</p>'

    body = [_sec("当前数据", "统一库里的条数，三块看板共用这一个库。", counts_html)]
    body.append(_sec("手动更新数据", "会联网采集，几分钟到十几分钟不等；进度看下方任务输出。",
                     crawl_form + job_html()))
    body.append(_sec("上传数据", "离线补数据：把导出的 .db / .jsonl / .csv 传上来入库，"
                                 "类型按字段自动识别。", upload_form + rebuild_form))
    if output:
        body.append(_sec("执行结果", "", "<pre>%s</pre>" % _esc(output)))
    body.append(_sec("采集失败", "没修好的会一直挂在这里。", fails_html))
    body.append(_sec("最近导入", "每次采集 / 导入都会记一条。", runs_html))
    return "".join(body)


def _db_size():
    try:
        size = os.path.getsize(config.DB)
    except OSError:
        return "—"
    if size > 1024 * 1024:
        return "%.1f MB" % (size / 1024.0 / 1024.0)
    return "%.0f KB" % (size / 1024.0)


def _schema_version():
    try:
        conn = sqlite3.connect(config.DB, timeout=2)
        try:
            return conn.execute("PRAGMA user_version").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        return "—"


def _tab_system(counts):
    ai = config.ai_conf()
    ana = config.analytics_conf()
    try:
        from . import ai as aimod
        ai_state = aimod.settings()
    except Exception:                        # noqa: BLE001
        ai_state = {"enabled": False, "model": "", "keySource": ""}

    ai_rows = [
        ("状态", "已启用" if ai_state.get("enabled") else "未启用（没配 Key）"),
        ("Key 来源", ai_state.get("keySource") or "—"),
        ("模型", ai_state.get("model") or "—"),
        ("接口地址", ai.get("base_url") or "—"),
        ("超时", "%s 秒" % ai.get("timeout")),
        ("思考模式", ai.get("thinking") or "off"),
    ]
    cfg_rows = [
        ("配置文件", config.config_summary()),
        ("读取结果", "已生效" if config.CONFIG_SRC else "没有配置文件，全部用默认值"),
        ("库路径", config.DB),
        ("库大小", _db_size()),
        ("schema 版本", _schema_version()),
        ("监听", "%s:%s" % (config.BIND, config.PORT)),
        ("定时采集", ("每天 %s" % SCHEDULER["at"]) if SCHEDULER["on"]
         else "本进程内没开（server.schedule=false）；如果服务器用 systemd timer / cron 跑 manage.py update，这是正常的"),
        ("后台口令", "已设置（环境变量 / config.yaml）" if config.ADMIN_TOKEN else "未设置 → 只允许本机直连"),
        ("访问统计", ("开 · 保留 %d 天" % ana["retention_days"]) if ana["enabled"] else "关"),
        ("环境变量覆盖", "LEXIFY_* 环境变量优先级高于 config.yaml"),
    ]
    help_html = (
        '<div class="anotice"><b>要改 LLM Key？</b> 打开项目根目录的 <code>config.yaml</code>，'
        '把 <code>ai.api_key</code> 填上（形如 <code>sk-…</code>），重启进程即可；'
        '<code>curl -s 127.0.0.1:%s/api/ai/status</code> 返回 <code>enabled:true</code> 就生效了。'
        '不想写文件时也能用环境变量 <code>LEXIFY_AI_API_KEY</code>，它会盖掉配置文件。</div>'
        % config.PORT
    )
    body = [_sec("AI 咨询", "页面右下角的「乐菲」；Key 只从服务端读，前端永远拿不到。",
                 _table(("项", "值"), ai_rows))]
    body.append(_sec("运行与配置", "这些值在进程启动时读入，改了要重启。",
                     _table(("项", "值"), cfg_rows)))
    body.append(_sec("怎么改配置", "", help_html))
    body.append(_sec("常用命令", "在本机或服务器上直接跑。",
                     "<pre>python main.py --port %s --bind %s\n"
                     "python scripts/manage.py stats\n"
                     "python scripts/manage.py build\n"
                     "python tests/test_static.py</pre>" % (config.PORT, config.BIND)))
    return "".join(body)


def _duration(ms):
    ms = int(ms or 0)
    if ms <= 0:
        return "—"
    if ms < 1000:
        return "%d ms" % ms
    sec = ms / 1000.0
    if sec < 60:
        return "%.0f 秒" % sec
    if sec < 3600:
        return "%.0f 分" % (sec / 60.0)
    return "%.1f 小时" % (sec / 3600.0)


DEVICE_NAMES = {"desktop": "桌面端", "mobile": "手机", "tablet": "平板", "bot": "机器人"}
REF_NAMES = {"direct": "直接访问", "internal": "站内跳转", "search": "搜索引擎",
             "external": "外部链接"}


def _dev(name):
    return DEVICE_NAMES.get(name or "", name or "—")


def _ref(r):
    kind = r.get("ref_kind") or ""
    if kind in ("direct", "internal"):
        return REF_NAMES[kind]
    return r.get("ref_host") or REF_NAMES.get(kind, "—")


def _page_cell(r):
    """最近访问的「页面」列：路径 + 解码后的查询串（搜索词不会再显示成 %E6%8B%9B）。"""
    path = r["path"] or "/"
    query = r.get("query") or ""
    if query:
        try:
            from urllib.parse import unquote
            query = unquote(query)
        except Exception:      # noqa: BLE001 - 解不开就原样显示
            pass
        path += query
    if len(path) > 46:
        path = path[:44] + "…"
    return path


# ── 入口 ───────────────────────────────────────────────────────────
def render(output="", tab="overview", days=7):
    days = clamp_days(days)
    tab = tab if tab in dict(TABS) else "overview"
    counts, runs, fails = db_stats()

    writable, why = db_writable()
    notice = ""
    if not writable:
        notice = ('<div class="anotice bad"><b>数据目录不可写：</b>%s<br>'
                  '采集、上传、重建都会失败（站点浏览、访问统计入库也会失败）。'
                  '通常是之前用 <code>sudo</code> 跑过，<code>data/</code> 属主变成了 root：<br>'
                  '<code>sudo chown -R USER:USER %s</code>，然后重启进程。</div>'
                  % (_esc(why), _esc(os.path.dirname(os.path.abspath(config.DB)))))
    elif counts is not None and not db_tables_ok():
        notice = ('<div class="anotice"><b>库里没有核心表</b>（schema 没建成）。'
                  '多半是库可写但建表时被中断 —— 点「采集全部并更新」会重新建表。</div>')

    st = analytics.stats(days)
    if tab == "traffic":
        content = _tab_traffic(st, days)
    elif tab == "data":
        content = _tab_data(counts, runs, fails, output)
    elif tab == "system":
        content = _tab_system(counts)
    else:
        content = _tab_overview(st, counts, runs, output)

    title, en, sub = TAB_META.get(tab, TAB_META["overview"])
    meta = []
    if tab in ("overview", "traffic"):
        meta.append(_seg(days, tab))
    meta_html = '<div class="bhead-meta">%s</div>' % "".join(meta)

    refresh = '<meta http-equiv="refresh" content="5">' if JOB["running"] else ""
    page = (
        '<!DOCTYPE html><html lang="zh-CN" data-mlp><head><meta charset="utf-8">%s'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="robots" content="noindex,nofollow">'
        '<title>%s · lexify 控制台</title>'
        '<link rel="stylesheet" href="/style.css">'
        '<link rel="stylesheet" href="/millennium.css">'
        '<link rel="stylesheet" href="/admin.css">'
        '</head><body class="adm">%s'
        '<section class="bhead"><div class="wrap bhead-in">'
        '<h1>%s <span class="en">%s</span></h1><p class="sub">%s</p>%s</div></section>'
        '<main class="wrap adm-main">%s%s</main>'
        '<footer class="foot"><div class="wrap foot-in">'
        '<a class="foot-brand" href="/"><strong>lexify</strong> 控制台</a>'
        '<p class="foot-note">数据来自公开渠道，仅供参考；后台操作会直接改本地库。</p>'
        '</div></footer></body></html>'
        % (refresh, _esc(title), _topbar_html(tab), _esc(title), _esc(en), _esc(sub),
           meta_html, notice, content))
    return page
