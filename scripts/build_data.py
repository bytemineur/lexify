# -*- coding: utf-8 -*-
"""把三个看板的数据源统一导出成前端可直接加载的 JS。

数据来源各自独立（招标在 tender-radar，法院两块在 bounty-radar 的库里），
这里只做「读 → 归一 → 写 web/data/*.js」，不改动源库。

用法：
    python scripts/build_data.py
    python scripts/build_data.py --tender-db /path/tender.db --court-db /path/bounty.db
"""
import argparse
import json
import os
import sqlite3
import sys
from datetime import date, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "web", "data")

DEFAULT_TENDER_DB = r"C:\Users\zhh\Desktop\tender-radar\data\tender.db"
DEFAULT_COURT_DB = r"C:\Users\zhh\WorkBuddy\2026-11-10-23-26-21\bounty-radar\data\bounty.db"

BODY_LIMIT = 8000

# 省级行政区后缀，用于跨源并集：招标记「吉林」、法院记「吉林省」，
# 不归一化会把同一个省算成两个。
_PROV_SUFFIX = ("壮族自治区", "回族自治区", "维吾尔自治区", "特别行政区", "自治区", "省", "市")


def norm_prov(p):
    p = (p or "").strip()
    for suf in _PROV_SUFFIX:
        if p.endswith(suf) and len(p) > len(suf):
            return p[: -len(suf)]
    return p or None


# 城市字段跨源不一致：「深圳」「深圳市」「深圳前海」指的是同一个市。
# 不归一化，前端「城市」筛选会把一个深圳拆成三项。
_CITY_ALIAS = {
    "深圳前海": "深圳市",
    "深圳": "深圳市",
    "前海": "深圳市",
}
_CITY_SUFFIX = ("市", "自治州", "地区", "盟", "区", "县")


def norm_city(c):
    c = (c or "").strip()
    if not c:
        return None
    c = _CITY_ALIAS.get(c, c)
    if not c.endswith(_CITY_SUFFIX):
        c += "市"
    return c


def norm_body(v):
    if not v:
        return None
    s = " ".join(str(v).split())
    return s[:BODY_LIMIT] if len(s) > BODY_LIMIT else s


def connect(path):
    if not os.path.exists(path):
        sys.exit(f"数据库不存在：{path}")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def day_diff(d):
    """距今天数，正数表示未来。"""
    if not d:
        return None
    try:
        y, m, dd = (int(x) for x in str(d)[:10].split("-"))
        return (date(y, m, dd) - date.today()).days
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------- 招标
def build_tender(db_path):
    conn = connect(db_path)
    rows = []
    for r in conn.execute(
            "SELECT id, title, notice_type, is_result, purchaser, agency, budget_amount,"
            " province, city, industry, publish_time, deadline, deadline_raw,"
            " deadline_confident, source_url, content_text, source_id"
            " FROM tenders WHERE is_primary IS NOT 0 OR cluster_id IS NULL"
            " ORDER BY publish_time DESC, id DESC"):
        rows.append({
            "id": r["id"],
            "title": r["title"],
            "org": r["purchaser"],
            "agency": r["agency"],
            "amount": r["budget_amount"],
            "prov": r["province"],
            "city": norm_city(r["city"]),
            "cat": r["industry"],
            "pub": (r["publish_time"] or "")[:10] or None,
            "due": (r["deadline"] or "")[:10] or None,
            "dueRaw": r["deadline_raw"],
            "dueConfident": bool(r["deadline_confident"]),
            "type": r["notice_type"],
            "isResult": bool(r["is_result"]),
            "url": r["source_url"],
            "src": r["source_id"],
            "body": norm_body(r["content_text"]),
        })
    total_src = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
    conn.close()

    with_amount = sum(1 for x in rows if x["amount"])
    soon = sum(1 for x in rows if x["due"] and 0 <= (day_diff(x["due"]) or -1) <= 7)
    provs = {x["prov"] for x in rows if x["prov"]}
    return {
        "key": "tender",
        "name": "招标雷达",
        "en": "Tender Radar",
        "tagline": "全国政府采购与公共资源交易平台的法律服务类招标，自动抽取预算金额与投标截止时间。",
        "rows": rows,
        "meta": {
            "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "total": len(rows),
            "sources": total_src,
            "provinces": len(provs),
            "withAmount": with_amount,
            "dueSoon": soon,
        },
        "_provs": sorted({norm_prov(x) for x in provs if norm_prov(x)}),
    }


