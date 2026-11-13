# -*- coding: utf-8 -*-
"""采集 -> 解析 -> 入库 主流程。

支持两类数据源：
  rmfygg      人民法院公告网（全国范围，关键词检索）
  court       各地法院官网公告栏目（按法院逐站抓取）
"""
import hashlib
import json
import os
import sys
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for p in (_ROOT, _HERE, os.path.join(_ROOT, "ref")):
    if p not in sys.path:
        sys.path.insert(0, p)

import config
import db
import parser_bounty
import parser_hearing
import regions
from http_client import Http
from sources import court_schedule, court_sites, rmfygg, rmfygg_kt


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

    # 法院名识别不出时，尝试从正文里的地址线索推断
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
        # province 已有值时不再让 regions 覆盖（法院栏目源自带准确省市）
        rec = build_record(raw, province_map=not r["province"])
        db.upsert(conn, rec)
        n += 1
    conn.commit()
    conn.close()
    if verbose:
        print(f"重新解析完成：{n} 条")
    return n


# ============================================================
# 开庭公告
# ============================================================
def build_hearing_record(raw):
    """原始记录 -> hearings 行。

    两种源形态不同：
      rmfygg_kt       只有正文，全部字段靠解析
      court_schedule  表格已给出案号/案由/开庭时间/法庭，正文是拼出来的
    这里让源自带的字段覆盖解析结果，避免二次解析失真。
    """
    text = raw.get("content") or ""
    pub = raw.get("publish_date")
    info = parser_hearing.parse_hearing(
        text, publish_date=pub,
        send_to=raw.get("debtor") or raw.get("send_to") or raw.get("defendant"),
        court=raw.get("court"))

    if raw.get("case_no"):
        info["case_no"] = raw["case_no"]
    if raw.get("cause"):
        info["cause"] = raw["cause"]
        info["cause_group"] = parser_hearing.cause_group(raw["cause"])
        info["is_commercial"] = 1 if info["cause_group"] not in (None, "其他民商") else 0
    if raw.get("hearing_at"):
        info["hearing_at"] = raw["hearing_at"]
        info["hearing_date"] = raw["hearing_at"][:10]
        info["time_estimated"] = 0
    if raw.get("courtroom"):
        info["courtroom"] = raw["courtroom"]

    court = (raw.get("court") or "").strip()
    prov, city = raw.get("province"), raw.get("city")
    if not prov and court:
        prov, city, _ = regions.resolve(court)
    # 法院名识别不出时用案号省级代字兜底（"（2026）云26民终…" -> 云南省）
    if not prov and info.get("case_no"):
        prov = parser_hearing.province_from_case_no(info["case_no"])

    if not info.get("hearing_date") and info.get("hearing_at"):
        info["hearing_date"] = info["hearing_at"][:10]

    return {
        "source": raw.get("source") or "unknown",
        "source_id": str(raw.get("source_id") or ""),
        "url": raw.get("url"),
        "court": court,
        "province": prov,
        "city": city,
        "case_no": info.get("case_no"),
        "case_kind": info.get("case_kind"),
        "cause": info.get("cause"),
        "cause_group": info.get("cause_group"),
        "is_commercial": info.get("is_commercial") or 0,
        "plaintiff": info.get("plaintiff"),
        "defendant": info.get("defendant"),
        "defendant_type": info.get("defendant_type"),
        "company_count": info.get("company_count") or 0,
        "person_count": info.get("person_count") or 0,
        "hearing_at": info.get("hearing_at"),
        "hearing_date": info.get("hearing_date"),
        "time_estimated": info.get("time_estimated") or 0,
        "courtroom": info.get("courtroom"),
        "claim_amount": info.get("claim_amount"),
        "amount_basis": info.get("amount_basis"),
        "notice_type": raw.get("notice_type") or "",
        "publish_date": pub,
        "content": text,
        "content_hash": _content_hash(text),
        "parse_quality": info.get("parse_quality"),
        "raw_json": json.dumps(
            {k: v for k, v in raw.items() if k != "content"}, ensure_ascii=False),
        "fetched_at": _now(),
        "parsed_at": _now(),
    }


