# -*- coding: utf-8 -*-
"""执行悬赏看板：采集 -> 解析 -> 入库 主流程。

数据源：
  rmfygg      人民法院公告网（全国范围，关键词检索）
  court       各地法院官网公告栏目（按法院逐站抓取）

共享基础设施在 collectors/_shared（http / regions / courts / rmfygg）。
本模块只写 notices 表，不碰 hearings。
"""
import hashlib
import json
import os
import sys
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_COLLECTORS = os.path.dirname(_HERE)
for p in (_COLLECTORS, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import config
import db
import parser as parser_bounty
from _shared import regions
from _shared.http import Http
from _shared import court_sites, rmfygg


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _content_hash(text):
    """正文归一化哈希：忽略全部空白差异，用于跨源/重复发布去重。"""
    norm = "".join((text or "").split())
    return hashlib.md5(norm.encode("utf-8")).hexdigest()


def build_record(raw, province_map=True):
    """统一的原始记录 -> 完整入库记录。raw 需含 source/source_id/court/content 等。"""
    text = raw.get("content") or ""
    info = parser_bounty.parse_notice(text, publish_date=raw.get("publish_date") or None)

    court = (raw.get("court") or "").strip()
    prov = raw.get("province")
    city = raw.get("city")
    conf = 1.0 if prov else 0.0
    if province_map and not prov:
        prov, city, conf = regions.resolve(court)

    if not prov:
        m = parser_bounty.PROVINCE_HINT.search(info.get("content_norm", ""))
        if m:
            p2, c2, _ = regions.resolve(m.group(1))
            prov, city = p2 or prov, c2 or city

    debtor = (raw.get("debtor") or "").strip() or info.get("debtor") or None

    return {
        "source": raw.get("source") or "unknown",
        "source_id": str(raw.get("source_id") or ""),
        "url": raw.get("url"),
        "court": court,
        "province": prov,
        "city": city,
        "case_no": info.get("case_no"),
        "debtor": debtor,
        "debtor_count": info.get("debtor_count") or 1,
        "subject_amount": info.get("subject_amount"),
        "reward_ratio": info.get("reward_ratio"),
        "reward_cap": info.get("reward_cap"),
        "reward_fixed": info.get("reward_fixed"),
        "reward_est": info.get("reward_est"),
        "reward_type": info.get("reward_type"),
        "publish_date": raw.get("publish_date") or info.get("publish_date"),
        "valid_from": info.get("valid_from"),
        "valid_to": info.get("valid_to"),
        "contact_phone": info.get("contact_phone"),
        "content": text,
        "content_hash": _content_hash(text),
        "parse_quality": info.get("parse_quality"),
        "raw_json": json.dumps(
            {k: v for k, v in raw.items() if k != "content"}, ensure_ascii=False),
        "fetched_at": _now(),
        "parsed_at": _now(),
    }


# ---------------- 源：人民法院公告网 ----------------
def crawl_rmfygg(http, conn, keywords=None, limit=None, verbose=True):
    keywords = keywords or config.SEARCH_KEYWORDS
    total_seen = total_new = total_kept = total_dup = 0
    handled = set()

    for kw in keywords:
        started = _now()
        seen_this = new_this = 0
        if verbose:
            print(f"\n[公告网] 关键词[{kw}] 开始采集…")
        try:
            for raw in rmfygg.iter_keyword(http, kw):
                if limit and total_kept >= limit:
                    break
                rec_raw = rmfygg.to_record(raw)
                sid = rec_raw["source_id"]
                total_seen += 1
                seen_this += 1
                if sid in handled:
                    continue
                handled.add(sid)
                if config.REQUIRE_BOUNTY_SIGNAL and not parser_bounty.looks_like_bounty(rec_raw["content"]):
                    continue
                rec = build_record(rec_raw)
                if db.find_same_content(conn, rec["content_hash"], rec["source_id"]):
                    total_dup += 1
                    continue
                if db.upsert(conn, rec):
                    total_new += 1
                    new_this += 1
                total_kept += 1
                if verbose and total_kept % 25 == 0:
                    conn.commit()
                    print(f"    已处理 {total_kept} 条（新增 {total_new}）")
        except Exception as e:
            print(f"    ! 关键词[{kw}] 采集异常: {e}")
            db.log_crawl(conn, source="rmfygg", keyword=kw, started_at=started,
                         finished_at=_now(), total_seen=seen_this,
                         total_new=new_this, status=f"error: {e}")
            continue

        conn.commit()
        db.log_crawl(conn, source="rmfygg", keyword=kw, started_at=started,
                     finished_at=_now(), total_seen=seen_this,
                     total_new=new_this, status="ok")
        if verbose:
            print(f"[公告网][{kw}] 扫描 {seen_this} 条，新增 {new_this} 条")
        if limit and total_kept >= limit:
            break

    conn.commit()
    if verbose and total_dup:
        print(f"[公告网] 跳过正文重复 {total_dup} 条")
    return total_seen, total_kept, total_new, total_dup


# ---------------- 源：各地法院官网栏目 ----------------
def crawl_courts(http, conn, sites=None, limit=None, max_pages=None, verbose=True):
    sites = sites or config.COURT_SITES
    max_pages = config.COURT_MAX_PAGES if max_pages is None else max_pages
    total_seen = total_new = total_kept = total_dup = 0

    print(f"\n[法院官网] 共 {len(sites)} 个栏目待抓")
    for site in sites:
        started = _now()
        seen_this = new_this = 0
        if verbose:
            print(f"\n[法院官网] {site['court']}")
        try:
            for raw in court_sites.iter_site(http, site, max_pages=max_pages,
                                             verbose=verbose):
                seen_this += 1
                total_seen += 1
                if limit and total_kept >= limit:
                    break
                if config.REQUIRE_BOUNTY_SIGNAL and not parser_bounty.looks_like_bounty(raw["content"]):
                    continue
                rec = build_record(raw)
                if db.find_same_content(conn, rec["content_hash"], rec["source_id"]):
                    total_dup += 1
                    continue
                if db.upsert(conn, rec):
                    total_new += 1
                    new_this += 1
                total_kept += 1
                if verbose and total_kept % 20 == 0:
                    conn.commit()
                    print(f"    已处理 {total_kept} 条（新增 {total_new}）")
        except Exception as e:
            print(f"    ! {site['court']} 采集异常: {e}")
            db.log_crawl(conn, source="court", keyword=site["court"],
                         started_at=started, finished_at=_now(),
                         total_seen=seen_this, total_new=new_this,
                         status=f"error: {e}")
            conn.commit()
            continue

        conn.commit()
        db.log_crawl(conn, source="court", keyword=site["court"],
                     started_at=started, finished_at=_now(),
                     total_seen=seen_this, total_new=new_this, status="ok")
        if verbose:
            print(f"  -> 扫描 {seen_this} 条，入库 {new_this} 条新增")
        if limit and total_kept >= limit:
            print(f"已达条数上限 {limit}，停止。")
            break

    conn.commit()
    if verbose and total_dup:
        print(f"[法院官网] 跳过正文重复 {total_dup} 条")
    return total_seen, total_kept, total_new, total_dup


# ---------------- 总入口 ----------------
def crawl(keywords=None, limit=None, sources=None, verbose=True):
    """sources: None=全部；可传 ["rmfygg"], ["court"] 或两者。"""
    sources = sources or ["rmfygg", "court"]
    http = Http()
    conn = db.connect()
    db.init_db(conn)

    seen = kept = new = dup = 0
    if "rmfygg" in sources:
        s, k, n, d = crawl_rmfygg(http, conn, keywords=keywords, limit=limit,
                                  verbose=verbose)
        seen, kept, new, dup = seen + s, kept + k, new + n, dup + d
    if "court" in sources:
        s, k, n, d = crawl_courts(http, conn, limit=limit, verbose=verbose)
        seen, kept, new, dup = seen + s, kept + k, new + n, dup + d

    st = db.stats(conn)
    conn.close()
    if verbose:
        print("\n" + "=" * 56)
        print(f"本次扫描 {seen} 条，入库 {kept} 条，新增 {new} 条，"
              f"跳过重复 {dup} 条")
        print(f"数据库共 {st['n']} 条 | 有标的 {st['has_amount']} | "
              f"有比例 {st['has_ratio']} | 可估算奖励 {st['has_est']}")
        print("=" * 56)
    return new


def reparse_all(verbose=True):
    """重新解析库内全部记录（改进正则后使用），保留原 source。"""
    conn = db.connect()
    db.init_db(conn)
    rows = conn.execute(
        "SELECT source, source_id, url, court, province, city, debtor, "
        "publish_date, content FROM notices").fetchall()
    n = 0
    for r in rows:
        raw = {
            "source": r["source"], "source_id": r["source_id"], "url": r["url"],
            "court": r["court"], "debtor": r["debtor"],
            "publish_date": r["publish_date"], "content": r["content"],
            "province": r["province"], "city": r["city"],
        }
        rec = build_record(raw, province_map=not r["province"])
        db.upsert(conn, rec)
        n += 1
    conn.commit()
    conn.close()
    if verbose:
        print(f"重新解析完成：{n} 条")
    return n
