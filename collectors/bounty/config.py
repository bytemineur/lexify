# -*- coding: utf-8 -*-
"""执行悬赏看板的采集配置。

只放本看板关心的关键词、站点、阈值。网络参数（节流/UA/重试）在
collectors/_shared/config.py；深圳/珠三角法院清单在 collectors/_shared/courts.py。
"""
import os

from _shared import courts as _courts

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
# 直接写统一库；单独跑这个模块时才回落到自己的 data/bounty.db
DB_PATH = os.environ.get("LEXIFY_DB") or os.path.join(DATA_DIR, "bounty.db")
WEB_DIR = os.path.join(BASE_DIR, "web")
EXPORT_JS = os.path.join(WEB_DIR, "data", "bounties.js")

# ---- 采集 ----
# 人民法院公告网按关键词搜索，多个关键词结果会被 source_id 去重合并。
SEARCH_KEYWORDS = [
    "悬赏", "执行悬赏", "悬赏公告", "悬赏执行", "财产线索", "赏金",
]

# 公告类型过滤：留空表示不限；"66" = 执行文书、拍卖
NOTICE_TYPE_CODE = ""

# 仅入库"看起来是执行悬赏公告"的记录，过滤掉普通开庭/送达公告
REQUIRE_BOUNTY_SIGNAL = True

# ---- 地方法院官网栏目源（悬赏栏目）----
COURT_SITES = [
    {
        "court": "广东省深圳市中级人民法院",
        "base": "https://www.szcourt.gov.cn",
        "prefix": "/xsgg",                 # 司法公开 > 悬赏公告
        "province": "广东省", "city": "深圳市",
    },
    {
        "court": "深圳市南山区人民法院",
        "base": "https://gw.nscourt.gov.cn",
        "prefix": "/nscourt/wzsy/swgk/zxxsgg",   # 审务公开 > 执行悬赏公告
        "province": "广东省", "city": "深圳市",
    },
    {
        "court": "深圳市福田区人民法院",
        "base": "https://www.ftcourt.gov.cn",
        "prefix": "/xwzx/fygg/xsgg",       # 法院公告 > 悬赏公告
        "province": "广东省", "city": "深圳市",
    },
]

# ---- 华南 + 江浙沪：中院官网悬赏 / 执行公告栏目 ----
# 人民法院公告网被限流时的备用线路。每条都是实际抓取验证过能出公告的栏目；
# 新增法院只需照抄结构，href_re 可覆盖默认的详情页模板。
REGION_BOUNTY_SITES = [
    {
        # 广州中院执行悬赏公告，详情页 /other/zxgkw/zxxs/YYYY/MM/DDHHMMSSmmm.html
        "court": "广州市中级人民法院",
        "base": "https://www.gzcourt.gov.cn",
        "prefix": "/other/zxgkw/zxxs",
        "province": "广东省", "city": "广州市",
        "notice_type": "执行悬赏公告",
    },
    {
        # 珠海中院「本院公告」
        "court": "珠海市中级人民法院",
        "base": "http://www.zhcourt.gov.cn",
        "entry": "/article/index/id/M8zJNTBIMiAOAAA.shtml",
        "province": "广东省", "city": "珠海市",
    },
    {
        # 珠海中院「开庭、听证公告」
        "court": "珠海市中级人民法院",
        "base": "http://www.zhcourt.gov.cn",
        "entry": "/article/index/id/M8zJNTBIMjAwMiACAAA.shtml",
        "province": "广东省", "city": "珠海市",
        "notice_type": "开庭公告",
    },
    {
        # 佛山中院「公告」栏目（南方网 NFCMS，420 条 20 页）
        "court": "佛山市中级人民法院",
        "base": "https://www.fszjfy.gov.cn",
        "prefix": "/gzgg",
        "province": "广东省", "city": "佛山市",
    },
    {
        # 广东高院「工作公告」
        "court": "广东省高级人民法院",
        "base": "https://www.gdcourts.gov.cn",
        "prefix": "/gsxx/fayuangonggao",
        "province": "广东省", "city": "广州市",
    },
    {
        # 海南高院「法院公告」
        "court": "海南省高级人民法院",
        "base": "https://www.hicourt.gov.cn",
        "entry": "/article/index/id/MyhJMjAwNTAwNCACAAA.shtml",
        "province": "海南省", "city": "海口市",
    },
    {
        # 福建高院「法院公告」
        "court": "福建省高级人民法院",
        "base": "https://www.fjcourt.gov.cn",
        "entry": "/page/public/personnelreport.html",
        "province": "福建省", "city": "福州市",
        "href_re": r"""href=["']([^"']*/Page/Court/News/ArticleTradition/[0-9a-f-]+\.html)["']""",
    },
    {
        # 江苏高院「工作公告」
        "court": "江苏省高级人民法院",
        "base": "https://www.jsfy.gov.cn",
        "entry": "/articles/16.html",
        "province": "江苏省", "city": "南京市",
        "href_re": r"""href=["'](/article/\d+\.html)["']""",
    },
    {
        # 镇江中院「工作公告」
        "court": "镇江市中级人民法院",
        "base": "https://www.zjzy.gov.cn",
        "entry": "/swgk/gzgg/",
        "province": "江苏省", "city": "镇江市",
        "href_re": r"""href=["'](/swgk/gzgg/\d{4}/\d{4}/\d+\.html)["']""",
    },
]

