"""去重与跨源折叠。

三层设计，逐层收紧：
  L1 主键去重  hash(normalize(title) + publish_date + source_id) —— 本源自重复
  L2 归一化    全角半角、空白、【】前缀统一（scripts/dedup.normalize_title）
  L3 跨源折叠  SimHash 相似度聚类 —— 同一个标转投多个平台时归为一簇

L3 是 CaseBoard「跨来源重复折叠」的结构化实现：只保留最早记录对外暴露
（is_primary），其余仍留在库里但列表默认聚合，点开可看同源镜像。
"""

from __future__ import annotations

import hashlib
import os
import re
import sys

# 去重键与标题归一化收敛到 scripts/dedup.py —— 与 scripts/manage.py 的文件导入
# 共用同一份定义，避免「采集 sha256 / 导入 sha1」两个键互不认账、同一条存两份。
# 向上拿到仓库根再进 scripts/（本文件在 collectors/tender/app/crawler/ 下，
# parents[4] 正好是仓库根），不依赖调用方是否已经把 scripts 加进 sys.path。
_SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", "scripts")
_SCRIPTS = os.path.normpath(_SCRIPTS)
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from scripts.dedup import make_hash_key, normalize_title  # noqa: E402

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Tender

# 停用词：招标文件标题里的高频无信息词，参与相似度计算前剔除
_STOPWORDS = {
    "公告", "招标", "采购", "项目", "结果", "中标", "成交", "更正", "变更",
    "补充", "通知", "关于", "的", "为", "和", "与", "及", "有限公司",
    "公司", "中心", "管理", "服务", "第", "号", "次", "重新", "废标",
}


def _tokens(title: str) -> set[str]:
    """标题切词。中文按 2-gram，英文数字按词。"""
    s = normalize_title(title)
    if not s:
        return set()
    s = re.sub(r"[^\w\u4e00-\u9fff]+", " ", s)
    toks: set[str] = set()
    # 英文/数字词
    for w in re.findall(r"[a-z0-9]{2,}", s):
        toks.add(w)
    # 中文 2-gram
    han = re.findall(r"[\u4e00-\u9fff]+", s)
    for seg in han:
        if len(seg) < 2:
            toks.add(seg)
            continue
        for i in range(len(seg) - 1):
            toks.add(seg[i : i + 2])
    return {t for t in toks if t not in _STOPWORDS}


def simhash(title: str) -> int:
    """64 位 SimHash。相同/相近标题会得到相近指纹。"""
    toks = _tokens(title)
    if not toks:
        return 0
    vec = [0] * 64
    for t in toks:
        h = int(hashlib.md5(t.encode("utf-8")).hexdigest(), 16)
        for i in range(64):
            vec[i] += 1 if (h >> i) & 1 else -1
    fp = 0
    for i in range(64):
        if vec[i] > 0:
            fp |= 1 << i
    return fp


def hamming(a: int, b: int) -> int:
    """汉明距离。"""
    return bin(a ^ b).count("1")


# make_hash_key / normalize_title 由 scripts/dedup.py 提供（见文件头 import）。
# 这段保留历史说明：第三参数经历过一次修正，早先是 purchaser，但列表页这一栏
# 极不稳定，同一条公告两次抓取会得到两个不同值 → 两个 hash → 雷达里出现两条；
# 改用 source_id 后本源复抓必同键，跨源重复交给 L3 SimHash 折叠，职责清晰。


def jaccard(a: str, b: str) -> float:
    """标题 token 集合的 Jaccard 相似度，作为 SimHash 的二次确认。"""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def is_duplicate(title_a: str, title_b: str, ham_threshold: int = 12, jac_threshold: float = 0.62) -> bool:
    """判定两条标题是否同一件事。

    SimHash 快（64 位整数比较，可全表扫）+ Jaccard 准（兜住 SimHash 边界抖动）。
    """
    ham = hamming(simhash(title_a), simhash(title_b))
    if ham > ham_threshold:
        return False
    return jaccard(title_a, title_b) >= jac_threshold


def find_existing_cluster(session: Session, title: str, exclude_id: int | None = None) -> int | None:
    """在库里找同簇。命中则返回 cluster_id，否则 None。

    只与主记录（is_primary=True）比对，避免簇内自我膨胀。
    """
    rows = session.execute(
        select(Tender.id, Tender.title, Tender.cluster_id).where(Tender.is_primary.is_(True))
    ).all()

    fp_target = simhash(title)
    best_id: int | None = None
    best_ham = 64
    for tid, t_title, t_cluster in rows:
        if exclude_id is not None and tid == exclude_id:
            continue
        ham = hamming(fp_target, simhash(t_title))
        if ham < best_ham:
            best_ham = ham
            best_id = tid
            best_cluster = t_cluster

    if best_id is None or best_ham > 12:
        return None
    # SimHash 接近时再用 Jaccard 确认一次
    row = session.get(Tender, best_id)
    if row and jaccard(title, row.title) >= 0.62:
        return best_cluster if best_cluster is not None else best_id
    return None


def assign_cluster(session: Session, tender: Tender) -> None:
    """给新记录分配 cluster_id，并把同簇里最早的设为 is_primary。"""
    cid = find_existing_cluster(session, tender.title, exclude_id=tender.id)

    if cid is None:
        tender.cluster_id = tender.id
        tender.is_primary = True
        return

    tender.cluster_id = cid
    # 同簇保留最早发布的为唯一主记录
    siblings = session.execute(
        select(Tender).where(Tender.cluster_id == cid)
    ).scalars().all()
    siblings.append(tender)

    def _key(t: Tender):
        return t.publish_time or t.created_at

    ordered = sorted(siblings, key=_key)
    for idx, t in enumerate(ordered):
        t.is_primary = idx == 0
        t.cluster_id = cid
