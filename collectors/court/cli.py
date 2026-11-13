# -*- coding: utf-8 -*-
"""法院公告雷达 - 命令行入口

两块看板共用一套采集/解析/导出管线：

  悬赏看板（index.html）
    python cli.py init / crawl / reparse / dedupe / export / stats / serve / update

  开庭看板（hearings.html）
    python cli.py hearing crawl     采集开庭公告
    python cli.py hearing update    采集 + 解析 + 导出
    python cli.py hearing export    只导出
    python cli.py hearing reparse   用最新规则重解
    python cli.py hearing stats     统计
    python cli.py hearing serve     打开开庭看板

示例:
  python cli.py crawl --limit 50    试跑，最多入库 50 条
  python cli.py crawl --kw 悬赏     指定关键词
"""
import argparse
import http.server
import os
import socketserver
import sys
import webbrowser

ROOT = os.path.dirname(os.path.abspath(__file__))
for p in (ROOT, os.path.join(ROOT, "src"), os.path.join(ROOT, "ref")):
    if p not in sys.path:
        sys.path.insert(0, p)

import config
import db
import export as exporter
import pipeline


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
    kws = [args.kw] if args.kw else None
    pipeline.crawl(keywords=kws, limit=args.limit, sources=_sources(args.source))


def cmd_reparse(_):
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
    for r in conn.execute("""SELECT source, COUNT(*) n FROM notices
                             GROUP BY source ORDER BY n DESC"""):
        print(f"  {r['source']:<10} {r['n']}")
    print("\n-- 按法院（前 12）--")
    for r in conn.execute("""SELECT COALESCE(court,'未识别') c, COUNT(*) n
                             FROM notices GROUP BY c ORDER BY n DESC LIMIT 12"""):
        print(f"  {r['n']:>4}  {r['c']}")
    print("\n-- 按省份 --")
    for r in conn.execute("""SELECT COALESCE(province,'未识别') p, COUNT(*) n
                             FROM notices GROUP BY p ORDER BY n DESC LIMIT 15"""):
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


def cmd_serve(args):
    host = args.host
    port = args.port
    page = getattr(args, "page", None) or "index.html"
    os.chdir(config.WEB_DIR)

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer((host, port), Handler) as httpd:
        httpd.daemon_threads = True
        shown = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
        url = f"http://{shown}:{port}/{page}"
        print(f"监听 {host}:{port}")
        print(f"看板地址: {url}")
        print(f"悬赏看板: http://{shown}:{port}/index.html")
        print(f"开庭看板: http://{shown}:{port}/hearings.html\nCtrl+C 停止")
        if not args.no_browser:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n已停止")


def cmd_update(args):
    """采集 -> 重新解析 -> 导出看板数据，一条命令完成增量更新。"""
    pipeline.crawl(keywords=[args.kw] if args.kw else None, limit=args.limit,
                   sources=_sources(args.source))
    pipeline.reparse_all()
    exporter.export()


# ---------------- 开庭看板 ----------------
def _h_sources(arg):
    if not arg or arg == "all":
        return None
    return [s.strip() for s in arg.split(",") if s.strip()]


def cmd_h_crawl(args):
    pipeline.crawl_hearings(keywords=[args.kw] if args.kw else None,
                            max_pages=args.pages, limit=args.limit,
                            sources=_h_sources(args.source))


def cmd_h_update(args):
    pipeline.crawl_hearings(keywords=[args.kw] if args.kw else None,
                            max_pages=args.pages, limit=args.limit,
                            sources=_h_sources(args.source))
    conn = db.connect()
    db.dedupe_hearings(conn)
    conn.close()
    exporter.export_hearings()


def cmd_h_export(_):
    exporter.export_hearings()


def cmd_h_reparse(_):
    pipeline.reparse_hearings()
    exporter.export_hearings()


def cmd_h_stats(_):
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
    for r in conn.execute("""SELECT source, COUNT(*) n FROM hearings
                             GROUP BY source ORDER BY n DESC"""):
        print(f"  {r['source']:<16} {r['n']}")

    print("\n-- 被告类型 --")
    for r in conn.execute("""SELECT COALESCE(defendant_type,'unknown') t,
                                    COUNT(*) n
                             FROM hearings GROUP BY t ORDER BY n DESC"""):
        print(f"  {r['t']:<10} {r['n']}")

    print("\n-- 案由分组 --")
    for r in conn.execute("""SELECT COALESCE(cause_group,'未归类') g, COUNT(*) n
                             FROM hearings GROUP BY g ORDER BY n DESC"""):
        print(f"  {r['g']:<10} {r['n']}")

    print("\n-- 未来 30 天内开庭（前 10 场）--")
    for r in conn.execute("""SELECT hearing_at, court, cause, defendant
                             FROM hearings
                             WHERE hearing_date >= date('now')
                               AND hearing_date <= date('now','+30 day')
                             ORDER BY hearing_at LIMIT 10"""):
        print(f"  {r['hearing_at']}  {(r['court'] or '')[:16]:<16} "
              f"{(r['cause'] or '')[:12]:<12} {(r['defendant'] or '')[:20]}")
    conn.close()


def cmd_h_serve(args):
    args.page = "hearings.html"
    cmd_serve(args)


# ---------------- 深圳专项 ----------------
def cmd_sz_sites(args):
    """法院官网栏目：悬赏 + 开庭（含结构化排期表）。"""
    import sz
    src = args.source or "all"
    if src in ("all", "bounty"):
        sz.crawl_bounty(max_pages=args.pages, limit=args.limit)
    if src in ("all", "hearing"):
        sz.crawl_hearing(max_pages=args.pages, limit=args.limit)
        sz.crawl_schedule(max_pages=args.pages)


