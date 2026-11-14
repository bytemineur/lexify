# -*- coding: utf-8 -*-
"""开庭公告看板的采集配置。

只放本看板关心的关键词、站点、阈值。网络参数在 collectors/_shared/config.py；
深圳/珠三角法院清单在 collectors/_shared/courts.py。
"""
import os

from _shared import courts as _courts

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.environ.get("LEXIFY_DB") or os.path.join(DATA_DIR, "hearing.db")
WEB_DIR = os.path.join(BASE_DIR, "web")
HEARING_EXPORT_JS = os.path.join(WEB_DIR, "data", "hearings.js")

# 公告网的公告类型（中文名，服务端按 noticeTypeVal 过滤）
HEARING_NOTICE_TYPES = ["开庭传票", "起诉状副本及开庭传票"]

# 按案由关键词检索。公告网接口不支持空关键词，只能逐案由检索。
HEARING_KEYWORDS = [
    "建设工程施工合同", "买卖合同", "票据", "商标", "金融借款合同",
    "借款合同", "租赁合同", "承揽合同", "装饰装修合同", "股权转让",
    "买卖合同纠纷", "商品房销售合同", "物业服务合同", "服务合同",
    "保证合同", "合伙协议", "公司决议", "股东资格", "侵害商标权", "债权转让",
]

HEARING_MAX_PAGES = 8          # 每个关键词最多翻多少页（15 条/页）
HEARING_HORIZON_DAYS = 180     # 只保留开庭日在未来多少天内的（0 = 不限）
HEARING_PUBLISH_DAYS = 60      # 只保留公告发布日在最近多少天内的（0 = 不限）
HEARING_URGENT_DAYS = (15, 30)

# 商事案由分组：组名 -> 命中关键词（按顺序匹配，先命中先归类）
CAUSE_GROUPS = [
    ("建筑工程", ["建设工程", "施工合同", "装饰装修", "工程款", "勘察", "设计合同",
                  "监理合同", "房屋买卖", "商品房销售", "房地产开发"]),
    ("票据", ["票据", "汇票", "本票", "支票", "追索权"]),
    ("商标知产", ["商标", "著作权", "专利", "不正当竞争", "商业秘密", "特许经营"]),
    ("金融借贷", ["金融借款", "借款合同", "民间借贷", "融资租赁", "保理",
                  "保证合同", "担保", "追偿权", "信用卡"]),
    ("公司股权", ["股权", "股东", "公司决议", "损害公司", "公司人格", "公司清算",
                  "清算责任", "出资", "增资", "减资", "公司合并", "公司分立",
                  "公司解散", "证照返还", "与公司有关"]),
    ("合同纠纷", ["买卖合同", "租赁合同", "承揽合同", "运输合同", "委托合同",
                  "服务合同", "物业服务", "供用电", "供水", "仓储", "居间",
                  "合同纠纷", "债权转让", "债权债务", "合伙"]),
]

# 被告名称命中这些词，判定为企业
COMPANY_HINTS = [
    "公司", "有限", "集团", "股份", "厂", "商行", "经营部", "商店", "门市",
    "中心", "合作社", "事务所", "酒店", "宾馆", "超市", "银行", "医院",
    "学校", "幼儿园", "物业", "地产", "置业", "建设", "工程", "科技",
    "贸易", "商贸", "实业", "投资", "咨询", "工作室", "协会", "基金会",
    "市场", "商城", "农庄", "养殖场", "煤矿", "砂场", "车队",
]

PERSON_HINTS = ["先生", "女士", "同志"]

# 静态开庭排期表源（法院官网直接发布的表格，含案由）
SCHEDULE_SITES = [
    {
        "court": "广东省广州市中级人民法院",
        "url": "https://www.gzcourt.gov.cn/ktgg/440100StaticPage.html",
        "kind": "table",
        "province": "广东省", "city": "广州市",
    },
]

# ---- 深圳专项 ----
SZ_PROVINCE = _courts.SZ_PROVINCE
SZ_CITY = _courts.SZ_CITY
SZ_DISTRICT_COURT = _courts.SZ_DISTRICT_COURT

SZ_HEARING_SITES = [
    {"court": "广东省深圳市中级人民法院", "base": "https://www.szcourt.gov.cn",
     "prefix": "/jxjsktgg", "detect_court": True,
     "province": "广东省", "city": "深圳市"},
    {"court": "深圳市福田区人民法院", "base": "https://www.ftcourt.gov.cn",
     "prefix": "/xwzx/fygg", "detect_court": True,
     "province": "广东省", "city": "深圳市"},
    {"court": "深圳市罗湖区人民法院", "base": "https://www.szlhfy.gov.cn",
     "prefix": "/sfgk/sdgg", "detect_court": True,
     "province": "广东省", "city": "深圳市"},
    {"court": "深圳市盐田区人民法院", "base": "https://www.shenpan.gov.cn",
     "prefix": "/sfgk/sdgg", "detect_court": True,
     "province": "广东省", "city": "深圳市"},
    {"court": "深圳市龙岗区人民法院", "base": "https://fy.lg.gov.cn",
     "prefix": "/fytz", "detect_court": True,
     "province": "广东省", "city": "深圳市"},
]

SZ_RMFYGG_COURTS = [c for _, c in SZ_DISTRICT_COURT]
SZ_RMFYGG_MAX_PAGES = 40
SZ_RMFYGG_INTERVAL = 8.0
SZ_RMFYGG_COOLDOWN = 300

SZ_SCHEDULE_SITES = [
    {
        "court": "深圳市盐田区人民法院",
        "base": "https://www.shenpan.gov.cn",
        "prefix": "/sfgk/ktpq",
        "province": "广东省", "city": "深圳市",
    },
]

# ---- 珠三角专项 ----
PRD_PROVINCE = _courts.PRD_PROVINCE
PRD_COURTS = _courts.PRD_COURTS
PRD_CITY_OF = _courts.PRD_CITY_OF
PRD_RMFYGG_COURTS = _courts.PRD_RMFYGG_COURTS
PRD_RMFYGG_MAX_PAGES = 8
PRD_RMFYGG_INTERVAL = 4.0
