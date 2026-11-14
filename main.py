# -*- coding: utf-8 -*-
"""Lexify 单一入口：站点 + 管理后台 + 定时更新，一个进程全包。

不依赖 nginx、不依赖 Docker、不依赖第三方库，Linux 上装个 Python 3.9+ 就能跑：

    python main.py                       # http://0.0.0.0:8010
    python main.py --port 80             # 换端口
    python main.py --at 03:00            # 每天几点更新（默认 00:30）
    python main.py --no-schedule         # 只当 web 服务用
    python main.py --admin-only          # 只开后台，不对外发站点

管理后台在 /admin，默认只允许本机访问；要给公网用，设一个令牌：

    LEXIFY_ADMIN_TOKEN=随意一串 python main.py
    # 然后访问 /admin?token=随意一串
"""
import argparse
import email.parser
import email.policy
import gzip
import html
import json
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, "web")
UPLOAD_DIR = os.path.join(ROOT, "data", "uploads")
DB = os.environ.get("LEXIFY_DB") or os.path.join(ROOT, "data", "lexify.db")

# scripts/ 里放的是同一套工具（manage / build_data / publish_data / compat），
# 加进 sys.path 才能 import，也才能保证「后台调脚本」和「命令行跑脚本」用同一份兼容层。
SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)
from compat import setup_console, child_env, python_exe, describe as compat_describe  # noqa: E402

PY = python_exe()

ADMIN_TOKEN = os.environ.get("LEXIFY_ADMIN_TOKEN", "")
# 显式放弃防护（内网/单机玩具部署用）。默认 False，公网部署不要开。
ADMIN_OPEN = (os.environ.get("LEXIFY_ADMIN_OPEN", "") or "").lower() in ("1", "true", "yes", "on")
TOKEN_FILE = os.path.join(ROOT, "data", "admin_token.txt")
ADMIN_COOKIE = "lexify_admin"

MIME = {
    ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8", ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg",
    ".webp": "image/webp", ".ico": "image/x-icon", ".woff2": "font/woff2",
    ".ttf": "font/ttf", ".txt": "text/plain; charset=utf-8", ".csv": "text/csv; charset=utf-8",
}
COMPRESSIBLE = (".html", ".css", ".js", ".json", ".svg", ".txt")
_gz_cache = {}

# 客户端提前走的三种例外：关标签页、点停止、网络断。
# 这三种跟服务端无关，不该打堆栈（用户看到的就是 nohup.out 被 BrokenPipe 刷满）。
CLIENT_GONE = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)


# ────────────────────────────── 数据与子进程 ──────────────────────────────
DB_FIX_HINT = (
    "  库是 SQLite，%s 必须是可写的（要写 WAL 和临时文件）。\n"
    "  最常见原因：之前用 sudo 跑过一次，data/ 里的文件属主变成了 root。\n"
    "  修一下（把 USER 换成你的登录用户）：\n"
    "    sudo chown -R USER:USER %s\n"
    "  然后重启进程。"
)


def db_writable():
    """库到底能不能写 —— 提前探，别等采集跑一半才抛 sqlite 的原文。

    `attempt to write a readonly database` 这种报错对使用者毫无信息量：它既不说
    哪个路径、也不说该改什么权限。所以先自己探一遍目录/文件权限，再真开一次
    写事务兜底（root 身份下 os.access 会假阳性）。
    """
    d = os.path.dirname(os.path.abspath(DB))
    if not os.path.isdir(d) or not os.access(d, os.W_OK):
        return False, "目录不可写：%s" % d
    if not os.path.exists(DB):
        return True, ""                        # 库还没建，建库时再看
    if not os.access(DB, os.W_OK):
        return False, "库文件不可写：%s" % DB
    try:
        conn = sqlite3.connect(DB, timeout=2)
        try:
            conn.execute("BEGIN IMMEDIATE")    # 真拿一次写锁，readonly 会在这里炸
            conn.rollback()
        finally:
            conn.close()
        return True, ""
    except sqlite3.Error as e:
        return False, "%s（%s）" % (e, DB)


def db_tables_ok():
    """核心表在不在。缺表通常意味着 schema 从没建成（常见于库只读）。"""
    if not os.path.exists(DB):
        return False
    try:
        conn = sqlite3.connect(DB)
        have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
    except sqlite3.Error:
        return False
    return {"tenders", "hearings", "notices"}.issubset(have)


def db_is_empty():
    """三块看板合起来一条都没有 → 认为是全新的库。"""
    if not os.path.exists(DB):
        return True
    try:
        conn = sqlite3.connect(DB)
        n = sum(conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
                for t in ("tenders", "hearings", "notices"))
        conn.close()
        return n == 0
    except sqlite3.Error:
        return True


def db_stats():
    """后台首页的全部数据。任何一张表缺失都不能让整个 /admin 500。"""
    if not os.path.exists(DB):
        return None, [], []
    try:
        conn = sqlite3.connect(DB)
    except sqlite3.Error:
        return None, [], []
    conn.row_factory = sqlite3.Row
    counts = {}
    for t in ("tenders", "sources", "hearings", "notices"):
        try:
            counts[t] = conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
        except sqlite3.Error:
            counts[t] = -1
    try:
        runs = [dict(r) for r in conn.execute(
            "SELECT started_at,kind,mode,origin,total_seen,inserted,updated,skipped,status "
            "FROM ingest_runs ORDER BY id DESC LIMIT 8")]
    except sqlite3.Error:
        runs = []                              # 表还没建出来（库只读时就是这种）
    try:
        fails = [dict(r) for r in conn.execute(
            "SELECT started_at,board,source,stage,reason,detail,run_id FROM crawl_failures "
            "WHERE resolved = 0 ORDER BY id DESC LIMIT 25")]
    except sqlite3.Error:
        fails = []
    conn.close()
    return counts, runs, fails


