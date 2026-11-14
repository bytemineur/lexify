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
MIGRATIONS: list[tuple[int, callable]] = [
    # 示例占位：当前 SCHEMA_VERSION=2，从 2 迁到 3 时在这里加一条。
    # 目前没有需要回填的结构，保留这个框架，让未来第一次结构变更「有地方写」。
]


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
