# -*- coding: utf-8 -*-
"""离线自测：用假 HTTP 跑通「法院清单 → 遍历 → 解析 → 入库」整条链路。

不联网，验证的是代码逻辑：
  - 法院清单完整性（华南 + 江浙沪区县是否列全）
  - 翻页逻辑（total 大于一页时能不能翻完）
  - 限流判定（连续 0 时是否停下来）
  - 账本断点续跑（第二轮是否跳过已查的法院）

    python scripts/selftest_nationwide.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "collectors"))

from _shared import crawl_ledger, court_crawler, court_registry, rmfygg  # noqa: E402

FAILS = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


class FakeHttp:
    """按 courtParam 返回预设的公告条数，模拟公告网分页。"""

    def __init__(self, totals, block_after=None):
        self.totals = totals          # {法院短名: 总条数}
        self.block_after = block_after
        self.count = 0
        self.calls = []

    def request(self, url, data=None, headers=None, method=None):
        self.count += 1
        if self.block_after is not None and self.count > self.block_after:
            raise RuntimeError("403 限流")
        ao = json.loads(data[rmfygg.P + "aoData"])
        start = next(x["value"] for x in ao if x["name"] == "iDisplayStart")
        length = next(x["value"] for x in ao if x["name"] == "iDisplayLength")
        court = data[rmfygg.P + "courtParam"]
        self.calls.append((court, start, length))
        total = self.totals.get(court, 0)
        rows = []
        for i in range(start, min(start + length, total)):
            rows.append({
                "uuid": "u-%s-%d" % (court, i),
                "court": court,
                "noticeType": "开庭传票",
                "publishDate": "2026-11-01",
                "noticeContent": "本院定于2026年11月20日9时在第%d法庭公开开庭" % i,
                "tosendPeople": "",
            })
        return json.dumps({"iTotalRecords": total, "data": rows},
                          ensure_ascii=False)


def test_registry():
    print("\n[1] 法院清单完整性")
    pri = court_registry.priority_courts(levels=("基层",))
    names = set(c["name"] for c in pri)
    check(len(pri) >= 540, "华南+江浙沪基层法院 %d 家（≥540）" % len(pri))
    must = ["广州市越秀区人民法院", "深圳市罗湖区人民法院", "上海市浦东新区人民法院",
            "杭州市余杭区人民法院", "南京市江宁区人民法院", "厦门市思明区人民法院",
            "南宁市青秀区人民法院", "海口市龙华区人民法院", "三亚市城郊人民法院",
            "东莞市第一人民法院", "儋州市人民法院", "五指山市人民法院"]
    for m in must:
        check(m in names, "清单包含 %s" % m)
    bad = [n for n in names if "市市" in n or "区区" in n or "县县" in n]
    check(not bad, "没有 市市/区区 这类拼接错误（%s）" % (bad[:3] or "无"))
    check(court_registry.by_province("海南省") and
          "儋州市儋州市人民法院" not in names, "儋州只有一个法院")


def test_pagination():
    print("\n[2] 翻页（以前每次只抓一页）")
    http = FakeHttp({"罗湖": 40})           # 40 条 = 3 页
    c = court_crawler.CourtCrawler(http, interval=0, max_pages=5,
                                   notice_types=["开庭传票"], keywords=[""])
    rows = list(c.iter_court("深圳市罗湖区人民法院", "罗湖", verbose=False))
    check(len(rows) == 40, "40 条全部抓到（实际 %d）" % len(rows))
    check(http.count == 3, "发了 3 次请求（实际 %d）" % http.count)
    starts = [s for _, s, _ in http.calls]
    check(starts == [0, 15, 30], "翻页 offset 正确：%s" % starts)


def test_max_pages():
    print("\n[3] max_pages 上限生效")
    http = FakeHttp({"福田": 200})
    c = court_crawler.CourtCrawler(http, interval=0, max_pages=2,
                                   notice_types=["开庭传票"], keywords=[""])
    rows = list(c.iter_court("深圳市福田区人民法院", "福田", verbose=False))
    check(len(rows) == 30, "两个组合各 2 页 = 30 条（实际 %d）" % len(rows))


def test_blocked():
    print("\n[4] 连续返回 0 时判定限流并停止")
    http = FakeHttp({})
    courts = [{"name": "甲法院", "short": "甲"}, {"name": "乙法院", "short": "乙"},
               {"name": "丙法院", "short": "丙"}, {"name": "丁法院", "short": "丁"},
               {"name": "戊法院", "short": "戊"}]
    c = court_crawler.CourtCrawler(http, interval=0, notice_types=[""],
                                   keywords=[""], stop_after_zero=3)
    done = []
    try:
        for court, _ in c.iter_all(courts=courts, verbose=False,
                                   on_done=lambda ct, n, st, note:
                                   done.append((ct["name"], st))):
            pass
    except court_crawler.BlockedError:
        pass
    check(c.blocked, "已标记 blocked")
    check(all(st in ("ok", "blocked") for _, st in done), "账本回调收到状态：%s" % done)
    check(len(done) <= 3, "没把 5 家全跑完（只跑了 %d 家）" % len(done))


def test_budget():
    print("\n[5] 请求预算")
    http = FakeHttp({"罗湖": 100, "福田": 100})
    c = court_crawler.CourtCrawler(http, interval=0, max_pages=10,
                                   notice_types=[""], keywords=[""], budget=3)
    courts = [{"name": "深圳市罗湖区人民法院", "short": "罗湖"},
              {"name": "深圳市福田区人民法院", "short": "福田"}]
    n = 0
    try:
        for _ in c.iter_all(courts=courts, verbose=False):
            n += 1
    except court_crawler.BlockedError:
        pass
    check(http.count <= 3, "请求数不超过预算（实际 %d）" % http.count)


def test_ledger(tmpdir):
    print("\n[6] 账本断点续跑")
    path = crawl_ledger.path_for(tmpdir)
    data = crawl_ledger.load(path)
    courts = [{"name": "甲法院"}, {"name": "乙法院"}, {"name": "丙法院"}]
    check(len(crawl_ledger.pick_courts(data, courts, "开庭")) == 3, "初次全部待查")
    crawl_ledger.record(data, "甲法院", "开庭", found=3)
    crawl_ledger.save(path, data)
    data = crawl_ledger.load(path)
    todo = crawl_ledger.pick_courts(data, courts, "开庭", cool_hours=6)
    check([c["name"] for c in todo] == ["乙法院", "丙法院"],
          "第二轮跳过已查的甲法院：%s" % [c["name"] for c in todo])
    todo2 = crawl_ledger.pick_courts(data, courts, "开庭", cool_hours=0)
    check("甲法院" in [c["name"] for c in todo2], "冷却期过了会重查甲法院")
    s = crawl_ledger.summary(data, courts, "开庭")
    check(s == {"total": 3, "done": 1, "todo": 2}, "统计正确：%s" % s)
    os.remove(path)


def main():
    test_registry()
    test_pagination()
    test_max_pages()
    test_blocked()
    test_budget()
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        test_ledger(td)
    print("\n" + "=" * 50)
    if FAILS:
        print("失败 %d 项：" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        sys.exit(1)
    print("全部通过")


if __name__ == "__main__":
    main()
