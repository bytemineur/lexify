# -*- coding: utf-8 -*-
"""开庭公告解析：从公告正文抽取案号、案由、当事人、开庭时间、法庭、涉案金额。

法院公告的写法高度口语化且不统一，这里按「锚点优先、词典校正、启发式兜底」处理：

  案由     先用案由词典做最长后缀匹配，避免把「被告赵金龙租赁合同纠纷」
           里的当事人名字一起吞进来
  案号     （2026）粤0303民初27098号 / (2026)粤01民终28947号
  开庭时间  优先「定于/决定于/开庭时间」锚点，兜底取正文中最晚日期，
           并校验不得早于公告发布日
  金额     诉讼请求段落里的「X元 / X万元」，剔除诉讼费等费用条目后取最大值
  企业被告  名称含公司/厂/商行等词且不含先生女士

解析结果全部带置信度，前端会展示，避免让人误把估算当原文。
"""
import re
from datetime import date, datetime, timedelta

import config

# ---------- 基础 ----------
CASE_NO_RE = re.compile(
    r"[（(]\s*(20\d{2})\s*[）)]\s*"
    r"([\u4e00-\u9fa5]{1,2}\s*\d{1,6})\s*"
    r"([\u4e00-\u9fa5]{1,6}?)\s*"
    r"(\d{1,6})\s*号")
CASE_NO_LOOSE_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*([\u4e00-\u9fa5]{1,2}\d{2,6})\s*([\u4e00-\u9fa5]{1,6}?)\s*(\d{1,6})\s*号")

CN_DATE_RE = re.compile(
    r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"
    r"(?:\s*(上午|下午|晚上|中午|凌晨))?"
    r"(?:\s*(\d{1,2})\s*[时点]\s*(?:(\d{1,2})\s*分?)?)?")
NUM_DATE_RE = re.compile(
    r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\s*(?:日)?\s*"
    r"(\d{1,2})[:：](\d{2})")
COMPACT_RE = re.compile(r"(20\d{2})(\d{2})(\d{2})\s+(\d{1,2})[:：](\d{2})")

