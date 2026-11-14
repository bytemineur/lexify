# -*- coding: utf-8 -*-
"""珠三角专项（开庭公告）：东莞、惠州、广州基层法院，全部走公告网 courtParam 检索。

这批法院没有结构化排期表，开庭时间来自公告正文（传票上写的日期）。
"""
import config
from _shared.http import Http
import sz


def _http():
    return Http(interval=config.PRD_RMFYGG_INTERVAL)


def crawl_hearing(conn=None, http=None, courts=None, max_pages=None,
                  verbose=True):
    """开庭公告：公告网按法院检索。"""
    return sz.crawl_rmfygg_hearing(
        conn=conn,
        http=http or _http(),
        courts=courts or config.PRD_RMFYGG_COURTS,
        max_pages=(config.PRD_RMFYGG_MAX_PAGES if max_pages is None
                   else max_pages),
        verbose=verbose, tag="珠三角",
        city_of=config.PRD_CITY_OF, province=config.PRD_PROVINCE)


def stats(verbose=True):
    import db
    conn = db.connect()
    db.init_db(conn)
    out = {}
    for city in ("东莞市", "惠州市", "广州市"):
        out[city] = conn.execute(
            "SELECT COUNT(*) FROM hearings WHERE city=?", (city,)).fetchone()[0]
    if verbose:
        print("-- 珠三角开庭专项 --")
        for city, v in out.items():
            print(f"  {city}: {v}")
        print("-- 按法院 --")
        for r in conn.execute(
                "SELECT COALESCE(court,'未识别') c, COUNT(*) n FROM hearings"
                " WHERE city IN ('东莞市','惠州市','广州市')"
                " GROUP BY c ORDER BY n DESC"):
            print(f"  {r['n']:>4}  {r['c']}")
    conn.close()
    return out
