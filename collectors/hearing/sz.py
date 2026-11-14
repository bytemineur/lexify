# -*- coding: utf-8 -*-
"""深圳专项（开庭公告）：市中院 + 区法院的开庭公告 / 送达公告 / 排期表。

三条来源互补：法院官网送达公告栏目、公告网按法院检索（区法院主力）、
盐田法院的静态排期表。法院名规范化见 collectors/_shared/courts.py。
"""
import re

import config
import db
import parser as parser_hearing
from _shared import courts as _courts
from _shared.http import Http
from _shared import court_sites, rmfygg

SRC_COURT_KT = "court_kt"          # 法院官网 · 开庭公告
SRC_RMFYGG_KT = "rmfygg_kt"        # 公告网 · 开庭传票


def crawl_site_rows(http, sites, max_pages=None, limit=None, verbose=True):
    """遍历若干官网栏目，产出 (site, raw) 原始记录。"""
    max_pages = config.COURT_MAX_PAGES if hasattr(config, "COURT_MAX_PAGES") else 40
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


def crawl_hearing(conn=None, http=None, sites=None, max_pages=None, limit=None,
                  verbose=True):
    """开庭公告：法院官网栏目（开庭公告 / 送达公告）。"""
    import pipeline

    sites = sites or config.SZ_HEARING_SITES
    http = http or Http()
    own = conn is None
    conn = conn or db.connect()
    db.init_db(conn)

    seen = kept = new = dup = skipped = 0
    for site, raw in crawl_site_rows(http, sites, max_pages, limit, verbose):
        seen += 1
        text = raw.get("content") or ""
        if not parser_hearing.looks_like_hearing(text):
            skipped += 1
            continue
        raw["source"] = SRC_COURT_KT
        rec = pipeline.build_hearing_record(raw)
        if not rec.get("hearing_date"):
            skipped += 1
            continue
        if db.hearing_exists_content(conn, rec["content_hash"], rec["source_id"]):
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
        print(f"\n[深圳·开庭·官网] 扫描 {seen}，入池 {kept}，新增 {new}，"
              f"重复 {dup}，非开庭跳过 {skipped}")
    return new


def _rmfygg_http():
    return Http(interval=config.SZ_RMFYGG_INTERVAL)


def crawl_rmfygg_hearing(conn=None, http=None, courts=None, max_pages=None,
                         verbose=True, tag="深圳", city_of=None, province=None):
    """开庭公告：公告网按法院检索（开庭传票 + 起诉状副本及开庭传票）。"""
    import pipeline

    courts = courts or config.SZ_RMFYGG_COURTS
    http = http or _rmfygg_http()
    own = conn is None
    conn = conn or db.connect()
    db.init_db(conn)
    max_pages = config.SZ_RMFYGG_MAX_PAGES if max_pages is None else max_pages
    types = config.HEARING_NOTICE_TYPES

    new = kept = seen = skipped = 0
    for court in courts:
        for ntv in types:
            if verbose:
                print(f"\n[{tag}·开庭·公告网] {court} · {ntv}")
            try:
                for raw in rmfygg.iter_keyword(http, "", max_pages=max_pages,
                                               notice_type_val=ntv,
                                               court_name=court, verbose=False):
                    rec_raw = rmfygg.to_record(raw)
                    rec_raw["source"] = SRC_RMFYGG_KT
                    rec_raw["notice_type"] = ntv
                    seen += 1
                    text = rec_raw["content"]
                    if not parser_hearing.looks_like_hearing(text):
                        skipped += 1
                        continue
                    rec_raw["court"] = _courts._clean_court(
                        _courts.detect_court(rec_raw.get("court"), text, court))
                    if city_of:
                        rec_raw["province"] = province or config.SZ_PROVINCE
                        rec_raw["city"] = city_of.get(court) or ""
                    rec = pipeline.build_hearing_record(rec_raw)
                    if not rec.get("hearing_date"):
                        skipped += 1
                        continue
                    if db.hearing_exists_content(conn, rec["content_hash"],
                                                 rec["source_id"]):
                        continue
                    if db.upsert(conn, rec):
                        new += 1
                    kept += 1
                    if verbose and kept % 40 == 0:
                        conn.commit()
                        print(f"    已入库 {kept} 条（新增 {new}）")
            except Exception as e:
                print(f"    ! {court}/{ntv} 中断: {e}")
                conn.commit()
                if own:
                    conn.close()
                return new
        conn.commit()
    if own:
        conn.close()
    if verbose:
        print(f"\n[{tag}·开庭·公告网] 扫描 {seen}，入池 {kept}，"
              f"新增 {new}，跳过 {skipped}")
    return new


# ---- 官网排期表（盐田） ----
KT_DATE_RE = re.compile(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})")
KT_TIME_RE = re.compile(r"(\d{1,2})[:：](\d{2})")
KT_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
KT_CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
KT_TAG_RE = re.compile(r"<[^>]+>")


def _kt_clean(s):
    t = KT_TAG_RE.sub("", s or "")
    t = (t.replace("&nbsp;", " ").replace("&amp;", "&")
          .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))
    return re.sub(r"\s+", " ", t).strip()


