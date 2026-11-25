# -*- coding: utf-8 -*-
"""实时数据接口：页面直接读库，不再依赖导出的静态快照。

为什么要这一层：`web/data/*.js` 是「库里有什么 → 导出 → 页面读」的快照，
一旦采集停了，页面还停在旧快照上，看起来有数据、其实库是空的。
这里所有数字都**现查 SQLite**，保证「有就是有，没有就是没有」。

    GET /api/data/health          库状态：各表行数、最后采集时间（公开，首页用）
    GET /api/data/board/<key>     整块看板数据，字段与 build_data.py 导出的结构一致
    GET /api/data/stats/overview  招标概览：总量 / 未截止 / 7 天内到期 / 预算合计
    GET /api/data/stats/trend     按日发布趋势
    GET /api/data/stats/dist     区域 / 行业 / 类型 / 来源 分布
    GET /api/data/stats/upcoming  即将截止
    GET /api/data/sources         采集源状态（真实 crawl_runs 记录）
    GET /api/data/tenders         招标列表（服务端筛选 + 分页）

只读：全部用 `mode=ro` 打开，跑在 ThreadingHTTPServer 的多线程里也不会写坏库。
"""
import json
import os
import sqlite3
import time
import urllib.parse
from datetime import date, datetime, timedelta

from .config import DB

# 三个看板 → 库里的表。导出口径与 build_data.py 保持一致，避免两边对不上。
BOARD_TABLES = {"tender": "tenders", "hearing": "hearings", "bounty": "notices",
                "falseclaim": "false_claims"}

# 只取「主记录、非结果公告」的口径（与 build_data.py 的导出一致）
_PRIMARY = "(is_primary IS NOT 0 OR cluster_id IS NULL)"


