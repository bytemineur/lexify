# -*- coding: utf-8 -*-
"""SQLite 存储层。零第三方依赖。"""
import json
import os
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS notices (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,          -- 数据源标识
    source_id       TEXT NOT NULL,          -- 源站唯一 ID
    url             TEXT,                   -- 详情页链接
    court           TEXT,                   -- 发布法院
    province        TEXT,                   -- 省级行政区
    city            TEXT,                   -- 地市
    case_no         TEXT,                   -- 执行案号
    debtor          TEXT,                   -- 被执行人
    debtor_count    INTEGER DEFAULT 1,
    subject_amount  REAL,                   -- 执行标的（元）
    reward_ratio    REAL,                   -- 奖励比例（0.05 = 5%）
    reward_cap      REAL,                   -- 封顶金额（元）
    reward_fixed    REAL,                   -- 固定赏金（元）
    reward_est      REAL,                   -- 预估最高奖励（元）
    reward_type     TEXT,                   -- property / whereabouts / both
    publish_date    TEXT,                   -- 公告发布日期 YYYY-MM-DD
    valid_from      TEXT,
    valid_to        TEXT,
    contact_phone   TEXT,
    content         TEXT,                   -- 公告全文
    content_hash    TEXT,
    parse_quality   REAL,                   -- 解析置信度 0~1
    raw_json        TEXT,
    fetched_at      TEXT,
    parsed_at       TEXT,
    UNIQUE(source, source_id)
);

CREATE INDEX IF NOT EXISTS idx_pub_date   ON notices(publish_date);
CREATE INDEX IF NOT EXISTS idx_amount     ON notices(subject_amount);
CREATE INDEX IF NOT EXISTS idx_ratio      ON notices(reward_ratio);
CREATE INDEX IF NOT EXISTS idx_est        ON notices(reward_est);
CREATE INDEX IF NOT EXISTS idx_province   ON notices(province);
CREATE INDEX IF NOT EXISTS idx_court      ON notices(court);

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

-- ============ 开庭公告看板 ============
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
"""


def connect(path=None):
    from config import DB_PATH
    path = path or DB_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(conn):
    conn.executescript(SCHEMA)
    conn.commit()


FIELDS = [
    "source", "source_id", "url", "court", "province", "city", "case_no",
    "debtor", "debtor_count", "subject_amount", "reward_ratio", "reward_cap",
    "reward_fixed", "reward_est", "reward_type", "publish_date", "valid_from",
    "valid_to", "contact_phone", "content", "content_hash", "parse_quality",
    "raw_json", "fetched_at", "parsed_at",
]


def upsert(conn, rec):
    """按 (source, source_id) 幂等写入。返回 True 表示新增。"""
    cols = ",".join(FIELDS)
    ph = ",".join(["?"] * len(FIELDS))
    vals = [rec.get(f) for f in FIELDS]
    cur = conn.execute(
        f"INSERT OR IGNORE INTO notices ({cols}) VALUES ({ph})", vals
    )
    if cur.rowcount:
        return True
    # 已存在：仅刷新易变字段（解析结果/正文可能被优化过）
    conn.execute(
        """UPDATE notices SET court=?, province=?, city=?, case_no=?, debtor=?,
               debtor_count=?, subject_amount=?, reward_ratio=?, reward_cap=?,
               reward_fixed=?, reward_est=?, reward_type=?, publish_date=?,
               valid_from=?, valid_to=?, contact_phone=?, content=?,
               content_hash=?, parse_quality=?, raw_json=?, parsed_at=?
           WHERE source=? AND source_id=?""",
        [rec.get("court"), rec.get("province"), rec.get("city"),
         rec.get("case_no"), rec.get("debtor"), rec.get("debtor_count"),
         rec.get("subject_amount"), rec.get("reward_ratio"),
         rec.get("reward_cap"), rec.get("reward_fixed"),
         rec.get("reward_est"), rec.get("reward_type"),
         rec.get("publish_date"), rec.get("valid_from"), rec.get("valid_to"),
         rec.get("contact_phone"), rec.get("content"),
         rec.get("content_hash"), rec.get("parse_quality"),
         rec.get("raw_json"), rec.get("parsed_at"),
         rec.get("source"), rec.get("source_id")],
    )
    return False


def existing_ids(conn, source):
    return {r[0] for r in conn.execute(
        "SELECT source_id FROM notices WHERE source=?", (source,))}


def find_same_content(conn, content_hash, source_id=None):
    """按正文哈希找已存在的记录（跨源去重）。"""
    if not content_hash:
        return None
    row = conn.execute(
        "SELECT id, source, source_id FROM notices WHERE content_hash=? "
        "ORDER BY id LIMIT 1", (content_hash,)).fetchone()
    if not row:
        return None
    if source_id is not None and str(row["source_id"]) == str(source_id):
        return None          # 就是自己
    return row


def dedupe(conn, verbose=True):
    """清理正文完全相同的重复记录，保留 id 最小的一条。返回删除条数。"""
    rows = conn.execute(
        "SELECT id, content_hash FROM notices ORDER BY id").fetchall()
    keep = {}
    drop = []
    for r in rows:
        h = r["content_hash"]
        if not h:
            continue
        if h in keep:
            drop.append(r["id"])
        else:
            keep[h] = r["id"]
    if drop:
        conn.executemany("DELETE FROM notices WHERE id=?", [(i,) for i in drop])
        conn.commit()
    if verbose:
        print(f"去重完成：删除 {len(drop)} 条重复，剩余 "
              f"{conn.execute('SELECT COUNT(*) FROM notices').fetchone()[0]} 条")
    return len(drop)


def stats(conn):
    row = conn.execute("""
        SELECT COUNT(*) n,
               SUM(subject_amount IS NOT NULL) has_amount,
               SUM(reward_ratio   IS NOT NULL) has_ratio,
               SUM(reward_est     IS NOT NULL) has_est,
               SUM(reward_cap     IS NOT NULL) has_cap
        FROM notices
    """).fetchone()
    return dict(row)


def log_crawl(conn, **kw):
    conn.execute(
        """INSERT INTO crawl_log (source, keyword, started_at, finished_at,
               total_seen, total_new, status) VALUES (?,?,?,?,?,?,?)""",
        [kw.get("source"), kw.get("keyword"), kw.get("started_at"),
         kw.get("finished_at"), kw.get("total_seen", 0),
         kw.get("total_new", 0), kw.get("status")],
    )
    conn.commit()


# ==================== 开庭公告 ====================
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


def upsert_hearing(conn, rec):
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
