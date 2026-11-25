# -*- coding: utf-8 -*-
"""证券虚假陈述：从巨潮资讯（cninfo）抓证监会系统的行政处罚 / 立案公告。

为什么从这里抓：投资者索赔（证券虚假陈述责任纠纷）的起算点，是上市公司公告
「被证监会立案」「收到行政处罚事先告知书」「收到行政处罚决定书」这些时点。
这些公告都发在巨潮资讯，检索接口是公开的，不依赖任何付费数据源。

用法：
    python collectors/falseclaim/fetch.py              # 抓默认范围，写到 out/
    python collectors/falseclaim/fetch.py --days 3650  # 只抓最近十年
    python collectors/falseclaim/fetch.py --pages 5    # 每个关键词最多 5 页（调试用）

输出：collectors/falseclaim/out/falseclaim_<日期>.json
       每行一条公告事件（不是每家公司一条），字段见 build_rows()。
"""
import argparse
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

API = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
REFERER = "http://www.cninfo.com.cn/new/fulltextSearch"

# 全文检索的四个入口词。cninfo 的 searchkey 是全文命中，不是标题命中，
# 所以「行政处罚决定书」能把正文里提到决定书的公告都捞出来。
SEARCH_KEYS = ("行政处罚决定书", "行政处罚事先告知书", "立案告知书", "虚假陈述")

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "out")

# 标题里的阶段判定（顺序很重要：立案的通知书也带「告知书」，必须先判立案）
STAGE_RULES = (
    ("decision", ("行政处罚决定书", "处罚决定书", "行政处罚决定")),
    ("investigation", ("立案告知书", "立案调查", "调查通知书", "被立案")),
    ("notice", ("事先告知书", "行政处罚事先告知", "处罚告知书")),
    ("measure", ("行政监管措施决定书", "监管函", "警示函")),
    ("litigation", ("责任纠纷", "赔偿纠纷", "纠纷", "民事诉讼", "判决", "裁决",
                    "调解", "索赔", "起诉")),
)

# 第三方文书（承销商受托管理报告、评级机构关注公告、律所核查意见…）不是公司自己的公告，
# 留在看板里只会制造噪音，直接排除。
THIRD_PARTY_RE = re.compile(
    r"(受托管理事务报告|受托管理人|关注公告|信用评级|评级报告|评级机构|"
    r"核查意见|法律意见书|律师事务所|会计师事务所|保荐机构|保荐人|"
    r"专项核查|核查报告|独立财务顾问)")

# 处罚机关：只留证监会系统，地方环保/税务/海关的处罚和证券虚假陈述无关
CSRC_RE = re.compile(r"(中国证券监督管理委员会|证监会|证监局|证券监督管理委员会)")
OTHER_ORG_RE = re.compile(
    r"(生态环境局|环境保护局|税务局|海关|市场监督管理局|应急管理局|"
    r"自然资源局|住房和城乡建设局|交通运输局|卫生健康|消防救援|公安)")

# 虚假陈述的法定类型（《证券法》第 78 条 + 司法解释的口径）
TYPE_RULES = (
    ("虚假记载", ("虚假记载", "虚假陈述", "财务造假", "虚增", "虚构")),
    ("重大遗漏", ("重大遗漏", "未按规定披露", "未披露", "遗漏")),
    ("误导性陈述", ("误导性陈述", "误导")),
)

SCOPE_RULES = (
    ("subsidiary", ("子公司", "控股子公司", "全资子公司", "参股公司")),
    ("person", ("董事", "监事", "高级管理人员", "财务总监", "总经理", "实际控制人",
                "控股股东", "股东", "独立董事", "董监高")),
)

CODE_BOARD = (
    (("600", "601", "603", "605"), "沪市主板"),
    (("688", "689"), "科创板"),
    (("000", "001", "002", "003"), "深市主板"),
    (("300", "301"), "创业板"),
    (("8", "4", "920"), "北交所"),
)


def board_of(code):
    code = (code or "").strip()
    for prefix, name in CODE_BOARD:
        if code.startswith(prefix):
            return name
    return ""


def clean_title(t):
    """去掉 cninfo 的 <em> 高亮标签与多余空白。"""
    t = re.sub(r"</?em>", "", t or "")
    return re.sub(r"\s+", " ", t).strip()


def stage_of(title):
    for stage, keys in STAGE_RULES:
        for k in keys:
            if k in title:
                return stage
    return ""


def types_of(title):
    out = []
    for name, keys in TYPE_RULES:
        if any(k in title for k in keys) and name not in out:
            out.append(name)
    return out


def scope_of(title):
    for scope, keys in SCOPE_RULES:
        if any(k in title for k in keys):
            return scope
    return "company"


def org_of(title):
    """从标题里抠出处罚机关，抠不到就按有没有「证监局」兜底。"""
    m = re.search(r"(中国证券监督管理委员会[^《》]{0,12}?(?:监管局|证监局)?(?:[\u4e00-\u9fa5]{2,6}监管局)?)", title)
    if m:
        return m.group(1).strip()
    if "证监局" in title:
        m2 = re.search(r"([\u4e00-\u9fa5]{2,4}证监局)", title)
        return m2.group(1) if m2 else "证监局"
    if "证监会" in title:
        return "中国证监会"
    if "证券监督管理委员会" in title:
        return "中国证券监督管理委员会"
    return ""


