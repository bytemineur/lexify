# -*- coding: utf-8 -*-
"""项目文档：Markdown 渲染 + 实时表结构。"""
import html
import os
import re
import sqlite3

from .config import DB, ROOT

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
