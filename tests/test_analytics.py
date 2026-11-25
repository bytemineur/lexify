# -*- coding: utf-8 -*-
"""访问统计自检：入库、去机器人、聚合口径、留存期清理。

用法：
    python tests/test_analytics.py

用临时库跑，不碰 data/lexify.db。覆盖：
    1. 建表幂等、record 入库、UV 按会话去重
    2. 机器人 UA 不算 PV/UV
    3. 来源分类（直接 / 搜索 / 外链）与搜索词提取
    4. 停留时长取较大值、IP 只存加盐哈希
    5. 趋势补 0 天、页面排行、stats() 结构完整
    6. 保留期清理
"""
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server import analytics, config   # noqa: E402

fails, passes = [], []
_orig = {"DB": config.DB, "analytics_conf": config.analytics_conf}


def ok(name, cond, extra=""):
    (passes if cond else fails).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (" :: " + str(extra) if extra else ""))


def fake_conf(**over):
    base = {"enabled": True, "retention_days": 180, "respect_dnt": True, "salt": "test-salt"}
    base.update(over)
    return lambda: dict(base)


CHROME_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
BOT_UA = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"


def main():
    tmpdir = tempfile.mkdtemp(prefix="lexify-analytics-")
    config.DB = os.path.join(tmpdir, "test.db")
    config.analytics_conf = fake_conf()
    analytics._ensured = 0
    try:
        return run()
    finally:
        config.DB = _orig["DB"]
        config.analytics_conf = _orig["analytics_conf"]
        analytics._ensured = 0