HEARING_ANCHOR = re.compile(
    r"(?:定于|决定于|兹定于|现定于|开庭时间|开庭审理时间|开庭日期)"
    r"[^。；;]{0,40}?(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
ESTIMATE_ANCHOR = re.compile(
    r"举证(?:期限)?(?:届满|期满)(?:后|之后)(?:的)?第?\s*([一二三四五六七八九十\d]{1,3})\s*[日天]")

COURTROOM_RE = re.compile(
    r"(?:在|于)\s*(?:本院)?\s*(第[一二三四五六七八九十百\d]{1,4}"
    r"(?:审判庭|法庭|调解室|会议室|审判室)"
    r"|[\u4e00-\u9fa5]{2,10}(?:审判庭|法庭))")

PLAINTIFF_RE = re.compile(r"原告[:：]?\s*([^，,。；;：:（(]{2,120})")
DEFENDANT_RE = re.compile(
    r"被告(?:[一二三四五六七八九十\d]{1,2})?[:：]?\s*([^，,。；;：:（(]{2,120})")

MONEY_RE = re.compile(r"([\d]{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*(万元|万|元)")
FEE_HINTS = ("诉讼费", "受理费", "保全费", "公告费", "律师费", "鉴定费",
             "执行费", "差旅费", "公证费", "保全保险费", "担保费", "代理费")

PARTY_SPLIT_RE = re.compile(r"[、,，;；:：\s]+")
LEAD_NOISE_RE = re.compile(
    r"^(?:本院受理|本院已受理|受理|上诉人|被上诉人|再审申请人|申请执行人|"
    r"被执行人|起诉人|原审原告|原审被告|申请人|原告|被告)")

CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
          "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


# ---------------- 案由词典 ----------------
# 依据《民事案件案由规定》常见项整理，按最长后缀匹配。
# 新增案由直接往对应分组里加一行即可，无需改解析逻辑。
_CAUSE_SRC = """
买卖合同纠纷 房屋买卖合同纠纷 商品房买卖合同纠纷 商品房销售合同纠纷 商品房预售合同纠纷
商品房委托代理销售合同纠纷 供用电合同纠纷 供用水合同纠纷 供用气合同纠纷 供用热力合同纠纷
赠与合同纠纷 借款合同纠纷 金融借款合同纠纷 民间借贷纠纷 同业拆借纠纷 企业借贷纠纷
小额借款合同纠纷 金融不良债权追偿纠纷 金融不良债权转让合同纠纷
租赁合同纠纷 房屋租赁合同纠纷 车辆租赁合同纠纷 建筑设备租赁合同纠纷 融资租赁合同纠纷
承揽合同纠纷 加工合同纠纷 定作合同纠纷 修理合同纠纷 复制合同纠纷 测试合同纠纷
检验合同纠纷 印刷合同纠纷 出版合同纠纷 拍定合同纠纷
建设工程合同纠纷 建设工程施工合同纠纷 建设工程分包合同纠纷 建设工程监理合同纠纷
建设工程勘察合同纠纷 建设工程设计合同纠纷 装饰装修合同纠纷 建设工程价款优先受偿权纠纷
运输合同纠纷 公路货物运输合同纠纷 铁路货物运输合同纠纷 水路货物运输合同纠纷
航空货物运输合同纠纷 多式联运合同纠纷 货运代理合同纠纷 管道运输合同纠纷
保管合同纠纷 仓储合同纠纷 委托合同纠纷 委托理财合同纠纷 行纪合同纠纷 居间合同纠纷
中介合同纠纷 服务合同纠纷 物业服务合同纠纷 电信服务合同纠纷 网络服务合同纠纷
旅游合同纠纷 餐饮服务合同纠纷 教育培训合同纠纷 医疗服务合同纠纷 家政服务合同纠纷
广告合同纠纷 展览合同纠纷 演出合同纠纷 庆典服务合同纠纷 殡葬服务合同纠纷
保证合同纠纷 抵押合同纠纷 质押合同纠纷 定金合同纠纷 典当纠纷 保理合同纠纷
债权转让合同纠纷 债权债务概括转移合同纠纷 债务加入纠纷 缔约过失责任纠纷
确认合同效力纠纷 合同纠纷 合伙协议纠纷 合伙合同纠纷 联营合同纠纷 合作协议纠纷
储蓄存款合同纠纷 银行卡纠纷 信用卡纠纷 信用证纠纷 票据纠纷 票据追索权纠纷
票据返还请求权纠纷 票据损害责任纠纷 汇票承兑合同纠纷 确认票据无效纠纷
股权转让纠纷 股东资格确认纠纷 股东出资纠纷 股东知情权纠纷 公司决议纠纷
公司决议效力确认纠纷 公司决议撤销纠纷 公司合并纠纷 公司分立纠纷 公司减资纠纷
公司增资纠纷 公司解散纠纷 公司清算纠纷 损害股东利益责任纠纷 损害公司利益责任纠纷
损害公司债权人利益责任纠纷 公司证照返还纠纷 请求公司收购股份纠纷 发起人责任纠纷
清算责任纠纷 股东损害公司债权人利益责任纠纷 追加被执行人异议之诉
侵害商标权纠纷 侵害著作权纠纷 著作权权属纠纷 著作权侵权纠纷 侵害专利权纠纷
侵害作品信息网络传播权纠纷 侵害计算机软件著作权纠纷 不正当竞争纠纷 商业秘密纠纷
特许经营合同纠纷 垄断纠纷 仿冒纠纷 虚假宣传纠纷 商业诋毁纠纷
劳动合同纠纷 确认劳动关系纠纷 追索劳动报酬纠纷 经济补偿金纠纷 工伤保险待遇纠纷
社会保险纠纷 福利待遇纠纷 人事争议 劳动争议 劳务合同纠纷 劳务派遣合同纠纷
工伤保险待遇纠纷 非全日制用工纠纷
离婚纠纷 离婚后财产纠纷 抚养纠纷 变更抚养关系纠纷 赡养纠纷 扶养纠纷
继承纠纷 法定继承纠纷 遗嘱继承纠纷 婚约财产纠纷 夫妻财产约定纠纷
同居关系纠纷 收养关系纠纷 分家析产纠纷 婚姻家庭纠纷
物权确认纠纷 返还原物纠纷 排除妨害纠纷 消除危险纠纷 恢复原状纠纷
财产损害赔偿纠纷 相邻关系纠纷 共有纠纷 建筑物区分所有权纠纷 业主撤销权纠纷
业主知情权纠纷 土地承包经营权纠纷 宅基地使用权纠纷 土地租赁合同纠纷
农村土地承包合同纠纷 房地产开发经营合同纠纷 房屋拆迁安置补偿合同纠纷
房屋买卖合同纠纷 车位纠纷 车库纠纷
机动车交通事故责任纠纷 医疗损害责任纠纷 产品责任纠纷 饲养动物损害责任纠纷
高度危险责任纠纷 环境污染责任纠纷 网络侵权责任纠纷 名誉权纠纷 肖像权纠纷
隐私权纠纷 姓名权纠纷 荣誉权纠纷 一般人格权纠纷 生命权、身体权、健康权纠纷
健康权纠纷 身体权纠纷 生命权纠纷 物件损害责任纠纷 教育机构责任纠纷
违反安全保障义务责任纠纷 监护人责任纠纷 雇主责任纠纷 触电人身损害责任纠纷
铁路运输人身损害责任纠纷 水上运输人身损害责任纠纷 航空运输人身损害责任纠纷
不当得利纠纷 无因管理纠纷 悬赏广告纠纷 侵权责任纠纷 债务纠纷 追偿权纠纷
申请执行人执行异议之诉 案外人执行异议之诉 执行分配方案异议之诉
破产债权确认纠纷 破产撤销权纠纷 取回权纠纷 别除权纠纷 抵销权纠纷
证券虚假陈述责任纠纷 证券纠纷 期货纠纷 信托纠纷 保险纠纷 财产保险合同纠纷
人身保险合同纠纷 再保险合同纠纷 保险代理合同纠纷 保险费纠纷
"""

CAUSE_WORDS = []
_seen = set()
for _w in _CAUSE_SRC.split():
    _w = _w.strip()
    if _w and _w not in _seen:
        _seen.add(_w)
        CAUSE_WORDS.append(_w)
CAUSE_WORDS.sort(key=len, reverse=True)
CAUSE_SET = set(CAUSE_WORDS)

# 允许出现在案由前的泛称，词典匹配失败时据此裁剪
_CAUSE_SUFFIX_RE = re.compile(r"([\u4e00-\u9fa5]{2,24}(?:纠纷|争议|赔偿|案件))$")


def _cn_int(s):
    if not s:
        return None
    if s.isdigit():
        return int(s)
    if s == "十":
        return 10
    if len(s) == 1 and s in CN_NUM:
        return CN_NUM[s]
    if s.startswith("十") and len(s) == 2 and s[1] in CN_NUM:
        return 10 + CN_NUM[s[1]]
    if s.endswith("十") and len(s) == 2 and s[0] in CN_NUM:
        return CN_NUM[s[0]] * 10
    return None


def _norm(text):
    if not text:
        return ""
    t = text.replace("\u3000", " ").replace("\xa0", " ")
    t = re.sub(r"[ \t\r\f\v]+", " ", t)
    return t.strip()


def _mk_date(y, m, d, hh=None, mm=None, ampm=None):
    try:
        y, m, d = int(y), int(m), int(d)
        if not (1990 <= y <= 2100 and 1 <= m <= 12 and 1 <= d <= 31):
            return None
        h = 0 if hh in (None, "") else int(hh)
        mi = 0 if mm in (None, "") else int(mm)
        if ampm in ("下午", "晚上", "中午") and 1 <= h < 12:
            h += 12
        if not (0 <= h <= 23 and 0 <= mi <= 59):
            h, mi = 0, 0
        return datetime(y, m, d, h, mi)
    except (ValueError, TypeError):
        return None


def _from_cn(m):
    """CN_DATE_RE 的组顺序是 (y,m,d,ampm,hh,mm)。"""
    return _mk_date(m.group(1), m.group(2), m.group(3),
                    m.group(5), m.group(6), m.group(4))


# ---------------- 案号 ----------------
# 案号第二字是省级代字，比从法院名猜更准（县区法院名往往不含省市）
CASE_PROVINCE = {
    "京": "北京市", "津": "天津市", "冀": "河北省", "晋": "山西省",
    "蒙": "内蒙古自治区", "辽": "辽宁省", "吉": "吉林省", "黑": "黑龙江省",
    "沪": "上海市", "苏": "江苏省", "浙": "浙江省", "皖": "安徽省",
    "闽": "福建省", "赣": "江西省", "鲁": "山东省", "豫": "河南省",
    "鄂": "湖北省", "湘": "湖南省", "粤": "广东省", "桂": "广西壮族自治区",
    "琼": "海南省", "渝": "重庆市", "川": "四川省", "贵": "贵州省",
    "云": "云南省", "藏": "西藏自治区", "陕": "陕西省", "甘": "甘肃省",
    "青": "青海省", "宁": "宁夏回族自治区", "新": "新疆维吾尔自治区",
    "兵": "新疆维吾尔自治区",   # 生产建设兵团分院
}

CASE_PROV_RE = re.compile(r"[（(]\s*20\d{2}\s*[）)]\s*([\u4e00-\u9fa5])")


def province_from_case_no(case_no):
    """（2026）云26民终2020号 -> 云南省"""
    if not case_no:
        return None
    m = CASE_PROV_RE.search(case_no)
    return CASE_PROVINCE.get(m.group(1)) if m else None


def extract_case_no(text):
    t = _norm(text)
    m = CASE_NO_RE.search(t) or CASE_NO_LOOSE_RE.search(t)
    if not m:
        return None, None
    y, court_code, kind, num = m.group(1), m.group(2), m.group(3), m.group(4)
    court_code = re.sub(r"\s+", "", court_code)
    return "（%s）%s%s%s号" % (y, court_code, kind, num), kind


# ---------------- 案由 ----------------
def _trim_by_dict(s):
    """在字符串里找最长的、属于案由词典的后缀。"""
    s = s.strip()
    if s in CAUSE_SET:
        return s
    for i in range(1, len(s) - 2):
        sub = s[i:]
        if sub in CAUSE_SET:
            return sub
    return None


def extract_cause(text):
    """抽取案由。

    1) 在「一案」前的窗口里做词典最长后缀匹配（效果最好）
    2) 全文词典匹配
    3) 词典都没命中时退回「XX纠纷」形状的尾串，并剔除前导当事人
    """
    t = _norm(text)

    windows = []
    for m in re.finditer(r"一案", t):
        windows.append(t[max(0, m.start() - 70): m.start()])

    for w in windows:
        w = re.sub(r"\d{4}\s*[）)]?\s*[\u4e00-\u9fa5]{1,2}\d{2,6}[\u4e00-\u9fa5]{0,4}\d+\s*号", " ", w)
        best = _trim_by_dict(w[-26:]) or _trim_by_dict(w)
        if best:
            return best

    for cw in CAUSE_WORDS:
        if cw in t:
            return cw

    # 兜底：形状匹配后裁掉疑似人名（姓氏 + 1~2 字）
    for w in windows:
        m = _CAUSE_SUFFIX_RE.search(w)
        if not m:
            continue
        cand = m.group(1)
        for cut in (3, 2, 4):
            if len(cand) > cut + 3 and cand[cut:] in CAUSE_SET:
                return cand[cut:]
        cand = re.sub(r"^(?:你们|你方|你|我|原告|被告|第三人)", "", cand)
        return cand
    return None


def cause_group(cause):
    """归入商事分组；非商事返回 None。"""
    if not cause:
        return None
    for name, words in config.CAUSE_GROUPS:
        for w in words:
            if w in cause:
                return name
    if "纠纷" in cause or "案件" in cause:
        return "其他民商"
    return None


# ---------------- 时间 ----------------
def _plausible(dt, publish_date):
    """开庭日必须晚于公告发布日（至少 1 天），且不超过两年。"""
    if not dt:
        return False
    pd = parse_date(publish_date)
    if pd:
        delta = (dt.date() - pd).days
        return 1 <= delta <= 730
    return True


def extract_hearing_time(text, publish_date=None):
    """返回 (datetime|None, 是否推算)。"""
    t = _norm(text)

    for m in HEARING_ANCHOR.finditer(t):
        dt = _mk_date(m.group(1), m.group(2), m.group(3))
        if dt and _plausible(dt, publish_date):
            # 锚点后紧跟「14时30分」或「09:30」就把时刻补上
            tail = t[m.end(): m.end() + 24]
            tm = re.match(r"\s*(上午|下午|晚上|中午)?\s*(\d{1,2})\s*[时点]\s*(\d{1,2})?\s*分?",
                          tail)
            if tm:
                dt = _mk_date(m.group(1), m.group(2), m.group(3),
                              tm.group(2), tm.group(3), tm.group(1)) or dt
            else:
                tm = re.match(r"\s*(\d{1,2})[:：](\d{2})", tail)
                if tm:
                    dt = _mk_date(m.group(1), m.group(2), m.group(3),
                                  tm.group(1), tm.group(2)) or dt
            return dt, False

    # 「于 2026 年 12 月 28 日下午 4 时 30 分……开庭」
    for m in re.finditer(r"(?:于|在)[^。；;]{0,18}?(20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日"
                         r"[^。；;]{0,26}?开庭)", t):
        dm = CN_DATE_RE.search(m.group(1))
        dt = _from_cn(dm) if dm else None
        if dt and _plausible(dt, publish_date):
            return dt, False

    for rx in (NUM_DATE_RE, COMPACT_RE):
        for m in rx.finditer(t):
            dt = _mk_date(*m.groups())
            if dt and _plausible(dt, publish_date):
                return dt, False

    # 兜底：正文中最晚日期（开庭日几乎总是晚于签约日、利息起算日）
    dates = [d for d in (_from_cn(m) for m in CN_DATE_RE.finditer(t))
             if d and _plausible(d, publish_date)]
    if dates:
        return max(dates), False

    # 推算：举证期限届满后第 N 日开庭
    m = ESTIMATE_ANCHOR.search(t)
    if m and publish_date:
        n = _cn_int(m.group(1)) or 3
        base = parse_date(publish_date)
        if base:
            period = 60 if re.search(r"经(?:过)?\s*(?:六十|60)\s*日", t) else 30
            answer = 30 if re.search(r"答辩.{0,10}(?:三十|30)\s*日", t) else 15
            dt = datetime.combine(
                base + timedelta(days=period + answer + n), datetime.min.time())
            return dt, True

    return None, False


def parse_date(s):
    if not s:
        return None
    m = re.search(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})", str(s))
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


