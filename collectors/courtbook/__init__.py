# -*- coding: utf-8 -*-
"""法院通讯录采集包。

源站页面提供「法院名称 / 地址 / 邮编 / 电话」，落地到 data/lexify.db 的
courts 表，覆盖情况写 court_coverage（缺的省要明确标出来）。

    python -m collectors.courtbook            # 全量抓取并入库
    python -m collectors.courtbook status     # 看库里现在有什么
"""
import os
import sys

_COLL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _COLL not in sys.path:
    sys.path.insert(0, _COLL)
