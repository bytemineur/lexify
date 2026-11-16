# -*- coding: utf-8 -*-
"""法院通讯录采集入口：抓 → 解析 → 写 courts / court_coverage。

    python -m collectors.courtbook                 # 全量 31 省
    python -m collectors.courtbook --only 广东,海南  # 只跑指定省
    python -m collectors.courtbook --dry           # 只抓不写库
    python -m collectors.courtbook status          # 看库里现在有什么
"""
import argparse
import os
import sqlite3
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
COLL = os.path.dirname(HERE)
ROOT = os.path.dirname(COLL)
if COLL not in sys.path:
    sys.path.insert(0, COLL)

from _shared import http as H  # noqa: E402
from . import source as S  # noqa: E402

DB_PATH = os.environ.get("LEXIFY_DB") or os.path.join(ROOT, "data", "lexify.db")

UPSERT = """
INSERT INTO courts
    (name, short, level, province, city, address, postcode, phone,
     phone_day, phone_night, source, source_id, source_url, fetched_at)
VALUES
    (:name, :short, :level, :province, :city, :address, :postcode, :phone,
     :phone_day, :phone_night, :source, :source_id, :source_url, :fetched_at)
ON CONFLICT(source, source_id) DO UPDATE SET
    name=excluded.name, short=excluded.short, level=excluded.level,
    province=excluded.province, city=excluded.city, address=excluded.address,
    postcode=excluded.postcode, phone=excluded.phone,
    phone_day=excluded.phone_day, phone_night=excluded.phone_night,
    source_url=excluded.source_url, fetched_at=excluded.fetched_at
"""

