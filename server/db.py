# -*- coding: utf-8 -*-
"""数据库探测与后台只读统计。

只读查询 + 权限探测，不写库、不依赖 HTTP，供 main 启动检查和 /admin 展示复用。
"""
import os
import sqlite3

from .config import DB


def db_writable():
    """库到底能不能写 —— 提前探，别等采集跑一半才抛 sqlite 的原文。"""
    d = os.path.dirname(os.path.abspath(DB))
    if not os.path.isdir(d) or not os.access(d, os.W_OK):
        return False, "目录不可写：%s" % d
    if not os.path.exists(DB):
        return True, ""                        # 库还没建，建库时再看
    if not os.access(DB, os.W_OK):
        return False, "库文件不可写：%s" % DB
    try:
        conn = sqlite3.connect(DB, timeout=2)
        try:
            conn.execute("BEGIN IMMEDIATE")    # 真拿一次写锁，readonly 会在这里炸
            conn.rollback()
        finally:
            conn.close()
        return True, ""
    except sqlite3.Error as e:
        return False, "%s（%s）" % (e, DB)


def db_tables_ok():
    """核心表在不在。缺表通常意味着 schema 从没建成（常见于库只读）。"""
    if not os.path.exists(DB):
        return False
    try:
        conn = sqlite3.connect(DB)
        have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
    except sqlite3.Error:
        return False
    return {"tenders", "hearings", "notices"}.issubset(have)


def db_is_empty():
    """三块看板合起来一条都没有 → 认为是全新的库。"""
    if not os.path.exists(DB):
        return True
    try:
        conn = sqlite3.connect(DB)
        n = sum(conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
                for t in ("tenders", "hearings", "notices"))
        conn.close()
        return n == 0
    except sqlite3.Error:
        return True


def db_stats():
    """后台首页的全部数据。任何一张表缺失都不能让整个 /admin 500。"""
    if not os.path.exists(DB):
        return None, [], []
    try:
        conn = sqlite3.connect(DB)
    except sqlite3.Error:
        return None, [], []
    conn.row_factory = sqlite3.Row
    counts = {}
    for t in ("tenders", "sources", "hearings", "notices"):
        try:
            counts[t] = conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
        except sqlite3.Error:
            counts[t] = -1
    try:
        runs = [dict(r) for r in conn.execute(
            "SELECT started_at,kind,mode,origin,total_seen,inserted,updated,skipped,status "
            "FROM ingest_runs ORDER BY id DESC LIMIT 8")]
    except sqlite3.Error:
        runs = []                              # 表还没建出来（库只读时就是这种）
    try:
        fails = [dict(r) for r in conn.execute(
            "SELECT started_at,board,source,stage,reason,detail,run_id FROM crawl_failures "
            "WHERE resolved = 0 ORDER BY id DESC LIMIT 25")]
    except sqlite3.Error:
        fails = []
    conn.close()
    return counts, runs, fails
