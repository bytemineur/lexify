# -*- coding: utf-8 -*-
"""Lexify 单一入口：站点 + 管理后台 + 定时更新，一个进程全包。

不依赖 nginx、不依赖 Docker、不依赖第三方库，Linux 上装个 Python 3.9+ 就能跑：

    python main.py                       # http://0.0.0.0:8000
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
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, "web")
UPLOAD_DIR = os.path.join(ROOT, "data", "uploads")
DB = os.environ.get("LEXIFY_DB") or os.path.join(ROOT, "data", "lexify.db")
PY = sys.executable
ADMIN_TOKEN = os.environ.get("LEXIFY_ADMIN_TOKEN", "")

MIME = {
    ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8", ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg",
    ".webp": "image/webp", ".ico": "image/x-icon", ".woff2": "font/woff2",
    ".ttf": "font/ttf", ".txt": "text/plain; charset=utf-8", ".csv": "text/csv; charset=utf-8",
}
COMPRESSIBLE = (".html", ".css", ".js", ".json", ".svg", ".txt")
_gz_cache = {}


# ────────────────────────────── 数据与子进程 ──────────────────────────────
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
    if not os.path.exists(DB):
        return None, [], []
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    counts = {}
    for t in ("tenders", "sources", "hearings", "notices"):
        try:
            counts[t] = conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
        except sqlite3.Error:
            counts[t] = -1
    runs = [dict(r) for r in conn.execute(
        "SELECT started_at,kind,mode,origin,total_seen,inserted,updated,skipped,status "
        "FROM ingest_runs ORDER BY id DESC LIMIT 8")]
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
                           encoding="utf-8", errors="replace", timeout=timeout)
        return p.returncode == 0, ((p.stdout or "") + (p.stderr or "")).strip()[-8000:]
    except subprocess.TimeoutExpired:
        return False, "超时（%d 秒）" % timeout


# ────────────────────────────── 定时更新 ──────────────────────────────
def next_run(at_hhmm, now=None):
    now = now or datetime.now()
    hh, mm = [int(x) for x in at_hhmm.split(":")]
    t = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return t if t > now else t + timedelta(days=1)


def run_update():
    print("[%s] 开始更新" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"), flush=True)
    ok, out = sh(["scripts/manage.py", "update"])
    print(out, flush=True)
    print("[%s] 更新结束（%s）" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "ok" if ok else "失败"), flush=True)
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
                                 errors="replace", bufsize=1)
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
</style></head><body>
<h1>Lexify 数据后台</h1>
<div class="sub">统一库 <code>{db}</code> · <a href="/docs">项目文档</a> · <a href="/">← 回到站点</a></div>
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
    return PAGE.format(db=html.escape(DB), counts=counts_html, runs=runs_html,
                       failures=fails_html, output=out, job=job_html(), refresh=refresh)


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

    def log_message(self, fmt, *args):
        if "/admin" in (self.path or ""):
            print("[%s] %s" % (datetime.now().strftime("%H:%M:%S"), self.path), flush=True)

    def _send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _admin_ok(self):
        if ADMIN_TOKEN:
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            return q.get("token", [""])[0] == ADMIN_TOKEN or self.headers.get("X-Admin-Token") == ADMIN_TOKEN
        return self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")

    # ---------- GET ----------
    def do_GET(self):
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

        if path == "/admin" or path.startswith("/admin/"):
            if not self._admin_ok():
                return self._send(403, "forbidden：后台只允许本机访问，或带 ?token=")
            return self._send(200, render())
        if path == "/admin/api/stats":
            if not self._admin_ok():
                return self._send(403, "{}")
            c, _ = db_stats()
            return self._send(200, json.dumps(c, ensure_ascii=False), "application/json; charset=utf-8")
        if path in ("/", ""):
            path = "/index.html"
        return self._static(path)

    def do_HEAD(self):
        self.do_GET()

    def _static(self, path):
        rel = urllib.parse.unquote(path).lstrip("/")
        full = os.path.normpath(os.path.join(WEB, rel))
        if not full.startswith(WEB) or not os.path.isfile(full):
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
        path = urllib.parse.urlparse(self.path).path
        if not (path.startswith("/admin/")):
            return self._send(404, "not found")
        if not self._admin_ok():
            return self._send(403, "forbidden")

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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("LEXIFY_PORT", "8000")))
    ap.add_argument("--bind", default=os.environ.get("LEXIFY_BIND", "0.0.0.0"))
    ap.add_argument("--at", default=os.environ.get("LEXIFY_UPDATE_AT", "00:30"), help="每天几点更新 HH:MM")
    ap.add_argument("--no-schedule", action="store_true", help="不跑定时任务")
    ap.add_argument("--admin-only", action="store_true", help="只开后台（本地维护用）")
    ap.add_argument("--run-now", action="store_true", help="启动时先更新一次")
    ap.add_argument("--no-auto-crawl", action="store_true", help="库里没数据时不要自动采集")
    args = ap.parse_args()

    if not os.path.exists(DB):
        print("库里还没有数据，先建一个空的：%s" % DB)
        sh(["scripts/manage.py", "init"])
    else:
        # 幂等补表：老库也能拿到后加的 crawl_failures
        try:
            sys.path.insert(0, os.path.join(ROOT, "scripts"))
            import manage
            manage.ensure_schema(quiet=True)
        except Exception as e:
            print("⚠ schema 检查失败（不影响启动）：%s" % e)

    # 首次启动：三块合起来一条数据都没有 → 后台自动跑一次采集
    if db_is_empty():
        if args.no_auto_crawl:
            print("库是空的（--no-auto-crawl 已关闭自动采集）→ 去 /admin 点「采集全部并更新」")
        else:
            print("库是空的 → 后台自动执行第一次采集，进度看 /admin")
            start_job("首次采集", ["scripts/manage.py", "crawl"])
    elif args.run_now:
        threading.Thread(target=run_update, daemon=True).start()

    if args.admin_only:
        args.bind = "127.0.0.1"

    if not args.no_schedule:
        start_scheduler(args.at)

    srv = ThreadingHTTPServer((args.bind, args.port), Handler)
    srv.daemon_threads = True
    host = "127.0.0.1" if args.bind == "0.0.0.0" else args.bind
    print("站点：  http://%s:%d/" % (host, args.port))
    print("后台：  http://%s:%d/admin%s" % (host, args.port, "" if not ADMIN_TOKEN else "?token=…"))
    print("定时：  %s" % ("关闭" if args.no_schedule else "每天 " + args.at))
    print("统一库：%s" % DB)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