# ---------------- 当事人 ----------------
def is_company(name):
    if not name:
        return False
    n = name.strip()
    if any(h in n for h in config.PERSON_HINTS):
        return False
    return any(h in n for h in config.COMPANY_HINTS)


def split_parties(raw):
    if not raw:
        return []
    out, seen = [], set()
    for p in PARTY_SPLIT_RE.split(raw):
        p = p.strip(" 。，,、；;：:")
        p = re.sub(r"\(以下简称.*?\)|（以下简称.*?）", "", p).strip()
        p = re.sub(r"[（(].*?[）)]", "", p).strip()
        if len(p) < 2 or len(p) > 40 or p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def classify_parties(names):
    if not names:
        return "unknown", 0, 0
    comp = sum(1 for n in names if is_company(n))
    pers = len(names) - comp
    if comp and pers:
        return "mixed", comp, pers
    if comp:
        return "company", comp, 0
    return "person", 0, pers


def _clean_party(s):
    s = (s or "").strip(" ，,、。：:")
    s = LEAD_NOISE_RE.sub("", s).strip(" ，,、。：:")
    if len(s) < 2 or s in ("方", "你", "你们", "其"):
        return None
    return s


# 当事人名称里出现这些词，基本可以断定抓到的是句子片段而不是主体名
_PARTY_BAD = ("请求", "判令", "支付", "偿还", "依法", "本院", "诉讼", "承担",
              "赔偿", "解除", "确认", "主张", "要求", "认为", "诉称", "辩称",
              "送达", "开庭", "审理", "共计", "合计", "履行", "清偿", "返还",
              "交付", "上述", "如下", "以上", "本案", "该案", "另案")