def is_claimable(title):
    """初筛：只留可能构成证券虚假陈述的公告。

    - 必须落在证监会系统；
    - 标题含虚假陈述三类情形之一，或属于没收违法所得/罚款类的处罚决定；
    - 排除子公司/董监高为主体的公告（不是上市公司自己的责任，索赔对象不同）。
    """
    if OTHER_ORG_RE.search(title) and not CSRC_RE.search(title):
        return False
    if THIRD_PARTY_RE.search(title):
        return False
    if not CSRC_RE.search(title) and "虚假陈述" not in title:
        return False
    if scope_of(title) in ("subsidiary", "person"):
        return False
    if types_of(title):
        return True
    # 标题只写「行政处罚决定书」的：证监会给上市公司的处罚绝大多数是信息披露违法
    return bool(stage_of(title))


def request_page(searchkey, page, size, se_date, retries=3):
    form = {
        "pageNum": str(page), "pageSize": str(size), "column": "szse",
        "tabName": "fulltext", "plate": "", "stock": "", "searchkey": searchkey,
        "secdate": "", "tdate": "", "category": "", "trade": "", "seDate": se_date,
        "sortName": "", "sortType": "", "isHLtitle": "true",
    }
    data = urllib.parse.urlencode(form).encode()
    headers = {
        "User-Agent": UA,
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": REFERER,
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "http://www.cninfo.com.cn",
    }
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(API, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError("cninfo 请求失败：%r" % (last,))


def build_rows(args):
    se_date = args.sedate or ""
    rows = {}
    for key in SEARCH_KEYS:
        page = 1
        total_pages = 1
        while page <= total_pages:
            if args.pages and page > args.pages:
                break
            obj = request_page(key, page, args.page_size, se_date)
            total_pages = int(obj.get("totalpages") or 1)
            anns = obj.get("announcements") or []
            if not anns:
                break
            for a in anns:
                aid = str(a.get("announcementId") or "")
                title = clean_title(a.get("announcementTitle"))
                if not aid or not title:
                    continue
                if not is_claimable(title):
                    continue
                # 同一公告可能被多个关键词命中，用 announcementId 去重
                if aid in rows:
                    continue
                ts = a.get("announcementTime")
                try:
                    day = datetime.fromtimestamp(int(ts) / 1000).strftime("%Y-%m-%d")
                except Exception:  # noqa: BLE001
                    day = ""
                rows[aid] = {
                    "source": "cninfo",
                    "source_id": aid,
                    "sec_code": (a.get("secCode") or "").strip(),
                    "sec_name": (a.get("secName") or "").strip(),
                    "board": board_of(a.get("secCode")),
                    "stage": stage_of(title),
                    "scope": scope_of(title),
                    "org": org_of(title),
                    "types": json.dumps(types_of(title), ensure_ascii=False),
                    "title": title,
                    "ann_date": day,
                    "first_date": "",
                    "event_no": 1,
                    "url": ("http://static.cninfo.com.cn/" + str(a.get("adjunctUrl") or "")).strip(),
                    "hit_key": key,
                }
            page += 1
            time.sleep(args.sleep)
            if page % 10 == 0:
                print("  %s 第 %d/%d 页，累计 %d 条" % (key, page, total_pages, len(rows)),
                      flush=True)
    return list(rows.values())


def annotate(rows):
    """按公司补两条派生信息：首次事件日、事件序号。

    首次事件日 ≈ 揭露日（最早被市场知道的时间点），是索赔区间最常见的起点口径。
    """
    by_company = {}
    for r in rows:
        by_company.setdefault(r["sec_code"], []).append(r)
    for code, items in by_company.items():
        items.sort(key=lambda r: r.get("ann_date") or "")
        first = items[0].get("ann_date") or ""
        for i, r in enumerate(items):
            r["first_date"] = first
            r["event_no"] = i + 1
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description="抓 cninfo 上的证监会处罚公告（证券虚假陈述线索）")
    ap.add_argument("--page-size", type=int, default=30)
    ap.add_argument("--pages", type=int, default=0, help="每个关键词最多抓几页，0 = 全部")
    ap.add_argument("--days", type=int, default=0,
                    help="只抓最近 N 天，0 = 不限（cninfo 最早到 2010 年前后）")
    ap.add_argument("--sleep", type=float, default=0.35, help="翻页间隔秒数")
    args = ap.parse_args(argv)

    if args.days > 0:
        start = (datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%d")
        end = datetime.now().strftime("%Y-%m-%d")
        args.sedate = "%s~%s" % (start, end)
    else:
        args.sedate = ""

    t0 = time.time()
    rows = annotate(build_rows(args))
    rows.sort(key=lambda r: (r.get("ann_date") or "", r.get("sec_code") or ""), reverse=True)

    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "falseclaim_%s.json" % datetime.now().strftime("%Y%m%d"))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)

    print("抓到 %d 条（%.1fs）→ %s" % (len(rows), time.time() - t0, path))
    by_stage = {}
    for r in rows:
        by_stage[r["stage"] or "other"] = by_stage.get(r["stage"] or "other", 0) + 1
    print("阶段分布：", by_stage)
    return 0


if __name__ == "__main__":
    sys.exit(main())
