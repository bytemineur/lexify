# -*- coding: utf-8 -*-
"""执行悬赏公告文本解析器。

从公告全文中抽取：执行标的额、悬赏比例、封顶金额、固定赏金、
悬赏期限、执行案号、联系电话、悬赏类型，并推算预期最高奖励。
"""
import re
from datetime import date, timedelta

# ---------------- 文本规范化 ----------------

_FULL2HALF = {
    "０": "0", "１": "1", "２": "2", "３": "3", "４": "4", "５": "5",
    "６": "6", "７": "7", "８": "8", "９": "9", "％": "%", "．": ".",
    "，": ",", "：": ":", "；": ";", "（": "(", "）": ")", "　": " ",
    "－": "-", "—": "-", "～": "~", "／": "/", "、": ",",
}

CN_NUM = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
          "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
CN_UNIT = {"十": 10, "百": 100, "千": 1000}
CN_SEC = {"万": 10000, "亿": 100000000}
# 大写数字（公告中偶见）
CN_CAP = {"壹": 1, "贰": 2, "叁": 3, "肆": 4, "伍": 5, "陆": 6, "柒": 7,
          "捌": 8, "玖": 9, "拾": 10, "佰": 100, "仟": 1000,
          "萬": 10000, "亿": 100000000}


def normalize(text: str) -> str:
    if not text:
        return ""
    out = []
    for ch in text:
        out.append(_FULL2HALF.get(ch, ch))
    s = "".join(out)
    s = re.sub(r"[ \t\r\u3000]+", " ", s)
    s = re.sub(r"\n{2,}", "\n", s)
    return s


def cn_to_num(s: str):
    """中文数字转阿拉伯数字，支持 '五万' '二十万' '叁万元' '一百五十万' 等。"""
    if not s:
        return None
    total, section, num = 0, 0, 0
    found = False
    for ch in s:
        if ch in CN_NUM:
            num = CN_NUM[ch]
            found = True
        elif ch in CN_CAP and ch not in ("萬", "亿"):
            v = CN_CAP[ch]
            if v >= 10:
                section += (num or 1) * v
                num = 0
            else:
                num = v
            found = True
        elif ch in CN_UNIT:
            section += (num or 1) * CN_UNIT[ch]
            num = 0
            found = True
        elif ch in CN_SEC:
            mult = CN_SEC[ch]
            section = (section + num) * mult
            total += section
            section, num = 0, 0
            found = True
        else:
            return None
    if not found:
        return None
    return total + section + num


# ---------------- 金额 ----------------

_AMT = r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*(亿元|亿|万元|万|元)?"
# 单位必填版：防止"支付2023年9月"被当成 2023 元
_AMT_U = r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*(亿元|亿|万元|万|元)"


def to_yuan(num_str, unit):
    if not num_str:
        return None
    try:
        v = float(num_str.replace(",", ""))
    except ValueError:
        return None
    if unit in ("亿元", "亿"):
        v *= 100000000
    elif unit in ("万元", "万"):
        v *= 10000
    return v


def parse_amount_at(text, pos, window=8):
    """从 pos 处向后取一个金额。"""
    seg = text[pos:pos + window]
    m = re.match(r"\s*(?:人民币)?\s*" + _AMT, seg)
    if not m:
        return None
    val = to_yuan(m.group(1), m.group(2))
    return val


# ---------------- 各字段抽取 ----------------

