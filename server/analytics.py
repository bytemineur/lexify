# -*- coding: utf-8 -*-
"""访问统计：记录浏览、出报表。

设计上只做三件事：把 PV 记进 page_views、按时间/页面/来源聚合、定期清理。
不做用户画像 —— 没有登录，也不留原始 IP：IP 只存 sha256(盐+IP) 的前 16 位，
用于「同一台机器算一个访客」，反推不出地址。

所有函数都容错：库不存在 / 表没建 / 库只读时返回空统计，绝不让 /admin 500。
"""
import hashlib
import re
import sqlite3
import time
from datetime import date, datetime, timedelta

from . import config

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS page_views (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    vid         TEXT NOT NULL,
    ts          INTEGER NOT NULL,
    day         TEXT NOT NULL,
    hour        INTEGER NOT NULL,
    path        TEXT NOT NULL,
    query       TEXT,
    kw          TEXT,
    title       TEXT,
    referrer    TEXT,
    ref_host    TEXT,
    ref_kind    TEXT,
    ua          TEXT,
    browser     TEXT,
    os          TEXT,
    device      TEXT,
    ip_hash     TEXT,
    sid         TEXT,
    dwell_ms    INTEGER DEFAULT 0,
    is_bot      INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_pv_ts   ON page_views(ts);
CREATE INDEX IF NOT EXISTS idx_pv_day  ON page_views(day);
CREATE INDEX IF NOT EXISTS idx_pv_path ON page_views(path);
CREATE INDEX IF NOT EXISTS idx_pv_sid  ON page_views(sid);
CREATE UNIQUE INDEX IF NOT EXISTS idx_pv_vid ON page_views(vid);
"""

_ensured = 0                 # 上一次 ensure 成功的库路径 mtime，避免每请求都建表

BOT_RE = re.compile(
    r"bot|crawl|spider|slurp|curl|wget|python-requests|python-urllib|httpx|"
    r"go-http-client|okhttp|java/|scrapy|headlesschrome|phantomjs|linguee|"
    r"bingpreview|facebookexternalhit|semrush|ahrefs|mj12|dotbot|petalbot",
    re.I)
SEARCH_HOSTS = ("baidu.", "bing.", "google.", "so.com", "sogou.", "sm.cn", "duckduckgo.",
                "yandex.", "naver.", "ecosia.", "yisou.", "360.cn")


# ── 工具 ───────────────────────────────────────────────────────────
def _conn():
    conn = sqlite3.connect(config.DB, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def ensure():
    """建表建索引（幂等，每次进程只成功建一次）。"""
    global _ensured
    if _ensured == config.DB:
        return True
    try:
        conn = _conn()
        try:
            conn.executescript(TABLE_SQL)
            conn.commit()
        finally:
            conn.close()
        _ensured = config.DB
        return True
    except sqlite3.Error:
        return False


def enabled():
    return bool(config.analytics_conf()["enabled"])


def respect_dnt():
    """浏览器发了 DNT:1 就不记（可在 config.yaml 的 analytics.respect_dnt 关掉）。"""
    return bool(config.analytics_conf()["respect_dnt"])


def ip_hash(ip):
    salt = config.analytics_conf()["salt"]
    return hashlib.sha256(("%s|%s" % (salt, ip)).encode("utf-8")).hexdigest()[:16]


def parse_ua(ua):
    """从 UA 里认出浏览器 / 系统 / 设备类型。够用就行，不做完整指纹。"""
    ua = ua or ""
    low = ua.lower()
    device = "desktop"
    if "ipad" in low or "tablet" in low or ("android" in low and "mobile" not in low):
        device = "tablet"
    elif "mobile" in low or "iphone" in low or "android" in low:
        device = "mobile"
    if BOT_RE.search(ua) or not ua:
        device = "bot"

    if "edg/" in low:
        browser = "Edge"
    elif "opr/" in low or "opera" in low:
        browser = "Opera"
    elif "firefox/" in low:
        browser = "Firefox"
    elif "chrome/" in low or "crios/" in low:
        browser = "Chrome"
    elif "safari/" in low:
        browser = "Safari"
    elif "msie" in low or "trident/" in low:
        browser = "IE"
    else:
        browser = "其他"

    if "windows" in low:
        os_name = "Windows"
    elif "android" in low:
        os_name = "Android"
    elif "iphone" in low or "ipad" in low or "ios" in low:
        os_name = "iOS"
    elif "mac os" in low or "macintosh" in low:
        os_name = "macOS"
    elif "linux" in low:
        os_name = "Linux"
    else:
        os_name = "其他"
    return browser, os_name, device


def _split_ref(referrer):
    """来源页 → (host, 分类)。分类：direct / internal / search / external。"""
    ref = (referrer or "").strip()
    if not ref:
        return "", "direct"
    host = ""
    try:
        from urllib.parse import urlparse
        host = (urlparse(ref).netloc or "").lower()
    except ValueError:
        host = ""
    host = host.split("@")[-1].split(":")[0]
    if not host:
        return "", "direct"
    if host in ("127.0.0.1", "localhost"):
        return host, "internal"
    for h in SEARCH_HOSTS:
        if h in host:
            return host, "search"
    return host, "external"


def _kw_from_query(query):
    """从 ?q= / ?keyword= 里取搜索词（只存 60 字以内，够看趋势）。"""
    query = (query or "").lstrip("?")
    if not query:
        return ""
    try:
        from urllib.parse import parse_qs
        pairs = parse_qs(query)
    except ValueError:
        return ""
    for key in ("q", "kw", "keyword", "search"):
        v = (pairs.get(key) or [""])[0].strip()
        if v:
            return v[:60]
    return ""


def record(payload, ip=""):
    """记一条浏览。返回 True/False（没记上也不该影响页面）。"""
    if not enabled():
        return False
    if not ensure():
        return False
    now = int(time.time())
    local = time.localtime(now)
    ua = str(payload.get("ua") or "")[:200]
    browser, os_name, device = parse_ua(ua)
    path = str(payload.get("path") or "/")[:300]
    query = str(payload.get("query") or "")[:300]
    ref_host, ref_kind = _split_ref(payload.get("referrer"))
    vid = re.sub(r"[^0-9a-z\-]", "", str(payload.get("vid") or "").lower())[:40]
    if not vid:
        vid = hashlib.md5(("%s|%s|%s" % (ip, ua, now)).encode()).hexdigest()[:16]
    row = (
        vid, now, time.strftime("%Y-%m-%d", local), local.tm_hour,
        path, query, _kw_from_query(query),
        str(payload.get("title") or "")[:120],
        str(payload.get("referrer") or "")[:300], ref_host, ref_kind,
        ua, browser, os_name, device, ip_hash(ip),
        re.sub(r"[^0-9a-z]", "", str(payload.get("sid") or "").lower())[:32],
        0, 1 if device == "bot" else 0,
    )
    try:
        conn = _conn()
        try:
            conn.execute(
                "INSERT INTO page_views (vid,ts,day,hour,path,query,kw,title,referrer,ref_host,"
                "ref_kind,ua,browser,os,device,ip_hash,sid,dwell_ms,is_bot) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
            conn.commit()
        finally:
            conn.close()
        return True
    except sqlite3.Error:
        return False


def record_leave(vid, dwell_ms):
    """离页时补停留时长：取已有值和本次的较大者（防止重复上报互相覆盖）。"""
    if not enabled():
        return False
    if not ensure():
        return False
    vid = re.sub(r"[^0-9a-z\-]", "", str(vid or "").lower())[:40]
    if not vid:
        return False
    try:
        ms = max(0, min(int(dwell_ms or 0), 6 * 3600 * 1000))
    except (TypeError, ValueError):
        ms = 0
    if ms <= 0:
        return False
    try:
        conn = _conn()
        try:
            conn.execute("UPDATE page_views SET dwell_ms = MAX(dwell_ms, ?) WHERE vid = ?",
                         (ms, vid))
            conn.commit()
        finally:
            conn.close()
        return True
    except sqlite3.Error:
        return False


def cleanup():
    """按保留期删旧记录。启动时清一次，之后每天一次即可。"""
    if not ensure():
        return 0
    days = max(1, int(config.analytics_conf()["retention_days"]))
    cutoff = int(time.time()) - days * 86400
    try:
        conn = _conn()
        try:
            cur = conn.execute("DELETE FROM page_views WHERE ts < ?", (cutoff,))
            conn.commit()
            return cur.rowcount or 0
        finally:
            conn.close()
    except sqlite3.Error:
        return 0


# ── 聚合 ───────────────────────────────────────────────────────────
def _pct(part, whole):
    return round(100.0 * part / whole, 1) if whole else 0.0


def _since(days):
    days = max(1, min(int(days or 7), 365))
    today = date.today()
    start = today - timedelta(days=days - 1)
    return int(time.mktime(start.timetuple())), start, today


def _empty():
    return {"rows": 0, "pv": 0, "uv": 0, "sessions": 0, "pagesPerVisit": 0.0,
            "avgDwell": 0, "bounce": 0.0, "todayPv": 0, "todayUv": 0,
            "yesterdayPv": 0, "yesterdayUv": 0, "live": 0, "total": 0}


def overview(days=7):
    """区间概览 + 今日/昨日 + 实时在线。"""
    stats = _empty()
    if not ensure():
        return stats
    start_ts, _start, today = _since(days)
    today_str = today.strftime("%Y-%m-%d")
    yest_str = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        conn = _conn()
        try:
            base = "SELECT COUNT(*) FROM page_views WHERE is_bot=0 AND ts>=?"
            stats["total"] = conn.execute("SELECT COUNT(*) FROM page_views").fetchone()[0]
            stats["rows"] = conn.execute(base, (start_ts,)).fetchone()[0]
            stats["pv"] = stats["rows"]
            stats["uv"] = conn.execute(
                "SELECT COUNT(DISTINCT COALESCE(NULLIF(sid,''), ip_hash)) FROM page_views "
                "WHERE is_bot=0 AND ts>=?", (start_ts,)).fetchone()[0]
            stats["sessions"] = conn.execute(
                "SELECT COUNT(DISTINCT COALESCE(NULLIF(sid,''), ip_hash)) FROM page_views "
                "WHERE is_bot=0 AND ts>=?", (start_ts,)).fetchone()[0]
            row = conn.execute(
                "SELECT AVG(dwell_ms), SUM(CASE WHEN cnt=1 THEN 1 ELSE 0 END), COUNT(*) FROM ("
                "  SELECT sid, COUNT(*) cnt, MAX(dwell_ms) dwell_ms FROM page_views"
                "  WHERE is_bot=0 AND ts>=? AND sid<>'' GROUP BY sid)",
                (start_ts,)).fetchone()
            if row:
                stats["avgDwell"] = int(row[0] or 0)
                one_page = row[1] or 0
                total_sessions = row[2] or 0
                stats["bounce"] = _pct(one_page, total_sessions)
            if stats["sessions"]:
                stats["pagesPerVisit"] = round(stats["pv"] / stats["sessions"], 2)
            stats["todayPv"] = conn.execute(
                "SELECT COUNT(*) FROM page_views WHERE is_bot=0 AND day=?", (today_str,)).fetchone()[0]
            stats["todayUv"] = conn.execute(
                "SELECT COUNT(DISTINCT COALESCE(NULLIF(sid,''), ip_hash)) FROM page_views "
                "WHERE is_bot=0 AND day=?", (today_str,)).fetchone()[0]
            stats["yesterdayPv"] = conn.execute(
                "SELECT COUNT(*) FROM page_views WHERE is_bot=0 AND day=?", (yest_str,)).fetchone()[0]
            stats["yesterdayUv"] = conn.execute(
                "SELECT COUNT(DISTINCT COALESCE(NULLIF(sid,''), ip_hash)) FROM page_views "
                "WHERE is_bot=0 AND day=?", (yest_str,)).fetchone()[0]
            stats["live"] = conn.execute(
                "SELECT COUNT(DISTINCT COALESCE(NULLIF(sid,''), ip_hash)) FROM page_views "
                "WHERE is_bot=0 AND ts>=?", (int(time.time()) - 300,)).fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        return stats
    return stats


def trend(days=7):
    """每天的 PV / UV，缺的天补 0。"""
    start_ts, start, today = _since(days)
    out = []
    if not ensure():
        return out
    try:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT day, COUNT(*) pv, COUNT(DISTINCT COALESCE(NULLIF(sid,''), ip_hash)) uv "
                "FROM page_views WHERE is_bot=0 AND ts>=? GROUP BY day ORDER BY day",
                (start_ts,)).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return out
    seen = {r["day"]: r for r in rows}
    day = start
    while day <= today:
        key = day.strftime("%Y-%m-%d")
        r = seen.get(key)
        out.append({"day": key, "label": day.strftime("%m-%d"),
                    "pv": r["pv"] if r else 0, "uv": (r["uv"] if r else 0)})
        day += timedelta(days=1)
    return out


def top_pages(days=7, limit=10):
    start_ts, _s, _t = _since(days)
    try:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT path, COUNT(*) pv, COUNT(DISTINCT COALESCE(NULLIF(sid,''), ip_hash)) uv, "
                "AVG(dwell_ms) dwell FROM page_views WHERE is_bot=0 AND ts>=? "
                "GROUP BY path ORDER BY pv DESC LIMIT ?", (start_ts, limit)).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    top = rows[0]["pv"] if rows else 0
    total = sum(r["pv"] for r in rows) or 0
    # share 给条形图当宽度（相对最高的一行），pct 是占区间总 PV 的真实百分比
    return [{"path": r["path"], "pv": r["pv"], "uv": r["uv"],
             "dwell": int(r["dwell"] or 0), "share": _pct(r["pv"], top),
             "pct": _pct(r["pv"], total)} for r in rows]


def ref_kinds(days=7):
    start_ts, _s, _t = _since(days)
    try:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT ref_kind, COUNT(*) n FROM page_views WHERE is_bot=0 AND ts>=? "
                "GROUP BY ref_kind ORDER BY n DESC", (start_ts,)).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    total = sum(r["n"] for r in rows) or 0
    names = {"direct": "直接访问", "external": "外部链接", "search": "搜索引擎", "internal": "站内跳转"}
    return [{"key": r["ref_kind"] or "direct", "name": names.get(r["ref_kind"], r["ref_kind"]),
             "n": r["n"], "share": _pct(r["n"], total)} for r in rows]


def ref_hosts(days=7, limit=8):
    start_ts, _s, _t = _since(days)
    try:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT ref_host, COUNT(*) n FROM page_views "
                "WHERE is_bot=0 AND ts>=? AND ref_host<>'' AND ref_kind<>'internal' "
                "GROUP BY ref_host ORDER BY n DESC LIMIT ?", (start_ts, limit)).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    top = rows[0]["n"] if rows else 0
    return [{"host": r["ref_host"], "n": r["n"], "share": _pct(r["n"], top)} for r in rows]


def keywords(days=7, limit=8):
    start_ts, _s, _t = _since(days)
    try:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT kw, COUNT(*) n FROM page_views WHERE is_bot=0 AND ts>=? AND kw<>'' "
                "GROUP BY kw ORDER BY n DESC LIMIT ?", (start_ts, limit)).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    top = rows[0]["n"] if rows else 0
    return [{"kw": r["kw"], "n": r["n"], "share": _pct(r["n"], top)} for r in rows]


def hourly(days=7):
    """24 小时分布（看用户什么时候来）。"""
    start_ts, _s, _t = _since(days)
    buckets = [0] * 24
    try:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT hour, COUNT(*) n FROM page_views WHERE is_bot=0 AND ts>=? "
                "GROUP BY hour", (start_ts,)).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return buckets
    for r in rows:
        h = int(r["hour"] or 0) % 24
        buckets[h] += r["n"]
    return buckets


def devices(days=7):
    start_ts, _s, _t = _since(days)
    try:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT device, COUNT(*) n FROM page_views WHERE is_bot=0 AND ts>=? "
                "GROUP BY device ORDER BY n DESC", (start_ts,)).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    total = sum(r["n"] for r in rows) or 0
    names = {"desktop": "桌面端", "mobile": "手机", "tablet": "平板", "bot": "爬虫"}
    return [{"name": names.get(r["device"], r["device"] or "其他"), "n": r["n"],
             "share": _pct(r["n"], total)} for r in rows]


def browsers(days=7):
    start_ts, _s, _t = _since(days)
    try:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT browser, COUNT(*) n FROM page_views WHERE is_bot=0 AND ts>=? "
                "GROUP BY browser ORDER BY n DESC LIMIT 6", (start_ts,)).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    total = sum(r["n"] for r in rows) or 0
    return [{"name": r["browser"] or "其他", "n": r["n"], "share": _pct(r["n"], total)} for r in rows]


def recent(limit=15):
    """最近的真人访问。机器人不进这张表，否则后台第一屏全是爬虫。"""
    try:
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT ts,path,query,ref_kind,ref_host,device,browser,os,dwell_ms,is_bot,ip_hash "
                "FROM page_views WHERE is_bot=0 ORDER BY ts DESC, id DESC LIMIT ?", (limit,)).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    return [{"at": datetime.fromtimestamp(r["ts"]).strftime("%m-%d %H:%M:%S"),
             "path": r["path"], "query": r["query"] or "",
             "ref": ("直接访问" if r["ref_kind"] == "direct" else
                     (r["ref_host"] or r["ref_kind"] or "")),
             "ref_kind": r["ref_kind"] or "", "ref_host": r["ref_host"] or "",
             "device": r["device"], "browser": r["browser"], "os": r["os"],
             "dwell": int(r["dwell_ms"] or 0), "bot": bool(r["is_bot"]),
             "ip": r["ip_hash"] or ""} for r in rows]


def stats(days=7):
    """后台 / API 共用的一份完整快照。"""
    days = max(1, min(int(days or 7), 365))
    return {
        "days": days,
        "enabled": enabled(),
        "overview": overview(days),
        "trend": trend(days),
        "pages": top_pages(days, 10),
        "refKinds": ref_kinds(days),
        "refHosts": ref_hosts(days, 8),
        "keywords": keywords(days, 8),
        "hourly": hourly(days),
        "devices": devices(days),
        "browsers": browsers(days),
        "recent": recent(20),
        "retentionDays": int(config.analytics_conf()["retention_days"]),
    }
