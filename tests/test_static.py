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

    print("\n[4b] KPI 量纲")
    # KPI 的数字和标签必须是同一个量：条数配「条」，金额配「亿」。
    # 招标这一项曾经是「带预算金额 259」——259 是条数，标签写的是金额。
    if "tender" in boards:
        d = boards["tender"]
        s = sum(r["amount"] for r in d["rows"] if r.get("amount"))
        ok("招标 meta 的预算合计等于各行金额之和",
           abs((d["meta"].get("amountSum") or 0) - s) < 1,
           "%s / %s" % (d["meta"].get("amountSum"), s))
        html = read(os.path.join(WEB, "tender.html"))
        ok("招标预算 KPI 取金额（amountSum）而非条数",
           "m.amountSum" in html, "见 tender.html 的 kpis")
        ok("招标预算 KPI 按亿展示",
           re.search(r"amountSum[\s\S]{0,120}?fmt:\s*\"yi\"", html) is not None)
        ok("招标预算 KPI 把条数写进口径说明", "m.withAmount + \" 条\"" in html)

    # 开庭这一项原来是「企业被告 · 15–30 天 33」，和右侧标签讲同一件事；
    # 换成涉案金额后，同样要求金额对得上、条数退到标签里。
    if "hearing" in boards:
        d = boards["hearing"]
        s = sum(r["amount"] for r in d["rows"] if r.get("amount"))
        ok("开庭 meta 的涉案金额合计等于各行金额之和",
           abs((d["meta"].get("amountSum") or 0) - s) < 1,
           "%s / %s" % (d["meta"].get("amountSum"), s))
        ok("开庭涉案金额只累计带金额的公告",
           d["meta"].get("withAmount") == sum(1 for r in d["rows"] if r.get("amount")),
           "%s 条" % d["meta"].get("withAmount"))
        html = read(os.path.join(WEB, "hearing.html"))
        ok("开庭 KPI 取金额（amountSum）而非企业被告条数",
           "m.amountSum" in html and "m.precise" not in html)
        ok("开庭 KPI 把有金额的条数写进口径说明", "m.withAmount + \" 条\"" in html)


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
        # 千禧年改版后只剩浅色一种主题，切换按钮与首帧防闪脚本一并撤掉；
        # 断言改为「必须显式声明 light，且不能再残留主题读写的痕迹」。
        ok("%s 主题固定为浅色（切换已撤）" % page,
           'data-theme="light"' in s and "lx-theme" not in s and "themeSwitch" not in s)
        ok("%s 有页面标题" % page, re.search(r"<title>[^<]+</title>", s) and
           "Lexify" in re.search(r"<title>([^<]+)</title>", s).group(1))
        ok("%s 有 meta description" % page, 'name="description"' in s)
        ok("%s 声明 lang=zh-CN" % page, 'lang="zh-CN"' in s)

    idx = read(os.path.join(WEB, "index.html"))
    ok("首页加载 overview.js", "data/overview.js" in idx)
    ok("首页三张卡片齐全", idx.count('data-board="') == 3, "卡片 %d 张" % idx.count('data-board="'))
    ok("首页指向三块看板", all(h in idx for h, _, _ in BOARDS))
    ok("首页数字由 data-m 驱动（不是写死）", idx.count("data-m=") >= 8, idx.count("data-m="))
    # 光绑了 data-m 不够：路径写错时 app.js 会静默跳过，页面留着 HTML 里的旧数字。
    # 开庭看板从 1,735 涨到 5,278 那次，首页卡片还写着 1,735，就是这么来的。
    ov_path = os.path.join(DATA, "overview.js")
    ov = json.loads(re.match(r"^\s*window\.__OVERVIEW__\s*=\s*([\s\S]*);\s*$",
                             read(ov_path)).group(1))
    bad = []
    for path in set(re.findall(r'data-m="([^"]+)"', idx)):
        node = ov
        for part in path.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        if node is None:
            bad.append(path)
    ok("首页每个 data-m 都能在 overview 里取到值", not bad, ",".join(sorted(bad)))
    # 首屏右侧是真实记录预览，不是装饰图：容器在，数据来自 overview.sample
    ok("首屏有真实记录预览容器", 'id="previewRows"' in idx)
    ok("无装饰性迷你柱图残留", "data-spark=" not in idx)
    # 类名撞车是本项目反复踩的坑：.hot 撞过 KPI、.soon 撞过路线图区块
    # （.pv-d.soon 被 72px 上内边距顶成两行高）。状态类必须带模块前缀，
    # 这里挡住「在 CSS 里重新定义一个裸状态类」这种写法。
    css = read(os.path.join(WEB, "style.css")) + read(os.path.join(WEB, "board.css"))
    bare = [n for n in ("soon", "past", "week", "hot", "warn", "money", "accent", "on")
            if re.search(r"(?m)^\.%s\s*[,{]" % n, css)]
    ok("无裸状态类定义（状态类必须带前缀）", not bare, ",".join(bare))