def _conn():
    if not os.path.exists(DB):
        raise FileNotFoundError("数据库不存在：%s" % DB)
    # mode=ro：只读打开，多个请求并发读不会互相阻塞写。
    uri = "file:%s?mode=ro" % DB.replace("\\", "/")
    conn = sqlite3.connect(uri, uri=True, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _q(conn, sql, args=()):
    return conn.execute(sql, args).fetchall()


def _count(conn, table, where="", args=()):
    sql = "SELECT COUNT(*) FROM %s" % table
    if where:
        sql += " WHERE " + where
    return conn.execute(sql, args).fetchone()[0]


def _table_exists(conn, name):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _cols(conn, table):
    return {r["name"] for r in conn.execute("PRAGMA table_info(%s)" % table)}


def _day_diff(s):
    """距今天数：今天=0，将来为正，过去为负。解析不了返回 None。"""
    if not s:
        return None
    s = str(s)[:10]
    for fmt in ("%Y-%m-%d",):
        try:
            d = datetime.strptime(s, fmt).date()
            return (d - date.today()).days
        except ValueError:
            pass
    return None


def _pick(row, *names, default=None):
    """按候选字段名取值（不同表字段名略有差异）。"""
    for n in names:
        if n in row.keys() and row[n] is not None:
            return row[n]
    return default


# ── 健康状态 ───────────────────────────────────────────────────────
def health():
    if not os.path.exists(DB):
        return {"ok": False, "error": "数据库不存在", "db": os.path.basename(DB),
                "boards": {}, "sources": 0, "lastRun": None}
    conn = _conn()
    try:
        boards = {}
        for key, table in BOARD_TABLES.items():
            if _table_exists(conn, table):
                cols = _cols(conn, table)
                where = _PRIMARY if "is_primary" in cols and "cluster_id" in cols else ""
                boards[key] = _count(conn, table, where)
            else:
                boards[key] = 0
        sources = _count(conn, "sources") if _table_exists(conn, "sources") else 0
        last = None
        if _table_exists(conn, "crawl_runs"):
            r = conn.execute(
                "SELECT MAX(finished_at) a FROM crawl_runs WHERE status='success'"
            ).fetchone()
            last = r["a"] if r else None
        if not last and _table_exists(conn, "sources"):
            r = conn.execute("SELECT MAX(last_success_at) a FROM sources").fetchone()
            last = r["a"] if r else None
        return {
            "ok": True,
            "db": os.path.basename(DB),
            "boards": boards,
            "total": sum(boards.values()),
            "sources": sources,
            "lastRun": last,
            "now": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    finally:
        conn.close()


# ── 看板数据 ───────────────────────────────────────────────────────
def board(key):
    """返回与 build_data.py 导出结构一致的看板对象；没有数据就是空 rows。

    注意：这里**不**伪造、不补位。库里 0 条就返回 0 条。
    """
    table = BOARD_TABLES.get(key)
    if not table:
        raise KeyError("未知看板：%s" % key)
    conn = _conn()
    try:
        if not _table_exists(conn, table):
            return _empty_board(key)
        cols = _cols(conn, table)
        where = _PRIMARY if is_primary_ok(cols) else ""
        rows = _q(conn, "SELECT * FROM %s%s ORDER BY id DESC" % (
            table, (" WHERE " + where) if where else ""))
        if key == "tender":
            return _board_tender(conn, rows)
        if key == "hearing":
            return _board_hearing(conn, rows)
        if key == "falseclaim":
            return _board_falseclaim(conn, rows)
        return _board_bounty(conn, rows)
    finally:
        conn.close()


def is_primary_ok(cols):
    return "is_primary" in cols and "cluster_id" in cols


def _empty_board(key):
    base = {"key": key, "rows": [], "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M")}
    return dict(base, name={"tender": "招标雷达", "hearing": "开庭公告",
                            "bounty": "执行悬赏", "falseclaim": "证券虚假陈述"}.get(key, key),
                en={"tender": "Tender Radar", "hearing": "Hearing Docket",
                    "bounty": "Bounty Notice",
                    "falseclaim": "Misrepresentation Claims"}.get(key, ""),
                tagline="", meta={"total": 0, "generatedAt": base["generatedAt"]})


def _board_tender(conn, rows):
    out = []
    for r in rows:
        body = (r["content_text"] or "")[:8000]
        due = (r["deadline"] or "")[:10]
        out.append({
            "id": r["id"],
            "title": r["title"] or "",
            "org": r["purchaser"] or "",
            "agency": r["agency"] or "",
            "amount": r["budget_amount"],
            "prov": r["province"] or "",
            "city": r["city"] or "",
            "cat": r["industry"] or "",
            "type": r["notice_type"] or "",
            "pub": (r["publish_time"] or "")[:10],
            "due": due,
            "dueRaw": r["deadline_raw"] or "",
            "src": r["source_id"] or "",
            "url": r["source_url"] or "",
            "body": body,
        })
    amount_sum = sum(x["amount"] for x in out if x["amount"])
    with_amount = sum(1 for x in out if x["amount"])
    due_soon = sum(1 for x in out if x["due"] and 0 <= (_day_diff(x["due"]) or -1) <= 7)
    provs = {x["prov"] for x in out if x["prov"]}
    return {
        "key": "tender", "name": "招标雷达", "en": "Tender Radar",
        "tagline": "全国政府采购与公共资源交易平台的法律服务类招标。",
        "rows": out,
        "meta": {
            "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "total": len(out),
            "sources": len({x["src"] for x in out if x["src"]}),
            "provinces": len(provs),
            "withAmount": with_amount,
            "amountSum": amount_sum,
            "dueSoon": due_soon,
        },
    }


def _board_hearing(conn, rows):
    out = []
    for r in rows:
        hea = r["hearing_at"] or r["hearing_date"] or ""
        d = _day_diff(hea)
        out.append({
            "id": r["id"],
            "court": r["court"] or "",
            "caseNo": r["case_no"] or "",
            "cause": r["cause"] or "",
            "group": r["cause_group"] or "",
            "comm": bool(r["is_commercial"]),
            "plaintiff": r["plaintiff"] or "",
            "defendant": r["defendant"] or "",
            "defType": r["defendant_type"] or "unknown",
            "heaAt": hea,
            "heaDate": hea[:10],
            "room": r["courtroom"] or "",
            "est": bool(r["time_estimated"]),
            "amount": r["claim_amount"],
            "ntype": r["notice_type"] or "",
            "pub": (r["publish_date"] or "")[:10],
            "prov": r["province"] or "",
            "city": r["city"] or "",
            "src": r["source_id"] or r["source"] or "",
            "url": r["url"] or "",
            "body": (r["content"] or "")[:8000],
            "days": d,
        })
    company = sum(1 for x in out if x["defType"] in ("company", "mixed"))
    upcoming = sum(1 for x in out if x["days"] is not None and 0 <= x["days"] <= 30)
    precise = sum(1 for x in out if x["defType"] in ("company", "mixed")
                  and x["comm"] and x["days"] is not None and 15 <= x["days"] <= 30)
    amount_sum = sum(x["amount"] for x in out if x["amount"])
    return {
        "key": "hearing", "name": "开庭公告", "en": "Hearing Docket",
        "tagline": "法院开庭传票与排期表。",
        "rows": out,
        "meta": {
            "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "total": len(out),
            "courts": len({x["court"] for x in out if x["court"]}),
            "company": company,
            "upcoming": upcoming,
            "precise": precise,
            "withAmount": sum(1 for x in out if x["amount"]),
            "amountSum": amount_sum,
            "provinces": len({x["prov"] for x in out if x["prov"]}),
            "organizations": len({x["court"] for x in out if x["court"]}),
        },
    }


def _board_bounty(conn, rows):
    out = []
    for r in rows:
        out.append({
            "id": r["id"],
            "debtor": r["debtor"] or "",
            "court": r["court"] or "",
            "caseNo": r["case_no"] or "",
            "subject": r["subject_amount"],
            "ratio": r["reward_ratio"],
            "cap": r["reward_cap"],
            "fixed": r["reward_fixed"],
            "est": r["reward_est"],
            "type": r["reward_type"] or "",
            "ntype": r["reward_type"] or "",
            "phone": r["contact_phone"] or "",
            "vfrom": (r["valid_from"] or "")[:10],
            "vto": (r["valid_to"] or "")[:10],
            "pub": (r["publish_date"] or "")[:10],
            "prov": r["province"] or "",
            "city": r["city"] or "",
            "src": r["source_id"] or r["source"] or "",
            "url": r["url"] or "",
            "body": (r["content"] or "")[:8000],
        })
    subject_sum = sum(x["subject"] for x in out if x["subject"])
    reward_sum = sum(x["est"] for x in out if x["est"])
    return {
        "key": "bounty", "name": "执行悬赏", "en": "Bounty Notice",
        "tagline": "法院执行悬赏公告。",
        "rows": out,
        "meta": {
            "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "total": len(out),
            "courts": len({x["court"] for x in out if x["court"]}),
            "subjectSum": subject_sum,
            "rewardSum": reward_sum,
            "provinces": len({x["prov"] for x in out if x["prov"]}),
            "organizations": len({x["court"] for x in out if x["court"]}),
        },
    }


def _board_falseclaim(conn, rows):
    """证券虚假陈述：一行 = 一条公告事件（立案 / 事先告知 / 处罚决定 / 诉讼）。"""
    out = []
    for r in rows:
        try:
            types = json.loads(r["types"]) if r["types"] else []
        except (TypeError, ValueError, KeyError):
            types = []
        out.append({
            "id": r["id"],
            "code": r["sec_code"] or "",
            "name": r["sec_name"] or "",
            "board": r["board"] or "",
            "stage": r["stage"] or "other",
            "org": r["org"] or "",
            "types": types,
            "title": r["title"] or "",
            "date": (r["ann_date"] or "")[:10],
            "first": (r["first_date"] or "")[:10],
            "no": r["event_no"] or 1,
            "url": r["url"] or "",
        })
    stages = {}
    for x in out:
        stages[x["stage"]] = stages.get(x["stage"], 0) + 1
    return {
        "key": "falseclaim", "name": "证券虚假陈述", "en": "Misrepresentation Claims",
        "tagline": "证监会立案与行政处罚公告，按公司攒成链条。",
        "rows": out,
        "meta": {
            "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "total": len(out),
            "companies": len({x["code"] for x in out if x["code"]}),
            "decision": stages.get("decision", 0),
            "notice": stages.get("notice", 0),
            "investigation": stages.get("investigation", 0),
            "litigation": stages.get("litigation", 0),
        },
    }


# ── 统计（看板页用）────────────────────────────────────────────────
def _tender_base(conn):
    cols = _cols(conn, "tenders") if _table_exists(conn, "tenders") else set()
    conds = ["is_result = 0"] if "is_result" in cols else []
    if is_primary_ok(cols):
        conds.append(_PRIMARY)
    return (" WHERE " + " AND ".join(conds)) if conds else ""


def stats_overview():
    conn = _conn()
    try:
        w = _tender_base(conn)
        total = _count(conn, "tenders", w.replace(" WHERE ", "")) if w else _count(conn, "tenders")
        today = date.today().isoformat()
        week = (date.today() + timedelta(days=7)).isoformat()
        base_where = w.replace(" WHERE ", "") if w else ""
        and_ = " AND " if base_where else ""
        active = _count(conn, "tenders", base_where + and_ + "(deadline IS NULL OR deadline='' OR substr(deadline,1,10) >= ?)", (today,))
        urgent = _count(conn, "tenders", base_where + and_ + "(substr(deadline,1,10) >= ? AND substr(deadline,1,10) <= ?)", (today, week))
        s = conn.execute("SELECT COALESCE(SUM(budget_amount),0) a, "
                         "SUM(CASE WHEN budget_amount>0 THEN 1 ELSE 0 END) n FROM tenders%s" % w).fetchone()
        return {"total": total, "active": active, "urgent": urgent,
                "budgetSum": float(s["a"] or 0), "withAmount": s["n"] or 0,
                "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M")}
    finally:
        conn.close()


