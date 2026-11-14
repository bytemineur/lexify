# -*- coding: utf-8 -*-
"""导出执行悬赏看板数据：生成 web/data/bounties.js（script 标签可直接加载，file:// 可用）。"""
import json
import os

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

# 正文全文上限。源站详情页有 WAF 拦截（高频访问直接 403），看板必须自带全文。
BODY_LIMIT = 8000


def _body(v):
    if not v:
        return None
    s = " ".join(str(v).split())
    return s[:BODY_LIMIT] if len(s) > BODY_LIMIT else s


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
    import datetime
    provs, srcs = {}, {}
    for r in rows:
        p = r.get("prov") or "未识别"
        provs[p] = provs.get(p, 0) + 1
        s = r.get("src") or "unknown"
        srcs[s] = srcs.get(s, 0) + 1
    ests = [r["est"] for r in rows if r.get("est")]
    subs = [r["subject"] for r in rows if r.get("subject")]
    return {
        "generatedAt": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
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

    json_path = os.path.join(os.path.dirname(config.EXPORT_JS), "bounties.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "rows": rows}, f, ensure_ascii=False, indent=1)

    size = os.path.getsize(config.EXPORT_JS) / 1024
    print(f"已导出 {len(rows)} 条 -> {config.EXPORT_JS} ({size:.0f} KB)")
    return len(rows)


if __name__ == "__main__":
    export()
