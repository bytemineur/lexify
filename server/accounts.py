# -*- coding: utf-8 -*-
"""短信验证码与登录会话。

两张表：
  sms_codes     —— 每次发送的验证码（只存哈希），带过期、尝试次数、是否已用
  auth_sessions —— 登录后的会话（只存 token 哈希），带过期与最后活跃时间

所有时间都用整数秒（time.time()），不依赖 SQLite 的时间函数，方便跨时区。
校验失败一律返回「验证码不正确或已过期」，不区分是过期、用过了还是位数不对。
"""
import hashlib
import re
import secrets
import sqlite3
import time

from . import config

COOKIE = "lexify_login"

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS sms_codes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    phone       TEXT NOT NULL,
    code_hash   TEXT NOT NULL,
    ip_hash     TEXT,
    created_at  INTEGER NOT NULL,
    expires_at  INTEGER NOT NULL,
    attempts    INTEGER DEFAULT 0,
    used        INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sc_phone ON sms_codes(phone, created_at);

CREATE TABLE IF NOT EXISTS auth_sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash  TEXT NOT NULL UNIQUE,
    phone       TEXT NOT NULL,
    created_at  INTEGER NOT NULL,
    expires_at  INTEGER NOT NULL,
    last_seen   INTEGER,
    ip_hash     TEXT,
    ua          TEXT
);
CREATE INDEX IF NOT EXISTS idx_as_phone ON auth_sessions(phone);
"""

_ensured = ""

_PHONE_RE = re.compile(r"^\+?86?(1[3-9]\d{9})$")


def _db():
    return sqlite3.connect(config.DB, timeout=10)


def ensure():
    """幂等建表；每个进程每个库只跑一次。库不可写时静默返回 False。"""
    global _ensured
    if _ensured == config.DB:
        return True
    try:
        conn = _db()
        try:
            conn.executescript(TABLE_SQL)
            conn.commit()
        finally:
            conn.close()
        _ensured = config.DB
        return True
    except sqlite3.Error:
        return False


def normalize_phone(raw):
    """把 13800138000 / +8613800138000 / 86-138-0013-8000 统一成 +8613800138000。"""
    s = re.sub(r"[\s\-()]", "", str(raw or ""))
    if not s:
        return None
    if s.startswith("+"):
        s = s[1:]
    if s.startswith("0086"):
        s = s[4:]
    elif s.startswith("86") and len(s) == 13:
        s = s[2:]
    if not re.fullmatch(r"1[3-9]\d{9}", s):
        return None
    return "+86" + s


def mask_phone(phone):
    """+8613800138000 → 138****8000。"""
    s = str(phone or "")
    if s.startswith("+86"):
        s = s[3:]
    if len(s) != 11:
        return s
    return s[:3] + "****" + s[-4:]


def ip_hash(ip):
    # 盐必须跨进程稳定：只认 config 里的 analytics.hash_salt，没有就用固定串。
    # 早期版本把 ADMIN_TOKEN 当兜底，但它在进程启动前后会变，导致会话查不到。
    salt = str(config.conf("analytics.hash_salt") or "") or "lexify-auth-v1"
    return hashlib.sha256(("%s|%s" % (salt, ip or "")).encode("utf-8")).hexdigest()[:16]


def _code_hash(phone, code):
    salt = str(config.conf("analytics.hash_salt") or "") or config.ADMIN_TOKEN or "lexify-auth"
    return hashlib.sha256(("%s|%s|%s" % (salt, phone, code)).encode("utf-8")).hexdigest()


def issue_code(phone, ip=""):
    """生成并记录一条验证码。返回 (ok, err, code)；code 为空表示不告诉调用方。"""
    if not ensure():
        return False, "数据库不可写，稍后再试", ""
    conf = config.auth_conf()
    now = int(time.time())
    conn = _db()
    try:
        row = conn.execute(
            "SELECT created_at FROM sms_codes WHERE phone=? ORDER BY id DESC LIMIT 1",
            (phone,)).fetchone()
        if row and now - int(row[0]) < conf["resend_seconds"]:
            wait = conf["resend_seconds"] - (now - int(row[0]))
            return False, "请 %d 秒后再试" % max(1, wait), ""
        today = conn.execute(
            "SELECT COUNT(*) FROM sms_codes WHERE phone=? AND created_at>?",
            (phone, now - 86400)).fetchone()[0]
        if today >= conf["daily_per_phone"]:
            return False, "今天这个号码收到的验证码太多了，明天再试", ""

        code = "%06d" % secrets.randbelow(1000000)
        conn.execute(
            "INSERT INTO sms_codes(phone, code_hash, ip_hash, created_at, expires_at)"
            " VALUES (?,?,?,?,?)",
            (phone, _code_hash(phone, code), ip_hash(ip), now, now + conf["code_ttl"]))
        conn.execute("DELETE FROM sms_codes WHERE expires_at < ?", (now - 86400,))
        conn.commit()
        return True, "", code
    except sqlite3.Error as e:
        return False, "数据库错误：%s" % e, ""
    finally:
        conn.close()


def verify_code(phone, code):
    """校验验证码，成功即作废。返回 (ok, err)。"""
    code = re.sub(r"\D", "", str(code or ""))
    if len(code) != 6:
        return False, "验证码是 6 位数字"
    if not ensure():
        return False, "数据库不可写，稍后再试"
    now = int(time.time())
    conn = _db()
    try:
        row = conn.execute(
            "SELECT id, code_hash, expires_at, attempts, used FROM sms_codes"
            " WHERE phone=? ORDER BY id DESC LIMIT 1", (phone,)).fetchone()
        if not row:
            return False, "还没有给这个号码发过验证码"
        cid, chash, expires_at, attempts, used = row
        if used:
            return False, "这条验证码已经用过了，重新获取"
        if int(expires_at) < now:
            return False, "验证码已过期，重新获取"
        if int(attempts) >= 5:
            return False, "尝试次数过多，重新获取"
        if not secrets.compare_digest(chash, _code_hash(phone, code)):
            conn.execute("UPDATE sms_codes SET attempts=attempts+1 WHERE id=?", (cid,))
            conn.commit()
            return False, "验证码不正确"
        conn.execute("UPDATE sms_codes SET used=1 WHERE id=?", (cid,))
        conn.commit()
        return True, ""
    except sqlite3.Error as e:
        return False, "数据库错误：%s" % e
    finally:
        conn.close()


def create_session(phone, ip="", ua=""):
    """登录成功后发一个新 token（明文只回给浏览器，库里存哈希）。"""
    if not ensure():
        return ""
    conf = config.auth_conf()
    now = int(time.time())
    token = secrets.token_urlsafe(32)
    conn = _db()
    try:
        conn.execute(
            "INSERT INTO auth_sessions(token_hash, phone, created_at, expires_at, last_seen,"
            " ip_hash, ua) VALUES (?,?,?,?,?,?,?)",
            (_code_hash("session", token), phone, now, now + conf["session_days"] * 86400,
             now, ip_hash(ip), (ua or "")[:200]))
        conn.execute("DELETE FROM auth_sessions WHERE expires_at < ?", (now,))
        conn.commit()
        return token
    except sqlite3.Error:
        return ""
    finally:
        conn.close()


def session_of(token):
    """按 token 查会话；有效就顺手更新 last_seen。返回 dict 或 None。"""
    if not token:
        return None
    if not ensure():
        return None
    now = int(time.time())
    conn = _db()
    try:
        row = conn.execute(
            "SELECT id, phone, expires_at FROM auth_sessions WHERE token_hash=?",
            (_code_hash("session", token),)).fetchone()
        if not row:
            return None
        sid, phone, expires_at = row
        if int(expires_at) < now:
            conn.execute("DELETE FROM auth_sessions WHERE id=?", (sid,))
            conn.commit()
            return None
        conn.execute("UPDATE auth_sessions SET last_seen=? WHERE id=?", (now, sid))
        conn.commit()
        return {"phone": phone, "masked": mask_phone(phone), "expires_at": int(expires_at)}
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def drop_session(token):
    if not token:
        return
    if not ensure():
        return
    conn = _db()
    try:
        conn.execute("DELETE FROM auth_sessions WHERE token_hash=?",
                     (_code_hash("session", token),))
        conn.commit()
    except sqlite3.Error:
        pass
    finally:
        conn.close()


def cookie_for(token, https=False):
    """登录 Cookie：HttpOnly + SameSite=Lax，HTTPS 下再加 Secure。"""
    conf = config.auth_conf()
    return "%s=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=%d%s" % (
        COOKIE, token, conf["session_days"] * 86400, "; Secure" if https else "")


def clear_cookie():
    return "%s=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax" % COOKIE


def token_from_cookie(headers):
    m = re.search(r"(?:^|;\s*)%s=([^;]+)" % COOKIE, headers.get("Cookie") or "")
    return m.group(1) if m else ""
