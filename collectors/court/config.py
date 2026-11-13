# -*- coding: utf-8 -*-
"""全局配置。改这里即可，无需改代码。"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
# 直接写统一库；单独跑这个模块时才回落到自己的 data/bounty.db
DB_PATH = os.environ.get("LEXIFY_DB") or os.path.join(DATA_DIR, "bounty.db")
WEB_DIR = os.path.join(BASE_DIR, "web")
EXPORT_JS = os.path.join(WEB_DIR, "data", "bounties.js")

# ---- 采集 ----
# 人民法院公告网按关键词搜索，多个关键词结果会被 source_id 去重合并。
# 覆盖不同类型表述，避免漏采。
SEARCH_KEYWORDS = [
    "悬赏",
    "执行悬赏",
    "悬赏公告",
    "悬赏执行",
    "财产线索",
    "赏金",
]

# 公告类型过滤：留空表示不限；"66" = 执行文书、拍卖
NOTICE_TYPE_CODE = ""

PAGE_SIZE = 15              # 接口固定返回 15 条/页
REQUEST_INTERVAL = 1.2      # 每次请求间隔（秒），对法院站点保持克制
REQUEST_TIMEOUT = 30
MAX_RETRY = 3
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# 仅入库"看起来是执行悬赏公告"的记录，过滤掉普通开庭/送达公告
REQUIRE_BOUNTY_SIGNAL = True

# ---- 地方法院官网栏目源 ----
# 均为南方网 NFCMS 站群，栏目页静态分页（index.html / index_2.html ...），
# 详情页 /content/post_NNNN.html。新增法院只需在这里加一条：
#   court  发布法院全称（会用于地区识别）
#   base   站点根，prefix 栏目路径
# 想确认某站有哪些栏目，可访问 <base>/postmeta/catreeall.json 看全站栏目树。
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

# 单个栏目最多抓多少页（0 = 不限，按站点分页上限）
COURT_MAX_PAGES = 40


# ============================================================
# 开庭公告看板
# ============================================================
HEARING_EXPORT_JS = os.path.join(WEB_DIR, "data", "hearings.js")

# 公告网的公告类型（中文名，服务端按 noticeTypeVal 过滤）
HEARING_NOTICE_TYPES = ["开庭传票", "起诉状副本及开庭传票"]

# 按案由关键词检索。公告网接口不支持空关键词，只能逐案由检索。
# 这里覆盖用户关心的商事方向 + 高频合同类，冷门案由可自行追加。
HEARING_KEYWORDS = [
    "建设工程施工合同",
    "买卖合同",
    "票据",
    "商标",
    "金融借款合同",
    "借款合同",
    "租赁合同",
    "承揽合同",
    "装饰装修合同",
    "股权转让",
    "买卖合同纠纷",
    "商品房销售合同",
    "物业服务合同",
    "服务合同",
    "保证合同",
    "合伙协议",
    "公司决议",
    "股东资格",
    "侵害商标权",
    "债权转让",
]

# 每个关键词最多翻多少页（15 条/页）
HEARING_MAX_PAGES = 8

# 只保留开庭日在未来多少天内的（0 = 不限）
HEARING_HORIZON_DAYS = 180

# 只保留公告发布日在最近多少天内的（0 = 不限）
HEARING_PUBLISH_DAYS = 60

# 公告发布到开庭的间隔通常 30~75 天（公告 30 日 + 答辩 15 日），
# 因此"距开庭 15~30 天"属于临近开庭的紧急档，看板默认高亮。
HEARING_URGENT_DAYS = (15, 30)

# 商事案由分组：组名 -> 命中关键词（按顺序匹配，先命中先归类）.
# 用户重点：建筑工程、票据、商标侵权、合同纠纷。
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

# 出现这些词的当事人一定不是企业（自然人）
PERSON_HINTS = ["先生", "女士", "同志"]

# 静态开庭排期表源（法院官网直接发布的表格，含案由）
#   kind=table  静态 HTML 表格，列：序号/审理法院/开庭时间/法庭/案号/案由
SCHEDULE_SITES = [
    {
        "court": "广东省广州市中级人民法院",
        "url": "https://www.gzcourt.gov.cn/ktgg/440100StaticPage.html",
        "kind": "table",
        "province": "广东省", "city": "广州市",
    },
]


# ============================================================
# 深圳专项：市中院 + 10 个区法院
# ------------------------------------------------------------
# 大鹏新区没有独立基层法院，辖区案件由「龙岗区人民法院大鹏法庭
# （环境资源法庭）」管辖，因此不单列法院，龙岗两条源已覆盖大鹏新区。
# 区名 -> 法院名映射见 SZ_DISTRICT_COURT，导出时用于补区县字段。
# ============================================================
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

# ---- 悬赏公告栏目（法院官网）----
# detect_court=True：栏目标题/正文里出现其他法院名时以正文为准
# （中院站点的悬赏栏目实际汇总了全市各区法院的悬赏公告）
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

# ---- 开庭公告 / 送达公告栏目（法院官网）----
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

# ---- 人民法院公告网：按法院检索（courtParam 支持简称模糊匹配）----
# 这条源是各区法院开庭公告的主力：区法院很少在自建站发送达公告，
# 基本都投到公告网。缺点是公告网有腾讯云 WAF，连续请求会 403 封 IP。
SZ_RMFYGG_COURTS = [c for _, c in SZ_DISTRICT_COURT]

# 每个法院最多翻多少页（15 条/页）
SZ_RMFYGG_MAX_PAGES = 40
# 公告网节流：深圳专项单独放慢，避免触发 WAF
SZ_RMFYGG_INTERVAL = 8.0
# 被 403 后冷却多久再试（秒）；仍失败则本轮放弃，下次续跑
SZ_RMFYGG_COOLDOWN = 300

# ---- 开庭排期表（法院官网静态表格）----
# 盐田法院是深圳唯一把全周排期做成表格挂出来的区院：
# 开庭日期|开庭时间|法庭|案号|案由|当事人|承办法官，比送达公告信息更全。
SZ_SCHEDULE_SITES = [
    {
        "court": "深圳市盐田区人民法院",
        "base": "https://www.shenpan.gov.cn",
        "prefix": "/sfgk/ktpq",
        "province": "广东省", "city": "深圳市",
    },
]


# ============================================================
# 珠三角专项：东莞、惠州、广州的基层法院
# ------------------------------------------------------------
# 这批法院与深圳不同：没有把悬赏/开庭做成静态栏目（逐站验证过，
# 要么是动态接口，要么栏目根本不存在），所以数据全部走人民法院
# 公告网的 courtParam 检索。
#
# 城市字段由本表直接给定，不走 regions.resolve —— 后者认不出
# 「博罗县」「惠东县」「龙门县」这类县名，会把省市算成空。
# ============================================================
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
    # 南沙法院同时挂「广东自由贸易区南沙片区人民法院」的牌子
    ("广州市", "广州市南沙区人民法院", ["广东自由贸易区南沙片区人民法院",
                                        "广州市南沙区人民法院（广东自由贸易区南沙片区人民法院）"]),
    ("广州市", "广州市从化区人民法院", []),
    ("广州市", "广州市增城区人民法院", []),
    ("广州市", "广州互联网法院", []),
]

PRD_CITY_OF = {name: city for city, name, _ in PRD_COURTS}
PRD_RMFYGG_COURTS = [name for _, name, _ in PRD_COURTS]

# 每个法院每种公告类型最多翻几页（15 条/页）。公告网单法院上限 750 条，
# 全量翻完要 50 页 × 21 院 × 2 类，没必要——看板要的是近期开庭与最新悬赏。
PRD_RMFYGG_MAX_PAGES = 8
# 公告网节流：比深圳稍快，实测 5s 未见 WAF 拦截
PRD_RMFYGG_INTERVAL = 4.0
# 悬赏关键词：公告网按正文检索，多写几种表述提高召回
PRD_BOUNTY_KEYWORDS = ["悬赏", "执行悬赏", "赏金"]
