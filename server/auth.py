# -*- coding: utf-8 -*-
"""后台口令判定与 403 页。

「本机」判定、口令来源、恒定时间比较、Cookie 签发，以及「为什么不对外」的
403 说明页。这些逻辑原本散在 Handler 里，抽出来独立可测。
"""
import html
import re
import secrets
import urllib.parse

from . import config

# 一旦请求带这几个转发头，就说明前面挂了反代，client_address 全是 127.0.0.1，
# 不能按「本机直连」对待。
_FORWARD_HEADERS = ("X-Forwarded-For", "X-Real-IP", "Forwarded",
                    "X-Forwarded-Host", "CF-Connecting-IP")


def is_local(client_ip, headers):
    """本机直连才算「本机」。带转发头的一律不算。"""
    ip = client_ip or ""
    if ip not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
        return False
    for h in _FORWARD_HEADERS:
        if headers.get(h):
            return False
    return True


def given_token(path, headers):
    """口令可以从三个地方来：?token=（首次）、X-Admin-Token（脚本）、Cookie（日常）。"""
    q = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
    t = (q.get("token") or [""])[0]
    if t:
        return t, "query"
    t = headers.get("X-Admin-Token") or ""
    if t:
        return t, "header"
    m = re.search(r"(?:^|;\s*)%s=([^;]*)" % config.ADMIN_COOKIE, headers.get("Cookie") or "")
    if m:
        return urllib.parse.unquote(m.group(1)), "cookie"
    return "", ""


def token_ok(given):
    return bool(config.ADMIN_TOKEN) and secrets.compare_digest(
        given.encode("utf-8"), config.ADMIN_TOKEN.encode("utf-8"))


def admin_ok(client_ip, headers, path):
    if is_local(client_ip, headers) or config.ADMIN_OPEN:
        return True
    if not config.ADMIN_TOKEN:
        return False
    return token_ok(given_token(path, headers)[0])


def set_cookie(headers, path):
    """从 ?token= 进来的，把口令种进 Cookie（30 天，HttpOnly，HTTPS 下加 Secure）。"""
    given, src = given_token(path, headers)
    if src != "query" or not token_ok(given):
        return None
    https = (headers.get("X-Forwarded-Proto") or "").lower() == "https"
    return "%s=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000%s" % (
        config.ADMIN_COOKIE, urllib.parse.quote(given, safe=""), "; Secure" if https else "")


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