def check_detail():
    """详情弹窗：按钮文字错位、「没有正文」的说明张冠李戴，都是一眼能看见的问题。"""
    print("\n[5] 详情弹窗")
    board_js = read(os.path.join(WEB, "board.js"))
    board_css = read(os.path.join(WEB, "board.css"))

    # <a class="btn-sm"> 和 <button class="btn-sm"> 并排时，只靠 padding 撑高的锚点
    # 会把文字顶到盒子上沿（millennium 主题还给 .btn-sm 定了 height:38px），
    # 「去采购平台看原文」和「关闭」就一个偏上一个居中。
    m = re.search(r"(?m)^\.btn-sm\s*\{([^}]*)\}", board_css)
    css_btn = m.group(1) if m else ""
    ok("详情按钮文字垂直居中（.btn-sm 用 inline-flex + align-items）",
       "display: inline-flex" in css_btn and "align-items: center" in css_btn)
    m = re.search(r"(?m)^\.modal-f\s*\{([^}]*)\}", board_css)
    ok("底部按钮行按中线对齐（.modal-f 有 align-items）",
       bool(m) and "align-items: center" in m.group(1))

    # 「没有正文」的说法必须由看板自己给：写在引擎里，招标页就会冒出法院排期表的说法
    ok("board.js 不再写死法院排期表的说法",
       "法院排期表类数据只提供" not in board_js and "CFG.bodyEmpty" in board_js)
    for page in ("tender.html", "hearing.html", "bounty.html"):
        s = read(os.path.join(WEB, page))
        ok("%s 声明了 bodyEmpty" % page, bool(re.search(r"(?m)^\s{4}bodyEmpty:", s)))

    # 详情里的「数据源」原来直接印采集器编号（cmhk / csg），没人看得懂；
    # 映射表还必须是完整的，漏一个就退回编号。
    s = read(os.path.join(WEB, "tender.html"))
    m = re.search(r"var SRC_NAME = \{([\s\S]*?)\n  \};", s)
    known = set(re.findall(r"(?m)^\s*(\w+):\s*\"", m.group(1))) if m else set()
    d = load_data("tender") or {}
    used = {r.get("src") for r in (d.get("rows") or []) if r.get("src")}
    missing = sorted(used - known)
    ok("招标数据源都有中文名（不是 cmhk 这种编号）",
       bool(known) and not missing, ",".join(missing))


