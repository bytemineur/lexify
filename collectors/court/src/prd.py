# -*- coding: utf-8 -*-
"""珠三角专项：东莞、惠州、广州的基层法院。

与深圳的差别：

  深圳各区法院有可用的官网公告栏目（悬赏 / 送达公告 / 排期表），所以
  sz.py 是「官网为主、公告网补齐」。这批法院没有——逐站验证过，要么
  是动态接口，要么根本没有悬赏栏目，所以数据全部走人民法院公告网的
  courtParam 检索。

  城市字段不从法院名推断（regions.resolve 认不出「博罗县」「惠东县」
  「龙门县」这类县名，会算成空省市），而是由 config.PRD_CITY_OF 直接给。

  开庭排期表：这批法院都没有挂结构化排期表，因此没有 schedule 采集器。
  开庭时间来自公告正文（传票上写的日期）。

命令行入口见 cli.py 的 prd 子命令。
"""
import config
from http_client import Http
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


def crawl_bounty(conn=None, http=None, courts=None, max_pages=None,
                 verbose=True):
    """悬赏公告：公告网按法院检索。

    公告网的悬赏量本来就少（悬赏多走法院自己的公众号/官网），
    这里多给几个关键词提高召回，最终仍有 looks_like_bounty 过滤。
    """
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
        out[city] = {
            "悬赏": conn.execute(
                "SELECT COUNT(*) FROM notices WHERE city=?", (city,)).fetchone()[0],
            "开庭": conn.execute(
                "SELECT COUNT(*) FROM hearings WHERE city=?", (city,)).fetchone()[0],
        }
    if verbose:
        print("-- 珠三角专项 --")
        for city, v in out.items():
            print(f"  {city}: 悬赏 {v['悬赏']} / 开庭 {v['开庭']}")
        print("-- 按法院 --")
        for r in conn.execute(
                "SELECT COALESCE(court,'未识别') c, COUNT(*) n FROM hearings"
                " WHERE city IN ('东莞市','惠州市','广州市')"
                " GROUP BY c ORDER BY n DESC"):
            print(f"  {r['n']:>4}  {r['c']}")
    conn.close()
    return out