COVER_UPSERT = """
INSERT INTO court_coverage
    (source, province, province_code, claimed, fetched, status, note, checked_at)
VALUES (:source, :province, :province_code, :claimed, :fetched, :status, :note, :checked_at)
ON CONFLICT(source, province) DO UPDATE SET
    province_code=excluded.province_code, claimed=excluded.claimed,
    fetched=excluded.fetched, status=excluded.status, note=excluded.note,
    checked_at=excluded.checked_at
"""


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def ensure_tables(con):
    """courts / court_coverage 缺失时补建（等价于 v3 迁移）。"""
    scripts = os.path.join(ROOT, "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from migrations import _v3_courts
    _v3_courts(con)
    con.commit()


def cmd_crawl(args):
    only = set(x.strip() for x in args.only.split(",") if x.strip()) if args.only else None
    targets = [(c, n) for c, n in S.PROVINCES if not only or n in only]
    print("目标 %d 个省%s；库：%s%s" % (
        len(targets), "（%s）" % "、".join(n for _, n in targets) if only else "",
        DB_PATH, "（dry-run 不写库）" if args.dry else ""))

    http = H.Http(interval=args.interval, timeout=60, max_retry=1)
    con = None
    if not args.dry:
        con = connect()
        ensure_tables(con)

    stamp = now()
    grand = 0
    report = []
    t0 = time.time()

    for code, short in targets:
        full = S.PROVINCE_FULL[short]
        n_new = n_seen = 0
        claimed = None
        status, note = "ok", ""
        try:
            for raw, claimed_total in S.iter_province(
                    http, code, short, verbose=True, max_pages=args.max_pages):
                if claimed is None:
                    claimed = claimed_total
                n_seen += 1
                if args.dry:
                    if n_seen <= 3:
                        day, night = S.split_phone(raw["phone"])
                        print("    %s | %s | %s | 昼 %s 夜 %s" % (
                            raw["name"], raw["address"][:26], raw["postcode"],
                            day, night))
                    continue
                row = S.to_row(raw, full, stamp)
                con.execute(UPSERT, row)
                n_new += 1
                if args.verbose:
                    print("    + %s" % row["name"])
        except Exception as e:  # noqa: BLE001
            status, note = "failed", "%s: %s" % (type(e).__name__, str(e)[:120])
            print("  ! %s 失败：%s" % (short, note))

        if n_seen == 0 and status == "ok":
            status, note = "missing", "源站该省无记录（页内搜索亦不可用）"
        elif claimed == 0 and n_seen > 0 and status == "ok":
            # 宁夏就是这种：页面没渲染出「总共：N条」分页块，但照样列出了条目。
            # 不能因为读不到声称数就当成「没有数据」。
            status, note = "partial", "源站未给出该省总数，按实际取到 %d 条计" % n_seen
        elif claimed and n_seen < claimed and status == "ok":
            status, note = "partial", "源站声称 %d 条，实际取到 %d 条" % (claimed, n_seen)

        grand += n_seen
        report.append((short, claimed, n_seen, status))
        print("  → %-6s 取到 %-4d 条（声称 %-5s）%s%s" % (
            short, n_seen, claimed, status, ("：" + note) if note else ""))

        if con:
            con.execute(COVER_UPSERT, {
                "source": S.SOURCE, "province": full, "province_code": str(code),
                "claimed": claimed, "fetched": n_seen, "status": status,
                "note": note, "checked_at": stamp})
            con.commit()

    if con:
        con.close()

    print("\n" + "=" * 60)
    print("合计取到 %d 条，耗时 %.0f 秒，请求 %d 次" % (
        grand, time.time() - t0, http.count))
    bad = [r for r in report if r[3] != "ok"]
    if bad:
        print("\n非 ok 的省份：")
        for short, claimed, got, st in bad:
            print("  %-6s %-8s 声称 %-5s 实取 %-4d" % (short, st, claimed, got))
    return 0


def cmd_status(args):
    con = connect()
    ensure_tables(con)
    tot = con.execute("SELECT COUNT(*) FROM courts").fetchone()[0]
    print("库里共 %d 家法院" % tot)
    if not tot:
        print("（还没抓过，先跑 python -m collectors.courtbook）")
        con.close()
        return 0
    print("\n按层级：")
    for r in con.execute("SELECT level, COUNT(*) n FROM courts GROUP BY level "
                         "ORDER BY n DESC"):
        print("  %-6s %d" % (r["level"] or "(未判定)", r["n"]))
    print("\n按省份（含覆盖状态）：")
    rows = con.execute("""
        SELECT c.province, COUNT(*) n,
               SUM(CASE WHEN c.phone IS NOT NULL AND c.phone<>'' THEN 1 ELSE 0 END) has_phone,
               SUM(CASE WHEN c.address IS NOT NULL AND c.address<>'' THEN 1 ELSE 0 END) has_addr,
               (SELECT status FROM court_coverage v
                 WHERE v.source=c.source AND v.province=c.province) st
        FROM courts c GROUP BY c.province ORDER BY n DESC""").fetchall()
    for r in rows:
        print("  %-10s %4d 家  电话 %4d  地址 %4d  %s" % (
            r["province"], r["n"], r["has_phone"], r["has_addr"], r["st"] or ""))
    print("\n覆盖台账：")
    for r in con.execute("SELECT province, claimed, fetched, status, note "
                         "FROM court_coverage ORDER BY province"):
        print("  %-10s 声称 %-5s 实取 %-4d %-8s %s" % (
            r["province"], r["claimed"], r["fetched"], r["status"],
            (r["note"] or "")[:50]))
    con.close()
    return 0


def main():
    ap = argparse.ArgumentParser(description="法院通讯录采集（110.com 全国法院地址电话）")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("crawl", help="抓取并入库（默认）")
    p.add_argument("--only", default="", help="只跑这些省，逗号分隔，如 广东,广西")
    p.add_argument("--dry", action="store_true", help="只抓不写库")
    p.add_argument("--max-pages", type=int, default=0, help="每省最多翻几页")
    p.add_argument("--interval", type=float, default=1.5, help="请求间隔秒")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_crawl)

    p = sub.add_parser("status", help="看库里已有的通讯录")
    p.set_defaults(func=cmd_status)

    argv = sys.argv[1:]
    # 不带子命令（或直接跟 --dry 之类的选项）时默认走 crawl，
    # 免得 `python -m collectors.courtbook --dry` 还要多敲一个词。
    if not argv or argv[0] not in ("crawl", "status", "-h", "--help"):
        argv = ["crawl"] + argv
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
