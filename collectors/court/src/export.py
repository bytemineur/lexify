# -*- coding: utf-8 -*-
"""导出看板数据：生成 web/data/bounties.js（script 标签可直接加载，file:// 可用）。"""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import config
import db

# (数据库列名, 前端字段名)
COLUMNS = [
    ("id", "id"),
    ("source", "src"),
    ("court", "court"),
    ("province", "prov"),
    ("city", "city"),
    ("debtor", "debtor"),
    ("case_no", "caseNo"),
    ("subject_amount", "subject"),
    ("reward_ratio", "ratio"),
    ("reward_cap", "cap"),
    ("reward_fixed", "fixed"),
    ("reward_est", "est"),
    ("reward_type", "type"),
    ("publish_date", "pub"),
    ("valid_from", "vfrom"),
    ("valid_to", "vto"),
    ("contact_phone", "phone"),
    ("parse_quality", "q"),
    ("url", "url"),
    ("content", "body"),
]

# 正文全文上限。源站详情页有 WAF 拦截（高频访问直接 403），看板必须自带全文，
# 否则原文链接一打不开，整条数据就没有可读内容了。
BODY_LIMIT = 8000


def _body(v):
    """正文归一化：压掉多余空白，超长截断。"""
    if not v:
        return None
    s = " ".join(str(v).split())
    return s[:BODY_LIMIT] if len(s) > BODY_LIMIT else s


# 数据源可读名（看板筛选器展示用）
SOURCE_LABELS = {
    "rmfygg": "人民法院公告网",
    "court": "法院官网",
}


def load_rows(conn):
    sql = "SELECT * FROM notices ORDER BY publish_date DESC, id DESC"
    out = []
    for r in conn.execute(sql):
        d = {}
        for col, k in COLUMNS:
            v = r[col]
            if k == "ratio" and v is not None:
                v = round(float(v) * 100, 3)      # 转成百分数便于前端展示
            if k == "body":
                v = _body(v)
            d[k] = v
        out.append(d)
    return out


