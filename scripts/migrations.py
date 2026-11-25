# -*- coding: utf-8 -*-
"""顺序化、只向前迁移（forward-only migrations）。

背景：schema.sql 只有 CREATE TABLE IF NOT EXISTS，能把「缺的表」补上，
但一旦要给既有表加减列 / 改约束，IF NOT EXISTS 就束手无策，只能手改五处。
这里用 SQLite 的 PRAGMA user_version 当版本号，逐条套用到目标版本：

    MIGRATIONS = [
        (3, "加 tenders 索引"),   # 版本号(目标) , 迁移逻辑(conn)
        ...
    ]

规则：
  - 只向前：当前 user_version < 目标版本号时才执行，绝不回退。
  - 一条一个独立的 ALTER / CREATE / INSERT，失败即抛，别留半截。
  - 幂等：迁移内部用 IF NOT EXISTS / 判断列是否存在，重复套用也不会炸。
  - 基底 schema（schema.sql）先被执行，它负责把空库建到含全部表，
    迁移只处理「已存在库往前走」的那部分结构变化。

加新迁移时：SCHEMA_VERSION 设成最新版本号，并把新结构也同步进 schema.sql
（新库直接建到最新，老库靠迁移补到最新——两条路都通）。
"""
from __future__ import annotations


def _column_exists(conn, table: str, column: str) -> bool:
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    return column in cols


# 迁移列表：(目标版本号, 迁移函数)。版本号必须严格递增。
# 注意：这里是函数对象，不能写成字符串名字——列表在模块加载时就求值，
# 写成字符串要到 migrate() 里调用时才炸（TypeError: 'str' object is not callable）。
MIGRATIONS: list[tuple[int, callable]] = []


def _v3_courts(conn):
    """v3：新增法院通讯录 courts 与覆盖台账 court_coverage。

    两张表本身在 schema.sql 里已有 CREATE TABLE IF NOT EXISTS，这里再写一遍
    是为了让老库单独跑迁移也能补齐（不依赖 schema.sql 是否先执行过）。
    """
    conn.executescript("""
CREATE TABLE IF NOT EXISTS courts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    short           TEXT,
    level           TEXT,
    province        TEXT,
    city            TEXT,
    address         TEXT,
    postcode        TEXT,
    phone           TEXT,
    phone_day       TEXT,
    phone_night     TEXT,
    source          TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    source_url      TEXT,
    fetched_at      TEXT,
    UNIQUE(source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_courts_name ON courts(name);
CREATE INDEX IF NOT EXISTS idx_courts_prov ON courts(province);
CREATE INDEX IF NOT EXISTS idx_courts_city ON courts(city);
CREATE INDEX IF NOT EXISTS idx_courts_lvl  ON courts(level);

CREATE TABLE IF NOT EXISTS court_coverage (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,
    province        TEXT NOT NULL,
    province_code   TEXT,
    claimed         INTEGER,
    fetched         INTEGER DEFAULT 0,
    status          TEXT,
    note            TEXT,
    checked_at      TEXT,
    UNIQUE(source, province)
);
""")


# 迁移函数定义完之后再登记，避免前向引用
MIGRATIONS.append((3, _v3_courts))


def _v4_analytics(conn):
    """v4：新增访问统计表 page_views（后台「访问分析」页用）。

    纯新增表，不动既有表，所以老库补这一张就够了；新库在 schema.sql 里直接建。
    """
    conn.executescript("""
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
""")


MIGRATIONS.append((4, _v4_analytics))


def _v5_falseclaim(conn):
    """v5：新增证券虚假陈述线索表 false_claims（第四块看板）。

    数据来自巨潮资讯的证监会处罚/立案公告，纯新增表，老库补一张就行。
    """
    conn.executescript("""
CREATE TABLE IF NOT EXISTS false_claims (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL DEFAULT 'cninfo',
    source_id       TEXT,
    sec_code        TEXT,
    sec_name        TEXT,
    board           TEXT,
    stage           TEXT,
    scope           TEXT DEFAULT 'company',
    org             TEXT,
    types           TEXT,
    title           TEXT,
    ann_date        TEXT,
    first_date      TEXT,
    event_no        INTEGER DEFAULT 1,
    url             TEXT,
    fetched_at      TEXT,
    parsed_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_fc_code  ON false_claims(sec_code);
CREATE INDEX IF NOT EXISTS idx_fc_date  ON false_claims(ann_date);
CREATE INDEX IF NOT EXISTS idx_fc_stage ON false_claims(stage);
CREATE UNIQUE INDEX IF NOT EXISTS idx_fc_src ON false_claims(source, source_id);
""")


MIGRATIONS.append((5, _v5_falseclaim))


def _v6_labor(conn):
    """v6：新增劳动争议线索表 labor_claims（第五块看板）。

    数据来自 IPO 招股说明书里披露的社保/公积金欠缴段落。
    """
    conn.executescript("""
CREATE TABLE IF NOT EXISTS labor_claims (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL DEFAULT 'cninfo',
    source_id       TEXT,
    sec_code        TEXT,
    sec_name        TEXT,
    board           TEXT,
    doc_type        TEXT,
    title           TEXT,
    ann_date        TEXT,
    url             TEXT,
    page_no         INTEGER DEFAULT 0,
    unpaid_kind     TEXT,
    snippet         TEXT,
    snippets        TEXT,
    promise         INTEGER DEFAULT 0,
    makegood_amount REAL DEFAULT 0,
    headcount       INTEGER DEFAULT 0,
    paid_count      INTEGER DEFAULT 0,
    paid_ratio      REAL DEFAULT 0,
    severity        TEXT,
    pages           INTEGER DEFAULT 0,
    event_no        INTEGER DEFAULT 1,
    fetched_at      TEXT,
    parsed_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_lc_code ON labor_claims(sec_code);
CREATE INDEX IF NOT EXISTS idx_lc_date ON labor_claims(ann_date);
CREATE INDEX IF NOT EXISTS idx_lc_sev ON labor_claims(severity);
CREATE UNIQUE INDEX IF NOT EXISTS idx_lc_src ON labor_claims(source, source_id);
""")


MIGRATIONS.append((6, _v6_labor))


def migrate(conn, target_version: int) -> list[str]:
    """把 conn 套用到 target_version。返回已应用的迁移说明（用于日志）。"""
    applied = []
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for ver, fn in MIGRATIONS:
        if ver <= current:
            continue
        if ver > target_version:
            break
        fn(conn)
        conn.execute(f"PRAGMA user_version = {ver:d}")
        applied.append(f"v{ver}")
        current = ver
    return applied