SUBJECT_PATTERNS = [
    # "执行标的：本金5000万元及迟延履行期间…"（金额前还有"本金/人民币"等修饰）
    r"执行标的(?:额|金额)?\s*(?:为|是)?\s*:?\s*(?:本金|人民币|款项)?\s*(?:人民币)?\s*" + _AMT_U,
    r"执行标的(?:额|金额)?\s*(?:为|是)?\s*:?\s*(?:人民币)?\s*" + _AMT_U,
    r"未履行(?:的)?(?:标的|标的额|金额|债务)\s*(?:为|是)?\s*:?\s*(?:本金|人民币)?\s*" + _AMT_U,
    r"涉案标的(?:额|金额)?\s*(?:为|是)?\s*:?\s*(?:人民币)?\s*" + _AMT_U,
    r"标的额\s*(?:为|是)?\s*:?\s*(?:人民币)?\s*" + _AMT_U,
    r"申请执行(?:的)?(?:标的|金额|标的额)\s*(?:为|是)?\s*:?\s*(?:人民币)?\s*" + _AMT_U,
    # "请求强制被执行人潘伟胜支付228万元及利息" / "偿付人民币408287.36元"
    r"请求(?:依法)?(?:强制)?(?:被执行人|被申请人)[^。;]{0,30}?(?:支付|偿付|给付|偿还|归还)\s*(?:人民币)?\s*" + _AMT_U,
    # "支付借款1120000元" "偿付款项248499.13元" "支付保险赔偿款3374752.44元" "请求返还款项375899.68元"
    r"(?:支付|偿付|偿还|归还|给付|返还|退还)(?:款项|借款|欠款|货款|本金|工程款|工资|劳动报酬|赔偿款|保险赔偿款|执行款)(?:本金)?\s*(?:人民币)?\s*" + _AMT_U,
    # "赔偿损失213811.91元"
    r"赔偿(?:损失|金|款)\s*(?:人民币)?\s*" + _AMT_U,
    # "保险赔偿款3374752.44元" / "赔偿金86万元"
    r"(?:赔偿款|赔偿金|保险金|代偿款)\s*(?:人民币)?\s*" + _AMT_U,
    r"(?:借款|欠款|货款)本金\s*(?:人民币)?\s*(?:\([^)]{0,8}\))?\s*" + _AMT_U,
    r"执行到位(?:金额|标的)?\s*(?:为|是)\s*:?\s*(?:人民币)?\s*" + _AMT_U,
]

RATIO_STRONG = [
    # 按实际执行到位金额的 X%
    r"(?:按|按照|以|就)?(?:实际)?(?:执行|变现|回款)到位(?:的)?(?:金额|款项|款额|标的额|财产|执行款)?(?:的)?(?:人民币)?\s*(\d+(?:\.\d+)?)\s*%",
    r"(?:悬赏|奖励|赏金)(?:金额|金|款)?(?:为|是|按|按照)?\s*(?:实际)?(?:执行)?到位(?:金额|款项|款额)?(?:的)?\s*(\d+(?:\.\d+)?)\s*%",
    r"(\d+(?:\.\d+)?)\s*%\s*(?:的)?\s*(?:资金|款项|奖励|悬赏金|比例|作为悬赏)",
    r"按(?:照)?(?:执行标的额|标的额|债权额)的?\s*(\d+(?:\.\d+)?)\s*%",
]

RATIO_WEAK = [
    r"(\d+(?:\.\d+)?)\s*%",
]

CAP_PATTERNS = [
    r"(?:最高|上限|封顶)(?:不超过|不高于|为|金额为)?\s*(?:人民币)?\s*" + _AMT_U,
    r"悬赏(?:总|总金额|总额)?(?:金额)?(?:不超过|上限为?)\s*(?:人民币)?\s*" + _AMT_U,
    # "（含税，以215737.51元为限）"
    r"以\s*(?:人民币)?\s*" + _AMT_U + r"\s*为限",
    r"不超过(?:人民币)?\s*" + _AMT_U,
]

# 固定赏金只在"悬赏条款"段落内抽取，避免把执行标的当成赏金
FIXED_PATTERNS = [
    r"悬赏(?:金|金额|奖励)(?:为|是|金额为)\s*(?:人民币)?\s*" + _AMT_U,
    r"(?:支付|给予|奖励)(?:悬赏金|赏金|奖励)(?:人民币)?\s*" + _AMT_U,
    # "给予举报人实际执行到位款项金额的1000元作为悬赏奖励"
    r"(?:给予|支付|奖励)[^。;]{0,30}?(?:金额|款项|款额|标的额|执行款)的?\s*(?:人民币)?\s*" + _AMT_U,
    # 兜底："支付500元悬赏金" —— 金额后紧跟赏金名词
    r"(?:人民币)?" + _AMT_U + r"\s*[^。;]{0,10}?(?:悬赏金|赏金|奖金|悬赏奖励)",
]

# 悬赏条款起始位置：此前的部分是案情/标的，此后的部分是赏金条件
BOUNTY_SECTION_START = re.compile(
    r"(悬赏金额|悬赏条件|悬赏内容|悬赏奖励|悬赏金|奖励标准|悬赏事项|悬赏金计算方式)"
)

DATE_RANGE = re.compile(
    r"(20\d{2})\s*[年./-]\s*(\d{1,2})\s*[月./-]\s*(\d{1,2})\s*日?\s*"
    r"(?:至|到|~|-|—|起至)\s*"
    r"(20\d{2})\s*[年./-]\s*(\d{1,2})\s*[月./-]\s*(\d{1,2})\s*日?"
)

