# -*- coding: utf-8 -*-
"""数据源：人民法院公告网「开庭传票」类公告。

与悬赏源同一个 Liferay 接口，差别只在 noticeTypeVal 传中文类型名。
公告全文里同时含：受送达人、原告、被告、案号、案由、开庭时间、法庭、
诉讼请求金额——是开庭看板信息最全的源，覆盖全国各级法院。

公告类型只取两种：
  开庭传票                 —— 已立案并排期，向被告送达传票
  起诉状副本及开庭传票      —— 起诉材料与传票一并送达，被告刚被起诉
"""
import config
import parser as parser_hearing
from _shared import rmfygg

SOURCE = "rmfygg_kt"


def to_record(raw, notice_type):
    rec = rmfygg.to_record(raw)
    rec["source"] = SOURCE
    rec["notice_type"] = notice_type or rec.get("notice_type") or ""
    return rec


def iter_type(http, keyword, notice_type, max_pages=None, verbose=False):
    """按「关键词 × 公告类型」翻页，产出统一原始记录。"""
    max_pages = config.HEARING_MAX_PAGES if max_pages is None else max_pages
    for raw in rmfygg.iter_keyword(http, keyword, max_pages=max_pages,
                                   notice_type="", notice_type_val=notice_type):
        rec = to_record(raw, notice_type)
        text = rec.get("content") or ""
        if not parser_hearing.looks_like_hearing(text):
            continue
        yield rec


def iter_all(http, keywords=None, notice_types=None, max_pages=None,
             verbose=True):
    keywords = keywords or config.HEARING_KEYWORDS
    notice_types = notice_types or config.HEARING_NOTICE_TYPES
    for kw in keywords:
        for nt in notice_types:
            if verbose:
                print(f"  [公告网] {nt} · {kw}")
            for rec in iter_type(http, kw, nt, max_pages=max_pages,
                                 verbose=verbose):
                yield rec