# 合并进主配置（去重按 court + 入口）
_seen_sites = set()
for _s in COURT_SITES + REGION_BOUNTY_SITES:
    _k = (_s["court"], _s.get("entry") or _s.get("prefix"))
    if _k in _seen_sites:
        continue
    _seen_sites.add(_k)
    if _s not in COURT_SITES:
        COURT_SITES.append(_s)
COURT_MAX_PAGES = 40

# ---- 深圳专项：悬赏公告栏目（法院官网） ----
SZ_PROVINCE = _courts.SZ_PROVINCE
SZ_CITY = _courts.SZ_CITY
SZ_DISTRICT_COURT = _courts.SZ_DISTRICT_COURT

SZ_BOUNTY_SITES = [
    {"court": "广东省深圳市中级人民法院", "base": "https://www.szcourt.gov.cn",
     "prefix": "/xsgg", "detect_court": True,
     "province": "广东省", "city": "深圳市"},
    {"court": "深圳市福田区人民法院", "base": "https://www.ftcourt.gov.cn",
     "prefix": "/xwzx/fygg/xsgg", "detect_court": True,
     "province": "广东省", "city": "深圳市"},
    {"court": "深圳市罗湖区人民法院", "base": "https://www.szlhfy.gov.cn",
     "prefix": "/sfgk/xsgg", "detect_court": True,
     "province": "广东省", "city": "深圳市"},
    {"court": "深圳市南山区人民法院", "base": "https://gw.nscourt.gov.cn",
     "prefix": "/nscourt/wzsy/swgk/zxxsgg", "detect_court": True,
     "province": "广东省", "city": "深圳市"},
    {"court": "深圳市盐田区人民法院", "base": "https://www.shenpan.gov.cn",
     "prefix": "/sfgk/zxgg", "detect_court": True,
     "province": "广东省", "city": "深圳市"},
    {"court": "深圳市龙华区人民法院", "base": "https://www.szlhcourt.gov.cn",
     "prefix": "/gonggao/zhixing", "detect_court": True,
     "province": "广东省", "city": "深圳市"},
]

SZ_RMFYGG_COURTS = [c for _, c in SZ_DISTRICT_COURT]
SZ_RMFYGG_MAX_PAGES = 40
SZ_RMFYGG_INTERVAL = 8.0      # 公告网节流（深圳专项单独放慢，避免触发 WAF）
SZ_RMFYGG_COOLDOWN = 300

# ---- 珠三角专项：东莞、惠州、广州的基层法院（全部走公告网） ----
PRD_PROVINCE = _courts.PRD_PROVINCE
PRD_COURTS = _courts.PRD_COURTS
PRD_CITY_OF = _courts.PRD_CITY_OF
PRD_RMFYGG_COURTS = _courts.PRD_RMFYGG_COURTS
PRD_RMFYGG_MAX_PAGES = 8
PRD_RMFYGG_INTERVAL = 4.0
PRD_BOUNTY_KEYWORDS = ["悬赏", "执行悬赏", "赏金"]