def stats_trend(days=30):
    days = max(7, min(int(days), 180))
    conn = _conn()
    try:
        w = _tender_base(conn)
        since = (date.today() - timedelta(days=days)).isoformat()
        and_ = " AND " if w else " WHERE "
        rows = _q(conn, "SELECT substr(publish_time,1,10) d, COUNT(*) c, "
                        "COALESCE(SUM(budget_amount),0) a FROM tenders%s%s"
                        "substr(publish_time,1,10) >= ? GROUP BY d" % (w, and_), (since,))
        by = {r["d"]: (r["c"], r["a"]) for r in rows}
        labels, counts, budgets = [], [], []
        for i in range(days, -1, -1):
            d = (date.today() - timedelta(days=i)).isoformat()
            labels.append(d)
            c, a = by.get(d, (0, 0))
            counts.append(c)
            budgets.append(round((a or 0) / 10000, 1))
        return {"labels": labels, "counts": counts, "budgetsWan": budgets}
    finally:
        conn.close()


def stats_dist(dim="province", limit=10):
    col = {"province": "province", "industry": "industry",
           "notice": "notice_type", "source": "source_id"}.get(dim)
    if not col:
        raise KeyError("未知维度：%s" % dim)
    conn = _conn()
    try:
        w = _tender_base(conn)
        and_ = " AND " if w else " WHERE "
        rows = _q(conn, "SELECT %s k, COUNT(*) c FROM tenders%s%s"
                        "COALESCE(%s,'') <> '' GROUP BY k ORDER BY c DESC LIMIT ?"
                  % (col, w, and_, col), (max(1, min(int(limit), 50)),))
        return {"dim": dim, "labels": [r["k"] for r in rows], "values": [r["c"] for r in rows]}
    finally:
        conn.close()


