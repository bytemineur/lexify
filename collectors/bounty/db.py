# -*- coding: utf-8 -*-
"""执行悬赏看板的 SQLite 存储层。零第三方依赖。

只负责 notices 表（悬赏）与 crawl_log 采集台账。开庭（hearings 表）
归 hearing 采集器管，两者各写各的，避免一个模块背两张表的账。
"""
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
