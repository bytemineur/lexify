# -*- coding: utf-8 -*-
"""深圳专项：市中院 + 10 个区法院的悬赏公告 / 开庭公告。

三条来源互补：

  1. 法院官网公告栏目（sources.court_sites 静态分页解析）
     悬赏公告质量最高，正文齐全、发布日期准确。中院站点的悬赏栏目
     实际汇总了全市各区法院的悬赏，所以开启 detect_court：标题里写了
     哪个法院就以哪个法院入库，避免全部记在中院名下。

  2. 人民法院公告网按法院检索（courtParam）
     区法院的开庭公告基本只投公告网，这是开庭看板的主力来源。
     公告网挂着腾讯云 WAF，连续请求会 403 封 IP，所以这里单独放慢
     节流、失败冷却，并且允许断点续跑（已入库的按正文哈希跳过）。

  3. 大鹏新区没有独立基层法院
     辖区案件由龙岗区人民法院大鹏法庭（环境资源法庭）管辖，
     因此不单列法院，龙岗的两条源已覆盖大鹏新区。

命令行入口见 cli.py 的 sz 子命令。
"""
import re

import config
import db
import parser_bounty
import parser_hearing
from http_client import Http
from sources import court_sites, rmfygg

SRC_COURT_KT = "court_kt"          # 法院官网 · 开庭公告
SRC_RMFYGG_KT = "rmfygg_kt"        # 公告网 · 开庭传票
SRC_COURT_XS = "court"             # 法院官网 · 悬赏公告
SRC_RMFYGG_XS = "rmfygg"           # 公告网 · 悬赏

# 正文/标题里的法院全称。先试「广东省深圳市…人民法院」这种完整写法，
# 再退回泛匹配，避免把地址串整段吞进来。
COURT_RE_FULL = re.compile(
    r"((?:广东(?:省)?)?深圳市[^\s，。、：:；;（）()]{2,12}?人民法院"
    r"|(?:广东(?:省)?)?深圳前海合作区人民法院)")
COURT_RE = re.compile(r"([\u4e00-\u9fa5]{2,12}?人民法院)")


def _build_registry():
    """已知法院：写法 -> 标准全称。目前收录珠三角各基层法院。

    config.PRD_COURTS 的每一条是 (城市, 标准全称, 别名列表)。别名用于
    收录「广东自由贸易区南沙片区人民法院」这类同院不同牌子的写法。
    """
    reg = {}
    for row in getattr(config, "PRD_COURTS", []):
        name, aliases = row[1], (row[2] if len(row) > 2 else [])
        reg[name] = name
        for a in aliases:
            if a:
                reg[a] = name
    return reg


KNOWN_COURTS = _build_registry()


def _match_known(text):
    """在文本里找已知法院全称/别名，返回标准全称。"""
    if not text:
        return None
    for key, canon in KNOWN_COURTS.items():
        if key in text:
            return canon
    return None


def detect_court(title, content, default):
    """标题优先、正文兜底，识别公告的真实发布法院（结果做规范化）。"""
    for text in (title or "", (content or "")[:600]):
        hit = _match_known(text)
        if hit:
            return hit
        m = COURT_RE_FULL.search(text)
        if m:
            return canon_court(m.group(1))
    m = COURT_RE.search(title or "")
    if m and m.group(1).startswith(("深圳市", "广东省")):
        return canon_court(m.group(1))
    return canon_court(default)


# 深圳各基层法院，用于把「深圳市龙岗区德政路8号龙岗区人民法院」这类
# 从正文地址里带出来的脏法院名收敛成标准全称。
SZ_DISTRICTS = ("福田", "罗湖", "南山", "盐田", "宝安", "龙华", "龙岗",
                "坪山", "光明", "大鹏新区")


def canon_court(name):
    """法院名规范化为标准全称。识别不出的一律不动。"""
    s = (name or "").strip()
    if not s:
        return s
    hit = _match_known(s)
    if hit:
        return hit
    if "中级人民法院" in s:
        # 只补全省级前缀，不改城市：正文里的「上诉至广州市中级人民法院」
        # 不能被顺手改写成深圳中院
        for city, full in (("深圳", "广东省深圳市中级人民法院"),
                           ("广州", "广东省广州市中级人民法院"),
                           ("东莞", "东莞市中级人民法院"),
                           ("惠州", "惠州市中级人民法院")):
            if city in s:
                return full
        return s
    if "前海" in s:
        return "深圳前海合作区人民法院"
    for d in SZ_DISTRICTS:
        if d + "区人民法院" in s or d + "人民法院" in s:
            if d == "大鹏新区":
                return "深圳市大鹏新区人民法院"
            return f"深圳市{d}区人民法院"
    return s


