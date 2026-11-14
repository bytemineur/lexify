# -*- coding: utf-8 -*-
"""执行悬赏看板采集器 - 命令行入口。

    python cli.py crawl           全国采集（公告网关键词 + 法院官网栏目）
    python cli.py sz sites        深圳专项：法院官网悬赏栏目
    python cli.py sz rmfygg       深圳专项：公告网按法院检索
    python cli.py prd crawl       珠三角专项：公告网按法院检索
    python cli.py update          采集 + 重新解析 + 导出

写入统一库的 notices 表（LEADIFY_DB 指向 data/lexify.db）。
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
    kws = [args.kw] if args.kw else None
    pipeline.crawl(keywords=kws, limit=args.limit, sources=_sources(args.source))


def cmd_reparse(_):
    import pipeline
    pipeline.reparse_all()
    exporter.export()


def cmd_export(_):
    exporter.export()


def cmd_dedupe(_):
    conn = db.connect()
    db.init_db(conn)
    db.dedupe(conn)
    conn.close()
    exporter.export()


def cmd_stats(_):
    conn = db.connect()
    db.init_db(conn)
    s = db.stats(conn)
    print(f"总记录      : {s['n']}")
    print(f"有执行标的  : {s['has_amount']}")
    print(f"有奖励比例  : {s['has_ratio']}")
    print(f"有封顶金额  : {s['has_cap']}")
    print(f"可估算奖励  : {s['has_est']}")
    print("\n-- 按数据源 --")
    for r in conn.execute("SELECT source, COUNT(*) n FROM notices "
                          "GROUP BY source ORDER BY n DESC"):
        print(f"  {r['source']:<10} {r['n']}")
    print("\n-- 按法院（前 12）--")
    for r in conn.execute("SELECT COALESCE(court,'未识别') c, COUNT(*) n "
                          "FROM notices GROUP BY c ORDER BY n DESC LIMIT 12"):
        print(f"  {r['n']:>4}  {r['c']}")
    print("\n-- 按省份 --")
    for r in conn.execute("SELECT COALESCE(province,'未识别') p, COUNT(*) n "
                          "FROM notices GROUP BY p ORDER BY n DESC LIMIT 15"):
        print(f"  {r['p']:<12} {r['n']}")
    print("\n-- 奖励比例分布 --")
    for r in conn.execute("""SELECT CASE
            WHEN reward_ratio IS NULL THEN '未标注'
            WHEN reward_ratio < 0.05 THEN '<5%'
            WHEN reward_ratio < 0.10 THEN '5%-10%'
            WHEN reward_ratio < 0.20 THEN '10%-20%'
            ELSE '>=20%' END k, COUNT(*) n
            FROM notices GROUP BY k ORDER BY n DESC"""):
        print(f"  {r['k']:<10} {r['n']}")
    conn.close()


def cmd_update(args):
    import pipeline
    pipeline.crawl(keywords=[args.kw] if args.kw else None, limit=args.limit,
                   sources=_sources(args.source))
    pipeline.reparse_all()
    exporter.export()


# ---------------- 深圳专项（悬赏） ----------------
def cmd_sz_sites(args):
    import sz
    sz.crawl_bounty(max_pages=args.pages, limit=args.limit)


def cmd_sz_rmfygg(args):
    import sz
    sz.crawl_rmfygg_bounty(max_pages=args.pages)


def cmd_sz_update(args):
    import sz
    sz.crawl_bounty(max_pages=args.pages)
    if not args.skip_rmfygg:
        sz.crawl_rmfygg_bounty(max_pages=args.pages)
    exporter.export()


def cmd_sz_stats(_):
    import sz
    sz.stats()


def cmd_sz_fixcourt(_):
    import sz
    sz.reattribute()


# ---------------- 珠三角专项（悬赏） ----------------
def cmd_prd_crawl(args):
    import prd
    prd.crawl_bounty(max_pages=args.pages)


def cmd_prd_update(args):
    import prd
    import export as _exporter
    prd.crawl_bounty(max_pages=args.pages)
    _exporter.export()


def cmd_prd_stats(_):
    import prd
    prd.stats()


def main():
    ap = argparse.ArgumentParser(description="执行悬赏公告采集器")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init").set_defaults(func=cmd_init)

    p = sub.add_parser("crawl")
    p.add_argument("--kw", help="只用单个关键词（仅对公告网生效）")
    p.add_argument("--limit", type=int, help="最多入库条数")
    p.add_argument("--source", help="all(默认) / rmfygg / court，可逗号组合")
    p.set_defaults(func=cmd_crawl)

    sub.add_parser("reparse").set_defaults(func=cmd_reparse)
    sub.add_parser("export").set_defaults(func=cmd_export)
    sub.add_parser("dedupe", help="清理正文完全相同的重复记录").set_defaults(func=cmd_dedupe)
    sub.add_parser("stats").set_defaults(func=cmd_stats)

    p = sub.add_parser("update", help="采集 + 重新解析 + 导出，一条命令增量更新")
    p.add_argument("--kw", help="只用单个关键词")
    p.add_argument("--limit", type=int, help="最多入库条数")
    p.add_argument("--source", help="all(默认) / rmfygg / court")
    p.set_defaults(func=cmd_update)

    # ---- 深圳专项：市中院 + 区法院 ----
    sp = sub.add_parser("sz", help="深圳专项：市中院与各区法院的悬赏公告")
    ssub = sp.add_subparsers(dest="szcmd", required=True)

    def _szcommon(p):
        p.add_argument("--pages", type=int, help="每个栏目/法院最多翻页数")
        p.add_argument("--limit", type=int, help="最多入库条数")

    p = ssub.add_parser("sites", help="采法院官网悬赏栏目")
    _szcommon(p)
    p.set_defaults(func=cmd_sz_sites)

    p = ssub.add_parser("rmfygg", help="采人民法院公告网（按法院检索，慢速）")
    _szcommon(p)
    p.set_defaults(func=cmd_sz_rmfygg)

    p = ssub.add_parser("update", help="官网 + 公告网全采一遍再导出")
    _szcommon(p)
    p.add_argument("--skip-rmfygg", action="store_true", help="跳过公告网（WAF 拦截时用）")
    p.set_defaults(func=cmd_sz_update)

    ssub.add_parser("stats", help="深圳数据覆盖情况").set_defaults(func=cmd_sz_stats)
    ssub.add_parser("fixcourt", help="按正文校准深圳记录的发布法院").set_defaults(func=cmd_sz_fixcourt)

    # ---- 珠三角专项：东莞 / 惠州 / 广州基层法院 ----
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
