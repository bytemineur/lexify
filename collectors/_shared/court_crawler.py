# -*- coding: utf-8 -*-
"""全国法院遍历采集：按法院名逐个检索人民法院公告网。

与现有的关键词检索互补：
  - 关键词检索（rmfygg.iter_keyword）  → 覆盖「说什么」：某类案由的公告
  - 法院遍历（本模块）                  → 覆盖「谁发的」：某家法院的全部公告

为什么需要后者：关键词检索天然漏掉不含这些词的公告；按法院遍历能把
某一家法院的开庭传票、悬赏公告整批拉全，覆盖率由「法院清单」保证。

设计要点：
  1. 法院清单来自 _shared/court_registry（省高院 / 地市中院 / 区县基层 / 特殊法院）
  2. 公告网的 courtParam 是模糊匹配，传短名召回更稳
  3. **节流是硬要求**：公告网对高频请求会返回 0 条（表现为「没数据」而不报错），
     所以默认每次请求间隔、每次运行最多查多少家法院都可配
  4. 一旦连续 N 家法院都返回 0，判定为被限流，立即停止本轮，不做无谓请求
"""
import time

from . import config, court_registry, rmfygg


class BlockedError(RuntimeError):
    """公告网判定限流 / 封禁，本轮必须停。"""


class CourtCrawler:
    """按法院遍历公告网。"""

    def __init__(self, http, *, interval=None, max_pages=3,
                 notice_types=(), keywords=(), stop_after_zero=8,
                 budget=0, cooldown=0.0):
        self.http = http
        self.interval = config.RMFYGG_INTERVAL if interval is None else interval
        self.max_pages = max_pages
        self.notice_types = list(notice_types or [])
        self.keywords = list(keywords or [])
        self.stop_after_zero = stop_after_zero
        self.budget = budget or 0          # 本次最多发多少个请求，0 = 不限
        self.cooldown = cooldown           # 请求异常时冷却秒数
        self._last = 0.0
        self.requests = 0
        self.blocked = False
        # 连续返回 0 的计数必须跨法院累计：限流的典型表现就是「每家都返回 0」，
        # 如果只在单家法院内部计数，永远到不了阈值，会白跑整张清单。
        self._zero_run = 0

    def _wait(self):
        gap = self.interval - (time.time() - self._last)
        if gap > 0:
            time.sleep(gap)
        self._last = time.time()

    def _fetch(self, kw, start, nt, key):
        """发一次请求；限流 / 403 时抛出 BlockedError。"""
        self._wait()
        self.requests += 1
        try:
            return rmfygg.fetch_page(
                self.http, kw, start=start, length=config.PAGE_SIZE,
                notice_type_val=nt, court_name=key)
        except RuntimeError as e:
            # http.py 里 403 会包成 RuntimeError
            self.blocked = True
            raise BlockedError(str(e)) from e

    def iter_court(self, court_name, short_name=None, verbose=True):
        """产出某一家法院的全部公告（原始 dict）。"""
        key = short_name or court_name
        for kw in (self.keywords or [""]):
            for nt in (self.notice_types or [""]):
                if self.budget and self.requests >= self.budget:
                    raise BlockedError("本次请求预算已用完（%d 次）" % self.budget)
                page = self._fetch(kw, 0, nt, key)
                total = page.get("iTotalRecords") or 0
                rows = page.get("data") or []
                if total == 0 and not rows:
                    self._zero_run += 1
                    if self._zero_run >= self.stop_after_zero:
                        self.blocked = True
                        raise BlockedError(
                            "连续 %d 次查询返回 0，判定为限流" % self._zero_run)
                    continue
                self._zero_run = 0
                got = 0
                for r in rows:
                    got += 1
                    yield r
                start = config.PAGE_SIZE
                while got < total and start < self.max_pages * config.PAGE_SIZE:
                    if self.budget and self.requests >= self.budget:
                        raise BlockedError("本次请求预算已用完（%d 次）" % self.budget)
                    page = self._fetch(kw, start, nt, key)
                    rows = page.get("data") or []
                    if not rows:
                        break
                    for r in rows:
                        got += 1
                        yield r
                    start += config.PAGE_SIZE
                if verbose:
                    print("    %s · %s%s → %d/%d 条" % (
                        court_name, kw or "全部",
                        ("·" + nt) if nt else "", got, total))

    def iter_all(self, courts=None, *, levels=None, province=None,
                 limit=0, verbose=True, on_done=None):
        """遍历法院清单，产出 (法院信息, 原始记录)。

        levels: 只跑哪些层级，如 ("基层",) 或 ("高院", "中院")
        limit:  最多查多少家法院（控制单轮耗时）
        on_done(court, n_records, status, note): 每家法院查完后的回调（记账本）
        """
        pool = courts if courts is not None else court_registry.ALL_COURTS
        if levels:
            pool = [c for c in pool if c["level"] in levels]
        if province:
            pool = [c for c in pool if c["province"] == province]
        if limit:
            pool = pool[:limit]

        for i, c in enumerate(pool, 1):
            if verbose:
                print("\n[%d/%d] %s（%s · %s）" % (
                    i, len(pool), c["name"], c["province"] or "-", c["level"]))
            n = 0
            status, note = "ok", ""
            try:
                for raw in self.iter_court(c["name"], c.get("short"), verbose=verbose):
                    n += 1
                    yield c, raw
            except BlockedError as e:
                status, note = "blocked", str(e)
                if verbose:
                    print("  ⚠ %s" % e)
                if on_done:
                    on_done(c, n, status, note)
                return
            except RuntimeError as e:
                # 单家法院的偶发失败不影响整轮
                status, note = "error", str(e)
                if verbose:
                    print("  ! %s 失败: %s" % (c["name"], str(e)[:80]))
            except KeyboardInterrupt:
                if on_done:
                    on_done(c, n, "interrupted", "")
                raise
            if status == "ok" and on_done:
                on_done(c, n, status, note)
            if self.cooldown:
                # 正常节奏下也留一点余量，进一步降低被限流的概率
                time.sleep(min(self.cooldown, 1.0))
