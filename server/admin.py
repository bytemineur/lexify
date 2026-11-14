# -*- coding: utf-8 -*-
"""后台管理页渲染。"""
import html
import os

from . import config
from .db import db_stats, db_tables_ok, db_writable
from .jobs import JOB, job_html

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

    writable, why = db_writable()
    if not writable:
        notice = ('<div class="warn"><b>数据目录不可写</b>：%s<br>'
                  '采集、上传、重建都会失败（站点浏览不受影响）。'
                  '通常是之前用 <code>sudo</code> 跑过，<code>data/</code> 属主变成了 root：<br>'
                  '<code>sudo chown -R USER:USER %s</code>，然后重启进程。</div>'
                  % (html.escape(why), html.escape(os.path.dirname(os.path.abspath(config.DB)))))
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
    if config.ADMIN_OPEN:
        auth = ' · <span style="color:#c62828">后台未设防</span>'
    elif config.ADMIN_TOKEN:
        auth = ' · <a href="/admin/logout">退出登录</a>'
    return PAGE.format(db=html.escape(config.DB), counts=counts_html, runs=runs_html,
                       failures=fails_html, output=out, job=job_html(), refresh=refresh,
                       auth=auth, notice=notice)