def sh(args, timeout=1800):
    try:
        p = subprocess.run([PY] + args, cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           env=child_env())
        return p.returncode == 0, ((p.stdout or "") + (p.stderr or "")).strip()[-8000:]
    except subprocess.TimeoutExpired:
        return False, "超时（%d 秒）" % timeout
    except OSError as e:
        # Windows 上少见的坑：解释器路径带空格但被拆开、杀软拦截子进程。
        # 报清楚比抛栈好排查。
        return False, "启动子进程失败：%s（解释器 %s）" % (e, PY)


# ────────────────────────────── 定时更新 ──────────────────────────────
LAST_UPDATE_FILE = os.path.join(ROOT, "data", "last_update.txt")


def read_last_update():
    """上次成功更新的时间。独立存文件而不是猜库里的 fetched_at：
    导入外部数据也会写库，但那不算「今天的定时更新跑过了」。"""
    try:
        with open(LAST_UPDATE_FILE, encoding="utf-8") as f:
            return datetime.strptime(f.read().strip(), "%Y-%m-%d %H:%M")
    except (OSError, ValueError):
        return None


def write_last_update():
    try:
        os.makedirs(os.path.dirname(LAST_UPDATE_FILE), exist_ok=True)
        with open(LAST_UPDATE_FILE, "w", encoding="utf-8") as f:
            f.write(datetime.now().strftime("%Y-%m-%d %H:%M"))
    except OSError:
        pass


def next_run(at_hhmm, now=None):
    now = now or datetime.now()
    hh, mm = [int(x) for x in at_hhmm.split(":")]
    t = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return t if t > now else t + timedelta(days=1)


def catch_up_needed(at_hhmm):
    """今天该跑的点已经过了、但今天还没更新过 → 需要补一次。

    服务器半夜重启（或断电）时最有用：不然要等到明天同一时刻才更新。
    README 里承诺过这个行为，这里让它真的成立。
    """
    now = datetime.now()
    hh, mm = [int(x) for x in at_hhmm.split(":")]
    sched = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if now < sched:
        return False
    last = read_last_update()
    return last is None or last < sched


def run_update():
    print("[%s] 开始更新" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"), flush=True)
    ok, out = sh(["scripts/manage.py", "update"])
    print(out, flush=True)
    print("[%s] 更新结束（%s）" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "ok" if ok else "失败"), flush=True)
    if ok:
        write_last_update()
    return ok


def start_scheduler(at):
    def loop():
        while True:
            nxt = next_run(at)
            print("[定时] 下次运行：%s" % nxt.strftime("%Y-%m-%d %H:%M"), flush=True)
            while datetime.now() < nxt:
                time.sleep(min(30, max(1, (nxt - datetime.now()).total_seconds())))
            run_update()

    t = threading.Thread(target=loop, daemon=True)
    t.start()


# ────────────────────────────── 后台任务 ──────────────────────────────
# 采集可能要跑十几分钟，不能卡住 HTTP 请求，所以放到后台线程里，
# 后台页面用 meta refresh 轮询进度。
JOB = {"running": False, "label": "", "started": "", "ended": "", "ok": None, "log": []}
JOB_MAX_LINES = 400


def start_job(label, args):
    if JOB["running"]:
        return False
    JOB.update(running=True, label=label, started=datetime.now().strftime("%H:%M:%S"),
               ended="", ok=None, log=[])

    def run():
        ok = True
        try:
            p = subprocess.Popen([PY] + args, cwd=ROOT, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                 errors="replace", bufsize=1, env=child_env())
            for line in p.stdout:
                line = line.rstrip("\n")
                if line.startswith("FAIL|"):
                    continue
                JOB["log"].append(line)
                if len(JOB["log"]) > JOB_MAX_LINES:
                    del JOB["log"][:100]
                print("  " + line, flush=True)
            p.wait()
            ok = p.returncode == 0
        except Exception as e:
            JOB["log"].append("异常：%s" % e)
            ok = False
        finally:
            JOB.update(running=False, ok=ok, ended=datetime.now().strftime("%H:%M:%S"))

    threading.Thread(target=run, daemon=True).start()
    return True


def job_html():
    if JOB["running"]:
        tail = "\n".join(JOB["log"][-18:]) or "启动中…"
        return ("<p><span class='pill'>运行中</span> %s（%s 开始）</p><pre>%s</pre>" %
                (html.escape(JOB["label"]), JOB["started"], html.escape(tail)))
    if JOB["ok"] is None:
        return '<p class="sub">还没有跑过更新。</p>'
    tail = "\n".join(JOB["log"][-18:])
    head = "✓ 完成" if JOB["ok"] else "✗ 失败"
    return ("<p><span class='pill'>%s</span> %s（%s → %s）</p><pre>%s</pre>" %
            (head, html.escape(JOB["label"]), JOB["started"], JOB["ended"], html.escape(tail)))


# ────────────────────────────── 后台口令 ──────────────────────────────
def load_admin_token():
    """确定后台口令：环境变量 > data/admin_token.txt > 首次自动生成。

    为什么要自动生成：/admin 默认只允许本机访问，部署到服务器后从公网打开
    必然 403，而大多数人是先撞上 403 才去翻文档的。这里生成一个随机口令落到
    data/admin_token.txt 并打印到启动日志——既不用改环境变量就能立刻进后台，
    又不至于像「放开所有人」那样把采集/上传/推送全暴露出去。

    返回 (token, 来源说明)。
    """
    global ADMIN_TOKEN
    if ADMIN_TOKEN:
        return ADMIN_TOKEN, "环境变量 LEXIFY_ADMIN_TOKEN"
    if os.path.exists(TOKEN_FILE):
        try:
            t = open(TOKEN_FILE, encoding="utf-8").read().strip()
            if t:
                ADMIN_TOKEN = t
                return t, TOKEN_FILE
        except OSError:
            pass
    t = secrets.token_urlsafe(24)
    saved = TOKEN_FILE
    try:
        os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(t + "\n")
        try:
            os.chmod(TOKEN_FILE, 0o600)      # Windows 上无效果，不报错即可
        except OSError:
            pass
    except OSError as e:
        saved = ""
        print("⚠ 后台口令写不进 %s（%s）" % (TOKEN_FILE, e))
        print("  → 本次口令只在进程内有效，重启就会变；想固定就设 LEXIFY_ADMIN_TOKEN 或 --admin-token")
        print("  → 通常是把 data/ 的属主弄成了 root：sudo chown -R USER:USER %s"
              % os.path.dirname(os.path.abspath(DB)))
    ADMIN_TOKEN = t
    return t, ("自动生成 → %s" % saved) if saved else "临时生成（未落盘，重启失效）"