def _clean_court(name):
    name = canon_court(name)
    # 只有认不出的「裸区名」（如「龙岗区人民法院」）才补深圳市前缀；
    # 东莞/惠州/广州的法院名本身就带城市，不能被改写成深圳市开头。
    if name and not name.startswith(("深圳市", "广东省", "深圳")):
        if any(d in name for d in SZ_DISTRICTS):
            return "深圳市" + name
    return name


# ---------------------------------------------------------------- 官网栏目
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
                    raw["court"] = _clean_court(
                        detect_court(raw.get("title"), raw.get("content"),
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
        if db.upsert_hearing(conn, rec):
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


# ---------------------------------------------------------------- 公告网按法院
BOUNTY_KWS = ["悬赏"]


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
                    rec_raw["court"] = _clean_court(
                        detect_court(rec_raw.get("court"), rec_raw["content"],
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
                    rec_raw["court"] = _clean_court(
                        detect_court(rec_raw.get("court"), text, court))
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
                    if db.upsert_hearing(conn, rec):
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


# ---------------------------------------------------------------- 官网排期表
# 盐田法院把全周排期做成一张静态表：开庭日期 | 开庭时间 | 法庭 | 案号 |
# 案由 | 当事人 | 承办法官。比送达公告信息更全（直接带当事人），
# 是区级法院里唯一提供结构化排期的站点。
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
            continue                      # 表头 / 非数据行
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
        # 拼一句和公告同构的正文，交给 parser_hearing 抽当事人与金额
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
    max_pages = config.COURT_MAX_PAGES if max_pages is None else max_pages

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
                    if db.upsert_hearing(conn, rec):
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


# ---------------------------------------------------------------- 统计
def reattribute(conn=None, verbose=True):
    """按正文重新校准深圳记录的发布法院。

    中院站点的悬赏栏目汇总了各区法院的公告，早先入库时统一记在中院名下。
    去重是按正文哈希做的，重新采集不会覆盖已有行，所以需要单独跑一遍
    把法院字段纠回来，否则「按法院筛选」看到的还是全部挂在中院。
    """
    own = conn is None
    conn = conn or db.connect()
    db.init_db(conn)

    fixed = 0
    for table in ("notices", "hearings"):
        rows = conn.execute(
            f"SELECT id, court, content FROM {table}"
            f" WHERE court LIKE '%深圳%' OR city LIKE '%深圳%'").fetchall()
        for r in rows:
            new = _clean_court(detect_court("", r["content"], r["court"] or ""))
            if new and new != r["court"]:
                conn.execute(f"UPDATE {table} SET court=? WHERE id=?",
                             (new, r["id"]))
                fixed += 1
    conn.commit()
    if own:
        conn.close()
    if verbose:
        print(f"[深圳·法院校准] 修正 {fixed} 条法院归属")
    return fixed


def stats(verbose=True):
    conn = db.connect()
    db.init_db(conn)
    out = {}
    for label, sql in (
        ("悬赏·深圳", "SELECT COUNT(*) FROM notices WHERE city LIKE '%深圳%'"),
        ("开庭·深圳", "SELECT COUNT(*) FROM hearings WHERE city LIKE '%深圳%'"),
    ):
        out[label] = conn.execute(sql).fetchone()[0]
    if verbose:
        print("-- 深圳专项 --")
        for k, v in out.items():
            print(f"  {k}: {v}")
        print("-- 悬赏按法院 --")
        for r in conn.execute(
                "SELECT COALESCE(court,'未识别') c, COUNT(*) n FROM notices"
                " WHERE city LIKE '%深圳%' GROUP BY c ORDER BY n DESC"):
            print(f"  {r['n']:>4}  {r['c']}")
        print("-- 开庭按法院 --")
        for r in conn.execute(
                "SELECT COALESCE(court,'未识别') c, COUNT(*) n FROM hearings"
                " WHERE city LIKE '%深圳%' GROUP BY c ORDER BY n DESC"):
            print(f"  {r['n']:>4}  {r['c']}")
    conn.close()
    return out