def build_meta(rows):
    provs = {}
    srcs = {}
    for r in rows:
        p = r.get("prov") or "未识别"
        provs[p] = provs.get(p, 0) + 1
        s = r.get("src") or "unknown"
        srcs[s] = srcs.get(s, 0) + 1
    ests = [r["est"] for r in rows if r.get("est")]
    subs = [r["subject"] for r in rows if r.get("subject")]
    return {
        "generatedAt": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M"),
        "count": len(rows),
        "provinces": dict(sorted(provs.items(), key=lambda x: -x[1])),
        "sources": [{"key": k, "label": SOURCE_LABELS.get(k, k), "n": v}
                    for k, v in sorted(srcs.items(), key=lambda x: -x[1])],
        "sumEst": round(sum(ests), 2) if ests else 0,
        "sumSubject": round(sum(subs), 2) if subs else 0,
        "maxEst": round(max(ests), 2) if ests else 0,
        "medianEst": round(sorted(ests)[len(ests) // 2], 2) if ests else 0,
    }


def export(build_meta_too=True):
    conn = db.connect()
    db.init_db(conn)
    rows = load_rows(conn)
    meta = build_meta(rows) if build_meta_too else {}
    conn.close()

    os.makedirs(os.path.dirname(config.EXPORT_JS), exist_ok=True)
    payload = json.dumps({"meta": meta, "rows": rows}, ensure_ascii=False,
                         separators=(",", ":"))
    with open(config.EXPORT_JS, "w", encoding="utf-8") as f:
        f.write("window.__BOUNTY_DATA__ = ")
        f.write(payload)
        f.write(";\n")

    # 同时导一份纯 JSON，便于其他系统对接
    json_path = os.path.join(os.path.dirname(config.EXPORT_JS), "bounties.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "rows": rows}, f, ensure_ascii=False, indent=1)

    size = os.path.getsize(config.EXPORT_JS) / 1024
    print(f"已导出 {len(rows)} 条 -> {config.EXPORT_JS} ({size:.0f} KB)")
    return len(rows)


# ============================================================
# 开庭公告看板
# ============================================================
HEARING_COLUMNS = [
    ("id", "id"),
    ("source", "src"),
    ("court", "court"),
    ("province", "prov"),
    ("city", "city"),
    ("case_no", "caseNo"),
    ("cause", "cause"),
    ("cause_group", "group"),
    ("is_commercial", "comm"),
    ("plaintiff", "plaintiff"),
    ("defendant", "defendant"),
    ("defendant_type", "defType"),
    ("hearing_at", "heaAt"),
    ("hearing_date", "heaDate"),
    ("time_estimated", "est"),
    ("courtroom", "room"),
    ("claim_amount", "amount"),
    ("amount_basis", "amtBasis"),
    ("notice_type", "ntype"),
    ("publish_date", "pub"),
    ("parse_quality", "q"),
    ("url", "url"),
    ("content", "body"),
]

HEARING_SOURCE_LABELS = {
    "rmfygg_kt": "公告网·开庭传票",
    "court_schedule": "法院官网排期表",
}


def load_hearing_rows(conn):
    from datetime import date
    today = date.today()
    out = []
    for r in conn.execute(
            "SELECT * FROM hearings "
            "ORDER BY hearing_date IS NULL, hearing_date ASC, id DESC"):
        d = {}
        for col, k in HEARING_COLUMNS:
            v = r[col]
            if k == "body":
                v = _body(v)
            d[k] = v
        # 距开庭天数：负数表示已过期，前端会重算以保持"今天"准确
        if r["hearing_date"]:
            try:
                y, m, dd = (int(x) for x in r["hearing_date"][:10].split("-"))
                d["days"] = (date(y, m, dd) - today).days
            except (ValueError, TypeError):
                d["days"] = None
        else:
            d["days"] = None
        out.append(d)
    return out


def build_hearing_meta(rows):
    provs, srcs, groups = {}, {}, {}
    for r in rows:
        p = r.get("prov") or "未识别"
        provs[p] = provs.get(p, 0) + 1
        s = r.get("src") or "unknown"
        srcs[s] = srcs.get(s, 0) + 1
        g = r.get("group") or "未识别"
        groups[g] = groups.get(g, 0) + 1

    amounts = [r["amount"] for r in rows if r.get("amount")]
    days = [r["days"] for r in rows if r.get("days") is not None and r["days"] >= 0]
    urgent = [d for d in days if config.HEARING_URGENT_DAYS[0] <= d <= config.HEARING_URGENT_DAYS[1]]

    return {
        "generatedAt": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M"),
        "today": __import__("datetime").date.today().isoformat(),
        "count": len(rows),
        "provinces": dict(sorted(provs.items(), key=lambda x: -x[1])),
        "sources": [{"key": k, "label": HEARING_SOURCE_LABELS.get(k, k), "n": v}
                    for k, v in sorted(srcs.items(), key=lambda x: -x[1])],
        "groups": dict(sorted(groups.items(), key=lambda x: -x[1])),
        "companies": sum(1 for r in rows if r.get("defType") in ("company", "mixed")),
        "commercial": sum(1 for r in rows if r.get("comm")),
        "urgent": len(urgent),
        "urgentRange": list(config.HEARING_URGENT_DAYS),
        "sumAmount": round(sum(amounts), 2) if amounts else 0,
        "maxAmount": round(max(amounts), 2) if amounts else 0,
        "medianAmount": round(sorted(amounts)[len(amounts) // 2], 2) if amounts else 0,
    }


def export_hearings():
    conn = db.connect()
    db.init_db(conn)
    rows = load_hearing_rows(conn)
    meta = build_hearing_meta(rows)
    conn.close()

    os.makedirs(os.path.dirname(config.HEARING_EXPORT_JS), exist_ok=True)
    payload = json.dumps({"meta": meta, "rows": rows}, ensure_ascii=False,
                         separators=(",", ":"))
    with open(config.HEARING_EXPORT_JS, "w", encoding="utf-8") as f:
        f.write("window.__HEARING_DATA__ = ")
        f.write(payload)
        f.write(";\n")

    json_path = os.path.join(os.path.dirname(config.HEARING_EXPORT_JS),
                             "hearings.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "rows": rows}, f, ensure_ascii=False, indent=1)

    size = os.path.getsize(config.HEARING_EXPORT_JS) / 1024
    print(f"已导出开庭公告 {len(rows)} 条 -> {config.HEARING_EXPORT_JS} "
          f"({size:.0f} KB)")
    return len(rows)


if __name__ == "__main__":
    export()
