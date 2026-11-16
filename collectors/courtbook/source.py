# -*- coding: utf-8 -*-
"""数据源：tool.110.com/fy.php「全国法院地址电话」→ courts 表。

这个源给的是**通讯录**（名称 / 地址 / 邮编 / 电话），跟 hearings / notices 里
按名字分组的 court 字段是两回事，所以单独存一张表。

抓取要点（都是实测踩出来的）：

1. **全国分页只到 400 条**。页面声称「总共 3462 条、总页 20」，但每页 20 条、
   翻到第 20 页最后一条是 id=401 —— 服务器只肯给前 400 条。
   所以必须**按省遍历**，每个省单独翻页（广东一次就能取到 id 3459）。
2. **省份编号必须用站点自己的 region1.js**，不能按民政部顺序猜：
   2 是上海不是天津、4 是重庆。
3. **海南、宁夏两个省在源站就是空的**（页内搜索也是坏的，直接吐 SQL 报错：
   `1054:Unknown column 'name'`）。这不是抓取失败，是源站确实没有，
   要写进 court_coverage 标明 status='missing'，不许静默当成功。
4. 站点偶发 read timeout，必须带退避重试，否则整省会缺一块。
"""
import re
import time

from _shared import regions

SOURCE = "fy110"
BASE = "https://tool.110.com"
LIST_URL = BASE + "/fy.php"
DETAIL_URL = BASE + "/fayuan.php?id="

# 站点 region1.js 里的省份编号（与民政部顺序不同，别改）
PROVINCES = [
    (1, "北京"), (2, "上海"), (3, "天津"), (4, "重庆"), (5, "河北"),
    (6, "山西"), (7, "内蒙古"), (8, "辽宁"), (9, "吉林"), (10, "黑龙江"),
    (11, "江苏"), (12, "浙江"), (13, "安徽"), (14, "福建"), (15, "江西"),
    (16, "山东"), (17, "河南"), (18, "湖北"), (19, "湖南"), (20, "广东"),
    (21, "广西"), (22, "海南"), (23, "四川"), (24, "贵州"), (25, "云南"),
    (26, "西藏"), (27, "陕西"), (28, "甘肃"), (29, "宁夏"), (30, "青海"),
    (31, "新疆"),
]
PROVINCE_FULL = {
    "北京": "北京市", "上海": "上海市", "天津": "天津市", "重庆": "重庆市",
    "河北": "河北省", "山西": "山西省", "内蒙古": "内蒙古自治区",
    "辽宁": "辽宁省", "吉林": "吉林省", "黑龙江": "黑龙江省", "江苏": "江苏省",
    "浙江": "浙江省", "安徽": "安徽省", "福建": "福建省", "江西": "江西省",
    "山东": "山东省", "河南": "河南省", "湖北": "湖北省", "湖南": "湖南省",
    "广东": "广东省", "广西": "广西壮族自治区", "海南": "海南省",
    "四川": "四川省", "贵州": "贵州省", "云南": "云南省", "西藏": "西藏自治区",
    "陕西": "陕西省", "甘肃": "甘肃省", "宁夏": "宁夏回族自治区",
    "青海": "青海省", "新疆": "新疆维吾尔自治区",
}

ROW_RE = re.compile(
    r'<li><span class="contenta"><a href="fayuan\.php\?id=(\d+)"[^>]*>(.*?)</a></span>'
    r'<span class="contentb">(.*?)</span>'
    r'<span class="contentc">(.*?)</span>'
    r'<span class="contentd">(.*?)</span></li>', re.S)

TOTAL_RE = re.compile(r"总共：(\d+)条")
PAGES_RE = re.compile(r"总页：(\d+)")

# 电话原文形如「010-65290225（昼）、65290389（夜）」
DAY_RE = re.compile(r"([0-9][0-9\-]{4,22}?) ?（昼）")
NIGHT_RE = re.compile(r"([0-9][0-9\-]{4,22}?) ?（夜）")
PHONE_SPLIT_RE = re.compile(r"[、,，;；/]+")


def _clean(s):
    t = re.sub(r"<[^>]+>", "", s or "")
    t = (t.replace("&nbsp;", " ").replace("&amp;", "&")
          .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))
    return re.sub(r"\s+", " ", t).strip()


def parse_rows(html):
    """从列表页取出 [{source, source_id, url, name, address, postcode, phone}]。"""
    out = []
    for m in ROW_RE.finditer(html):
        name = _clean(m.group(2))
        if not name:
            continue
        out.append({
            "source": SOURCE,
            "source_id": m.group(1),
            "url": DETAIL_URL + m.group(1),
            "name": name,
            "address": _clean(m.group(3)),
            "postcode": _clean(m.group(4)),
            "phone": _clean(m.group(5)),
        })
    return out


def split_phone(phone):
    """把「010-1（昼）、2（夜）」拆成（白天号, 夜间号）。"""
    phone = (phone or "").strip()
    if not phone:
        return None, None
    d = DAY_RE.search(phone)
    n = NIGHT_RE.search(phone)
    if d or n:
        day = d.group(1).strip() if d else None
        night = n.group(1).strip() if n else None
        # 夜间号常省略区号，补上白天的区号
        if night and day and "-" not in night and "-" in day:
            night = day.split("-")[0] + "-" + night
        return day, night
    parts = [p.strip() for p in PHONE_SPLIT_RE.split(phone) if p.strip()]
    return (parts[0] if parts else phone), (parts[1] if len(parts) > 1 else None)


