# -*- coding: utf-8 -*-
"""招标公告的「去重键」唯一定义（零依赖，纯标准库）。

这一份是全项目唯一的去重键事实源：采集入库（collectors/tender/app/crawler/dedupe.py）
与文件导入（scripts/manage.py::ensure_key）都必须调用同一个 make_hash_key，
否则两条等价公告会算出两个键、各存一份，去重形同虚设。

历史教训：早先键是 sha1(标题|采购人|发布日)，但采购人在列表页极不稳定，
同一条公告两次抓取会得到两个不同的采购人 → 两个键 → 雷达里出现两条重复。
后改成 sha256(normalize_title(标题)|发布日|source_id)：本源复抓必同键，
跨源重复交给 SimHash 聚类折叠，职责分离。

为什么放 scripts/ 而不是 collectors/ 下面：
  - manage.py 是「命令/导出层」，不该 import collectors/（那会连带拖进
    sqlalchemy 一整套采集依赖；站点、导入、导出应当是零第三方的）。
  - 采集层的 crawler/dedupe.py 需要时，向上 import 这个纯函数模块即可。
"""
from __future__ import annotations

import hashlib
import re

# 全角字母数字 → 半角
_FULL2HALF = {chr(0xFF01 + i): chr(0x21 + i) for i in range(94)}
_FULL2HALF[0x3000] = " "  # 全角空格

# 公告标题常见的装饰前缀，剥离后再做相似度比较，如「【采购】【变更】关于…」
_PREFIX_NOISE = re.compile(r"^\s*([【\[（(][^】\]）)]{1,12}[】\]）)]\s*)+")


def to_halfwidth(s: str) -> str:
    """全角转半角。"""
    return s.translate(_FULL2HALF) if s else ""


def normalize_title(title: str | None) -> str:
    """标题归一化——去重与跨源折叠的比对基准。

    步骤：全角转半角 → 去空白 → 剥离【xx】装饰前缀 → 统一标点 → 小写。
    """
    if not title:
        return ""
    s = to_halfwidth(title)
    s = re.sub(r"\s+", "", s)
    s = _PREFIX_NOISE.sub("", s)
    s = s.replace("：", ":").replace("，", ",").replace("；", ";")
    return s.strip().lower()


def make_hash_key(title: str, publish_date: str, source_id: str = "") -> str:
    """L1 去重键：一本源内复抓必同键。

    - title        原始标题（内部会归一化）
    - publish_date ISO 日期字符串（YYYY-MM-DD）
    - source_id    来源标识，跨源重复交给上一层的 SimHash 折叠处理
    """
    base = f"{normalize_title(title)}|{publish_date}|{source_id}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()
