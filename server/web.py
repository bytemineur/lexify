# -*- coding: utf-8 -*-
"""HTTP 处理：路由、静态服务、上传解析。

这是唯一接触 HTTP 协议的模块。Handler 只做「把请求路由到正确的业务函数」，
鉴权（auth）、后台页（admin）、文档（docs）、任务（jobs）、数据库（db）各自独立。

安全上这里守住三条边界：
  1. 静态文件：commonpath + realpath 双重校验，符号链接也逃不出 web/。
  2. 上传：限制体积、先校验扩展名再落盘、用 os.replace 避免并发写坏。
  3. 日志：绝不记录 ?token= 明文。
"""
import email.parser
import email.policy
import gzip
import html
import json
import os
import re
import sys
import traceback
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import auth as authmod
from . import admin as adminmod
from . import docs as docsmod
from . import jobs as jobsmod
from .config import ADMIN_COOKIE, CLIENT_GONE, COMPRESSIBLE, MIME, UPLOAD_DIR, WEB, DB

# 上传体上限（默认 32 MB）：没上限会让恶意请求灌满磁盘/内存。
MAX_UPLOAD_BYTES = int(os.environ.get("LEXIFY_MAX_UPLOAD", str(32 * 1024 * 1024)))

# gzip 结果按 (路径, mtime, size) 缓存，超出 40 项就整体清空。
_gz_cache = {}


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
        if "/admin" in (self.path or ""):
            print("[%s] %s" % (datetime.now().strftime("%H:%M:%S"), _redact_path(self.path)), flush=True)

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
            counts, runs, fails = adminmod.db_stats()
            body = {"counts": counts or {}, "runs": runs, "failures": fails,
                    "db": DB, "exists": os.path.exists(DB)}
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
            return self._send(200, adminmod.render(), extra=extra)
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


class QuietHTTPServer(ThreadingHTTPServer):
    """客户端断连不刷日志。"""
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], CLIENT_GONE):
            return
        super().handle_error(request, client_address)
