# -*- coding: utf-8 -*-
"""【劳动争议】看板的数据采集：招股说明书里「未缴 / 未足额缴纳社保公积金」的披露。

为什么是这条路：巨潮的检索只索引标题（搜「住房公积金」全市场只有 7 条），见微那类
正文检索要付费 Key。所以改成「按标题拉招股书 → 本地取文 → 正则定位社保段落」，
只抓真正需要的那几句，不落全文。

用法：:

    python -m collectors.labor --days 400 --workers 6

产物写到 ``collectors/labor/out/labor_YYYYMMDD.json``，再用
``python scripts/manage.py add <file> --kind labor`` 入库。
"""

import io
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".."))

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
PYLIBS = os.path.join(ROOT, "..", ".tmp", "pylibs")   # 本地 pypdf（没装进 requirements）

API = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
PDF_HOST = "http://static.cninfo.com.cn/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

# 标题里出现这些词的，不是「本公司招股说明书」本体
TITLE_NOISE = ("提示性公告", "摘要", "H 股", "H股", "责任保险", "英文", "附录", "更正",
               "关于", "公告", "上市公告书", "发行保荐书", "法律意见书")

# 判定「未缴」的句式：必须在同一段落里同时出现主体词和情形词
PAID_RE = re.compile(r"(未(?:为|给|能|及时)?[^。；\n]{0,12}?(?:缴纳|缴付|参保)|应缴未缴|欠缴|补缴)")
SOCIAL_RE = re.compile(r"(社会保险|社保)")
FUND_RE = re.compile(r"(住房公积金|公积金)")
NUM_RE = re.compile(r"\d[\d,]{2,7}")

SENT_SPLIT = re.compile(r"(?<=[。；])")
CLEAN_RE = re.compile(r"[ \t\u3000]+")


def _board_of(code):
    code = (code or "").strip()
    if code.startswith(("600", "601", "603", "605")):
        return "沪市主板"
    if code.startswith(("688", "689")):
        return "科创板"
    if code.startswith(("000", "001", "002", "003")):
        return "深市主板"
    if code.startswith(("300", "301")):
        return "创业板"
    if code.startswith(("8", "4", "920")):
        return "北交所"
    return "其他"


def _query(page, size, se_date, key="招股说明书"):
    data = {
        "pageNum": page, "pageSize": size, "column": "szse", "tabName": "fulltext",
        "plate": "", "stock": "", "searchkey": key, "secdate": "", "tdate": "",
        "category": "", "trade": "", "seDate": se_date, "sortName": "",
        "sortType": "", "isHLtitle": "true",
    }
    req = urllib.request.Request(
        API, data=urllib.parse.urlencode(data).encode("utf-8"),
        headers={
            "User-Agent": UA,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Referer": "http://www.cninfo.com.cn/new/fulltextSearch",
            "Origin": "http://www.cninfo.com.cn",
            "X-Requested-With": "XMLHttpRequest",
        })
    with urllib.request.urlopen(req, timeout=40, context=CTX) as r:
        return json.loads(r.read().decode("utf-8"))


