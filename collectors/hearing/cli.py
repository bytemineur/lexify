# -*- coding: utf-8 -*-
"""开庭公告看板采集器 - 命令行入口。

    python cli.py crawl           全国采集（公告网开庭传票 + 法院排期表）
    python cli.py sz sites        深圳专项：法院官网送达公告栏目
    python cli.py sz rmfygg       深圳专项：公告网按法院检索
    python cli.py sz schedule     深圳专项：盐田排期表
    python cli.py prd crawl       珠三角专项：公告网按法院检索
    python cli.py update          采集 + 去重 + 导出

写入统一库的 hearings 表（LEADIFY_DB 指向 data/lexify.db）。
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_COLLECTORS = os.path.dirname(HERE)
for p in (_COLLECTORS, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import config
import db
import export as exporter


def cmd_init(_):
    conn = db.connect()
    db.init_db(conn)
    conn.close()
    print(f"数据库已初始化: {config.DB_PATH}")


def _sources(arg):
    if not arg or arg == "all":
        return None
    return [s.strip() for s in arg.split(",") if s.strip()]


def cmd_crawl(args):
    import pipeline
    pipeline.crawl_hearings(keywords=[args.kw] if args.kw else None,
                            max_pages=args.pages, limit=args.limit,
                            sources=_sources(args.source))


def cmd_update(args):
    import pipeline
    pipeline.crawl_hearings(keywords=[args.kw] if args.kw else None,
                            max_pages=args.pages, limit=args.limit,
                            sources=_sources(args.source))
    conn = db.connect()
    db.dedupe_hearings(conn)
    conn.close()
    exporter.export_hearings()


def cmd_export(_):
    exporter.export_hearings()


def cmd_reparse(_):
    import pipeline
    pipeline.reparse_hearings()
    exporter.export_hearings()


def cmd_stats(_):
    conn = db.connect()
    db.init_db(conn)
    s = db.hearing_stats(conn)
    print(f"开庭记录    : {s['n']}")
    print(f"有开庭日期  : {s['has_date']}")
    print(f"有案由      : {s['has_cause']}")
    print(f"有涉案金额  : {s['has_amount']}")
    print(f"企业被告    : {s['has_company']}")
    print(f"商事案由    : {s['commercial']}")

    print("\n-- 按数据源 --")
    for r in conn.execute("SELECT source, COUNT(*) n FROM hearings "
                          "GROUP BY source ORDER BY n DESC"):
        print(f"  {r['source']:<16} {r['n']}")

    print("\n-- 被告类型 --")
    for r in conn.execute("SELECT COALESCE(defendant_type,'unknown') t, "
                          "COUNT(*) n FROM hearings GROUP BY t ORDER BY n DESC"):
        print(f"  {r['t']:<10} {r['n']}")

    print("\n-- 案由分组 --")
    for r in conn.execute("SELECT COALESCE(cause_group,'未归类') g, COUNT(*) n "
                          "FROM hearings GROUP BY g ORDER BY n DESC"):
        print(f"  {r['g']:<10} {r['n']}")

    print("\n-- 未来 30 天内开庭（前 10 场）--")
    for r in conn.execute("SELECT hearing_at, court, cause, defendant "
                          "FROM hearings WHERE hearing_date >= date('now') "
                          "AND hearing_date <= date('now','+30 day') "
                          "ORDER BY hearing_at LIMIT 10"):
        print(f"  {r['hearing_at']}  {(r['court'] or '')[:16]:<16} "
              f"{(r['cause'] or '')[:12]:<12} {(r['defendant'] or '')[:20]}")
    conn.close()


# ---------------- 深圳专项（开庭） ----------------
def cmd_sz_sites(args):
    import sz
    sz.crawl_hearing(max_pages=args.pages, limit=args.limit)


def cmd_sz_rmfygg(args):
    import sz
    sz.crawl_rmfygg_hearing(max_pages=args.pages)


def cmd_sz_schedule(args):
    import sz
    sz.crawl_schedule(max_pages=args.pages)


def cmd_sz_update(args):
    import sz
    sz.crawl_hearing(max_pages=args.pages)
    sz.crawl_schedule(max_pages=args.pages)
    if not args.skip_rmfygg:
        sz.crawl_rmfygg_hearing(max_pages=args.pages)
    exporter.export_hearings()


def cmd_sz_stats(_):
    import sz
    sz.stats()


def cmd_sz_fixcourt(_):
    import sz
    sz.reattribute()


# ---------------- 珠三角专项（开庭） ----------------
def cmd_prd_crawl(args):
    import prd
    prd.crawl_hearing(max_pages=args.pages)


def cmd_prd_update(args):
    import prd
    import export as _exporter
    prd.crawl_hearing(max_pages=args.pages)
    _exporter.export_hearings()


def cmd_prd_stats(_):
    import prd
    prd.stats()


def main():
    ap = argparse.ArgumentParser(description="开庭公告采集器")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(func=cmd_init)

    def _common(p):
        p.add_argument("--kw", help="只用单个案由关键词")
        p.add_argument("--pages", type=int, help="每个关键词/来源最多翻页数")
        p.add_argument("--limit", type=int, help="最多入池条数")
        p.add_argument("--source", help="all(默认) / rmfygg_kt / court_schedule")

    p = sub.add_parser("crawl", help="采集开庭公告")
    _common(p)
    p.set_defaults(func=cmd_crawl)

    p = sub.add_parser("update", help="采集 + 去重 + 导出")
    _common(p)
    p.set_defaults(func=cmd_update)

    sub.add_parser("export", help="只导出看板数据").set_defaults(func=cmd_export)
    sub.add_parser("reparse", help="用最新规则重解").set_defaults(func=cmd_reparse)
    sub.add_parser("stats", help="开庭库统计").set_defaults(func=cmd_stats)

    # ---- 深圳专项 ----
    sp = sub.add_parser("sz", help="深圳专项：市中院与各区法院的开庭公告")
    ssub = sp.add_subparsers(dest="szcmd", required=True)

    def _szcommon(p):
        p.add_argument("--pages", type=int, help="每个栏目/法院最多翻页数")
        p.add_argument("--limit", type=int, help="最多入库条数")

    p = ssub.add_parser("sites", help="采法院官网开庭/送达公告栏目")
    _szcommon(p)
    p.set_defaults(func=cmd_sz_sites)

    p = ssub.add_parser("rmfygg", help="采公告网按法院检索（慢速）")
    _szcommon(p)
    p.set_defaults(func=cmd_sz_rmfygg)

    p = ssub.add_parser("schedule", help="采法院官网排期表")
    _szcommon(p)
    p.set_defaults(func=cmd_sz_schedule)

    p = ssub.add_parser("update", help="官网 + 排期表 + 公告网全采再导出")
    _szcommon(p)
    p.add_argument("--skip-rmfygg", action="store_true", help="跳过公告网（WAF 拦截时用）")
    p.set_defaults(func=cmd_sz_update)

    ssub.add_parser("stats", help="深圳数据覆盖情况").set_defaults(func=cmd_sz_stats)
    ssub.add_parser("fixcourt", help="按正文校准深圳记录的发布法院").set_defaults(func=cmd_sz_fixcourt)

    # ---- 珠三角专项 ----
    pp = sub.add_parser("prd", help="珠三角专项：东莞、惠州、广州各基层法院")
    psub = pp.add_subparsers(dest="prdcmd", required=True)

    def _prdcommon(p):
        p.add_argument("--pages", type=int, help="每个法院最多翻页数")

    p = psub.add_parser("crawl", help="采公告网（按法院检索，慢速）")
    _prdcommon(p)
    p.set_defaults(func=cmd_prd_crawl)

    p = psub.add_parser("update", help="采集 + 导出")
    _prdcommon(p)
    p.set_defaults(func=cmd_prd_update)

    psub.add_parser("stats", help="珠三角数据覆盖情况").set_defaults(func=cmd_prd_stats)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