def run():
    print("[1] 建表与入库")
    ok("ensure 建表", analytics.ensure() is True)
    ok("ensure 幂等", analytics.ensure() is True)
    analytics._ensured = 0
    ok("重复建表不报错", analytics.ensure() is True)

    ok("记录桌面端浏览", analytics.record(
        {"vid": "v-1", "sid": "s-1", "path": "/", "title": "首页",
         "referrer": "", "ua": CHROME_UA}, ip="1.2.3.4") is True)
    ok("记录站内跳转", analytics.record(
        {"vid": "v-2", "sid": "s-1", "path": "/tender.html", "title": "招标雷达",
         "query": "?q=%E6%8B%9B%E6%A0%87&p=2",
         "referrer": "http://127.0.0.1:8010/", "ua": CHROME_UA}, ip="1.2.3.4") is True)
    ok("记录搜索来源", analytics.record(
        {"vid": "v-3", "sid": "s-2", "path": "/hearing.html", "title": "开庭公告",
         "referrer": "https://www.baidu.com/s?wd=lexify", "ua": MOBILE_UA}, ip="5.6.7.8") is True)
    ok("记录外链来源", analytics.record(
        {"vid": "v-4", "sid": "s-3", "path": "/bounty.html", "title": "执行悬赏",
         "referrer": "https://lawyer.example.com/post/1", "ua": CHROME_UA}, ip="9.9.9.9") is True)
    ok("机器人不入 PV", analytics.record(
        {"vid": "v-bot", "sid": "s-bot", "path": "/", "ua": BOT_UA}, ip="6.6.6.6") is True)

    print("[2] 概览口径")
    ov = analytics.overview(7)
    ok("PV 只算真人", ov["pv"] == 4, ov["pv"])
    ok("UV = 3 个会话", ov["uv"] == 3, ov["uv"])
    ok("今日 PV", ov["todayPv"] == 4, ov["todayPv"])
    ok("人均页数", ov["pagesPerVisit"] == round(4.0 / 3, 2), ov["pagesPerVisit"])
    ok("实时在线 > 0", ov["live"] >= 1, ov["live"])
    ok("库内总行数含机器人", ov["total"] == 5, ov["total"])

    print("[3] 来源与搜索词")
    kinds = {k["name"]: k["n"] for k in analytics.ref_kinds(7)}
    ok("直接访问 1", kinds.get("直接访问") == 1, kinds)
    ok("搜索引擎 1", kinds.get("搜索引擎") == 1, kinds)
    ok("外部链接 1", kinds.get("外部链接") == 1, kinds)
    ok("站内跳转不算外链", kinds.get("站内跳转", 0) == 1, kinds)
    hosts = {h["host"]: h["n"] for h in analytics.ref_hosts(7)}
    ok("来源域名 baidu", hosts.get("www.baidu.com") == 1, hosts)
    kws = {k["kw"]: k["n"] for k in analytics.keywords(7)}
    ok("搜索词取自 ?q= 的中文", kws.get("招标") == 1, kws)

    print("[4] 页面排行与趋势")
    pages = {p["path"]: p for p in analytics.top_pages(7)}
    ok("四个页面都在", set(pages) == {"/", "/tender.html", "/hearing.html", "/bounty.html"},
       sorted(pages))
    ok("首页 PV 最大", pages["/"]["pv"] == 1, pages["/"])
    ok("占比是真实百分比、合计 100",
       abs(sum(p["pct"] for p in pages.values()) - 100) < 0.5,
       [p["pct"] for p in pages.values()])
    ok("share 是相对最高的条形宽度", all(p["share"] <= 100 for p in pages.values()))
    trend = analytics.trend(7)
    ok("趋势正好 7 天", len(trend) == 7, len(trend))
    ok("趋势有 label", all(d.get("label") for d in trend))
    ok("今天有 PV", trend[-1]["pv"] == 4, trend[-1])

    print("[5] 停留时长")
    ok("离页上报", analytics.record_leave("v-1", 4200) is True)
    ok("更大值覆盖", analytics.record_leave("v-1", 9000) is True)
    ok("更小值不覆盖", analytics.record_leave("v-1", 100) is True)
    rec = analytics.recent(10)
    v1 = [r for r in rec if r["path"] == "/"]
    ok("最近访问里有首页", bool(v1), rec[:2])
    ok("停留取 9000", v1 and v1[0]["dwell"] == 9000, v1 and v1[0]["dwell"])
    ok("未知 vid 不报错", analytics.record_leave("nope-xxxx", 1000) is True)
    ok("非法时长忽略", analytics.record_leave("v-1", "abc") is False)

    print("[6] 隐私")
    h = analytics.ip_hash("1.2.3.4")
    ok("IP 哈希 16 位", len(h) == 16, h)
    ok("哈希不含原 IP", "1.2.3.4" not in h)
    ok("同 IP 同哈希", analytics.ip_hash("1.2.3.4") == h)
    ok("不同 IP 不同哈希", analytics.ip_hash("1.2.3.5") != h)
    rec_all = analytics.recent(50)
    ok("机器人不进最近访问", not any(r["bot"] for r in rec_all), len(rec_all))
    ok("最近访问只剩 4 条真人", len(rec_all) == 4, len(rec_all))

    print("[7] 聚合结构")
    st = analytics.stats(7)
    need = ["days", "enabled", "overview", "trend", "pages", "refKinds", "refHosts",
            "keywords", "hourly", "devices", "browsers", "recent", "retentionDays"]
    ok("stats 键齐全", set(need) <= set(st), sorted(set(need) - set(st)))
    ok("时段 24 桶", len(st["hourly"]) == 24, len(st["hourly"]))
    ok("设备有数据", sum(d["n"] for d in st["devices"]) >= 4, st["devices"])
    ok("浏览器认得 Chrome", any(b["name"] == "Chrome" for b in st["browsers"]), st["browsers"])
    ok("移动端识别", any(d["name"] in ("手机", "移动") for d in st["devices"]) or
       any(d["name"] == "mobile" for d in st["devices"]), st["devices"])

    print("[8] 关闭统计与清理")
    config.analytics_conf = fake_conf(enabled=False)
    ok("关闭后不记录", analytics.record({"vid": "v-x", "path": "/"}, ip="1.1.1.1") is False)
    config.analytics_conf = fake_conf()
    ok("清理未来数据不误删", analytics.cleanup() == 0)
    config.analytics_conf = fake_conf(retention_days=1)
    import sqlite3
    conn = sqlite3.connect(config.DB)
    conn.execute("INSERT INTO page_views (vid,ts,day,hour,path,is_bot) "
                 "VALUES ('old-1', ?, '2000-01-01', 0, '/old', 0)",
                 (int(time.time()) - 10 * 86400,))
    conn.commit()
    conn.close()
    ok("按保留期删旧", analytics.cleanup() >= 1)
    ok("新数据还在", analytics.overview(7)["total"] >= 4, analytics.overview(7)["total"])

    print("\n%d passed, %d failed" % (len(passes), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
