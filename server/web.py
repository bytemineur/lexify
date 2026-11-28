# -*- coding: utf-8 -*-
"""HTTP 处理：路由、静态服务、上传解析。

这是唯一接触 HTTP 协议的模块。Handler 只做「把请求路由到正确的业务函数」，
鉴权（auth）、后台页（admin）、文档（docs）、任务（jobs）、数据库（db）各自独立。

安全上这里守住三条边界：
  1. 静态文件：commonpath + realpath 双重校验，符号链接也逃不出 web/。
  2. 上传：限制体积、先校验扩展名再落盘、用 os.replace 避免并发写坏。
  3. 日志：绝不记录 ?token= 明文。

AI 咨询（/api/ai/*）是唯一对外放开的 POST：它不碰库、不写盘，只把问题发给模型，
所以不走后台口令，改为同源校验 + 体积上限 + 每 IP 限流。
"""
import email.parser
import email.policy
import gzip
import html
import json
import os
import re
import sys
import threading
import time
import traceback
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import accounts as acctmod
from . import sms as smsmod
from . import config
from . import ai as aimod
from . import analytics as anamod
from . import auth as authmod
from . import admin as adminmod
from . import data as datamod
from . import docs as docsmod
from . import jobs as jobsmod
from .config import (ADMIN_COOKIE, CLIENT_GONE, COMPRESSIBLE, MIME, UPLOAD_DIR, WEB, DB,
                     auth_conf, sms_ready)

# 上传体上限（默认 32 MB）：没上限会让恶意请求灌满磁盘/内存。
MAX_UPLOAD_BYTES = int(os.environ.get("LEXIFY_MAX_UPLOAD", str(32 * 1024 * 1024)))

# 站内文档（/docs）默认只给后台访问：里面是架构、部署、库结构这类内部信息。
# 设 LEXIFY_DOCS_PUBLIC=1 可以重新公开。
DOCS_PUBLIC = (os.environ.get("LEXIFY_DOCS_PUBLIC", "") or "").strip().lower() in (
    "1", "true", "yes", "on")

# AI 对话：单次请求体上限、每 IP 每分钟次数、全局并发上限（防止一把 Key 被刷爆）。
AI_MAX_BODY = 64 * 1024
AI_RATE_PER_MIN = int(os.environ.get("LEXIFY_AI_RATE", "20"))
AI_MAX_CONCURRENT = int(os.environ.get("LEXIFY_AI_CONCURRENCY", "4"))
_sem_ai = threading.BoundedSemaphore(max(1, AI_MAX_CONCURRENT))
_ai_hits = {}
_hits_lock = threading.Lock()

# gzip 结果按 (路径, mtime, size) 缓存，超出 40 项就整体清空。
_gz_cache = {}

# 访问统计：单条上限、每 IP 每分钟上报次数。前端每看一页发 1~2 条，
# 正常浏览远到不了上限，主要是挡住脚本灌数据。
TRACK_MAX_BODY = 4 * 1024
TRACK_RATE_PER_MIN = int(os.environ.get("LEXIFY_TRACK_RATE", "120"))
_track_hits = {}
_track_lock = threading.Lock()

# 短信登录：哪些页面属于「产品」、必须登录才能看；发送验证码的每 IP 限额。
BOARD_PAGES = ("/tender.html", "/hearing.html", "/bounty.html",
               "/falseclaim.html", "/labor.html")
AUTH_MAX_BODY = 2 * 1024
SEND_RATE_PER_HOUR = int(os.environ.get("LEXIFY_AUTH_SEND_PER_HOUR", "20"))
_send_hits = {}
_send_lock = threading.Lock()


def send_allow(ip):
    """发送验证码的每 IP 每小时限额（手机号维度另有一层）。"""
    now = time.time()
    with _send_lock:
        bucket = [t for t in _send_hits.get(ip, []) if now - t < 3600]
        if len(bucket) >= SEND_RATE_PER_HOUR:
            _send_hits[ip] = bucket
            return False
        bucket.append(now)
        _send_hits[ip] = bucket
        if len(_send_hits) > 2000:
            for k in [k for k, v in _send_hits.items() if not v or now - v[-1] > 7200]:
                _send_hits.pop(k, None)
    return True


def auth_required():
    """站点是否要求登录才能看看板。"""
    return config.auth_conf()["enabled"]


def current_user(headers):
    """从 Cookie 里取当前登录用户；没登录返回 None。"""
    if not auth_required():
        return None
    return acctmod.session_of(acctmod.token_from_cookie(headers))