def check_docs():
    """文档和代码不能各说各的：端口、后台用法这类「抄来抄去」的信息最容易漂。"""
    print("\n[5] 文档与代码一致")
    docs = ["README.md", "CONTRIBUTING.md", "docs/DEPLOY.md", "docs/ARCHITECTURE.md",
            "docs/DATABASE.md", "SECURITY.md"]
    # 只看「主机:端口」形态，正文里的「8000 字」这类数字不算端口
    PORT_RE = re.compile(r"(?:127\.0\.0\.1|localhost|0\.0\.0\.0|<服务器>|<host>):(\d{4,5})")

    stale = []
    for d in docs:
        p = os.path.join(ROOT, d)
        if not os.path.exists(p):
            continue
        for port in set(PORT_RE.findall(read(p))):
            if port in ("8899", "8080", "5000", "3000"):
                stale.append("%s:%s" % (d, port))
    ok("文档里没有历史端口（8899/8080/5000/3000）", not stale, ",".join(sorted(stale)))

    # main.py 已拆薄：实现进 server/ 包。下面到各模块里核对这些「抄来抄去容易漂」
    # 的关键点还在不在，而不是盯着一个薄入口文件自欺。
    server_dir = os.path.join(ROOT, "server")
    cli_py = read(os.path.join(server_dir, "cli.py"))
    cfg_py = read(os.path.join(server_dir, "config.py"))
    web_py = read(os.path.join(server_dir, "web.py"))
    auth_py = read(os.path.join(server_dir, "auth.py"))
    db_py = read(os.path.join(server_dir, "db.py"))
    admin_py = read(os.path.join(server_dir, "admin.py"))
    m = re.search(r'LEXIFY_PORT"\s*,\s*"(\d+)"', cli_py)
    default_port = m.group(1) if m else "?"
    ok("server/cli.py 默认端口为 8010", default_port == "8010", default_port)

    # 文档里出现的端口必须就是默认端口，别一边 8010 一边别的
    for d in docs:
        p = os.path.join(ROOT, d)
        if not os.path.exists(p):
            continue
        ports = set(PORT_RE.findall(read(p)))
        bad = {x for x in ports if x != default_port}
        ok("%s 里的端口都是 %s" % (d, default_port), not bad, ",".join(sorted(bad)))

    # 后台鉴权的三处入口缺一不可（缺 Cookie 就会出现「能看页面、点按钮 403」）
    for need, why, where in (("X-Admin-Token", "请求头", auth_py), ("ADMIN_COOKIE", "Cookie", auth_py),
                             ("is_local", "本机判定", auth_py), ("X-Forwarded-For", "反代识别", auth_py)):
        ok("server/auth.py 保留 %s 鉴权入口（%s）" % (need, why), need in where)

    # 线上真实踩过的两个坑：客户端断连刷堆栈、data/ 属主被 sudo 改成 root
    ok("server 静默客户端断连（CLIENT_GONE）",
       "CLIENT_GONE" in cfg_py and "except CLIENT_GONE" in web_py and "CLIENT_GONE" in web_py)
    ok("server 覆盖 handle_error，不再打断连堆栈", "def handle_error" in web_py)
    ok("server 启动前探测库可写", "def db_writable" in db_py)
    ok("只读库时给现成的修复命令", "chown -R" in cfg_py and "chown -R" in admin_py)
    ok("后台页有不可写横幅", "{notice}" in admin_py and "notice=notice" in admin_py)
    ok("后台口令不落日志（token 打码）", "token=" in web_py and "_redact_path" in web_py)
    ok("上传有体积上限（Content-Length 校验）", "Content-Length" in web_py and "MAX_UPLOAD" in web_py)
    ok("静态文件 realpath 防符号链接逃逸", "realpath" in web_py)

    manage_py = read(os.path.join(ROOT, "scripts", "manage.py"))
    ok("manage.py 写库前探测权限", "def writable_check" in manage_py)
    ok("manage.py 把 readonly 换成听得懂的话", "只读" in manage_py)

    compat = os.path.join(ROOT, "scripts", "compat.py")
    ok("跨平台底座 scripts/compat.py 存在", os.path.exists(compat))
    for f in ("scripts/manage.py", "scripts/build_data.py", "scripts/publish_data.py"):
        s = read(os.path.join(ROOT, f))
        ok("%s 调 setup_console 钉住控制台编码" % f, "setup_console()" in s)
    for f in ("scripts/manage.py", "server/config.py"):
        s = read(os.path.join(ROOT, f))
        ok("%s 用 compat 定位解释器" % f, "python_exe" in s)
        ok("%s 子进程带 UTF-8 环境" % f, "child_env" in s)


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
    check_detail()
    check_docs()

    print("\n合计 %d 项通过，%d 项失败，%d 项提醒" % (len(passes), len(fails), len(warns)))
    if fails:
        print("失败项：\n  - " + "\n  - ".join(fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