DURATION = re.compile(r"(?:悬赏)?(?:期限|有效期)[^。;]{0,10}?(\d+|[一二两三四五六七八九十]+)\s*(年|个月|月)")

CASE_NO = re.compile(
    r"[\(（]\s*(20\d{2})\s*[\)）]\s*([\u4e00-\u9fa5]{0,4}\d{0,4})\s*执"
    r"(?:恢|异|保|字|悬|监)?\s*[^\s,。;;、]{0,18}?\d+\s*号"
)

PHONE = re.compile(
    r"(?:(?:0\d{2,3}[-\s]?\d{7,8}(?:[-,、]\s?0?\d{2,3}[-\s]?\d{7,8})*)"
    r"|(?:1[3-9]\d{9}))"
)

PROVINCE_HINT = re.compile(r"(?:住|户籍地|住所地|地址|现住)[^,。;]{0,6}?([\u4e00-\u9fa5]{2,8}?(?:省|自治区|市))")


def _first_amount(patterns, text, window=14):
    """按模式顺序找第一个合理金额。"""
    for pat in patterns:
        for m in re.finditer(pat, text):
            g = m.groups()
            # 找到 (num, unit) 这一组
            num, unit = None, None
            for i in range(len(g) - 1, -1, -1):
                if g[i] and re.fullmatch(r"[0-9][0-9,]*(?:\.[0-9]+)?", g[i].strip()):
                    num = g[i]
                    unit = g[i + 1] if i + 1 < len(g) else None
                    break
            val = to_yuan(num, unit) if num else None
            if val and 100 <= val <= 5e10:
                return val, m.start()
    return None, -1


def extract_subject(text):
    v, _ = _first_amount(SUBJECT_PATTERNS, text)
    return v


def extract_ratio(text):
    """返回 0~1 的比例。优先强上下文，避免误取诉讼费/违约金比例。"""
    # 分段费率表（"基数金额100万元以下的部分 … 费率18%"）：取首档（最高档）费率
    m = re.search(r"费率[^0-9%]{0,8}(\d+(?:\.\d+)?)\s*%", text)
    if m:
        v = float(m.group(1))
        if 0.1 <= v <= 100:
            return v / 100.0
    for pat in RATIO_STRONG:
        for m in re.finditer(pat, text):
            try:
                v = float(m.group(1))
            except (ValueError, IndexError):
                continue
            if 0.1 <= v <= 100:
                return v / 100.0
    # 弱匹配：排除明显无关的百分比语境
    for m in re.finditer(RATIO_WEAK[0], text):
        v = float(m.group(1))
        if not (0.1 <= v <= 100):
            continue
        ctx = text[max(0, m.start() - 25): m.start()]
        if re.search(r"(利息|违约金|诉讼费|受理费|保全费|滞纳金|税费|评估费)", ctx):
            continue
        if re.search(r"(悬赏|奖励|赏金|举报|线索|执行到位|到位金额)", ctx):
            return v / 100.0
    return None


def extract_cap(text):
    for pat in CAP_PATTERNS:
        for m in re.finditer(pat, text):
            g = m.groups()
            num = unit = None
            for i in range(len(g) - 1, -1, -1):
                if g[i] and re.fullmatch(r"[0-9][0-9,]*(?:\.[0-9]+)?", g[i].strip()):
                    num, unit = g[i], g[i + 1] if i + 1 < len(g) else None
                    break
            val = to_yuan(num, unit) if num else None
            if val and 100 <= val <= 1e9:
                return val
    return None


def section_split(text):
    """切分为 (案情段, 悬赏条款段)。找不到锚点时两段相同。"""
    m = BOUNTY_SECTION_START.search(text)
    if not m:
        return text, text
    return text[:m.start()], text[m.start():]


def extract_fixed(text):
    """固定金额赏金（非比例）。只在悬赏条款段落内抽取。"""
    head, tail = section_split(text)
    scope = tail if len(tail) > 20 else text
    for pat in FIXED_PATTERNS:
        for m in re.finditer(pat, scope):
            g = m.groups()
            num = unit = None
            for i in range(len(g) - 1, -1, -1):
                if g[i] and re.fullmatch(r"[0-9][0-9,]*(?:\.[0-9]+)?", g[i].strip()):
                    num, unit = g[i], g[i + 1] if i + 1 < len(g) else None
                    break
            if not num or not unit:
                continue
            val = to_yuan(num, unit)
            if not val or not (50 <= val <= 1e8):
                continue
            after = text[m.end(): m.end() + 3]
            if "%" in after:      # "为执行到位金额的5%" 不是固定额
                continue
            # 金额之后同句内出现百分比 → 属于分段费率档位表
            # （"执行到位款项5000万元以内的，按5%发放悬赏金"），不是固定赏金
            s_end = scope.find("。", m.end())
            s_end = len(scope) if s_end < 0 else s_end
            if re.search(r"\d+(?:\.\d+)?\s*%", scope[m.end():s_end]):
                continue
            # "以215737.51元为限" 是封顶额，不是固定赏金
            if re.match(r"\s*元?\s*为\s*限", scope[m.end(): m.end() + 10]):
                continue
            # 前面是"最高不超过/上限/封顶"的，是封顶额不是固定赏金
            before = scope[max(0, m.start() - 16): m.start()]
            if re.search(r"(最高|不超过|不高于|上限|封顶)", before):
                continue
            return val
    return None


