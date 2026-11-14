# -*- coding: utf-8 -*-
"""珠三角专项（执行悬赏）：东莞、惠州、广州基层法院，全部走公告网 courtParam 检索。

城市字段由 _shared/courts.py 的 PRD_CITY_OF 直接给定，不走 regions.resolve
（后者认不出「博罗县」「惠东县」这类县名）。
"""
import config
from _shared.http import Http
import sz


def _http():
    return Http(interval=config.PRD_RMFYGG_INTERVAL)


def crawl_bounty(conn=None, http=None, courts=None, max_pages=None,
                 verbose=True):
    """悬赏公告：公告网按法院检索。"""
    return sz.crawl_rmfygg_bounty(
        conn=conn,
        http=http or _http(),
        courts=courts or config.PRD_RMFYGG_COURTS,
        max_pages=(3 if max_pages is None else max_pages),
        verbose=verbose, tag="珠三角",
        city_of=config.PRD_CITY_OF, province=config.PRD_PROVINCE,
        keywords=config.PRD_BOUNTY_KEYWORDS)


def stats(verbose=True):
    import db
    conn = db.connect()
    db.init_db(conn)
    out = {}
    for city in ("东莞市", "惠州市", "广州市"):
        out[city] = conn.execute(
            "SELECT COUNT(*) FROM notices WHERE city=?", (city,)).fetchone()[0]
    if verbose:
        print("-- 珠三角悬赏专项 --")
        for city, v in out.items():
            print(f"  {city}: {v}")
    conn.close()
    return out