def _hearing_keep(rec, today=None):
    """入池判定：开庭日必须在未来，且公告不能太旧。"""
    from datetime import date
    today = today or date.today()
    hd = parser_hearing.parse_date(rec.get("hearing_date"))
    if hd and hd < today:
        return False
    if hd and config.HEARING_HORIZON_DAYS:
        if (hd - today).days > config.HEARING_HORIZON_DAYS:
            return False
    pd = parser_hearing.parse_date(rec.get("publish_date"))
    if pd and config.HEARING_PUBLISH_DAYS:
        if (today - pd).days > config.HEARING_PUBLISH_DAYS:
            return False
    return True


def crawl_hearings(conn=None, http=None, sources=None, keywords=None,
                   max_pages=None, limit=None, verbose=True):
    """采集开庭公告。sources: None=全部，或 ["rmfygg_kt"], ["court_schedule"]。"""
    sources = sources or ["rmfygg_kt", "court_schedule"]
    http = http or Http()
    own = conn is None
    conn = conn or db.connect()
    db.init_db(conn)

    seen = kept = new = dup = skipped = 0

    def _take(raw, tag):
        nonlocal seen, kept, new, dup, skipped
        seen += 1
        if limit and kept >= limit:
            return False
        rec = build_hearing_record(raw)
        if not _hearing_keep(rec):
            skipped += 1
            return True
        if db.hearing_exists_content(conn, rec["content_hash"], rec["source_id"]):
            dup += 1
            return True
        if db.upsert_hearing(conn, rec):
            new += 1
        kept += 1
        if verbose and kept % 40 == 0:
            conn.commit()
            print(f"    已处理 {kept} 条（新增 {new}）")
        return True

    if "rmfygg_kt" in sources:
        if verbose:
            print("\n[开庭] 人民法院公告网 · 开庭传票")
        started = _now()
        n_before = seen
        try:
            for raw in rmfygg_kt.iter_all(http, keywords=keywords,
                                          max_pages=max_pages, verbose=verbose):
                if not _take(raw, "rmfygg_kt"):
                    break
        except Exception as e:
            print(f"  ! 公告网开庭源异常: {e}")
        conn.commit()
        db.log_crawl(conn, source="rmfygg_kt", keyword="hearing",
                     started_at=started, finished_at=_now(),
                     total_seen=seen - n_before, total_new=new, status="ok")

    if "court_schedule" in sources:
        if verbose:
            print("\n[开庭] 法院官网开庭排期表")
        started = _now()
        n_before = seen
        try:
            for raw in court_schedule.iter_all(http, verbose=verbose):
                if not _take(raw, "court_schedule"):
                    break
        except Exception as e:
            print(f"  ! 排期表源异常: {e}")
        conn.commit()
        db.log_crawl(conn, source="court_schedule", keyword="schedule",
                     started_at=started, finished_at=_now(),
                     total_seen=seen - n_before,
                     total_new=new, status="ok")

    st = db.hearing_stats(conn)
    if own:
        conn.close()
    if verbose:
        print("\n" + "=" * 56)
        print(f"开庭采集：扫描 {seen} 条，入池 {kept} 条，新增 {new} 条，"
              f"跳过重复 {dup}，超期/过期 {skipped}")
        print(f"库内合计 {st['n']} 条 | 有开庭日 {st['has_date']} | "
              f"有案由 {st['has_cause']} | 有金额 {st['has_amount']} | "
              f"企业被告 {st['has_company']} | 商事 {st['commercial']}")
        print("=" * 56)
    return new


def reparse_hearings(verbose=True):
    """重解开庭库（改进案由词典/正则后使用）。"""
    conn = db.connect()
    db.init_db(conn)
    rows = conn.execute(
        "SELECT * FROM hearings").fetchall()
    n = 0
    for r in rows:
        try:
            rj = json.loads(r["raw_json"] or "{}")
        except (ValueError, TypeError):
            rj = {}
        raw = {
            "source": r["source"], "source_id": r["source_id"], "url": r["url"],
            "court": r["court"], "province": r["province"], "city": r["city"],
            "publish_date": r["publish_date"],
            "content": r["content"],
            "notice_type": r["notice_type"],
            "debtor": rj.get("tosendPeople") or rj.get("debtor"),
        }
        # 排期表源的结构化字段要保留，避免被正文重新解析覆盖
        if r["source"] == court_schedule.SOURCE:
            raw.update({"case_no": r["case_no"], "cause": r["cause"],
                        "hearing_at": r["hearing_at"],
                        "courtroom": r["courtroom"]})
        db.upsert_hearing(conn, build_hearing_record(raw))
        n += 1
    conn.commit()
    conn.close()
    if verbose:
        print(f"开庭重新解析完成：{n} 条")
    return n