def extract_validity(text, publish_date=None):
    m = DATE_RANGE.search(text)
    if m:
        g = m.groups()
        try:
            f = date(int(g[0]), int(g[1]), int(g[2]))
            t = date(int(g[3]), int(g[4]), int(g[5]))
            if t >= f:
                return f.isoformat(), t.isoformat()
        except ValueError:
            pass
    m = DURATION.search(text)
    if m and publish_date:
        raw, unit = m.group(1), m.group(2)
        n = int(raw) if raw.isdigit() else (cn_to_num(raw) or 1)
        try:
            d0 = date.fromisoformat(publish_date)
        except Exception:
            return None, None
        days = n * 365 if unit == "年" else n * 30
        return d0.isoformat(), (d0 + timedelta(days=days)).isoformat()
    return None, None


def extract_case_no(text):
    m = CASE_NO.search(text)
    if m:
        return m.group(0).strip()
    return None


def extract_phone(text):
    vals = [x.strip() for x in PHONE.findall(text) if len(re.sub(r"\D", "", x)) >= 7]
    if not vals:
        return None
    # 去重保序
    seen, out = set(), []
    for v in vals:
        k = re.sub(r"\D", "", v)
        if k not in seen:
            seen.add(k)
            out.append(v)
    return "、".join(out[:3])


def extract_reward_type(text):
    has_where = bool(re.search(r"(下落|行踪|藏匿|踪迹|人身线索|找到被执行人|控制被执行人)", text))
    has_prop = bool(re.search(r"(财产线索|可供执行财产|隐匿|转移财产|财产状况|财产信息)", text))
    if has_where and has_prop:
        return "both"
    if has_where:
        return "whereabouts"
    if has_prop:
        return "property"
    return "unknown"


def extract_debtor_count(text):
    """估算被执行人数：统计"被执行人:"出现次数 + 顿号人数描述。"""
    n = len(re.findall(r"被执行人(?:一|二|三|四|五|六|七|八|九|十|\d)?\s*[:：]", text))
    return max(n, 1)


# 抽被执行人时常见的"假名字"（公告正文里的泛指表述）
_DEBTOR_BAD = (
    "财产", "下落", "未履行", "拒不", "名下", "线索", "信息", "已经履行",
    "存在", "户籍", "身份", "基本", "其他", "有关", "所提供", "具体", "相关",
    "可供执行", "隐匿", "转移", "履行", "悬赏", "本院", "申请执行人",
)
_CN_NAME = r"[\u4e00-\u9fa5A-Za-z·]{2,40}"
# 案由词：名字无标点直接粘连案由时（"董德波借款合同纠纷"）用它切开
_CAUSE_RE = re.compile(
    r"(?:借款|民间借贷|买卖|承揽|劳动争议|合同|损害|合伙|不当得利|机动车|"
    r"房屋|金融|租赁|建设工程|追偿|保险|票据|婚姻|继承|仲裁裁决|非涉外)")
_COMPANY_TAIL = re.compile(
    r"(有限公司|有限责任公司|股份公司|股份有限责任公司|合伙企业|合作社|"
    r"事务所|经营部|工作室|中心|厂|银行|分公司)$")


def _clean_debtor(s):
    s = (s or "").strip()
    s = re.sub(r"^[：:、\s]+", "", s)
    s = re.sub(r"[（(][^）)]{0,24}[）)]", "", s)      # 去掉括号注释
    # 砍掉紧跟的证件/住址说明（"刘庆明身份证号：..."）
    s = re.split(r"身份证|公民身份|身份号码|住址|住所地|统一社会信用|法定代表人", s)[0]
    s = re.split(r"[，,。；;、\s]", s)[0].strip()
    # 名字后面没标点、直接粘连案由的情况
    if s and not _COMPANY_TAIL.search(s):
        s = _CAUSE_RE.split(s)[0].strip()
    if not s or not (2 <= len(s) <= 40):
        return None
    if any(b in s for b in _DEBTOR_BAD):
        return None
    if re.fullmatch(r"[0-9\W]+", s):
        return None
    return s