def list_prospectuses(days=400, max_pages=0, sleep=0.3):
    """按日期窗口列出招股说明书（标题检索，命中标题含「招股说明书」的公告）。"""
    end = datetime.now()
    start = end - timedelta(days=days)
    se_date = "%s~%s" % (start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    first = _query(1, 30, se_date)
    total_pages = int(first.get("totalpages") or 0)
    if max_pages:
        total_pages = min(total_pages, max_pages)
    seen, out = set(), []
    pages = [first]
    for p in range(2, total_pages + 1):
        time.sleep(sleep)
        pages.append(_query(p, 30, se_date))
    for data in pages:
        for a in data.get("announcements") or []:
            title = re.sub(r"</?em>", "", a.get("announcementTitle") or "").strip()
            if "招股说明书" not in title:
                continue
            if any(bad in title for bad in TITLE_NOISE):
                continue
            aid = a.get("announcementId")
            if not aid or aid in seen:
                continue
            seen.add(aid)
            out.append({
                "source": "cninfo",
                "source_id": str(aid),
                "sec_code": (a.get("secCode") or "").strip(),
                "sec_name": (a.get("secName") or "").strip(),
                "board": _board_of(a.get("secCode")),
                "doc_type": "招股说明书",
                "title": title,
                "ann_date": datetime.fromtimestamp(
                    (a.get("announcementTime") or 0) / 1000.0).strftime("%Y-%m-%d"),
                "url": PDF_HOST + (a.get("adjunctUrl") or "")
                if a.get("adjunctUrl") else "",
            })
    return out


def _clean(text):
    text = CLEAN_RE.sub(" ", text)
    return text.replace("\n", " ").strip()


def _sentences(text):
    for chunk in SENT_SPLIT.split(text):
        chunk = _clean(chunk)
        if len(chunk) >= 12:
            yield chunk


def _score_sentence(sent):
    """给候选句打分：直接说「存在未缴」的比「如果被要求就补偿」的更有信息量。"""
    score = 0
    if re.search(r"(存在|出现)[^。；]{0,20}(未|欠)(?:全员|部分|依法)?[^。；]{0,10}(缴|参保)", sent):
        score += 5
    if re.search(r"未(?:全员|足额|依法|及时|为)[^。；]{0,12}(缴纳|参保)", sent):
        score += 4
    if "应缴未缴" in sent or "欠缴" in sent:
        score += 4
    if re.search(r"\d+(?:\.\d+)?\s*(?:万)?元", sent):
        score += 2
    if re.search(r"\d+\s*人", sent):
        score += 2
    if "承诺" in sent or "补偿" in sent:
        score -= 2
    if "不存在" in sent and "未" not in sent.replace("不存在", ""):
        score -= 3
    return score


def _first_int(flat, pattern):
    m = re.search(pattern, flat)
    if not m:
        return 0
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return 0


def scan_document(item, max_pages=520):
    """下载 + 取文 + 定位社保段落。返回 (row 或 None)。"""
    if not item.get("url"):
        return None
    try:
        from pypdf import PdfReader
    except ImportError:
        sys.path.insert(0, PYLIBS)
        from pypdf import PdfReader

    try:
        req = urllib.request.Request(
            item["url"], headers={"User-Agent": UA,
                                  "Referer": "http://www.cninfo.com.cn/"})
        raw = urllib.request.urlopen(req, timeout=180, context=CTX).read()
        reader = PdfReader(io.BytesIO(raw))
        total_pages = len(reader.pages)
        limit = min(total_pages, max_pages)
    except Exception as e:
        return {"_error": "%s: %s" % (item.get("sec_code"), e)}

    cands = []            # (score, page_no, sentence)
    kinds, promise_any = set(), 0
    amount = None
    headcount = paid = 0
    for i in range(limit):
        try:
            text = reader.pages[i].extract_text() or ""
        except Exception:
            continue
        if not text:
            continue
        if not ("社会保险" in text or "社保" in text or "公积金" in text):
            continue
        spaced = re.sub(r"\s+", " ", text)
        if not headcount:
            headcount = _first_int(spaced, r"员工总\s*(?:人数|数)\s*（?人）?\s*([\d,]+)")
        if not paid:
            paid = _first_int(spaced, r"(?:社会保险|社保)缴纳人\s*(?:数|人数)\s*（?人）?\s*([\d,]+)")
        if "承诺" in text:
            promise_any = 1
        for sent in _sentences(text):
            if not PAID_RE.search(sent):
                continue
            if not (SOCIAL_RE.search(sent) or FUND_RE.search(sent)):
                continue
            if re.search(r"(均已|已经|依法|按期|足额)缴纳", sent) and "未" not in sent:
                continue
            score = _score_sentence(sent)
            if score <= 0:
                continue
            cands.append((score, i + 1, sent))
            if SOCIAL_RE.search(sent):
                kinds.add("社保")
            if FUND_RE.search(sent):
                kinds.add("公积金")
            m = re.search(r"补缴[^。；]{0,40}?(\d[\d,.]*)\s*万元", sent)
            if m and amount is None:
                try:
                    amount = float(m.group(1).replace(",", ""))
                except ValueError:
                    pass

    if not cands:
        return None

    cands.sort(key=lambda c: (-c[0], c[1]))
    top = cands[0]
    picked = []
    for _score, _pg, sent in cands:
        if sent in picked:
            continue
        picked.append(sent)
        if len(picked) >= 3:
            break

    row = dict(item)
    ratio = round(paid * 100.0 / headcount, 1) if headcount and paid else 0
    if headcount and paid:
        severity = "high" if ratio < 85 else ("mid" if ratio < 95 else "low")
    else:
        severity = "mid" if any("未" in s for s in picked) else "low"
    row.update({
        "page_no": top[1],
        "unpaid_kind": "+".join(k for k in ("社保", "公积金") if k in kinds),
        "snippet": picked[0][:300],
        "snippets": json.dumps(picked, ensure_ascii=False),
        "promise": promise_any,
        "makegood_amount": amount or 0,
        "headcount": headcount or 0,
        "paid_count": paid or 0,
        "paid_ratio": ratio,
        "severity": severity,
        "pages": total_pages,
    })
    return row


def annotate(rows):
    """同一家公司多条公告：按公告日排序，第一条记为「首次披露」。"""
    rows.sort(key=lambda r: (r.get("sec_code") or "", r.get("ann_date") or ""))
    seen = {}
    for r in rows:
        code = r.get("sec_code") or ""
        n = seen.get(code, 0) + 1
        seen[code] = n
        r["event_no"] = n
    rows.sort(key=lambda r: (r.get("ann_date") or "", r.get("sec_code") or ""), reverse=True)
    return rows


def main():
    import argparse
    ap = argparse.ArgumentParser(description="招股书社保公积金披露 → 结构化")
    ap.add_argument("--days", type=int, default=400, help="公告日期窗口（天）")
    ap.add_argument("--max-pages", type=int, default=0, help="列表最多翻几页（0=全部）")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 份（调试用）")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--sleep", type=float, default=0.3)
    args = ap.parse_args()

    t0 = time.time()
    items = list_prospectuses(days=args.days, max_pages=args.max_pages, sleep=args.sleep)
    print("招股说明书 %d 份（窗口 %d 天，%.1fs）" % (len(items), args.days, time.time() - t0))
    if args.limit:
        items = items[:args.limit]

    rows, errors = [], 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(scan_document, it): it for it in items}
        done = 0
        for fut in as_completed(futs):
            done += 1
            try:
                r = fut.result()
            except Exception as e:
                errors += 1
                r = {"_error": str(e)}
            if r and r.get("_error"):
                errors += 1
            elif r:
                rows.append(r)
            if done % 20 == 0 or done == len(items):
                print("  进度 %d/%d，命中 %d，失败 %d" % (done, len(items), len(rows), errors))

    rows = annotate(rows)
    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, "labor_%s.json" % datetime.now().strftime("%Y%m%d"))
    with io.open(out, "w", encoding="utf-8", newline="") as f:
        f.write(json.dumps(rows, ensure_ascii=False, indent=1))
    print("命中 %d 家（%d 条），失败 %d，写入 %s（%.1fs）" % (
        len(set(r["sec_code"] for r in rows)), len(rows), errors, out, time.time() - t0))


if __name__ == "__main__":
    main()
