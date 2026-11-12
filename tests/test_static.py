# -*- coding: utf-8 -*-
"""Lexify 静态自检：不开浏览器，只查「站点能不能离线跑、数据自不自洽、页面之间有没有断链」。

用法：
    python tests/test_static.py

覆盖：
    1. 零外部依赖 —— 不能出现 CDN / 外链字体 / 远程脚本，否则部署到内网或 file:// 会白屏
    2. 相对链接可达 —— 所有 href/src 指向的文件真实存在
    3. 数据文件格式 —— web/data/*.js 必须是 window.__BOARD__ = {合法 JSON};
    4. 数据自洽 —— 行数 = meta.total，首页汇总 = 三块之和，省份数不越界
    5. 看板骨架 —— 每页都有三块导航、当前页高亮、首屏不闪主题、必要的 DOM 挂载点
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")
DATA = os.path.join(WEB, "data")

BOARDS = [
    ("tender.html", "tender", "招标雷达"),
    ("hearing.html", "hearing", "开庭公告"),
    ("bounty.html", "bounty", "执行悬赏"),
]

# 页面必须挂载的节点，引擎按 id 取
REQUIRED_IDS = ["bTitle", "bSub", "bMeta", "kpis", "facets", "q", "tcount",
                "btnReset", "btnExport", "thead", "tbody", "pager", "aside", "modal"]

fails, warns, passes = [], [], []


def ok(name, cond, extra=""):
    (passes if cond else fails).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (" :: " + str(extra) if extra else ""))


def warn(name, extra=""):
    warns.append(name)
    print("  WARN " + name + (" :: " + str(extra) if extra else ""))


def read(p):
    return open(p, encoding="utf-8").read()


def check_offline(html_files):
    print("\n[1] 零外部依赖")
    for f in html_files:
        s = read(f)
        tag_ext = re.findall(r'<(?:script|link|img)[^>]+(?:src|href)\s*=\s*["\'](https?://[^"\']+)', s)
        # <a href="http..."> 是内容链接，允许；<link rel="icon" href="data:..."> 也允许
        ok("%s 无外部脚本/样式/图片" % os.path.basename(f), not tag_ext, ",".join(tag_ext[:3]))
        ok("%s 无 Google Fonts / CDN 引用" % os.path.basename(f),
           "fonts.googleapis" not in s and "cdn.jsdelivr" not in s and "unpkg.com" not in s)


def check_links(html_files):
    print("\n[2] 相对链接可达")
    for f in html_files:
        s = read(f)
        html_body = re.sub(r"<script[\s\S]*?</script>", "", s)   # 脚本里的字符串不算
        refs = re.findall(r'(?:href|src)\s*=\s*"([^"]+)"', html_body)
        broken = []
        for r in refs:
            if r.startswith(("http://", "https://", "data:", "#", "mailto:", "javascript:")):
                continue
            target = os.path.join(WEB, r.split("?")[0].split("#")[0])
            if not os.path.exists(target):
                broken.append(r)
        ok("%s 引用文件均存在" % os.path.basename(f), not broken, "断链 " + ", ".join(broken))


def load_data(key):
    path = os.path.join(DATA, key + ".js")
    if not os.path.exists(path):
        return None
    s = read(path)
    m = re.match(r"^\s*window\.__BOARD__\s*=\s*([\s\S]*);\s*$", s)
    if not m:
        return "FORMAT"
    try:
        return json.loads(m.group(1))
    except ValueError as e:
        return "JSON:%s" % e


def load_overview():
    s = read(os.path.join(DATA, "overview.js"))
    m = re.match(r"^\s*window\.__OVERVIEW__\s*=\s*([\s\S]*);\s*$", s)
    return json.loads(m.group(1)) if m else None


def check_data():
    print("\n[3] 数据文件格式")
    boards = {}
    for _, key, name in BOARDS:
        d = load_data(key)
        ok("%s.js 可解析为 JSON" % key, isinstance(d, dict), d if isinstance(d, str) else "")
        if isinstance(d, dict):
            boards[key] = d
            ok("%s.js 有 rows / meta" % key, "rows" in d and "meta" in d)
            ok("%s 行数与 meta.total 一致" % key, len(d["rows"]) == d["meta"].get("total"),
               "%d / %s" % (len(d["rows"]), d["meta"].get("total")))
            ok("%s 有生成时间" % key, bool(d["meta"].get("generatedAt")),
               d["meta"].get("generatedAt"))
            ok("%s 每行都有 id" % key, all(r.get("id") is not None for r in d["rows"]))
            ok("%s 没有遗留内部字段 _provs" % key, "_provs" not in d)

    print("\n[4] 数据自洽")
    ov = load_overview()
    ok("overview.js 可解析", isinstance(ov, dict))
    if isinstance(ov, dict) and boards:
        total = sum(d["meta"]["total"] for d in boards.values())
        ok("首页总数 = 三块之和", ov.get("total") == total, "%s / %s" % (ov.get("total"), total))
        ok("首页含三块明细", set(ov.get("boards", {})) == set(boards),
           ",".join(sorted(ov.get("boards", {}))))
        ok("首页省份数合理（1–34）", 1 <= (ov.get("provinces") or 0) <= 34, ov.get("provinces"))

        # 每条记录的正文不能是 undefined 字符串、不能超长（前端按 8000 截断）
        for key, d in boards.items():
            over = [r for r in d["rows"] if r.get("body") and len(r["body"]) > 8000]
            ok("%s 正文长度均 ≤ 8000" % key, not over, "%d 条超长" % len(over))
            bad = [r for r in d["rows"] if r.get("body") == "None"]
            ok("%s 无 'None' 字符串正文" % key, not bad, "%d 条" % len(bad))

        # 省份名不能带「省/市」后缀混排（跨源并集的老坑）
        import collections
        for key, d in boards.items():
            provs = collections.Counter(r.get("prov") for r in d["rows"] if r.get("prov"))
            twins = [p for p in provs if p.endswith("省") and p[:-1] in provs]
            ok("%s 省份无「吉林省/吉林」重复" % key, not twins, ",".join(twins))


def check_shell(html_files):
    print("\n[5] 看板骨架")
    for page, key, name in BOARDS:
        s = read(os.path.join(WEB, page))
        missing = [i for i in REQUIRED_IDS if ('id="%s"' % i) not in s]
        ok("%s 挂载点齐全" % page, not missing, ",".join(missing))
        ok("%s 加载 data/%s.js" % (page, key), ("data/%s.js" % key) in s)
        ok("%s 加载 board.js" % page, "board.js" in s)
        ok("%s 三块导航齐全" % page,
           all(h in s for h in ("tender.html", "hearing.html", "bounty.html")))
        ok("%s 当前页导航高亮" % page, 'data-b="%s" class="on"' % key in s)
        ok("%s 首屏不闪主题（内联脚本）" % page, "lx-theme" in s and "data-theme" in s)
        ok("%s 有页面标题" % page, re.search(r"<title>[^<]+</title>", s) and
           "Lexify" in re.search(r"<title>([^<]+)</title>", s).group(1))
        ok("%s 有 meta description" % page, 'name="description"' in s)
        ok("%s 声明 lang=zh-CN" % page, 'lang="zh-CN"' in s)

    idx = read(os.path.join(WEB, "index.html"))
    ok("首页加载 overview.js", "data/overview.js" in idx)
    ok("首页三张卡片齐全", idx.count('data-board="') == 3, "卡片 %d 张" % idx.count('data-board="'))
    ok("首页指向三块看板", all(h in idx for h, _, _ in BOARDS))
    ok("首页数字由 data-m 驱动（不是写死）", idx.count("data-m=") >= 8, idx.count("data-m="))
    # 首屏右侧是真实记录预览，不是装饰图：容器在，数据来自 overview.sample
    ok("首屏有真实记录预览容器", 'id="previewRows"' in idx)
    ok("无装饰性迷你柱图残留", "data-spark=" not in idx)


def main():
    html_files = [os.path.join(WEB, p) for p in
                  ("index.html", "tender.html", "hearing.html", "bounty.html")]
    missing = [p for p in html_files if not os.path.exists(p)]
    if missing:
        print("缺少页面：" + ", ".join(missing))
        return 1

    check_offline(html_files)
    check_links(html_files)
    check_data()
    check_shell(html_files)

    print("\n合计 %d 项通过，%d 项失败，%d 项提醒" % (len(passes), len(fails), len(warns)))
    if fails:
        print("失败项：\n  - " + "\n  - ".join(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
