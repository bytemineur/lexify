# -*- coding: utf-8 -*-
"""深圳专项（执行悬赏）：市中院 + 区法院的悬赏公告。

两条来源互补：法院官网悬赏栏目（质量最高）+ 公告网按法院检索（区法院主力）。
法院名规范化见 collectors/_shared/courts.py。
"""
import config
import db
import parser as parser_bounty
from _shared import courts as _courts
from _shared.http import Http
from _shared import court_sites, rmfygg

SRC_RMFYGG = "rmfygg"

BOUNTY_KWS = ["悬赏"]


def crawl_site_rows(http, sites, max_pages=None, limit=None, verbose=True):
    """遍历若干官网栏目，产出 (site, raw) 原始记录。"""
    max_pages = config.COURT_MAX_PAGES if max_pages is None else max_pages
    n = 0
    for site in sites:
        site = dict(site)
        site.setdefault("province", config.SZ_PROVINCE)
        site.setdefault("city", config.SZ_CITY)
        if verbose:
            print(f"\n[深圳·官网] {site['court']} {site['prefix']}")
        try:
            for raw in court_sites.iter_site(http, site, max_pages=max_pages,
                                             verbose=verbose):
                if site.get("detect_court"):
                    raw["court"] = _courts._clean_court(
                        _courts.detect_court(raw.get("title"), raw.get("content"),
                                             site["court"]))
                yield site, raw
                n += 1
                if limit and n >= limit:
                    return
        except Exception as e:
            print(f"    ! {site['court']} 采集异常: {e}")
            continue


def crawl_bounty(conn=None, http=None, sites=None, max_pages=None, limit=None,
                 verbose=True):
    """悬赏公告：法院官网栏目。"""
    import pipeline

    sites = sites or config.SZ_BOUNTY_SITES
    http = http or Http()
    own = conn is None
    conn = conn or db.connect()
    db.init_db(conn)

    seen = kept = new = dup = skipped = 0
    for site, raw in crawl_site_rows(http, sites, max_pages, limit, verbose):
        seen += 1
        text = raw.get("content") or ""
        if config.REQUIRE_BOUNTY_SIGNAL and not parser_bounty.looks_like_bounty(text):
            skipped += 1
            continue
        rec = pipeline.build_record(raw)
        if db.find_same_content(conn, rec["content_hash"], rec["source_id"]):
            dup += 1
            continue
        if db.upsert(conn, rec):
            new += 1
        kept += 1
        if verbose and kept % 20 == 0:
            conn.commit()
            print(f"    已入库 {kept} 条（新增 {new}）")
    conn.commit()
    if own:
        conn.close()
    if verbose:
        print(f"\n[深圳·悬赏·官网] 扫描 {seen}，入池 {kept}，新增 {new}，"
              f"重复 {dup}，非悬赏跳过 {skipped}")
    return new


def _rmfygg_http():
    return Http(interval=config.SZ_RMFYGG_INTERVAL)


def crawl_rmfygg_bounty(conn=None, http=None, courts=None, max_pages=None,
                        verbose=True, tag="深圳", city_of=None, province=None,
                        keywords=None):
    """悬赏公告：公告网按法院检索。"""
    import pipeline

    courts = courts or config.SZ_RMFYGG_COURTS
    http = http or _rmfygg_http()
    own = conn is None
    conn = conn or db.connect()
    db.init_db(conn)
    max_pages = config.SZ_RMFYGG_MAX_PAGES if max_pages is None else max_pages
    kws = keywords or BOUNTY_KWS

    new = kept = seen = 0
    for court in courts:
        for kw in kws:
            if verbose:
                print(f"\n[{tag}·悬赏·公告网] {court} · {kw}")
            try:
                for raw in rmfygg.iter_keyword(http, kw, max_pages=max_pages,
                                               court_name=court, verbose=False):
                    rec_raw = rmfygg.to_record(raw)
                    seen += 1
                    if not parser_bounty.looks_like_bounty(rec_raw["content"]):
                        continue
                    rec_raw["court"] = _courts._clean_court(
                        _courts.detect_court(rec_raw.get("court"), rec_raw["content"],
                                             court))
                    if city_of:
                        rec_raw["province"] = province or config.SZ_PROVINCE
                        rec_raw["city"] = city_of.get(court) or ""
                    rec = pipeline.build_record(rec_raw)
                    if db.find_same_content(conn, rec["content_hash"],
                                            rec["source_id"]):
                        continue
                    if db.upsert(conn, rec):
                        new += 1
                    kept += 1
            except Exception as e:
                print(f"    ! {court}/{kw} 中断: {e}")
                conn.commit()
                if own:
                    conn.close()
                return new
        conn.commit()
    if own:
        conn.close()
    if verbose:
        print(f"\n[{tag}·悬赏·公告网] 扫描 {seen}，入池 {kept}，新增 {new}")
    return new


def reattribute(conn=None, verbose=True):
    """按正文重新校准深圳悬赏记录的发布法院（中院栏目混入各区公告）。"""
    own = conn is None
    conn = conn or db.connect()
    db.init_db(conn)

    fixed = 0
    rows = conn.execute(
        "SELECT id, court, content FROM notices"
        " WHERE court LIKE '%深圳%' OR city LIKE '%深圳%'").fetchall()
    for r in rows:
        new = _courts._clean_court(_courts.detect_court("", r["content"], r["court"] or ""))
        if new and new != r["court"]:
            conn.execute("UPDATE notices SET court=? WHERE id=?", (new, r["id"]))
            fixed += 1
    conn.commit()
    if own:
        conn.close()
    if verbose:
        print(f"[深圳·悬赏·法院校准] 修正 {fixed} 条法院归属")
    return fixed


def stats(verbose=True):
    conn = db.connect()
    db.init_db(conn)
    n = conn.execute(
        "SELECT COUNT(*) FROM notices WHERE city LIKE '%深圳%'").fetchone()[0]
    if verbose:
        print("-- 深圳悬赏专项 --")
        print(f"  悬赏·深圳: {n}")
        print("-- 按法院 --")
        for r in conn.execute(
                "SELECT COALESCE(court,'未识别') c, COUNT(*) n FROM notices"
                " WHERE city LIKE '%深圳%' GROUP BY c ORDER BY n DESC"):
            print(f"  {r['n']:>4}  {r['c']}")
    conn.close()
    return {"悬赏·深圳": n}