def extract_debtor(text):
    """从正文抽第一个被执行人名称（个人或单位）。

    公告网接口自带该字段（tosendPeople），只有法院官网源需要从这里抽。
    """
    if not text:
        return None
    t = re.sub(r"\s+", " ", text)

    # 1) 明确列举：被执行人：XXX，男/女… | 被执行人：XX有限公司，住所地…
    for m in re.finditer(r"被执行人[一二三四五六七八九十\d]?\s*[：:]\s*(" + _CN_NAME + ")", t):
        c = _clean_debtor(m.group(1))
        if c:
            return c

    # 2) 无冒号但紧跟性别：（被执行人周赵，男）
    for m in re.finditer(r"被执行人\s*(" + _CN_NAME + r")\s*[，,]\s*(?:男|女)", t):
        c = _clean_debtor(m.group(1))
        if c:
            return c

    # 3) 名字后跟案由：被执行人董德波借款合同纠纷一案
    #    用贪婪匹配再回溯到"纠纷"前，这样顿号并列的名单能整段吃掉，
    #    _clean_debtor 只取第一个，避免把"吴流明"截成"吴流"。
    for m in re.finditer(r"被执行人\s*([^\n。；;]{2,120})(?=[^。；;]{0,16}纠纷)", t):
        c = _clean_debtor(m.group(1))
        if c:
            return c

    # 4) 名字后跟"发布悬赏公告"/"的财产"/"名下"
    for m in re.finditer(r"被执行人\s*(" + _CN_NAME + r"?)(?=发布|的财产|的线索|名下)", t):
        c = _clean_debtor(m.group(1))
        if c:
            return c

    return None


def looks_like_bounty(text):
    if not text:
        return False
    if "悬赏" not in text and "赏金" not in text:
        return False
    if not re.search(r"(执行|被执行人|执行案号|执\s*\d)", text):
        return False
    return True


# ---------------- 汇总 ----------------

def compute_reward(subject, ratio, cap, fixed):
    """推算预期最高奖励。

    规则：
    - 有比例+标的 -> 标的*比例，若有封顶则取小
    - 只有固定额 -> 固定额
    - 比例有但标的缺失 -> None（无法推算）
    返回 (est, basis) basis 说明口径
    """
    est_pct = None
    if ratio and subject:
        est_pct = subject * ratio
        if cap:
            est_pct = min(est_pct, cap)
    candidates = [v for v in (est_pct, fixed) if v]
    if not candidates:
        return None, "unknown"
    best = max(candidates)
    if est_pct and fixed and est_pct >= fixed:
        return best, "ratio"
    if est_pct and not fixed:
        return best, "ratio"
    if fixed and not est_pct:
        return best, "fixed"
    return best, "fixed"


def parse_notice(content, publish_date=None):
    text = normalize(content or "")
    if not text:
        return {}
    subject = extract_subject(text)
    ratio = extract_ratio(text)
    cap = extract_cap(text)
    fixed = extract_fixed(text)
    case_no = extract_case_no(text)

    # 一致性校验：固定赏金不可能约等于执行标的或封顶额（说明抽串了）
    if fixed and subject and fixed >= subject * 0.95:
        fixed = None
    if fixed and cap and abs(fixed - cap) <= max(1.0, cap * 0.01):
        fixed = None
    if cap and subject and cap >= subject * 0.95 and ratio:
        cap = None

    est, basis = compute_reward(subject, ratio, cap, fixed)
    vf, vt = extract_validity(text, publish_date)

    quality = 0.0
    for v, w in ((subject, .3), (ratio, .25), (est, .25), (vf, .1),
                 (case_no, .05), (publish_date, .05)):
        if v:
            quality += w

    return {
        "subject_amount": subject,
        "reward_ratio": ratio,
        "reward_cap": cap,
        "reward_fixed": fixed,
        "reward_est": round(est, 2) if est else None,
        "reward_basis": basis,
        "reward_type": extract_reward_type(text),
        "valid_from": vf,
        "valid_to": vt,
        "case_no": case_no,
        "contact_phone": extract_phone(text),
        "debtor_count": extract_debtor_count(text),
        "debtor": extract_debtor(text),
        "parse_quality": round(quality, 2),
        "content_norm": text,
    }