def stats_upcoming(days=7, limit=20):
    days = max(1, min(int(days), 30))
    conn = _conn()
    try:
        w = _tender_base(conn)
        and_ = " AND " if w else " WHERE "
        today = date.today().isoformat()
        until = (date.today() + timedelta(days=days)).isoformat()
        rows = _q(conn, "SELECT id, title, purchaser, deadline, budget_amount, province FROM tenders%s%s"
                        "substr(deadline,1,10) >= ? AND substr(deadline,1,10) <= ?"
                        " ORDER BY substr(deadline,1,10) ASC LIMIT ?"
                  % (w, and_), (today, until, max(1, min(int(limit), 100))))
        items = []
        for r in rows:
            d = (r["deadline"] or "")[:10]
            items.append({"id": r["id"], "title": r["title"] or "",
                          "purchaser": r["purchaser"] or "", "deadline": d,
                          "budget": r["budget_amount"], "province": r["province"] or "",
                          "daysLeft": _day_diff(d)})
        return {"items": items, "days": days}
    finally:
        conn.close()


# ── 来源（真实采集记录）────────────────────────────────────────────
def sources():
    """采集源的真实状态：来自 sources + crawl_runs，不虚构。

    `last_success_at` 有值才说明这站真采到过；`crawl_runs` 里最近一条
    成功/失败也一并带出来，页面上「有就是有，没有就是没有」。
    """
    if not os.path.exists(DB):
        return {"sources": [], "total": 0}
    conn = _conn()
    try:
        if not _table_exists(conn, "sources"):
            return {"sources": [], "total": 0}
        rows = _q(conn, "SELECT * FROM sources ORDER BY id")
        runs = {}
        if _table_exists(conn, "crawl_runs"):
            for r in _q(conn, "SELECT * FROM crawl_runs ORDER BY id DESC"):
                sid = r["source_id"]
                if sid not in runs:
                    runs[sid] = r
        # 每个来源真实入库的条数
        counts = {}
        if _table_exists(conn, "tenders"):
            for r in _q(conn, "SELECT source_id, COUNT(*) c FROM tenders GROUP BY source_id"):
                counts[r["source_id"]] = r["c"]
        out = []
        for r in rows:
            sid = r["id"]
            last = runs.get(sid)
            out.append({
                "id": sid,
                "name": r["name"] or sid,
                "baseUrl": r["base_url"] or "",
                "enabled": bool(r["enabled"]),
                "mode": r["mode"] or "http",
                "health": r["health"] or "idle",
                "rows": counts.get(sid, 0),
                "lastRunAt": r["last_run_at"] or "",
                "lastSuccessAt": r["last_success_at"] or "",
                "lastError": r["last_error"] or "",
                "failCount": r["fail_count"] or 0,
                "lastStatus": (last["status"] if last else ""),
                "lastRunItemsNew": (last["items_new"] if last else None),
                "lastRunError": (last["error_msg"] if last else "") or "",
                "lastRunAt": (last["started_at"] if last else "") or r["last_run_at"] or "",
            })
        return {"sources": out, "total": len(out),
                "collecting": sum(1 for s in out if not s["enabled"] or s["rows"] > 0)}
    finally:
        conn.close()