DENY_PAGE = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>后台需要口令 · Lexify</title>
<style>
body{margin:0;padding:56px 24px;font:14px/1.8 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
color:#111;background:#fff}
.box{max-width:560px;margin:0 auto}
h1{font-size:20px;font-weight:600;margin:0 0 6px}
p{color:#555;margin:10px 0}
code{background:#F4F4F6;padding:1px 5px;font-size:13px;font-family:ui-monospace,Consolas,monospace}
form{margin:22px 0;padding:16px 18px;border:1px solid #E5E5E5}
input{font:inherit;font-size:13px;padding:7px 10px;border:1px solid #ccc;width:100%;box-sizing:border-box}
button{margin-top:10px;font:inherit;font-size:13px;padding:7px 16px;background:#1434CB;color:#fff;
border:1px solid #1434CB;cursor:pointer}
ol{padding-left:20px;color:#555}
</style></head><body><div class="box">
<h1>这个后台不对外开放</h1>
<p>后台能触发采集、上传数据、推送仓库，所以默认只允许<b>本机直连</b>访问。
你现在是从 <code>{ip}</code> 过来的，被拦下了。</p>
<form method="get" action="/admin">
  <label for="t">有口令就直接贴进来（会记进 Cookie，之后不用再带）：</label>
  <input id="t" name="token" type="password" placeholder="后台口令" autocomplete="current-password" autofocus>
  <button type="submit">进入后台</button>
</form>
<p>口令从哪来，三选一：</p>
<ol>
  <li>服务器上的 <code>data/admin_token.txt</code>（首次启动自动生成）</li>
  <li>启动日志里那行「后台口令」</li>
  <li>自己设环境变量 <code>LEXIFY_ADMIN_TOKEN</code> 后重启</li>
</ol>
<p>不想暴露到公网？用 SSH 隧道把端口映射到本机：
<code>ssh -L 8010:127.0.0.1:8010 用户@服务器</code>，然后开 <code>http://127.0.0.1:8010/admin</code>。</p>
</div></body></html>"""


def deny_page(ip):
    return DENY_PAGE.replace("{ip}", html.escape(ip or "未知地址"))


# ────────────────────────────── 管理页面 ──────────────────────────────
PAGE = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">{refresh}
<title>Lexify 数据后台</title>
<style>
:root{{--b:#1434CB;--m:#6E6E80;--l:#E5E5E5}}
*{{box-sizing:border-box}}
body{{margin:0;padding:32px 40px 80px;font:14px/1.7 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;color:#111;background:#fff;max-width:1040px}}
h1{{font-size:22px;font-weight:600;margin:0 0 4px;letter-spacing:-.01em}}
h2{{font-size:14px;font-weight:600;margin:34px 0 10px;color:var(--b)}}
.sub{{color:var(--m);margin-bottom:22px}}
a{{color:var(--b)}}
table{{border-collapse:collapse;width:100%}}
th,td{{text-align:left;padding:7px 12px 7px 0;border-bottom:1px solid var(--l);font-size:13px}}
th{{color:var(--m);font-weight:500}}
td.n{{font-variant-numeric:tabular-nums}}
form{{margin:0 0 16px;padding:16px 18px;border:1px solid var(--l)}}
select,button,input[type=text]{{font:inherit;font-size:13px;padding:5px 10px;border:1px solid #ccc;background:#fff}}
button{{border-color:var(--b);color:var(--b);cursor:pointer}}
button.primary{{background:var(--b);color:#fff}}
pre{{background:#F7F7F8;border:1px solid var(--l);padding:14px;white-space:pre-wrap;font-size:12px;max-height:420px;overflow:auto}}
.pill{{display:inline-block;margin:0 8px 8px 0;padding:2px 9px;border:1px solid var(--l);font-size:12px;color:var(--m)}}
.hint{{margin-top:8px;font-size:12px;color:var(--m)}}
.err{{color:#c62828}}
.warn{{margin:14px 0 0;padding:14px 18px;border:1px solid #F0B7B4;background:#FDF3F2;font-size:13px}}
.warn b{{color:#c62828}}
.warn code{{background:#fff;border:1px solid #F0B7B4;padding:1px 5px;font-family:ui-monospace,Consolas,monospace;font-size:12px}}
</style></head><body>
<h1>Lexify 数据后台</h1>{notice}
<div class="sub">统一库 <code>{db}</code> · <a href="/docs">项目文档</a> · <a href="/">← 回到站点</a>{auth}</div>
<h2>当前数据</h2>{counts}
<h2>手动更新数据</h2>
<form method="post" action="/admin/crawl">
  <button class="primary" name="mode" value="all">采集全部并更新</button>
  <button name="mode" value="tender" style="margin-left:6px">只采招标</button>
  <button name="mode" value="hearing" style="margin-left:6px">只采开庭</button>
  <button name="mode" value="bounty" style="margin-left:6px">只采悬赏</button>
</form>
{job}
<h2>上传数据</h2>
<form method="post" action="/admin/upload" enctype="multipart/form-data">
  <input type="file" name="file" required>
  <input type="text" name="source" placeholder="来源标识（可选）" size="14">
  <button class="primary" type="submit">上传并入库</button>
  <div class="hint">支持 .db / .jsonl / .json / .csv，类型按字段自动识别</div>
</form>
<form method="post" action="/admin/rebuild">
  <button type="submit" name="publish" value="0">只重建页面数据</button>
  <button class="primary" type="submit" name="publish" value="1" style="margin-left:8px">重建并推送到 GitHub</button>
</form>
{output}
<h2>采集失败 <span class="sub" style="font-weight:400">（没修好的会一直挂在这里）</span></h2>{failures}
<h2>最近导入</h2>{runs}
</body></html>"""


def render(output=""):
    counts, runs, fails = db_stats()

    # 库只读是最常见的一种「后台看起来正常、点按钮全失败」。
    # 与其让用户从 sqlite 的 readonly 报错里猜，不如直接在页面上说清楚。
    writable, why = db_writable()
    if not writable:
        notice = ('<div class="warn"><b>数据目录不可写</b>：%s<br>'
                  '采集、上传、重建都会失败（站点浏览不受影响）。'
                  '通常是之前用 <code>sudo</code> 跑过，<code>data/</code> 属主变成了 root：<br>'
                  '<code>sudo chown -R USER:USER %s</code>，然后重启进程。</div>'
                  % (html.escape(why), html.escape(os.path.dirname(os.path.abspath(DB)))))
    elif counts is not None and not db_tables_ok():
        notice = ('<div class="warn"><b>库里没有核心表</b>（schema 没建成）。'
                  '多半是库可写但建表时被中断——点下面的「采集全部并更新」会重新建表。</div>')
    else:
        notice = ""

    if counts is None:
        counts_html = '<p class="err">还没有库，先跑：python scripts/manage.py init</p>'
    else:
        rows = "".join('<tr><td>%s</td><td class="n">%s</td></tr>' % (html.escape(k), v if v >= 0 else "—")
                       for k, v in counts.items())
        counts_html = "<table><tr><th>表</th><th>条数</th></tr>%s</table>" % rows

    if fails:
        agg = {}
        for f in fails:
            agg[f["source"]] = agg.get(f["source"], 0) + 1
        top = "".join("<span class='pill'>%s ×%d</span>" % (html.escape(k), v)
                      for k, v in sorted(agg.items(), key=lambda kv: -kv[1])[:12])
        rows = "".join(
            "<tr><td>%s</td><td>%s</td><td><code>%s</code></td><td>%s</td>"
            "<td title='%s'>%s</td></tr>" % (
                html.escape((x["started_at"] or "")[5:16]), html.escape(x["board"] or ""),
                html.escape(x["source"] or ""), html.escape(x["stage"] or ""),
                html.escape(x["detail"] or ""), html.escape((x["reason"] or "")[:70]))
            for x in fails)
        fails_html = ("<p>%s</p><table><tr><th>时间</th><th>看板</th><th>来源</th><th>阶段</th>"
                      "<th>原因</th></tr>%s</table>") % (top, rows)
    else:
        fails_html = '<p class="sub">没有未处理的采集失败。</p>'

    if runs:
        rows = "".join(
            '<tr><td>%s</td><td>%s</td><td>%s</td><td class="n">%s</td><td class="n">+%s</td>'
            '<td class="n">~%s</td><td class="n">skip %s</td><td>%s</td></tr>' % (
                html.escape(x["started_at"] or ""), html.escape(x["kind"] or ""), html.escape(x["mode"] or ""),
                x["total_seen"], x["inserted"], x["updated"], x["skipped"], html.escape(x["status"] or ""))
            for x in runs)
        runs_html = ("<table><tr><th>时间</th><th>类型</th><th>方式</th><th>读到</th><th>新增</th>"
                     "<th>更新</th><th>跳过</th><th>状态</th></tr>%s</table>") % rows
    else:
        runs_html = '<p class="sub">还没有导入记录。</p>'

    out = ('<h2>执行结果</h2><pre>%s</pre>' % html.escape(output)) if output else ""
    refresh = '<meta http-equiv="refresh" content="3">' if JOB["running"] else ""
    auth = ""
    if ADMIN_OPEN:
        auth = ' · <span style="color:#c62828">后台未设防</span>'
    elif ADMIN_TOKEN:
        auth = ' · <a href="/admin/logout">退出登录</a>'
    return PAGE.format(db=html.escape(DB), counts=counts_html, runs=runs_html,
                       failures=fails_html, output=out, job=job_html(), refresh=refresh,
                       auth=auth, notice=notice)


def parse_fields(ctype, body):
    """普通 <form> 发的是 urlencoded，带文件的才是 multipart。两种都要能读。"""
    if "multipart/form-data" in ctype:
        return parse_multipart(ctype, body)
    if "application/x-www-form-urlencoded" in ctype:
        q = urllib.parse.parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True)
        return {k: (v[0] if v else "") for k, v in q.items()}, []
    return {}, []


def parse_multipart(ctype, body):
    raw = b"Content-Type: " + ctype.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + body
    msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(raw)
    fields, files = {}, []
    if not msg.is_multipart():
        return fields, files
    for part in msg.iter_parts():
        disp = part.get("Content-Disposition", "")
        m = re.search(r'name="([^"]+)"', disp)
        name = m.group(1) if m else ""
        fn = part.get_filename()
        payload = part.get_payload(decode=True) or b""
        if fn:
            files.append((name, fn, payload))
        else:
            fields[name] = payload.decode("utf-8", "replace")
    return fields, files


# ────────────────────────────── 项目文档 ──────────────────────────────
DOCS = [
    ("architecture", "架构与数据流", "docs/ARCHITECTURE.md"),
    ("database", "数据库", "docs/DATABASE.md"),
    ("deploy", "部署", "docs/DEPLOY.md"),
    ("readme", "项目说明", "README.md"),
]


def _inline(text):
    t = html.escape(text)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', t)
    return t


def md_to_html(md):
    """够用的 Markdown 渲染：标题 / 段落 / 列表 / 表格 / 代码块 / 强调。"""
    out, code, in_code = [], [], False
    lines = md.split("\n")
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        if line.strip().startswith("```"):
            if in_code:
                out.append("<pre><code>%s</code></pre>" % html.escape("\n".join(code)))
                code, in_code = [], False
            else:
                in_code = True
            i += 1
            continue
        if in_code:
            code.append(line)
            i += 1
            continue

        # 表格
        if line.strip().startswith("|") and i + 1 < n and re.match(r"^\s*\|[\s:\-|]+\|\s*$", lines[i + 1]):
            head = [c.strip() for c in line.strip().strip("|").split("|")]
            i += 2
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            out.append("<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table>" % (
                "".join("<th>%s</th>" % _inline(c) for c in head),
                "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % _inline(c) for c in r) for r in rows)))
            continue

        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            lvl = len(m.group(1))
            out.append("<h%d>%s</h%d>" % (lvl, _inline(m.group(2)), lvl))
            i += 1
            continue

        if re.match(r"^\s*[-*]\s+", line):
            items = []
            while i < n and re.match(r"^\s*[-*]\s+", lines[i]):
                items.append(re.sub(r"^\s*[-*]\s+", "", lines[i]))
                i += 1
            out.append("<ul>%s</ul>" % "".join("<li>%s</li>" % _inline(x) for x in items))
            continue

        if re.match(r"^\s*\d+\.\s+", line):
            items = []
            while i < n and re.match(r"^\s*\d+\.\s+", lines[i]):
                items.append(re.sub(r"^\s*\d+\.\s+", "", lines[i]))
                i += 1
            out.append("<ol>%s</ol>" % "".join("<li>%s</li>" % _inline(x) for x in items))
            continue

        if line.strip() == "---":
            out.append("<hr>")
            i += 1
            continue
        if line.startswith(">"):
            buf = []
            while i < n and lines[i].startswith(">"):
                buf.append(lines[i].lstrip("> ").strip())
                i += 1
            out.append("<blockquote>%s</blockquote>" % _inline(" ".join(buf)))
            continue
        if not line.strip():
            i += 1
            continue

        buf = []
        while i < n and lines[i].strip() and not re.match(r"^(#{1,4}\s|\s*[-*]\s|\s*\d+\.\s|```|\|)", lines[i]):
            buf.append(lines[i])
            i += 1
        out.append("<p>%s</p>" % _inline(" ".join(buf)))
    if in_code:
        out.append("<pre><code>%s</code></pre>" % html.escape("\n".join(code)))
    return "\n".join(out)


DOC_CSS = """
*{box-sizing:border-box}
body{margin:0;font:15px/1.75 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;color:#1a1a1a;background:#fff}
.wrap{display:flex;max-width:1180px;margin:0 auto}
nav{width:210px;flex:none;padding:36px 20px 60px;border-right:1px solid #eee;position:sticky;top:0;height:100vh;overflow:auto}
nav .brand{font-size:19px;color:#1434CB;text-decoration:none;letter-spacing:-.01em}
nav .brand small{display:block;font-size:11px;color:#8a8a90;letter-spacing:.12em;text-transform:uppercase;margin-top:2px}
nav a.item{display:block;padding:6px 10px;margin:14px 0 0;font-size:13px;color:#444;text-decoration:none;border-left:2px solid transparent}
nav a.item:hover{color:#1434CB}
nav a.item.on{color:#1434CB;border-left-color:#1434CB;font-weight:600}
main{flex:1;min-width:0;padding:36px 48px 90px}
h1{font-size:26px;margin:0 0 18px;letter-spacing:-.01em}
h2{font-size:18px;margin:34px 0 10px;padding-top:6px}
h3{font-size:15px;margin:24px 0 8px}
p{margin:10px 0}
ul,ol{margin:10px 0;padding-left:22px}
li{margin:3px 0}
code{background:#F4F4F6;padding:1px 5px;font-size:13px;font-family:ui-monospace,Consolas,monospace}
pre{background:#F7F7F8;border:1px solid #eee;padding:14px 16px;overflow:auto;font-size:12.5px;line-height:1.6}
pre code{background:none;padding:0}
table{border-collapse:collapse;width:100%;margin:14px 0;font-size:13.5px}
th,td{border-bottom:1px solid #eee;padding:8px 12px 8px 0;text-align:left;vertical-align:top}
th{color:#6E6E80;font-weight:500;white-space:nowrap}
blockquote{margin:14px 0;padding:8px 16px;border-left:2px solid #1434CB;color:#444;background:#FAFAFE}
hr{border:0;border-top:1px solid #eee;margin:28px 0}
a{color:#1434CB}
.badge{display:inline-block;padding:1px 7px;border:1px solid #e5e5e5;font-size:11px;color:#6E6E80;margin-left:8px}
"""


def docs_page(active="architecture", content=""):
    nav = "".join(
        '<a class="item%s" href="/docs/%s">%s</a>' % (" on" if k == active else "", k, html.escape(t))
        for k, t, _ in DOCS)
    nav += '<a class="item%s" href="/docs/schema">表结构（实时）</a>' % (" on" if active == "schema" else "")
    nav += '<a class="item" href="/">← 回到站点</a>'
    # 用拼接而不是 % 格式化：CSS 里有 width:100%，会被当成格式符
    return ('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Lexify 文档</title><style>' + DOC_CSS + '</style></head><body>'
            '<div class="wrap"><nav><a class="brand" href="/docs">lexify<small>docs</small></a>'
            + nav + '</nav><main>' + content + '</main></div></body></html>')


def render_doc(key):
    page = None
    title = key
    for k, t, path in DOCS:
        if k == key:
            page, title = path, t
            break
    if page is None:
        return None
    full = os.path.join(ROOT, page)
    if not os.path.exists(full):
        return docs_page(key, "<h1>%s</h1><p class='err'>找不到 %s</p>" % (html.escape(title), page))
    md = open(full, encoding="utf-8").read()
    return docs_page(key, md_to_html(md))


def render_schema():
    """直接从库里读 PRAGMA，保证文档和实际结构永远一致。"""
    if not os.path.exists(DB):
        return docs_page("schema", "<h1>表结构</h1><p class='err'>还没有库。</p>")
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    parts = ["<h1>表结构<span class='badge'>实时读取</span></h1>",
             "<p class='sub'>下面是 <code>%s</code> 里真实的表与字段，随库自动更新，不会和文档对不上。</p>"
             % html.escape(os.path.basename(DB))]
    for t in tables:
        n = conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
        parts.append("<h2>%s<span class='badge'>%d 行</span></h2>" % (html.escape(t), n))
        cols = conn.execute("PRAGMA table_info(%s)" % t).fetchall()
        rows = []
        for c in cols:
            flags = []
            if c["pk"]:
                flags.append("主键")
            if c["notnull"]:
                flags.append("非空")
            rows.append("<tr><td><code>%s</code></td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                html.escape(c["name"]), html.escape(c["type"] or ""),
                html.escape("、".join(flags)) or "—",
                html.escape(str(c["dflt_value"]) if c["dflt_value"] is not None else "—")))
        parts.append("<table><thead><tr><th>字段</th><th>类型</th><th>约束</th><th>默认</th></tr>"
                     "</thead><tbody>%s</tbody></table>" % "".join(rows))
        idx = conn.execute("PRAGMA index_list(%s)" % t).fetchall()
        if idx:
            names = []
            for x in idx:
                cols_i = [r["name"] for r in conn.execute("PRAGMA index_info(%s)" % x["name"])]
                names.append("%s(%s)%s" % (x["name"], ", ".join(cols_i), " 唯一" if x["unique"] else ""))
            parts.append("<p><strong>索引：</strong>%s</p>" % "；".join("<code>%s</code>" % html.escape(n) for n in names))
    conn.close()
    return docs_page("schema", "\n".join(parts))


# ────────────────────────────── HTTP ──────────────────────────────
class Handler(BaseHTTPRequestHandler):
    server_version = "Lexify"
    protocol_version = "HTTP/1.1"
    _client_gone = False

    def log_message(self, fmt, *args):
        if "/admin" in (self.path or ""):
            print("[%s] %s" % (datetime.now().strftime("%H:%M:%S"), self.path), flush=True)

    def _send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
        data = body.encode("utf-8") if isinstance(body, str) else body
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)
        except CLIENT_GONE:
            # 浏览器切页/取消加载/手机切后台，客户端先断开了。7 MB 的 hearing.js
            # 最容易撞上，这既不是错误也不可修，安静收工即可。
            self.close_connection = True
            self._client_gone = True

    # ---------- 鉴权 ----------
    def _is_local(self):
        """本机直连才算「本机」。

        只看 client_address 是不够的：挂在 Caddy / nginx 后面时，所有请求的
        来源地址都变成 127.0.0.1，后台等于对全网敞开。所以带转发头的一律
        不算本机，必须给口令。
        """
        ip = self.client_address[0] or ""
        if ip not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            return False
        for h in ("X-Forwarded-For", "X-Real-IP", "Forwarded",
                  "X-Forwarded-Host", "CF-Connecting-IP"):
            if self.headers.get(h):
                return False
        return True

    def _given_token(self):
        """口令可以从三个地方来：?token=（首次）、X-Admin-Token（脚本）、Cookie（日常）。"""
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        t = (q.get("token") or [""])[0]
        if t:
            return t, "query"
        t = self.headers.get("X-Admin-Token") or ""
        if t:
            return t, "header"
        m = re.search(r"(?:^|;\s*)%s=([^;]*)" % ADMIN_COOKIE, self.headers.get("Cookie") or "")
        if m:
            return urllib.parse.unquote(m.group(1)), "cookie"
        return "", ""

    def _token_ok(self, given):
        return bool(ADMIN_TOKEN) and secrets.compare_digest(
            given.encode("utf-8"), ADMIN_TOKEN.encode("utf-8"))

    def _admin_ok(self):
        if self._is_local() or ADMIN_OPEN:
            return True
        if not ADMIN_TOKEN:
            return False
        return self._token_ok(self._given_token()[0])

    def _admin_set_cookie(self):
        """从 ?token= 进来的，把口令种进 Cookie。

        这是修「带 token 能看页面、点按钮却 403」的关键：表单 POST 到
        /admin/crawl 时 URL 上的 token 已经丢了，只有 Cookie 能跟过去。
        """
        given, src = self._given_token()
        if src != "query" or not self._token_ok(given):
            return None
        https = (self.headers.get("X-Forwarded-Proto") or "").lower() == "https"
        return "%s=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000%s" % (
            ADMIN_COOKIE, urllib.parse.quote(given, safe=""), "; Secure" if https else "")

    def _same_origin(self):
        """挡一下跨站表单：带了 Origin 就必须同源；没带的（curl / 老浏览器）放行。"""
        origin = self.headers.get("Origin")
        if origin:
            return urllib.parse.urlparse(origin).netloc == (self.headers.get("Host") or "")
        site = self.headers.get("Sec-Fetch-Site")
        return site in (None, "", "same-origin", "none")

    # ---------- 统一兜底 ----------
    def _crash(self, e):
        """兜底：任何未处理异常都回一个 500，别让浏览器只看到「空响应」。

        http.server 的默认行为是断开连接，前端拿到的是 net::ERR_EMPTY_RESPONSE，
        完全看不出哪里错了；后台调脚本、读库都可能出意外，这里至少要留下痕迹。

        但客户端主动断开不算异常 —— 那种情况连 500 都发不出去，只会再抛一次。
        """
        if isinstance(e, CLIENT_GONE):
            self.close_connection = True
            return
        traceback.print_exc()
        try:
            self._send(500, "500 %s：%s（详细堆栈看服务端日志）"
                       % (type(e).__name__, str(e)), "text/plain; charset=utf-8")
        except Exception:
            pass

    # ---------- GET ----------
    def do_GET(self):
        try:
            self._do_GET()
        except Exception as e:
            self._crash(e)

    def _do_GET(self):
        path = urllib.parse.urlparse(self.path).path

        # 项目文档：公开，给部署后自己查用法用
        if path in ("/docs", "/docs/"):
            return self._send(200, render_doc("architecture"))
        if path == "/docs/schema":
            return self._send(200, render_schema())
        if path.startswith("/docs/"):
            page = render_doc(path[len("/docs/"):])
            if page:
                return self._send(200, page)
            return self._send(404, "没有这篇文档：%s" % html.escape(path), "text/plain; charset=utf-8")

        # 后台接口要排在 /admin/ 前缀判断之前，否则永远进不到这里
        if path == "/admin/api/stats":
            if not self._admin_ok():
                return self._send(403, '{"error":"forbidden"}', "application/json; charset=utf-8")
            # db_stats() 是三元组（表计数 / 导入记录 / 待处理失败），
            # 以前这里按二元组解包，一调就 ValueError（路由又是死的，没人发现）
            counts, runs, fails = db_stats()
            body = {"counts": counts or {}, "runs": runs, "failures": fails,
                    "db": DB, "exists": os.path.exists(DB)}
            return self._send(200, json.dumps(body, ensure_ascii=False, default=str),
                              "application/json; charset=utf-8")

        if path == "/admin/logout":
            return self._send(302, "", "text/plain; charset=utf-8",
                              {"Location": "/admin",
                               "Set-Cookie": "%s=; Path=/; Max-Age=0" % ADMIN_COOKIE})

        if path == "/admin" or path.startswith("/admin/"):
            if not self._admin_ok():
                return self._send(403, deny_page(self.client_address[0]),
                                  extra={"Cache-Control": "no-store"})
            extra = {"Cache-Control": "no-store"}
            cookie = self._admin_set_cookie()
            if cookie:
                extra["Set-Cookie"] = cookie
            return self._send(200, render(), extra=extra)
        if path in ("/", ""):
            path = "/index.html"
        return self._static(path)

    def do_HEAD(self):
        self.do_GET()

    def _static(self, path):
        rel = urllib.parse.unquote(path).lstrip("/")
        full = os.path.normpath(os.path.join(WEB, rel))
        # 用 commonpath 而不是 startswith：否则 <仓库>/web 旁边的 web2/ 也能被读出来
        try:
            inside = os.path.commonpath([full, WEB]) == WEB
        except ValueError:                     # Windows 跨盘符时 commonpath 会抛
            inside = False
        if not inside or not os.path.isfile(full):
            return self._send(404, "404 " + rel, "text/plain; charset=utf-8")
        ext = os.path.splitext(full)[1].lower()
        ctype = MIME.get(ext, "application/octet-stream")
        st = os.stat(full)
        cache = "no-cache" if ext == ".html" else "public, max-age=300"

        accepts_gzip = "gzip" in (self.headers.get("Accept-Encoding") or "")
        if accepts_gzip and ext in COMPRESSIBLE:
            key = (full, st.st_mtime, st.st_size)
            body = _gz_cache.get(key)
            if body is None:
                with open(full, "rb") as f:
                    body = gzip.compress(f.read(), 6)
                if len(_gz_cache) > 40:
                    _gz_cache.clear()
                _gz_cache[key] = body
            return self._send(200, body, ctype,
                              {"Content-Encoding": "gzip", "Cache-Control": cache,
                               "Vary": "Accept-Encoding"})
        with open(full, "rb") as f:
            body = f.read()
        return self._send(200, body, ctype, {"Cache-Control": cache})

    # ---------- POST ----------
    def do_POST(self):
        try:
            self._do_POST()
        except Exception as e:
            self._crash(e)

    def _do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if not (path.startswith("/admin/")):
            return self._send(404, "not found")
        if not self._admin_ok():
            return self._send(403, deny_page(self.client_address[0]))
        if not self._same_origin():
            return self._send(403, "forbidden：跨站提交被拒绝")

        # 三个按钮都要写库/写 data/。库不可写时直接说清楚，别让子进程抛 sqlite 原文。
        if path in ("/admin/crawl", "/admin/rebuild", "/admin/upload"):
            writable, why = db_writable()
            if not writable:
                return self._send(200, render(
                    "✗ 库不可写，这次操作没有执行。\n原因：%s\n\n"
                    "修：sudo chown -R USER:USER %s\n然后重启进程。"
                    % (why, os.path.dirname(os.path.abspath(DB)))))

        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        ctype = self.headers.get("Content-Type", "")

        if path == "/admin/rebuild":
            fields, _ = parse_fields(ctype, body)
            publish = str(fields.get("publish", "0")) == "1"
            cmd = ["scripts/manage.py", "update"] if publish else ["scripts/manage.py", "build"]
            ok, out = sh(cmd)
            return self._send(200, render(("✓ " if ok else "✗ ") + " ".join(cmd) + "\n\n" + out))

        if path == "/admin/crawl":
            fields, _ = parse_fields(ctype, body)
            mode = str(fields.get("mode", "all"))
            args = ["scripts/manage.py", "crawl"] + ([] if mode == "all" else ["--only", mode])
            label = {"all": "采集全部", "tender": "采集招标", "hearing": "采集开庭",
                     "bounty": "采集悬赏"}.get(mode, "采集")
            if start_job(label, args):
                return self._send(200, render())
            return self._send(200, render("已经有一个任务在跑了，等它结束。"))

        if path == "/admin/upload":
            fields, files = parse_multipart(ctype, body)
            if not files:
                return self._send(400, render("没有收到文件"))
            _, filename, payload = files[0]
            safe = re.sub(r"[^0-9A-Za-z._\-\u4e00-\u9fa5]", "_", os.path.basename(filename))
            os.makedirs(UPLOAD_DIR, exist_ok=True)
            dst = os.path.join(UPLOAD_DIR, safe)
            with open(dst, "wb") as f:
                f.write(payload)

            ext = os.path.splitext(safe)[1].lower()
            if ext not in (".db", ".jsonl", ".ndjson", ".json", ".csv"):
                return self._send(400, render("只支持 .db / .jsonl / .json / .csv"))

            cmd = ["scripts/manage.py", "add", dst]
            if fields.get("source"):
                cmd += ["--source", fields["source"]]
            ok, out = sh(cmd)
            return self._send(200, render(("✓ " if ok else "✗ ") + safe + "\n\n" + out))

        self._send(404, "not found")


class QuietHTTPServer(ThreadingHTTPServer):
    """客户端断连不刷日志。

    socketserver 会把所有逃出 handle() 的异常打成「Exception occurred during
    processing of request」+ 完整堆栈。浏览器取消加载时这是纯噪音：一页
    hearing.js 7 MB，用户随手切一下就是一段 BrokenPipe 堆栈，日志很快没法看。
    """

    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], CLIENT_GONE):
            return
        super().handle_error(request, client_address)


def main():
    setup_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("LEXIFY_PORT", "8010")))
    ap.add_argument("--bind", default=os.environ.get("LEXIFY_BIND", "0.0.0.0"))
    ap.add_argument("--at", default=os.environ.get("LEXIFY_UPDATE_AT", "00:30"), help="每天几点更新 HH:MM")
    ap.add_argument("--no-schedule", action="store_true", help="不跑定时任务")
    ap.add_argument("--admin-only", action="store_true", help="只开后台（本地维护用）")
    ap.add_argument("--admin-token", default="", help="后台口令（同 LEXIFY_ADMIN_TOKEN）")
    ap.add_argument("--admin-open", action="store_true", help="不设防，允许任何人进后台（仅限内网）")
    ap.add_argument("--run-now", action="store_true", help="启动时先更新一次")
    ap.add_argument("--no-auto-crawl", action="store_true", help="库里没数据时不要自动采集")
    args = ap.parse_args()

    global ADMIN_TOKEN, ADMIN_OPEN
    if args.admin_token:
        ADMIN_TOKEN = args.admin_token
    if args.admin_open:
        ADMIN_OPEN = True
    token, token_src = load_admin_token()

    # 先探库能不能写。只读库会让「自动采集」「后台按钮」「schema 补齐」全部失败，
    # 而 sqlite 的原始报错只说一句 readonly database，不说路径也不说怎么修。
    writable, why = db_writable()
    if not writable:
        print("⚠ 数据目录不可写：%s" % why)
        print(DB_FIX_HINT % (os.path.dirname(os.path.abspath(DB)),
                             os.path.dirname(os.path.abspath(DB))))
        print("  站点照常可以浏览（页面读的是 web/data/*.js 快照）；采集/上传/重建会失败。")

    if not os.path.exists(DB):
        print("库里还没有数据，先建一个空的：%s" % DB)
        sh(["scripts/manage.py", "init"])
    elif writable:
        # 幂等补表：老库也能拿到后加的 crawl_failures
        try:
            import manage
            manage.ensure_schema(quiet=True)
        except Exception as e:
            print("⚠ schema 检查失败（不影响启动）：%s" % e)

    # 首次启动：三块合起来一条数据都没有 → 后台自动跑一次采集
    if db_is_empty():
        if not writable:
            print("库是空的，但库不可写 → 自动采集已跳过，先按上面的提示修权限")
        elif args.no_auto_crawl:
            print("库是空的（--no-auto-crawl 已关闭自动采集）→ 去 /admin 点「采集全部并更新」")
        else:
            print("库是空的 → 后台自动执行第一次采集，进度看 /admin")
            start_job("首次采集", ["scripts/manage.py", "crawl"])
    elif not writable:
        pass                                   # 定时任务也别排，必然失败
    elif args.run_now:
        threading.Thread(target=run_update, daemon=True).start()
    elif not args.no_schedule and catch_up_needed(args.at):
        print("今天 %s 的更新还没跑 → 启动即补一次" % args.at)
        thread = threading.Thread(target=run_update, daemon=True)
        thread.start()

    if args.admin_only:
        args.bind = "127.0.0.1"

    if not args.no_schedule and writable:
        start_scheduler(args.at)

    srv = QuietHTTPServer((args.bind, args.port), Handler)
    host = "127.0.0.1" if args.bind == "0.0.0.0" else args.bind
    base = "http://%s:%d" % (host, args.port)
    print("站点：  %s/" % base)
    print("后台：  %s/admin" % base)
    print("定时：  %s" % ("关闭" if args.no_schedule else "每天 " + args.at))
    print("统一库：%s" % DB)
    print("运行于：%s（不依赖 shell，cmd / PowerShell / bash 都一样）" % compat_describe())
    if ADMIN_OPEN:
        print("⚠ 后台已设为不设防（--admin-open）：任何能访问端口的人都能进，别放公网")
    elif args.bind not in ("127.0.0.1", "localhost"):
        print("后台口令：%s" % token)
        print("         来源：%s" % token_src)
        print("         本机直连可直接打开；远程请在本页输入框粘贴口令，或 URL 加 ?token=…（记 30 天）")
        print("         用 SSH 隧道更安全：ssh -L %d:127.0.0.1:%d 用户@服务器" % (args.port, args.port))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