def parse_ktpq(html, court, url):
    """解析「开庭日期/时间/法庭/案号/案由/当事人/法官」排期表。"""
    out = []
    for tr in KT_ROW_RE.findall(html):
        cells = [_kt_clean(c) for c in KT_CELL_RE.findall(tr)]
        if len(cells) < 6:
            continue
        dm = KT_DATE_RE.search(cells[0])
        if not dm:
            continue
        tm = KT_TIME_RE.search(cells[1]) if len(cells) > 1 else None
        try:
            y, mo, d = (int(x) for x in dm.groups())
            hh, mi = (int(tm.group(1)), int(tm.group(2))) if tm else (9, 0)
            if not (1 <= mo <= 12 and 1 <= d <= 31 and hh <= 23 and mi <= 59):
                continue
            hearing_at = "%04d-%02d-%02d %02d:%02d" % (y, mo, d, hh, mi)
        except ValueError:
            continue
        room = cells[2] or None
        case_no = cells[3] or None
        if not case_no:
            continue
        cause = cells[4] if cells[4] and cells[4] not in ("详见", "—", "-") else None
        parties = cells[5] if len(cells) > 5 else ""
        content = "%s。案号 %s。%s本院定于 %s 在%s公开开庭审理。" % (
            parties, case_no, (cause + "。") if cause else "",
            hearing_at.replace("-", "年", 1).replace("-", "月", 1) + "日",
            room or "本院")
        out.append({
            "source": SRC_COURT_KT,
            "source_id": "%s|%s|%s" % (court, case_no, hearing_at),
            "url": url,
            "court": court,
            "province": config.SZ_PROVINCE,
            "city": config.SZ_CITY,
            "case_no": case_no,
            "cause": cause,
            "hearing_at": hearing_at,
            "courtroom": room,
            "notice_type": "开庭排期",
            "publish_date": None,
            "content": content,
        })
    return out


def crawl_schedule(conn=None, http=None, sites=None, max_pages=None,
                   verbose=True):
    """开庭公告：官网结构化排期表。"""
    import pipeline

    sites = sites or config.SZ_SCHEDULE_SITES
    http = http or Http()
    own = conn is None
    conn = conn or db.connect()
    db.init_db(conn)
    max_pages = max_pages or 40

    new = kept = 0
    for site in sites:
        base, prefix = site["base"].rstrip("/"), site["prefix"].rstrip("/")
        first = f"{base}{prefix}/index.html"
        if verbose:
            print(f"\n[深圳·排期表] {site['court']} {first}")
        try:
            html = http.get(first, headers={"Referer": base + "/"})
            total = court_sites.max_page(html)
            total = min(total, max_pages) if max_pages else total
            if verbose:
                print(f"    共 {total} 页")
            for pno in range(1, total + 1):
                page = html if pno == 1 else http.get(
                    f"{base}{prefix}/index_{pno}.html",
                    headers={"Referer": first})
                for raw in parse_ktpq(page, site["court"], first):
                    rec = pipeline.build_hearing_record(raw)
                    if not rec.get("hearing_date"):
                        continue
                    if db.hearing_exists_content(conn, rec["content_hash"],
                                                 rec["source_id"]):
                        continue
                    if db.upsert(conn, rec):
                        new += 1
                    kept += 1
                conn.commit()
                if verbose:
                    print(f"    第 {pno} 页完成，累计新增 {new}")
        except Exception as e:
            print(f"    ! {site['court']} 排期表异常: {e}")
    if own:
        conn.close()
    if verbose:
        print(f"\n[深圳·开庭·排期表] 入库 {kept}，新增 {new}")
    return new


def reattribute(conn=None, verbose=True):
    """按正文重新校准深圳开庭记录的发布法院。"""
    own = conn is None
    conn = conn or db.connect()
    db.init_db(conn)

    fixed = 0
    rows = conn.execute(
        "SELECT id, court, content FROM hearings"
        " WHERE court LIKE '%深圳%' OR city LIKE '%深圳%'").fetchall()
    for r in rows:
        new = _courts._clean_court(_courts.detect_court("", r["content"], r["court"] or ""))
        if new and new != r["court"]:
            conn.execute("UPDATE hearings SET court=? WHERE id=?", (new, r["id"]))
            fixed += 1
    conn.commit()
    if own:
        conn.close()
    if verbose:
        print(f"[深圳·开庭·法院校准] 修正 {fixed} 条法院归属")
    return fixed


def stats(verbose=True):
    conn = db.connect()
    db.init_db(conn)
    n = conn.execute(
        "SELECT COUNT(*) FROM hearings WHERE city LIKE '%深圳%'").fetchone()[0]
    if verbose:
        print("-- 深圳开庭专项 --")
        print(f"  开庭·深圳: {n}")
        print("-- 按法院 --")
        for r in conn.execute(
                "SELECT COALESCE(court,'未识别') c, COUNT(*) n FROM hearings"
                " WHERE city LIKE '%深圳%' GROUP BY c ORDER BY n DESC"):
            print(f"  {r['n']:>4}  {r['c']}")
    conn.close()
    return {"开庭·深圳": n}
