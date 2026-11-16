# -*- coding: utf-8 -*-
"""数据源：人民法院公告网 (rmfygg.court.gov.cn)

逆向自站内 DataTables 接口 initNoticeList（Liferay serveResource）。
返回 JSON：iTotalRecords / data[]，每条含 noticeContent 全文。

两个看板共用：悬赏按关键词检（court_name 空），开庭按「公告类型 × 法院」
检（notice_type_val + court_name）。这里只做「取数」，不做任何看板判断。
"""
import json

from . import config

BASE = "https://rmfygg.court.gov.cn"
LIST_URL = (BASE + "/web/rmfyportal/noticeinfo"
            "?p_p_id=noticelist_WAR_rmfynoticeListportlet&p_p_lifecycle=2"
            "&p_p_state=normal&p_p_mode=view&p_p_resource_id=initNoticeList"
            "&p_p_cacheability=cacheLevelPage&p_p_col_id=column-1&p_p_col_count=1")
DETAIL_URL = BASE + "/web/rmfyportal/noticedetail"

P = "_noticelist_WAR_rmfynoticeListportlet_"
SOURCE = "rmfygg"


def _ao_data(start, length):
    """DataTables 的 aoData。

    2026-11 站点接口收紧：完整的 DataTables 参数包（iColumns / mDataProp_i /
    mDataSort_i / bSortable_i / bSearchable_i / sColumns 全套）现在会**静默
    返回 iTotalRecords=0**，看上去像”没数据”，实际是服务端拒绝了这个组合。
    最小可用的只有三个键：sEcho / iDisplayStart / iDisplayLength。

    另外两点已验证：
      - 缺 User-Agent 直接 403
      - 同一个 IP 短时间内请求多了，会先变成「全部返回 0」，再变成 403，
        冷却后恢复，所以采集必须低频（见 court_crawler 的 interval）
    """
    return [
        {"name": "sEcho", "value": 1},
        {"name": "iDisplayStart", "value": start},
        {"name": "iDisplayLength", "value": length},
    ]


def fetch_page(http, keyword, start=0, length=None, notice_type="",
               notice_type_val="", court_name=""):
    length = length or config.PAGE_SIZE
    data = {
        P + "content": keyword,
        P + "courtParam": court_name,
        P + "flag": "court" if court_name else "content",
        P + "noticeType": notice_type,          # 类型代码，如 66
        P + "noticeTypeVal": notice_type_val,   # 类型中文名，如 开庭传票
        P + "sourceTypeVal": "全部",
        P + "aoData": json.dumps(_ao_data(start, length), ensure_ascii=False),
    }
    raw = http.request(LIST_URL + "&_=%d" % start, data=data, headers={
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"{BASE}/web/rmfyportal/noticeinfo",
        "Origin": BASE,
    })
    return json.loads(raw)


def iter_keyword(http, keyword, max_pages=200, notice_type=None,
                 notice_type_val="", court_name="", verbose=True):
    """逐页产出公告 dict（原始字段）。

    court_name 走服务端 courtParam，支持「罗湖区」这类简称模糊匹配，
    用于把一个法院的公告一次性拉全（地区专项按法院采集走这条路）。
    """
    notice_type = "" if notice_type is None else notice_type
    total = None
    start = 0
    pages = 0
    while pages < max_pages:
        try:
            j = fetch_page(http, keyword, start=start, notice_type=notice_type,
                           notice_type_val=notice_type_val,
                           court_name=court_name)
        except Exception as e:
            print(f"    ! 关键词[{keyword}] offset={start} 失败: {e}")
            break
        total = j.get("iTotalRecords", 0)
        rows = j.get("data") or []
        if not rows:
            break
        for r in rows:
            yield r
        pages += 1
        start += config.PAGE_SIZE
        if total and start >= total:
            break
    if total is not None and verbose:
        print(f"    关键词[{keyword}] 共 {total} 条，抓取 {pages} 页")


def to_record(raw):
    """把源站原始行转成统一的原始记录（不含解析结果）。"""
    code = raw.get("noticeCode") or raw.get("uuid")
    return {
        "source": SOURCE,
        "source_id": str(code),
        "url": f"{DETAIL_URL}?paramStr={raw.get('uuid','')}",
        "court": (raw.get("court") or "").strip(),
        "debtor": (raw.get("tosendPeople") or "").strip(),
        "publish_date": (raw.get("publishDate") or "").strip(),
        "notice_type": (raw.get("noticeType") or "").strip(),
        "content": (raw.get("noticeContent") or "").strip(),
        "raw_json": raw,
    }
