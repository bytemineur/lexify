# -*- coding: utf-8 -*-
"""开庭公告看板的 SQLite 存储层。零第三方依赖。

只负责 hearings 表与 crawl_log 台账。悬赏（notices 表）归 bounty 采集器管。
"""
import os
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS hearings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    url             TEXT,
    court           TEXT,
    province        TEXT,
    city            TEXT,
    case_no         TEXT,                   -- 案号
    case_kind       TEXT,                   -- 民初/民终/刑初…
    cause           TEXT,                   -- 案由
    cause_group     TEXT,                   -- 建筑工程/票据/商标知产/金融借贷/公司股权/合同纠纷
    is_commercial   INTEGER DEFAULT 0,      -- 是否商事案由
    plaintiff       TEXT,
    defendant       TEXT,
    defendant_type  TEXT,                   -- company / mixed / person / unknown
    company_count   INTEGER DEFAULT 0,
    person_count    INTEGER DEFAULT 0,
    hearing_at      TEXT,                   -- YYYY-MM-DD HH:MM
    hearing_date    TEXT,                   -- YYYY-MM-DD（建索引用）
    time_estimated  INTEGER DEFAULT 0,      -- 开庭时间是否为推算
    courtroom       TEXT,
    claim_amount    REAL,                   -- 涉案金额（元）
    amount_basis    TEXT,                   -- 金额来源
    notice_type     TEXT,
    publish_date    TEXT,
    content         TEXT,
    content_hash    TEXT,
    parse_quality   REAL,
    raw_json        TEXT,
    fetched_at      TEXT,
    parsed_at       TEXT,
    UNIQUE(source, source_id)
);

CREATE INDEX IF NOT EXISTS idx_h_date    ON hearings(hearing_date);
CREATE INDEX IF NOT EXISTS idx_h_amount  ON hearings(claim_amount);
CREATE INDEX IF NOT EXISTS idx_h_court   ON hearings(court);
CREATE INDEX IF NOT EXISTS idx_h_prov    ON hearings(province);
CREATE INDEX IF NOT EXISTS idx_h_group   ON hearings(cause_group);
CREATE INDEX IF NOT EXISTS idx_h_deftype ON hearings(defendant_type);

CREATE TABLE IF NOT EXISTS crawl_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT,
    keyword     TEXT,
    started_at  TEXT,
    finished_at TEXT,
    total_seen  INTEGER DEFAULT 0,
    total_new   INTEGER DEFAULT 0,
    status      TEXT
);
"""


def connect(path=None):
    import config
    path = path or config.DB_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(conn):
    conn.executescript(SCHEMA)
    conn.commit()


H_FIELDS = [
    "source", "source_id", "url", "court", "province", "city", "case_no",
    "case_kind", "cause", "cause_group", "is_commercial", "plaintiff",
    "defendant", "defendant_type", "company_count", "person_count",
    "hearing_at", "hearing_date", "time_estimated", "courtroom",
    "claim_amount", "amount_basis", "notice_type", "publish_date",
    "content", "content_hash", "parse_quality", "raw_json",
    "fetched_at", "parsed_at",
]

H_UPDATABLE = [f for f in H_FIELDS if f not in ("source", "source_id")]


def upsert(conn, rec):
    """按 (source, source_id) 幂等写入开庭记录。返回 True 表示新增。"""
    cols = ",".join(H_FIELDS)
    ph = ",".join(["?"] * len(H_FIELDS))
    cur = conn.execute(
        f"INSERT OR IGNORE INTO hearings ({cols}) VALUES ({ph})",
        [rec.get(f) for f in H_FIELDS])
    if cur.rowcount:
        return True
    sets = ",".join(f"{f}=?" for f in H_UPDATABLE)
    conn.execute(
        f"UPDATE hearings SET {sets} WHERE source=? AND source_id=?",
        [rec.get(f) for f in H_UPDATABLE] +
        [rec.get("source"), rec.get("source_id")])
    return False


def hearing_ids(conn, source):
    return {r[0] for r in conn.execute(
        "SELECT source_id FROM hearings WHERE source=?", (source,))}


def hearing_exists_content(conn, content_hash, source_id=None):
    if not content_hash:
        return None
    row = conn.execute(
        "SELECT id, source, source_id FROM hearings WHERE content_hash=? "
        "ORDER BY id LIMIT 1", (content_hash,)).fetchone()
    if not row:
        return None
    if source_id is not None and str(row["source_id"]) == str(source_id):
        return None
    return row


def hearing_stats(conn):
    row = conn.execute("""
        SELECT COUNT(*) n,
               SUM(hearing_date      IS NOT NULL) has_date,
               SUM(claim_amount      IS NOT NULL) has_amount,
               SUM(cause             IS NOT NULL) has_cause,
               SUM(defendant_type IN ('company','mixed')) has_company,
               SUM(is_commercial = 1) commercial
        FROM hearings
    """).fetchone()
    return dict(row)


def dedupe_hearings(conn, verbose=True):
    rows = conn.execute(
        "SELECT id, content_hash FROM hearings ORDER BY id").fetchall()
    keep, drop = {}, []
    for r in rows:
        h = r["content_hash"]
        if not h:
            continue
        if h in keep:
            drop.append(r["id"])
        else:
            keep[h] = r["id"]
    if drop:
        conn.executemany("DELETE FROM hearings WHERE id=?", [(i,) for i in drop])
        conn.commit()
    if verbose:
        n = conn.execute("SELECT COUNT(*) FROM hearings").fetchone()[0]
        print(f"开庭去重：删除 {len(drop)} 条重复，剩余 {n} 条")
    return len(drop)


def log_crawl(conn, **kw):
    conn.execute(
        """INSERT INTO crawl_log (source, keyword, started_at, finished_at,
               total_seen, total_new, status) VALUES (?,?,?,?,?,?,?)""",
        [kw.get("source"), kw.get("keyword"), kw.get("started_at"),
         kw.get("finished_at"), kw.get("total_seen", 0),
         kw.get("total_new", 0), kw.get("status")],
    )
    conn.commit()
