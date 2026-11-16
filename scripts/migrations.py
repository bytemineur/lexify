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