def track_allow(ip):
    """统计上报的每 IP 每分钟限流（固定窗口）。返回 (是否放行, 已用次数)。"""
    now = time.time()
    with _track_lock:
        bucket = [t for t in _track_hits.get(ip, []) if now - t < 60]
        if len(bucket) >= TRACK_RATE_PER_MIN:
            _track_hits[ip] = bucket
            return False, len(bucket)
        bucket.append(now)
        _track_hits[ip] = bucket
        if len(_track_hits) > 2000:
            for k in [k for k, v in _track_hits.items() if not v or now - v[-1] > 300]:
                _track_hits.pop(k, None)
    return True, len(bucket)


def ai_allow(ip):
    """每 IP 每分钟固定窗口限流。返回 (是否放行, 需等待秒数)。"""
    now = time.time()
    with _hits_lock:
        bucket = [t for t in _ai_hits.get(ip, []) if now - t < 60]
        if len(bucket) >= AI_RATE_PER_MIN:
            _ai_hits[ip] = bucket
            return False, int(60 - (now - bucket[0])) + 1
        bucket.append(now)
        _ai_hits[ip] = bucket
        if len(_ai_hits) > 2000:      # 控制内存，清掉过期 IP
            for k in [k for k, v in _ai_hits.items() if not v or now - v[-1] > 300]:
                _ai_hits.pop(k, None)
    return True, 0



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


def _redact_path(path):
    """日志里不落 ?token=… 的明文。"""
    if "token=" in path:
        return re.sub(r"([?&]token=)[^&\s]*", r"\1***", path)
    return path