def court_level(name):
    """按名字判层级。

    源站的命名并不统一，处理顺序很重要：
      · 「辽河油田中级法院」「临江林区基层法院」这类名字里没有「人民法院」，
        要先看「中级法院 / 基层法院」再回落到「人民法院」的判断；
      · 「新疆高院生产建设兵团分院」是兵团的分院，不是省级高院，归特殊；
      · 铁路 / 海事 / 知识产权 / 互联网 / 金融 / 林区 / 油田 都是专门法院。
    """
    if not name:
        return None
    if "兵团分院" in name:
        return "特殊"
    # 专门法院要排在中院之前判：「上海铁路运输中级法院」含「中级法院」，
    # 但它不是地市中院，先按专门法院归到特殊。
    if "海事法院" in name or "知识产权法院" in name or "互联网法院" in name \
            or "金融法院" in name:
        return "特殊"
    if re.search(r"铁路运输(?:中级)?法院$", name) or re.search(r"铁路运输第.+法院$", name):
        return "特殊"
    if re.search(r"林区(?:基层)?法院$", name):
        return "特殊"
    if re.search(r"油田(?:中级)?法院$", name):
        return "特殊"
    if re.search(r"农垦法院$", name):
        # 黑龙江农垦系统法院，2020 年起划归地方，仍按基层算
        return "基层"
    if "人世法院" in name:
        # 源站错字：黄石市铁山区「人世法院」应为「人民法院」
        return "基层"
    if "高新技术开发区法院" in name or "开发区法院" in name:
        return "基层"
    if "高级人民法院" in name:
        return "高院"
    if "中级人民法院" in name or name.endswith("中级法院"):
        return "中院"
    if name.endswith("基层法院"):
        return "基层"
    if name.endswith("人民法院"):
        return "基层"
    return None


def court_short(name):
    if not name:
        return None
    for suffix in ("人民法院", "中级法院", "基层法院", "法院"):
        if name.endswith(suffix):
            return name[:-len(suffix)]
    return name


def guess_city(name):
    """从法院名里猜地市：「广州市越秀区人民法院」→ 广州市。"""
    m = re.match(r"^(?:[\u4e00-\u9fa5]+?(?:省|自治区))?([\u4e00-\u9fa5]{2,4}?)(市|地区|自治州)",
                 name or "")
    if m:
        return m.group(1) + m.group(2)
    return None


def resolve_region(name, province_full):
    """省份优先用源站给的（最可靠），城市尽量走项目已有的地区映射。"""
    try:
        p, c, _ = regions.resolve(name or "")
        if p:
            return p, (c or guess_city(name))
    except Exception:  # noqa: BLE001
        pass
    return province_full, guess_city(name)


def fetch_page(http, province_code, page, retries=4, verbose=False):
    """取一页。站点偶发 read timeout，这里自己带退避重试。"""
    url = "%s?p=%d&q=&province=%d&city=0" % (LIST_URL, page, province_code)
    last = None
    for i in range(retries):
        try:
            return http.get(url, headers={"Referer": LIST_URL})
        except Exception as e:  # noqa: BLE001
            last = e
            if verbose:
                print("      ! 重试 %d/%d：%s" % (i + 1, retries, str(e)[:60]))
            time.sleep(2.0 * (i + 1))
    raise last


def iter_province(http, code, short_name, *, verbose=True, max_pages=0):
    """遍历一个省，产出 (raw_row, claimed_total)。"""
    html = fetch_page(http, code, 1, verbose=verbose)
    t = TOTAL_RE.search(html)
    claimed = int(t.group(1)) if t else 0
    pg = PAGES_RE.search(html)
    total_pages = int(pg.group(1)) if pg else 1
    if max_pages:
        total_pages = min(total_pages, max_pages)

    if verbose:
        print("  %-6s 声称 %-5d 条 / %d 页" % (short_name, claimed, total_pages))

    seen = set()
    for r in parse_rows(html):
        if r["source_id"] in seen:
            continue
        seen.add(r["source_id"])
        yield r, claimed

    for page in range(2, total_pages + 1):
        try:
            ph = fetch_page(http, code, page, verbose=verbose)
        except Exception as e:  # noqa: BLE001
            if verbose:
                print("    第 %d 页失败：%s" % (page, str(e)[:70]))
            break
        got = 0
        for r in parse_rows(ph):
            if r["source_id"] in seen:
                continue
            seen.add(r["source_id"])
            got += 1
            yield r, claimed
        if verbose:
            print("    第 %d 页 +%d" % (page, got))
        if got == 0:
            break


def to_row(raw, province_full, fetched_at):
    """原始行 → courts 表的一行。"""
    day, night = split_phone(raw.get("phone"))
    prov, city = resolve_region(raw.get("name"), province_full)
    return {
        "name": raw["name"],
        "short": court_short(raw["name"]),
        "level": court_level(raw["name"]),
        "province": prov,
        "city": city,
        "address": raw.get("address") or None,
        "postcode": raw.get("postcode") or None,
        "phone": raw.get("phone") or None,
        "phone_day": day,
        "phone_night": night,
        "source": SOURCE,
        "source_id": raw["source_id"],
        "source_url": raw.get("url"),
        "fetched_at": fetched_at,
    }
