# -*- coding: utf-8 -*-
"""法院名规范化与「真实发布法院」识别 + 深圳/珠三角的法院清单。

这是两个看板（执行悬赏 / 开庭公告）在做「按地区专项采集」时共用的一套：
  - detect_court / canon_court / _clean_court  识别正文里到底哪家法院发布
  - SZ_* / PRD_*                               深圳 / 珠三角的法院/栏目清单

为什么放这里：sz.py 里 crawl_bounty 和 crawl_hearing 用了同一份 detect_court，
拆成 bounty / hearing 两个 collector 后不能各存一份（会漂移）。
"""
import re

# 正文/标题里的法院全称。先试「广东省深圳市…人民法院」这种完整写法，
# 再退回泛匹配，避免把地址串整段吞进来。
COURT_RE_FULL = re.compile(
    r"((?:广东(?:省)?)?深圳市[^\s，。、：:；;（）()]{2,12}?人民法院"
    r"|(?:广东(?:省)?)?深圳前海合作区人民法院)")
COURT_RE = re.compile(r"([\u4e00-\u9fa5]{2,12}?人民法院)")

# 深圳各基层法院，用于把「深圳市龙岗区德政路8号龙岗区人民法院」这类
# 从正文地址里带出来的脏法院名收敛成标准全称。
SZ_DISTRICTS = ("福田", "罗湖", "南山", "盐田", "宝安", "龙华", "龙岗",
                "坪山", "光明", "大鹏新区")

SZ_PROVINCE = "广东省"
SZ_CITY = "深圳市"

SZ_DISTRICT_COURT = [
    ("福田区", "深圳市福田区人民法院"),
    ("罗湖区", "深圳市罗湖区人民法院"),
    ("南山区", "深圳市南山区人民法院"),
    ("盐田区", "深圳市盐田区人民法院"),
    ("宝安区", "深圳市宝安区人民法院"),
    ("龙华区", "深圳市龙华区人民法院"),
    ("龙岗区", "深圳市龙岗区人民法院"),
    ("坪山区", "深圳市坪山区人民法院"),
    ("光明区", "深圳市光明区人民法院"),
    ("大鹏新区", "深圳市龙岗区人民法院"),      # 大鹏法庭属龙岗法院
    ("市中级", "广东省深圳市中级人民法院"),
]

PRD_PROVINCE = "广东省"

# (城市, 标准全称, 别名)：别名用于把正文里的其他写法收敛到全称
PRD_COURTS = [
    # 东莞不设区，三个基层法院按镇街片区分工
    ("东莞市", "东莞市第一人民法院", []),
    ("东莞市", "东莞市第二人民法院", []),
    ("东莞市", "东莞市第三人民法院", []),
    # 惠州
    ("惠州市", "惠州市惠城区人民法院", []),
    ("惠州市", "惠州市惠阳区人民法院", []),
    ("惠州市", "惠州市大亚湾经济技术开发区人民法院", ["大亚湾经济技术开发区人民法院"]),
    ("惠州市", "博罗县人民法院", []),
    ("惠州市", "惠东县人民法院", []),
    ("惠州市", "龙门县人民法院", []),
    # 广州
    ("广州市", "广州市越秀区人民法院", []),
    ("广州市", "广州市海珠区人民法院", []),
    ("广州市", "广州市荔湾区人民法院", []),
    ("广州市", "广州市天河区人民法院", []),
    ("广州市", "广州市白云区人民法院", []),
    ("广州市", "广州市黄埔区人民法院", []),
    ("广州市", "广州市花都区人民法院", []),
    ("广州市", "广州市番禺区人民法院", []),
    ("广州市", "广州市南沙区人民法院", ["广东自由贸易区南沙片区人民法院",
                                        "广州市南沙区人民法院（广东自由贸易区南沙片区人民法院）"]),
    ("广州市", "广州市从化区人民法院", []),
    ("广州市", "广州市增城区人民法院", []),
    ("广州市", "广州互联网法院", []),
]

PRD_CITY_OF = {name: city for city, name, _ in PRD_COURTS}
PRD_RMFYGG_COURTS = [name for _, name, _ in PRD_COURTS]


def _build_registry():
    """已知法院：写法 -> 标准全称。"""
    reg = {}
    for row in PRD_COURTS:
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


def canon_court(name):
    """法院名规范化为标准全称。识别不出的一律不动。"""
    s = (name or "").strip()
    if not s:
        return s
    hit = _match_known(s)
    if hit:
        return hit
    if "中级人民法院" in s:
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