class Handler(BaseHTTPRequestHandler):
    server_version = "Lexify"
    protocol_version = "HTTP/1.1"
    _client_gone = False

    def log_message(self, fmt, *args):
        # 请求行解析失败（扫描器乱发字节）时 self.path 还不存在，
        # 这里必须用 getattr 兜底，否则 log_error 里会抛 AttributeError，
        # 在 nohup.out 里刷一大片假 traceback。
        path = getattr(self, "path", "") or ""
        if "/admin" in path:
            print("[%s] %s" % (datetime.now().strftime("%H:%M:%S"), _redact_path(path)), flush=True)

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
            self.close_connection = True
            self._client_gone = True

    def _same_origin(self):
        origin = self.headers.get("Origin")
        if origin:
            return urllib.parse.urlparse(origin).netloc == (self.headers.get("Host") or "")
        site = self.headers.get("Sec-Fetch-Site")
        return site in (None, "", "same-origin", "none")

    def _crash(self, e):
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

        if path == "/api/ai/status":
            return self._ai_status()

        # 登录状态：前端导航栏靠它决定显示「登录」还是「退出」。
        if path == "/api/auth/me":
            return self._auth_me()

        # 「产品」下的看板页面与看板数据接口要登录才能看。
        # 页面跳登录页（带 next），接口回 401 让前端自己引导。
        if auth_required() and (path in BOARD_PAGES or path.startswith("/api/data/board/")
                                or path == "/api/data/tenders"):
            if not current_user(self.headers):
                if path.startswith("/api/"):
                    return self._json(401, {"error": "login_required"})
                nxt = urllib.parse.quote(self.path, safe="")
                return self._send(302, "", "text/plain; charset=utf-8",
                                  {"Location": "/login.html?next=" + nxt,
                                   "Cache-Control": "no-store"})

        # 实时数据接口：页面直接读库，不经过静态快照
        if path.startswith("/api/data/"):
            return self._data_api(path, urllib.parse.urlparse(self.path).query)

        # 站内文档默认不对外：里面有架构、部署与库结构，属于内部信息。
        # 需要公开时设 LEXIFY_DOCS_PUBLIC=1。
        if path in ("/docs", "/docs/") or path == "/docs/schema" or path.startswith("/docs/"):
            if not DOCS_PUBLIC and not authmod.admin_ok(
                    self.client_address[0], self.headers, self.path):
                return self._send(403, authmod.deny_page(self.client_address[0]))

        if path in ("/docs", "/docs/"):
            return self._send(200, docsmod.render_doc("architecture"))
        if path == "/docs/schema":
            return self._send(200, docsmod.render_schema())
        if path.startswith("/docs/"):
            page = docsmod.render_doc(path[len("/docs/"):])
            if page:
                return self._send(200, page)
            return self._send(404, "没有这篇文档：%s" % html.escape(path), "text/plain; charset=utf-8")

        # 后台接口要排在 /admin/ 前缀判断之前，否则永远进不到这里
        if path == "/admin/api/stats":
            if not authmod.admin_ok(self.client_address[0], self.headers, self.path):
                return self._send(403, '{"error":"forbidden"}', "application/json; charset=utf-8")
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            days = adminmod.clamp_days((q.get("days") or ["7"])[0])
            counts, runs, fails = adminmod.db_stats()
            body = {"counts": counts or {}, "runs": runs, "failures": fails,
                    "db": DB, "exists": os.path.exists(DB),
                    "analytics": anamod.stats(days)}
            return self._send(200, json.dumps(body, ensure_ascii=False, default=str),
                              "application/json; charset=utf-8")

        if path == "/admin/logout":
            return self._send(302, "", "text/plain; charset=utf-8",
                              {"Location": "/admin",
                               "Set-Cookie": "%s=; Path=/; Max-Age=0" % ADMIN_COOKIE})

        if path == "/admin" or path.startswith("/admin/"):
            if not authmod.admin_ok(self.client_address[0], self.headers, self.path):
                return self._send(403, authmod.deny_page(self.client_address[0]),
                                  extra={"Cache-Control": "no-store"})
            extra = {"Cache-Control": "no-store"}
            cookie = authmod.set_cookie(self.headers, self.path)
            if cookie:
                extra["Set-Cookie"] = cookie
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            tab = (q.get("tab") or ["overview"])[0]
            days = adminmod.clamp_days((q.get("days") or ["7"])[0])
            return self._send(200, adminmod.render(tab=tab, days=days), extra=extra)
        if path in ("/", ""):
            path = "/index.html"
        return self._static(path)

    def do_HEAD(self):
        self.do_GET()

    def _static(self, path):
        rel = urllib.parse.unquote(path).lstrip("/")
        full = os.path.realpath(os.path.join(WEB, rel))
        web_real = os.path.realpath(WEB)
        # commonpath 挡住 web2/ 前缀绕过；realpath 再挡住符号链接跳出 web/。
        try:
            inside = os.path.commonpath([full, web_real]) == web_real
        except ValueError:
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
        if path.startswith("/api/auth/"):
            return self._auth_post(path)
        if path.startswith("/api/ai/"):
            return self._ai_post(path)
        if path == "/api/track":
            return self._track_post()
        if not (path.startswith("/admin/")):
            return self._send(404, "not found")
        if not authmod.admin_ok(self.client_address[0], self.headers, self.path):
            return self._send(403, authmod.deny_page(self.client_address[0]))
        if not self._same_origin():
            return self._send(403, "forbidden：跨站提交被拒绝")

        if path in ("/admin/crawl", "/admin/rebuild", "/admin/upload"):
            writable, why = adminmod.db_writable()
            if not writable:
                return self._send(200, adminmod.render(
                    "✗ 库不可写，这次操作没有执行。\n原因：%s\n\n"
                    "修：sudo chown -R USER:USER %s\n然后重启进程。"
                    % (why, os.path.dirname(os.path.abspath(DB)))))

        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_UPLOAD_BYTES:
            return self._send(413, adminmod.render("文件过大（上限 %d MB）" % (MAX_UPLOAD_BYTES // 1024 // 1024)))
        body = self.rfile.read(length)
        ctype = self.headers.get("Content-Type", "")

        if path == "/admin/rebuild":
            fields, _ = parse_fields(ctype, body)
            publish = str(fields.get("publish", "0")) == "1"
            cmd = ["scripts/manage.py", "update"] if publish else ["scripts/manage.py", "build"]
            ok, out = jobsmod.sh(cmd)
            return self._send(200, adminmod.render(("✓ " if ok else "✗ ") + " ".join(cmd) + "\n\n" + out))

        if path == "/admin/crawl":
            fields, _ = parse_fields(ctype, body)
            mode = str(fields.get("mode", "all"))
            args = ["scripts/manage.py", "crawl"] + ([] if mode == "all" else ["--only", mode])
            label = {"all": "采集全部", "tender": "采集招标", "hearing": "采集开庭",
                     "bounty": "采集悬赏"}.get(mode, "采集")
            if jobsmod.start_job(label, args):
                return self._send(200, adminmod.render())
            return self._send(200, adminmod.render("已经有一个任务在跑了，等它结束。"))

        if path == "/admin/upload":
            fields, files = parse_multipart(ctype, body)
            if not files:
                return self._send(400, adminmod.render("没有收到文件"))
            _, filename, payload = files[0]

            ext = os.path.splitext(filename)[1].lower()
            if ext not in (".db", ".jsonl", ".ndjson", ".json", ".csv"):
                return self._send(400, adminmod.render("只支持 .db / .jsonl / .json / .csv"))

            safe = re.sub(r"[^0-9A-Za-z._\-\u4e00-\u9fa5]", "_", os.path.basename(filename))
            os.makedirs(UPLOAD_DIR, exist_ok=True)
            dst = os.path.join(UPLOAD_DIR, safe)
            # 先写临时文件再原子改名，避免并发请求读到半截文件。
            tmp = dst + ".part"
            with open(tmp, "wb") as f:
                f.write(payload)
            os.replace(tmp, dst)

            cmd = ["scripts/manage.py", "add", dst]
            if fields.get("source"):
                cmd += ["--source", fields["source"]]
            ok, out = jobsmod.sh(cmd)
            return self._send(200, adminmod.render(("✓ " if ok else "✗ ") + safe + "\n\n" + out))

        self._send(404, "not found")


    # ---------- AI 咨询 ----------
    def _json(self, code, obj, extra=None):
        """返回 JSON。大响应会 gzip。

        为什么要压：看板接口一次下发几 MB（招标约 4.7 MB），服务器上带宽只有
        200 KB/s 左右，不压要 20 秒以上，前端 20 秒超时就把请求 abort 掉、
        退回离线快照——用户看到的是「又慢又是旧数据」。公告正文里有大量重复的
        中文与 HTML 片段，gzip 能压到十分之一左右。

        小响应（< 1 KB）不压：省不下多少，反而多一层开销。
        """
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        headers = {"Cache-Control": "no-store"}
        headers.update(extra or {})
        accepts_gzip = "gzip" in (self.headers.get("Accept-Encoding") or "")
        if accepts_gzip and len(data) >= 1024:
            data = gzip.compress(data, 6)
            headers["Content-Encoding"] = "gzip"
            headers["Vary"] = "Accept-Encoding"
        return self._send(code, data, "application/json; charset=utf-8", headers)

    # ---------- 访问统计上报 ----------
    def _no_content(self, code=204, extra=None):
        headers = {"Cache-Control": "no-store"}
        headers.update(extra or {})
        return self._send(code, "", "text/plain; charset=utf-8", headers)

    # ---------- 短信登录 ----------
    def _auth_me(self):
        user = acctmod.session_of(acctmod.token_from_cookie(self.headers))
        body = {
            "loggedIn": bool(user),
            "phone": user["masked"] if user else "",
            "loginRequired": auth_required(),
            "smsReady": sms_ready(),
            "devMode": bool(config.auth_conf()["dev_mode"]),
        }
        return self._json(200, body, extra={"Cache-Control": "no-store"})

    def _read_json_body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > AUTH_MAX_BODY:
            return None
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8", "replace"))
        except (ValueError, OSError):
            return None
        return payload if isinstance(payload, dict) else None

    def _auth_post(self, path):
        if not self._same_origin():
            return self._json(403, {"error": "cross_origin"})
        ip = self.client_address[0]
        if path == "/api/auth/send-code":
            return self._auth_send_code(ip)
        if path == "/api/auth/login":
            return self._auth_login(ip)
        if path == "/api/auth/logout":
            acctmod.drop_session(acctmod.token_from_cookie(self.headers))
            return self._no_content(204, {"Set-Cookie": acctmod.clear_cookie()})
        return self._json(404, {"error": "not_found"})

    def _auth_send_code(self, ip):
        payload = self._read_json_body() or {}
        phone = acctmod.normalize_phone(payload.get("phone"))
        if not phone:
            return self._json(400, {"error": "bad_phone", "message": "手机号看起来不对"})
        if not send_allow(ip):
            return self._json(429, {"error": "too_many", "message": "发送太频繁，稍后再试"})
        ok, err, code = acctmod.issue_code(phone, ip)
        if not ok:
            return self._json(429, {"error": "too_many", "message": err})

        conf = config.auth_conf()
        if sms_ready():
            ok2, rid, msg = smsmod.send_code(
                config.sms_conf()["secret_id"], config.sms_conf()["secret_key"],
                config.sms_conf()["sdk_app_id"], config.sms_conf()["sign_name"],
                config.sms_conf()["template_id"], phone, code,
                region=config.sms_conf()["region"])
            if not ok2:
                print("[sms] 发送失败 %s：%s" % (acctmod.mask_phone(phone), msg), flush=True)
                # 把腾讯云的人话错误带回去：签名没审核、模板不对、号码黑名单
                # 都靠这句话排查，比笼统的「稍后再试」有用得多。
                return self._json(502, {"error": "sms_failed",
                                        "message": msg or "短信没发出去，稍后再试"})
            print("[sms] 已发送 %s requestId=%s" % (acctmod.mask_phone(phone), rid), flush=True)
            body = {"ok": True, "phone": acctmod.mask_phone(phone),
                    "resendIn": conf["resend_seconds"]}
            return self._json(200, body, extra={"Cache-Control": "no-store"})

        # 没配短信服务：只有显式开 dev_mode 才把验证码回给前端（本地演示用）
        if conf["dev_mode"]:
            print("[sms] 未配置短信服务，dev_mode 验证码 %s → %s"
                  % (acctmod.mask_phone(phone), code), flush=True)
            return self._json(200, {"ok": True, "phone": acctmod.mask_phone(phone),
                                    "resendIn": conf["resend_seconds"],
                                    "devCode": code, "smsReady": False},
                              extra={"Cache-Control": "no-store"})
        print("[sms] 未配置短信服务（sms.secret_id 等），%s 的验证码发不出去"
              % acctmod.mask_phone(phone), flush=True)
        return self._json(503, {"error": "sms_not_configured",
                                "message": "短信服务还没配置，请联系管理员"})

    def _auth_login(self, ip):
        payload = self._read_json_body() or {}
        phone = acctmod.normalize_phone(payload.get("phone"))
        if not phone:
            return self._json(400, {"error": "bad_phone", "message": "手机号看起来不对"})
        ok, err = acctmod.verify_code(phone, payload.get("code"))
        if not ok:
            return self._json(400, {"error": "bad_code", "message": err})
        token = acctmod.create_session(phone, ip, self.headers.get("User-Agent") or "")
        if not token:
            return self._json(500, {"error": "session_failed", "message": "登录状态没保存下来"})
        https = (self.headers.get("X-Forwarded-Proto") or "").lower() == "https"
        return self._json(200, {"ok": True, "phone": acctmod.mask_phone(phone)},
                          extra={"Set-Cookie": acctmod.cookie_for(token, https),
                                 "Cache-Control": "no-store"})

    def _track_post(self):
        """前端 track.js 的上报入口。

        和 AI 接口一样是公开 POST，所以同样守住三条：同源、体积、限流。
        记不上也返回 204 —— 统计失败不该在浏览器控制台留一堆报错。
        """
        ip = self.client_address[0]
        if not anamod.enabled():
            return self._no_content()
        if anamod.respect_dnt() and (self.headers.get("DNT") or "").strip() == "1":
            return self._no_content()
        if not self._same_origin():
            return self._no_content(403)
        ok, _used = track_allow(ip)
        if not ok:
            return self._no_content(429, {"Retry-After": "30"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length > TRACK_MAX_BODY:
            return self._no_content(413)
        if length <= 0:
            return self._no_content()
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8", "replace"))
        except (ValueError, OSError):
            return self._no_content(400)
        if not isinstance(payload, dict):
            return self._no_content(400)
        if str(payload.get("event") or "view") == "leave":
            anamod.record_leave(payload.get("vid"), payload.get("dwell"))
        else:
            payload["ua"] = self.headers.get("User-Agent") or ""
            anamod.record(payload, ip)
        return self._no_content()

    # ---------- 实时数据 ----------
    def _data_api(self, path, query):
        """页面实时查询的入口。全部现查 SQLite，返回 JSON。

        这些接口是公开的（站点本身就对所有人开放），只读、不写盘。
        """
        q = urllib.parse.parse_qs(query or "")
        try:
            if path == "/api/data/health":
                return self._json(200, datamod.health())
            if path.startswith("/api/data/board/"):
                key = path[len("/api/data/board/"):].strip("/")
                return self._json(200, datamod.board(key))
            if path == "/api/data/stats/overview":
                return self._json(200, datamod.stats_overview())
            if path == "/api/data/stats/trend":
                days = (q.get("days") or ["30"])[0]
                return self._json(200, datamod.stats_trend(int(days) if days.isdigit() else 30))
            if path == "/api/data/stats/dist":
                dim = (q.get("dim") or ["province"])[0]
                limit = (q.get("limit") or ["10"])[0]
                return self._json(200, datamod.stats_dist(dim, int(limit) if limit.isdigit() else 10))
            if path == "/api/data/stats/upcoming":
                days = (q.get("days") or ["7"])[0]
                return self._json(200, datamod.stats_upcoming(int(days) if days.isdigit() else 7))
            if path == "/api/data/sources":
                return self._json(200, datamod.sources())
            if path == "/api/data/tenders":
                return self._json(200, datamod.tenders(q))
            return self._json(404, {"error": "not found"})
        except KeyError as e:
            return self._json(400, {"error": str(e)})
        except FileNotFoundError as e:
            return self._json(503, {"error": str(e), "hint": "先跑一次采集或建库"})
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return self._json(500, {"error": "%s: %s" % (type(e).__name__, e)})

    def _ai_status(self):
        """前端据此决定显示入口还是「未配置」提示。不返回 Key。"""
        try:
            st = aimod.warm_static_file()
        except Exception:
            st = {"enabled": False, "model": ""}
        text, meta = "", {}
        try:
            _, meta = aimod.build_knowledge()
        except Exception:
            pass
        return self._json(200, {
            "enabled": st["enabled"],
            "model": st["model"],
            "knowledge": {"files": meta.get("files", 0), "docs": meta.get("docs", 0),
                          "chars": meta.get("chars", 0), "builtAt": meta.get("builtAt", "")},
        })

    def _ai_post(self, path):
        if path != "/api/ai/chat":
            return self._json(404, {"error": "not found"})
        # 同源校验：只允许本站页面调用，挡住第三方站点盗刷。
        if not self._same_origin():
            return self._json(403, {"error": "跨站请求被拒绝"})

        ok, wait = ai_allow(self.client_address[0])
        if not ok:
            return self._json(429, {"error": "问得有点快，%d 秒后再试。" % wait})

        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > AI_MAX_BODY:
            return self._json(413, {"error": "请求体过大或为空"})
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8", "replace") or "{}")
        except ValueError:
            return self._json(400, {"error": "不是合法的 JSON"})
        if not isinstance(data, dict):
            return self._json(400, {"error": "参数格式不对"})

        question = str(data.get("message") or "").strip()
        if not question:
            return self._json(400, {"error": "问题不能为空"})
        if len(question) > 2000:
            return self._json(400, {"error": "问题太长了，请控制在 2000 字以内"})
        history = data.get("history") if isinstance(data.get("history"), list) else []

        cfg = aimod.settings()
        if not cfg["enabled"]:
            return self._json(503, {"error": "服务端还没配置 API Key（config.yaml 的 ai.api_key，"
                                             "或环境变量 LEXIFY_AI_API_KEY），AI 咨询暂时不可用。",
                                     "hint": "配好后重启进程即可，页面会自动出现入口。"})
        key, _ = aimod.api_key()
        cfg["key"] = key

        if not _sem_ai.acquire(blocking=False):
            return self._json(503, {"error": "当前咨询的人有点多，稍等几秒再发一次。"})
        try:
            knowledge, _ = aimod.build_knowledge()
            system = {"role": "system",
                      "content": aimod.SYSTEM_PROMPT.replace("{knowledge}", knowledge.strip())}
            msgs = [system] + aimod.build_messages(history, question)
            client = aimod.ChatClient(cfg)

            if not data.get("stream"):
                return self._json(200, {"reply": client.complete(msgs)})

            # ---- 流式：先发头，再逐字推。已开始响应后不能再改状态码，
            #      出错就发一条 error 事件让前端展示。
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
            except CLIENT_GONE:
                self.close_connection = True
                return
            self.close_connection = True

            def emit(event, payload):
                chunk = ("event: %s\ndata: %s\n\n" % (event, json.dumps(payload, ensure_ascii=False)))
                self.wfile.write(chunk.encode("utf-8"))
                self.wfile.flush()

            try:
                for piece in client.stream(msgs):
                    emit("delta", {"t": piece})
                emit("done", {"ok": True})
            except CLIENT_GONE:
                return
            except aimod.AiError as e:
                emit("error", {"error": str(e)})
            except Exception as e:            # 兜底，别让前端一直转圈
                emit("error", {"error": "服务端异常：%s" % type(e).__name__})
            return
        finally:
            _sem_ai.release()


class QuietHTTPServer(ThreadingHTTPServer):
    """客户端断连不刷日志。"""
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], CLIENT_GONE):
            return
        super().handle_error(request, client_address)
