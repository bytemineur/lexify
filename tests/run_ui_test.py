# -*- coding: utf-8 -*-
"""Lexify 看板交互回归：把 tests/selftest_board.js 注入三个看板页，用本机 Chrome 无头跑。

用法：
  python tests/run_ui_test.py            # 三个看板全跑
  python tests/run_ui_test.py hearing    # 只跑开庭公告
  python tests/run_ui_test.py all        # 同默认

零第三方依赖。找不到 Chrome 时回退 Edge / Chromium。
测试脚本对页面无侵入：临时文件写在 web/ 下（保证相对路径能取到 data/*.js），跑完删掉。
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/snap/bin/chromium",
]

# 新增看板时这里加一行即可
SUITES = [
    ("tender.html", "招标雷达"),
    ("hearing.html", "开庭公告"),
    ("bounty.html", "执行悬赏"),
]


def find_browser():
    for p in CHROME_CANDIDATES:
        if os.path.isfile(p):
            return p
    raise SystemExit("未找到 Chrome / Edge，无法运行 UI 测试")


def run_suite(browser, page, title):
    src = open(os.path.join(WEB, page), encoding="utf-8").read()
    script = open(os.path.join(ROOT, "tests", "selftest_board.js"), encoding="utf-8").read()

    if "</body>" not in src:
        print("!! %s 没有 </body>，无法注入" % page)
        return 1, 0

    html = src.replace("</body>", "<script>\n" + script + "\n</script>\n</body>")

    tmp = os.path.join(WEB, "_selftest_%s" % page)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(html)

    try:
        out = subprocess.run(
            [browser, "--headless=new", "--disable-gpu",
             "--no-sandbox", "--disable-dev-shm-usage",
             "--allow-file-access-from-files",
             "--dump-dom", "--virtual-time-budget=30000",
             "file:///" + tmp.replace("\\", "/")],
            capture_output=True, text=True, encoding="utf-8", errors="ignore",
            timeout=240,
        ).stdout
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

    print("\n=== %s (%s) ===" % (title, page))
    m = re.search(r'<pre[^>]*id="testout"[^>]*>(.*?)</pre>', out, re.S)
    if not m:
        print("!! 未拿到测试输出：页面可能没有正常初始化")
        return 1, 0

    lines = [l for l in m.group(1).split("\n") if l.startswith("TEST| ")]
    lines = [l for l in lines if "TEST| DONE" not in l]
    fails = [l for l in lines if "FAIL" in l or "CRASH" in l]
    for l in lines:
        tag = l[6:]
        print("   " + tag)
    if not any("DONE" in x for x in m.group(1).split("\n")):
        print("!! 测试未跑完（缺少 DONE 标记）")
        fails.append("CRASH| 未跑完")
    print("   小计 %d 项，失败 %d 项" % (len(lines), len(fails)))
    return (1 if fails else 0), len(lines)


def main():
    target = (sys.argv[1] if len(sys.argv) > 1 else "all").lower()
    if target in ("all", ""):
        suites = SUITES
    else:
        suites = [s for s in SUITES if s[0].split(".")[0] == target]
        if not suites:
            raise SystemExit("未知目标 %s（可选 %s / all）"
                             % (target, " / ".join(s[0].split(".")[0] for s in SUITES)))

    browser = find_browser()
    rc, total, failed = 0, 0, 0
    for page, title in suites:
        r, n = run_suite(browser, page, title)
        rc |= r
        total += n
        failed += 0 if r == 0 else 1

    print("\n合计 %d 项断言，%d 组有失败" % (total, failed))
    print("结果：%s" % ("通过" if rc == 0 else "有失败"))
    return rc


if __name__ == "__main__":
    sys.exit(main())