def _party_valid(s):
    if not s or len(s) < 2:
        return False
    if any(b in s for b in _PARTY_BAD):
        return False
    if re.search(r"\d", s) or s.endswith("元"):
        return False
    return True


_CAUSE_CUT_RE = re.compile(r"[\u4e00-\u9fa5]{2,24}(?:纠纷|案件)|一案")
_PARTY_CUT_RE = re.compile(r"第三人|上诉人|被上诉人|再审申请人|申请执行人|被执行人|"
                           r"原告|被告|[,，。；;：:（(]|诉|与")


def _cut_party(s, cause=None):
    """把「被告田进虎、王小军买卖合同纠纷一案」裁成「田进虎、王小军」。

    先按已知案由精确切（案由可能长达 20 字，正则长度上限会切坏公司名），
    再退回通用规则。
    """
    s = s or ""
    if cause and cause in s:
        s = s.split(cause)[0]
    else:
        s = _CAUSE_CUT_RE.split(s)[0]
    s = _PARTY_CUT_RE.split(s)[0]
    return s.strip(" ，,、。：:")


def extract_parties(text, send_to=None, cause=None):
    """返回 (原告, 被告原文, 被告类型, 企业数, 自然人数)。

    被告优先取正文「被告XXX」并裁掉后面的案由文字；正文没写时退回公告
    的受送达人字段（公告送达的受送达人通常就是下落不明的被告）。
    企业判定用「正文被告 + 受送达人」并集，避免第三人顶替了真正的企业被告。
    """
    t = _norm(text)
    if cause is None:
        cause = extract_cause(t)

    plaintiff = None
    for m in PLAINTIFF_RE.finditer(t):
        p = _clean_party(_cut_party(m.group(1), cause))
        if p and _party_valid(p):
            plaintiff = p
            break

    defendant = ""
    dm = DEFENDANT_RE.search(t)
    if dm:
        defendant = _cut_party(dm.group(1), cause)
    if not _party_valid(defendant):
        defendant = ""

    names = [n for n in split_parties(defendant) if _party_valid(n)]
    if send_to:
        for n in split_parties(_norm(send_to)):
            if _party_valid(n) and n not in names:
                names.append(n)
    if not names:
        dtype, comp, pers = "unknown", 0, 0
    else:
        dtype, comp, pers = classify_parties(names)

    if not defendant:
        valid = [n for n in split_parties(_norm(send_to or "")) if _party_valid(n)]
        defendant = "、".join(valid)

    if not plaintiff:
        m = re.search(r"([\u4e00-\u9fa5（）()]{4,30}?)(?:诉|与)[\u4e00-\u9fa5]{2,30}?"
                      r"[\u4e00-\u9fa5]{2,20}(?:纠纷|案件)", t)
        if m:
            p = _clean_party(_cut_party(m.group(1), cause))
            if p and _party_valid(p) and p != defendant:
                plaintiff = p

    return plaintiff, defendant, dtype, comp, pers