# ---------------------------------------------------------------- 开庭
def build_hearing(db_path):
    conn = connect(db_path)
    rows = []
    for r in conn.execute(
            "SELECT id, source, court, province, city, case_no, cause, cause_group,"
            " is_commercial, plaintiff, defendant, defendant_type, hearing_at, hearing_date,"
            " time_estimated, courtroom, claim_amount, notice_type, publish_date, url, content"
            " FROM hearings ORDER BY hearing_date IS NULL, hearing_date ASC, id DESC"):
        rows.append({
            "id": r["id"],
            "court": r["court"],
            "caseNo": r["case_no"],
            "cause": r["cause"],
            "group": r["cause_group"],
            "comm": bool(r["is_commercial"]),
            "plaintiff": r["plaintiff"],
            "defendant": r["defendant"],
            "defType": r["defendant_type"],
            "heaAt": r["hearing_at"],
            "heaDate": r["hearing_date"],
            "est": bool(r["time_estimated"]),
            "room": r["courtroom"],
            "amount": r["claim_amount"],
            "ntype": r["notice_type"],
            "pub": r["publish_date"],
            "prov": r["province"],
            "city": norm_city(r["city"]),
            "src": r["source"],
            "url": r["url"],
            "body": norm_body(r["content"]),
            "days": day_diff(r["hearing_date"]),
        })
    conn.close()

    provs = {x["prov"] for x in rows if x["prov"]}
    company = sum(1 for x in rows if x["defType"] in ("company", "mixed"))
    precise = sum(1 for x in rows
                  if x["defType"] in ("company", "mixed") and x["comm"]
                  and x["days"] is not None and 15 <= x["days"] <= 30)
    return {
        "key": "hearing",
        "name": "开庭公告",
        "en": "Hearing Docket",
        "tagline": "法院开庭传票与排期表，识别被告是企业、案由是商事纠纷的案件。",
        "rows": rows,
        "meta": {
            "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "total": len(rows),
            "courts": len({x["court"] for x in rows if x["court"]}),
            "provinces": len(provs),
            "company": company,
            "precise": precise,
            "upcoming": sum(1 for x in rows if x["days"] is not None and 0 <= x["days"] <= 30),
        },
        "_provs": sorted({norm_prov(x) for x in provs if norm_prov(x)}),
    }


# ---------------------------------------------------------------- 悬赏
def build_bounty(db_path):
    conn = connect(db_path)
    rows = []
    for r in conn.execute(
            "SELECT id, source, court, province, city, debtor, case_no, subject_amount,"
            " reward_ratio, reward_cap, reward_fixed, reward_est, reward_type, publish_date,"
            " valid_from, valid_to, contact_phone, url, content"
            " FROM notices ORDER BY publish_date DESC, id DESC"):
        ratio = r["reward_ratio"]
        rows.append({
            "id": r["id"],
            "court": r["court"],
            "debtor": r["debtor"],
            "caseNo": r["case_no"],
            "subject": r["subject_amount"],
            "ratio": round(float(ratio) * 100, 3) if ratio is not None else None,
            "cap": r["reward_cap"],
            "fixed": r["reward_fixed"],
            "est": r["reward_est"],
            "type": r["reward_type"],
            "pub": r["publish_date"],
            "vfrom": r["valid_from"],
            "vto": r["valid_to"],
            "phone": r["contact_phone"],
            "prov": r["province"],
            "city": norm_city(r["city"]),
            "src": r["source"],
            "url": r["url"],
            "body": norm_body(r["content"]),
        })
    conn.close()

    provs = {norm_prov(x["prov"]) for x in rows if x["prov"]}
    provs.discard(None)
    return {
        "key": "bounty",
        "name": "执行悬赏",
        "en": "Bounty Notice",
        "tagline": "法院执行悬赏公告，拆出被执行人与执行标的，换算成实际能拿到手的赏金。",
        "rows": rows,
        "meta": {
            "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "total": len(rows),
            "courts": len({x["court"] for x in rows if x["court"]}),
            "provinces": len(provs),
            "subjectSum": round(sum(x["subject"] or 0 for x in rows), 2),
            "rewardSum": round(sum(x["est"] or 0 for x in rows), 2),
            "maxReward": max([x["est"] or 0 for x in rows], default=0),
        },
        "_provs": sorted({norm_prov(x) for x in provs if norm_prov(x)}),
    }