# ── 雷达（服务端筛选 + 分页）──────────────────────────────────────
def tenders(query):
    """服务端筛选：关键词 / 地区 / 类型 / 预算区间 / 只看未截止 / 含结果公告。"""
    q = (query.get("q") or [""])[0].strip()
    prov = (query.get("prov") or [""])[0].strip()
    typ = (query.get("type") or [""])[0].strip()
    lo = _num((query.get("min") or [""])[0])
    hi = _num((query.get("max") or [""])[0])
    active = (query.get("active") or ["1"])[0] == "1"
    with_result = (query.get("result") or ["0"])[0] == "1"
    sort = (query.get("sort") or ["pub"])[0]
    page = max(1, int(_num((query.get("page") or ["1"])[0]) or 1))
    size = max(1, min(int(_num((query.get("size") or ["20"])[0]) or 20), 200))

    conn = _conn()
    try:
        if not _table_exists(conn, "tenders"):
            return {"items": [], "total": 0, "page": page, "size": size}
        cols = _cols(conn, "tenders")
        where, args = [], []
        if is_primary_ok(cols):
            where.append(_PRIMARY)
        if not with_result and "is_result" in cols:
            where.append("is_result = 0")
        if q:
            where.append("(title LIKE ? OR purchaser LIKE ? OR content_text LIKE ?)")
            args += ["%%%s%%" % q] * 3
        if prov:
            where.append("province = ?")
            args.append(prov)
        if typ:
            where.append("notice_type = ?")
            args.append(typ)
        if lo is not None:
            where.append("budget_amount >= ?")
            args.append(lo)
        if hi is not None:
            where.append("budget_amount <= ?")
            args.append(hi)
        if active:
            where.append("(deadline IS NULL OR deadline='' OR substr(deadline,1,10) >= ?)")
            args.append(date.today().isoformat())
        w = (" WHERE " + " AND ".join(where)) if where else ""
        total = conn.execute("SELECT COUNT(*) FROM tenders%s" % w, args).fetchone()[0]
        order = {"pub": "substr(publish_time,1,10) DESC", "due": "substr(deadline,1,10) ASC",
                 "budget": "budget_amount DESC", "title": "title ASC"}.get(sort, "id DESC")
        rows = _q(conn, "SELECT id,title,purchaser,agency,budget_amount,province,city,"
                        "industry,notice_type,publish_time,deadline,source_id,source_url"
                        " FROM tenders%s ORDER BY %s LIMIT ? OFFSET ?"
                  % (w, order), args + [size, (page - 1) * size])
        items = []
        for r in rows:
            items.append({
                "id": r["id"], "title": r["title"] or "", "purchaser": r["purchaser"] or "",
                "agency": r["agency"] or "", "budget": r["budget_amount"],
                "province": r["province"] or "", "city": r["city"] or "",
                "industry": r["industry"] or "", "type": r["notice_type"] or "",
                "pub": (r["publish_time"] or "")[:10], "due": (r["deadline"] or "")[:10],
                "src": r["source_id"] or "", "url": r["source_url"] or "",
                "daysLeft": _day_diff((r["deadline"] or "")[:10]),
            })
        return {"items": items, "total": total, "page": page, "size": size}
    finally:
        conn.close()


def _num(s):
    try:
        v = float(str(s).replace(",", "").strip())
        return v
    except (TypeError, ValueError):
        return None


def parse_query(query_string):
    return urllib.parse.parse_qs(query_string or "")
