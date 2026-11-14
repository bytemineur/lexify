# -*- coding: utf-8 -*-
"""开庭公告看板：采集 -> 解析 -> 入库 主流程。

数据源：
  rmfygg_kt       人民法院公告网（开庭传票 / 起诉状副本及开庭传票）
  court_schedule  各地法院官网的开庭排期表

共享基础设施在 collectors/_shared（http / regions / courts / rmfygg）。
本模块只写 hearings 表，不碰 notices。
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
import parser as parser_hearing
from _shared import regions
from _shared.http import Http
import court_schedule
import rmfygg_kt


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _content_hash(text):
    norm = "".join((text or "").split())
    return hashlib.md5(norm.encode("utf-8")).hexdigest()


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
        if db.upsert(conn, rec):
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
        err_kt = None
        try:
            for raw in rmfygg_kt.iter_all(http, keywords=keywords,
                                          max_pages=max_pages, verbose=verbose):
                if not _take(raw, "rmfygg_kt"):
                    break
        except Exception as e:
            err_kt = str(e)
            print(f"  ! 公告网开庭源异常: {e}")
        conn.commit()
        db.log_crawl(conn, source="rmfygg_kt", keyword="hearing",
                     started_at=started, finished_at=_now(),
                     total_seen=seen - n_before, total_new=new,
                     status=("error: " + err_kt) if err_kt else "ok")

    if "court_schedule" in sources:
        if verbose:
            print("\n[开庭] 法院官网开庭排期表")
        started = _now()
        n_before = seen
        err_sched = None
        try:
            for raw in court_schedule.iter_all(http, verbose=verbose):
                if not _take(raw, "court_schedule"):
                    break
        except Exception as e:
            err_sched = str(e)
            print(f"  ! 排期表源异常: {e}")
        conn.commit()
        db.log_crawl(conn, source="court_schedule", keyword="schedule",
                     started_at=started, finished_at=_now(),
                     total_seen=seen - n_before, total_new=new,
                     status=("error: " + err_sched) if err_sched else "ok")

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
    rows = conn.execute("SELECT * FROM hearings").fetchall()
    n = 0
    for r in rows:
        try:
            rj = json.loads(r["raw_json"] or "{}")
        except (ValueError, TypeError):
            rj = {}
        raw = {
            "source": r["source"], "source_id": r["source_id"], "url": r["url"],
            "court": r["court"], "province": r["province"], "city": r["city"],
            "publish_date": r["publish_date"], "content": r["content"],
            "notice_type": r["notice_type"],
            "debtor": rj.get("tosendPeople") or rj.get("debtor"),
        }
        if r["source"] == court_schedule.SOURCE:
            raw.update({"case_no": r["case_no"], "cause": r["cause"],
                        "hearing_at": r["hearing_at"], "courtroom": r["courtroom"]})
        db.upsert(conn, build_hearing_record(raw))
        n += 1
    conn.commit()
    conn.close()
    if verbose:
        print(f"开庭重新解析完成：{n} 条")
    return n