# ---------------- 金额 ----------------
def extract_amount(text):
    """从正文取涉案金额。返回 (金额, 依据)。"""
    t = _norm(text)
    m = re.search(r"(?:诉讼请求|诉请|请求(?:事项|判令|如下)?)[:：为]?(.{0,700})", t)

    def scan(scope):
        best = None
        for mm in MONEY_RE.finditer(scope):
            try:
                val = float(mm.group(1).replace(",", ""))
            except ValueError:
                continue
            if mm.group(2) in ("万元", "万"):
                val *= 10000
            if val < 1000:                   # 千元以下多为公告费、受理费
                continue
            ctx = scope[max(0, mm.start() - 16): mm.start()]
            if any(h in ctx for h in FEE_HINTS):
                continue
            if best is None or val > best:
                best = val
        return best

    if m:
        best = scan(m.group(1))
        if best is not None:
            return round(best, 2), "诉讼请求"

    best = scan(t)
    if best is not None:
        return round(best, 2), "正文最大金额"
    return None, None


# ---------------- 法庭 ----------------
def extract_courtroom(text):
    m = COURTROOM_RE.search(_norm(text))
    return m.group(1) if m else None


# ---------------- 总入口 ----------------
def parse_hearing(text, publish_date=None, send_to=None, court=None):
    """解析一份开庭公告，返回字段字典。"""
    t = _norm(text)
    case_no, case_kind = extract_case_no(t)
    cause = extract_cause(t)
    group = cause_group(cause)
    hearing_at, estimated = extract_hearing_time(t, publish_date)
    plaintiff, defendant, dtype, comp, pers = extract_parties(t, send_to, cause)
    amount, amount_basis = extract_amount(t)
    room = extract_courtroom(t)

    score = 0.0
    score += 0.3 if case_no else 0
    score += 0.3 if cause else 0
    score += 0.25 if hearing_at else 0
    score += 0.15 if defendant else 0
    if estimated:
        score -= 0.1
    score = max(0.0, min(1.0, round(score, 2)))

    return {
        "case_no": case_no,
        "case_kind": case_kind,
        "cause": cause,
        "cause_group": group,
        "is_commercial": 1 if group and group != "其他民商" else 0,
        "hearing_at": hearing_at.strftime("%Y-%m-%d %H:%M") if hearing_at else None,
        "hearing_date": hearing_at.strftime("%Y-%m-%d") if hearing_at else None,
        "time_estimated": 1 if estimated else 0,
        "plaintiff": (plaintiff or "")[:120] or None,
        "defendant": (defendant or "")[:300] or None,
        "defendant_type": dtype,
        "company_count": comp,
        "person_count": pers,
        "claim_amount": amount,
        "amount_basis": amount_basis,
        "courtroom": room,
        "parse_quality": score,
        "content_norm": t,
    }


# 判决书/裁定书类公告：不是开庭通知，直接排除
_CLOSING_HINTS = ("已审理终结", "民事判决书", "裁定书", "判决如下")


def looks_like_hearing(text):
    """判断是否是真的开庭公告（而不是判决送达、拍卖等）。"""
    t = _norm(text)
    if not t or len(t) < 30:
        return False
    if "开庭" not in t and "庭审" not in t and "传票" not in t:
        return False
    if "已审理终结" in t and "开庭" not in t:
        return False
    if any(h in t[:120] for h in _CLOSING_HINTS) and "开庭" not in t[:200]:
        return False
    return True
