# -*- coding: utf-8 -*-
"""路径、常量、口令。纯数据/纯函数，不 import 其它 server 模块。"""
import os
import secrets
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")
UPLOAD_DIR = os.path.join(ROOT, "data", "uploads")
DB = os.environ.get("LEXIFY_DB") or os.path.join(ROOT, "data", "lexify.db")

# scripts/ 里放同一套工具（manage / build_data / publish_data / compat）。
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

# 客户端提前走的三种例外：关标签页、点停止、网络断。不该打堆栈。
CLIENT_GONE = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)

DB_FIX_HINT = (
    "  库是 SQLite，%s 必须是可写的（要写 WAL 和临时文件）。\n"
    "  最常见原因：之前用 sudo 跑过一次，data/ 里的文件属主变成了 root。\n"
    "  修一下（把 USER 换成你的登录用户）：\n"
    "    sudo chown -R USER:USER %s\n"
    "  然后重启进程。"
)


def load_admin_token():
    """确定后台口令：环境变量 > data/admin_token.txt > 首次自动生成。返回 (token, 来源说明)。"""
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
            os.chmod(TOKEN_FILE, 0o600)
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
