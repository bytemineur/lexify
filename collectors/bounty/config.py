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
