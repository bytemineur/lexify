# -*- coding: utf-8 -*-
"""导出开庭公告看板数据：生成 web/data/hearings.js。"""
import json
import os

import config
import db

BODY_LIMIT = 8000

# (数据库列名, 前端字段名)
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


def _body(v):
    if not v:
        return None
    s = " ".join(str(v).split())
    return s[:BODY_LIMIT] if len(s) > BODY_LIMIT else s


def load_hearing_rows(conn):
    import datetime
    today = datetime.date.today()
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
        if r["hearing_date"]:
            try:
                y, m, dd = (int(x) for x in r["hearing_date"][:10].split("-"))
                d["days"] = (datetime.date(y, m, dd) - today).days
            except (ValueError, TypeError):
                d["days"] = None
        else:
            d["days"] = None
        out.append(d)
    return out


def build_hearing_meta(rows):
    import datetime
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
        "generatedAt": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "today": datetime.date.today().isoformat(),
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
    export_hearings()