def write_board(board):
    board.pop("_provs", None)
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, board["key"] + ".js")
    payload = json.dumps(board, ensure_ascii=False, separators=(",", ":"))
    with open(path, "w", encoding="utf-8") as f:
        f.write("window.__BOARD__ = ")
        f.write(payload)
        f.write(";\n")
    size = os.path.getsize(path) / 1024
    print(f"  {board['name']:<6} {board['meta']['total']:>5} 条 -> "
          f"{os.path.basename(path)} ({size:.0f} KB)")
    return board


def pick_sample(hearing, limit=3):
    """首屏右侧要展示的真实记录。

    挑「被告是企业、已定期、离现在最近、带涉案金额」的三条，
    让人一眼看到产品实际产出长什么样，而不是一张插画。
    """
    cand = [
        r for r in hearing["rows"]
        if r["defType"] in ("company", "mixed")
        and r["defendant"] and len(r["defendant"]) <= 28
        and r["heaDate"]
        and r["days"] is not None and r["days"] >= 0
        and r["amount"]
    ]
    # 优先临近的；被告名已过滤到能在一行里读全，不用再截
    cand.sort(key=lambda r: (r["days"], -(r["amount"] or 0)))
    out = []
    for r in cand:
        out.append({
            "court": r["court"],
            "caseNo": r["caseNo"],
            "cause": r["cause"],
            "defendant": r["defendant"],
            "heaDate": r["heaDate"],
            "days": r["days"],
            "amount": r["amount"],
        })
        if len(out) >= limit:
            break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tender-db", default=DEFAULT_TENDER_DB)
    ap.add_argument("--court-db", default=DEFAULT_COURT_DB)
    args = ap.parse_args()

    print("导出看板数据：")
    boards = {
        "tender": build_tender(args.tender_db),
        "hearing": build_hearing(args.court_db),
        "bounty": build_bounty(args.court_db),
    }
    all_provs = set()
    for b in boards.values():
        all_provs.update(b.get("_provs") or [])
    for b in boards.values():
        write_board(b)

    # 首页与全局检索用的汇总。首页所有数字都从这里取，
    # 不在 HTML 里写死，避免数据更新后文案对不上。
    summary = {
        "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total": sum(b["meta"]["total"] for b in boards.values()),
        "orgs": boards["hearing"]["meta"]["courts"] + boards["bounty"]["meta"]["courts"]
                + boards["tender"]["meta"]["sources"],
        "provinces": len(all_provs),
        "boards": {k: b["meta"] for k, b in boards.items()},
        # 首屏右侧展示的真实记录（不是占位数据）
        "sample": {"hearing": pick_sample(boards["hearing"])},
    }
    path = os.path.join(OUT_DIR, "overview.js")
    with open(path, "w", encoding="utf-8") as f:
        f.write("window.__OVERVIEW__ = ")
        f.write(json.dumps(summary, ensure_ascii=False, separators=(",", ":")))
        f.write(";\n")
    print(f"  汇总   {summary['total']} 条 / {summary['orgs']} 家机构 -> overview.js")


if __name__ == "__main__":
    main()