def cmd_sz_rmfygg(args):
    """公告网按法院检索（受 WAF 限流，慢速 + 续跑）。"""
    import sz
    src = args.source or "all"
    if src in ("all", "bounty"):
        sz.crawl_rmfygg_bounty(max_pages=args.pages)
    if src in ("all", "hearing"):
        sz.crawl_rmfygg_hearing(max_pages=args.pages)


def cmd_sz_update(args):
    """官网 + 公告网都采一遍，然后导数据。"""
    import sz
    sz.crawl_bounty(max_pages=args.pages)
    sz.crawl_hearing(max_pages=args.pages)
    if not args.skip_rmfygg:
        sz.crawl_rmfygg_bounty(max_pages=args.pages)
        sz.crawl_rmfygg_hearing(max_pages=args.pages)
    exporter.export()


def cmd_sz_stats(_):
    import sz
    sz.stats()


def cmd_sz_fixcourt(_):
    """按正文重新校准深圳记录的发布法院（中院栏目混入各区公告）。"""
    import sz
    sz.reattribute()


# ---------------- 珠三角专项 ----------------
def cmd_prd_crawl(args):
    """东莞 / 惠州 / 广州基层法院：全部走公告网按法院检索。"""
    import prd
    src = args.source or "all"
    if src in ("all", "hearing"):
        prd.crawl_hearing(max_pages=args.pages)
    if src in ("all", "bounty"):
        prd.crawl_bounty(max_pages=args.pages)


def cmd_prd_update(args):
    import prd
    import exporter
    prd.crawl_hearing(max_pages=args.pages)
    prd.crawl_bounty(max_pages=args.pages)
    exporter.export()


def cmd_prd_stats(_):
    import prd
    prd.stats()


def main():
    ap = argparse.ArgumentParser(description="执行悬赏公告数据库")
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

    p = sub.add_parser("serve")
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8848)))
    p.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"),
                   help="监听地址，容器内用 0.0.0.0")
    p.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("update", help="采集 + 重新解析 + 导出，一条命令增量更新")
    p.add_argument("--kw", help="只用单个关键词（仅对公告网生效）")
    p.add_argument("--limit", type=int, help="最多入库条数")
    p.add_argument("--source", help="all(默认) / rmfygg / court，可逗号组合")
    p.set_defaults(func=cmd_update)

    # ---- 开庭看板 ----
    hp = sub.add_parser("hearing", help="开庭公告看板（采集/导出/统计）")
    hsub = hp.add_subparsers(dest="hcmd", required=True)

    def _hcommon(p):
        p.add_argument("--kw", help="只用单个案由关键词")
        p.add_argument("--pages", type=int, help="每个关键词最多翻页数")
        p.add_argument("--limit", type=int, help="最多入池条数")
        p.add_argument("--source", help="all(默认) / rmfygg_kt / court_schedule")

    p = hsub.add_parser("crawl", help="采集开庭公告")
    _hcommon(p)
    p.set_defaults(func=cmd_h_crawl)

    p = hsub.add_parser("update", help="采集 + 去重 + 导出")
    _hcommon(p)
    p.set_defaults(func=cmd_h_update)

    hsub.add_parser("export", help="只导出看板数据").set_defaults(func=cmd_h_export)
    hsub.add_parser("reparse", help="用最新规则重解").set_defaults(func=cmd_h_reparse)
    hsub.add_parser("stats", help="开庭库统计").set_defaults(func=cmd_h_stats)

    p = hsub.add_parser("serve", help="打开开庭看板")
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8848)))
    p.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(func=cmd_h_serve)

    # ---- 深圳专项：市中院 + 10 区法院 ----
    sp = sub.add_parser("sz", help="深圳专项：市中院与各区法院的悬赏/开庭公告")
    ssub = sp.add_subparsers(dest="szcmd", required=True)

    def _szcommon(p):
        p.add_argument("--pages", type=int, help="每个栏目/法院最多翻页数")
        p.add_argument("--limit", type=int, help="最多入库条数")
        p.add_argument("--source", help="all(默认) / bounty / hearing")

    p = ssub.add_parser("sites", help="采法院官网公告栏目（悬赏 + 开庭）")
    _szcommon(p)
    p.set_defaults(func=cmd_sz_sites)

    p = ssub.add_parser("rmfygg", help="采人民法院公告网（按法院检索，慢速）")
    _szcommon(p)
    p.set_defaults(func=cmd_sz_rmfygg)

    p = ssub.add_parser("update", help="官网 + 公告网全采一遍再导出")
    _szcommon(p)
    p.add_argument("--skip-rmfygg", action="store_true",
                   help="跳过公告网（WAF 拦截时用）")
    p.set_defaults(func=cmd_sz_update)

    ssub.add_parser("stats", help="深圳数据覆盖情况").set_defaults(func=cmd_sz_stats)
    ssub.add_parser("fixcourt", help="按正文校准深圳记录的发布法院").set_defaults(
        func=cmd_sz_fixcourt)

    # ---- 珠三角专项：东莞 / 惠州 / 广州基层法院 ----
    pp = sub.add_parser("prd", help="珠三角专项：东莞、惠州、广州各基层法院")
    psub = pp.add_subparsers(dest="prdcmd", required=True)

    def _prdcommon(p):
        p.add_argument("--pages", type=int, help="每个法院每种类型最多翻页数")
        p.add_argument("--source", help="all(默认) / hearing / bounty")

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
