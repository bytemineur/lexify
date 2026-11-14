# -*- coding: utf-8 -*-
"""数据源：法院官网直接发布的「开庭排期表」。

部分法院把全周排期做成一张静态表格挂在网上，列固定为：
    序号 | 审理法院 | 开庭时间 | 法庭 | 案号 | 案由
相比公告网的送达公告，这类排期表没有当事人信息，但案由、开庭时间、
法庭都是法院自己录的，准确度最高，且都是未来几天的庭。

站点配置见 config.SCHEDULE_SITES，新增法院只要确认它的排期页也是
这种「一行一案」的表格即可。
"""
import re

import config

SOURCE = "court_schedule"

TAG_RE = re.compile(r"<[^>]+>")
ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)
TIME_RE = re.compile(r"(20\d{2})\s*(\d{2})\s*(\d{2})\s*(\d{1,2})[:：](\d{2})")
TIME2_RE = re.compile(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\s*(\d{1,2})[:：](\d{2})")
CASE_RE = re.compile(r"[（(]?\s*20\d{2}\s*[）)]?\s*[\u4e00-\u9fa5]{1,2}\d{2,6}"
                     r"[\u4e00-\u9fa5]{0,4}\d+\s*号")


def _clean(s):
    t = TAG_RE.sub("", s or "")
    t = (t.replace("&nbsp;", " ").replace("&amp;", "&")
          .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))
    return re.sub(r"\s+", " ", t).strip()


def _parse_time(s):
    m = TIME_RE.search(s or "") or TIME2_RE.search(s or "")
    if not m:
        return None
    try:
        y, mo, d, hh, mi = (int(x) for x in m.groups())
        if not (1 <= mo <= 12 and 1 <= d <= 31 and hh <= 23 and mi <= 59):
            return None
        return "%04d-%02d-%02d %02d:%02d" % (y, mo, d, hh, mi)
    except ValueError:
        return None


def parse_table(html, site):
    """解析「序号/法院/时间/法庭/案号/案由」六列表格。"""
    out = []
    for tr in ROW_RE.findall(html):
        cells = [_clean(c) for c in CELL_RE.findall(tr)]
        if len(cells) < 6:
            continue
        if not cells[0].isdigit():
            continue                      # 表头 / 空行
        hearing_at = _parse_time(cells[2])
        case_no = cells[4] or None
        if not hearing_at or not case_no:
            continue
        out.append({
            "source": SOURCE,
            "source_id": "%s|%s|%s" % (site["court"], case_no, hearing_at),
            "url": site["url"],
            "court": site["court"],
            "province": site.get("province"),
            "city": site.get("city"),
            "case_no": case_no if CASE_RE.search(case_no) else case_no,
            "cause": cells[5] or None,
            "hearing_at": hearing_at,
            "courtroom": cells[3] or None,
            "defendant": "",
            "notice_type": "开庭排期",
            "publish_date": None,
            "content": "%s 定于 %s 在%s开庭审理%s %s一案。" % (
                site["court"], hearing_at, cells[3] or "本院", case_no,
                cells[5] or ""),
        })
    return out


def iter_site(http, site, verbose=True):
    if verbose:
        print(f"  [排期表] {site['court']} {site['url']}")
    try:
        html = http.get(site["url"])
    except Exception as e:
        print(f"    ! 排期表抓取失败: {e}")
        return
    rows = parse_table(html, site)
    if verbose:
        print(f"    解析 {len(rows)} 条开庭排期")
    for r in rows:
        yield r


def iter_all(http, sites=None, verbose=True):
    sites = sites or config.SCHEDULE_SITES
    for site in sites:
        for r in iter_site(http, site, verbose=verbose):
            yield r
