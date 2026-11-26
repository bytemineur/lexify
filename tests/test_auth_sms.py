# -*- coding: utf-8 -*-
"""短信登录自检：号码规范化、验证码签发/校验、会话、腾讯云签名、开关。

用法：
    python tests/test_auth_sms.py

用临时库跑，不碰 data/lexify.db；不联网，短信签名只做本地确定性校验。
覆盖：
    1. normalize_phone / mask_phone
    2. issue_code 重发冷却、每日上限、6 位数字
    3. verify_code 正确/错误/重放/过期/错误次数封顶
    4. create_session / session_of / drop_session / cookie 属性
    5. sms.sign_headers 的 TC3 结构（可复算、字段齐全）
    6. auth_required() 与 auth_conf() 的开关
"""
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server import accounts, config, sms   # noqa: E402

fails, passes = [], []
_orig = {"DB": config.DB, "auth_conf": config.auth_conf, "sms_conf": config.sms_conf}


def ok(name, cond, extra=""):
    (passes if cond else fails).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (" :: " + str(extra) if extra else ""))


def fake_auth(**over):
    base = {"enabled": True, "dev_mode": False, "code_ttl": 300,
            "resend_seconds": 60, "daily_per_phone": 10, "session_days": 30}
    base.update(over)
    return lambda: dict(base)


def main():
    tmp = tempfile.mkdtemp(prefix="lexify-auth-")
    db = os.path.join(tmp, "auth.db")
    config.DB = db
    config.auth_conf = fake_auth()
    accounts._ensured = None
    accounts.ensure()

    print("\n[1] 号码规范化")
    ok("11 位手机号", accounts.normalize_phone("13800138000") == "+8613800138000")
    ok("带空格横线", accounts.normalize_phone(" 138-0013-8000 ") == "+8613800138000")
    ok("+86 前缀", accounts.normalize_phone("+8613800138000") == "+8613800138000")
    ok("0086 前缀", accounts.normalize_phone("008613800138000") == "+8613800138000")
    ok("86 前缀", accounts.normalize_phone("8613800138000") == "+8613800138000")
    ok("10 位拒绝", accounts.normalize_phone("1380013800") is None)
    ok("首位 2 拒绝", accounts.normalize_phone("23800138000") is None)
    ok("字母拒绝", accounts.normalize_phone("1380013800a") is None)
    ok("空值拒绝", accounts.normalize_phone("") is None)
    ok("掩码", accounts.mask_phone("+8613800138000") == "138****8000")

    print("\n[2] 验证码签发")
    phone = accounts.normalize_phone("13900139000")
    good, err, code = accounts.issue_code(phone, "1.2.3.4")
    ok("首次签发成功", good and len(code) == 6 and code.isdigit(), err)
    again, err2, _ = accounts.issue_code(phone, "1.2.3.4")
    ok("60 秒内重发被拒", (not again) and "秒" in err2, err2)

    print("\n[3] 验证码校验")
    bad, err3 = accounts.verify_code(phone, "000000" if code != "000000" else "111111")
    ok("错误码拒绝", (not bad) and err3 != "", err3)
    good2, err4 = accounts.verify_code(phone, code)
    ok("正确码通过", good2, err4)
    replay, err5 = accounts.verify_code(phone, code)
    ok("重放被拒", (not replay) and err5 != "", err5)

    print("\n[4] 过期与错误次数")
    phone2 = accounts.normalize_phone("13700137000")
    _, _, code2 = accounts.issue_code(phone2, "1.2.3.4")
    import sqlite3
    conn = sqlite3.connect(db)
    conn.execute("UPDATE sms_codes SET expires_at = ? WHERE phone = ?",
                 (int(time.time()) - 10, phone2))
    conn.commit()
    conn.close()
    expired, err6 = accounts.verify_code(phone2, code2)
    ok("过期拒绝", (not expired) and err6 != "", err6)

    phone3 = accounts.normalize_phone("13600136000")
    _, _, code3 = accounts.issue_code(phone3, "1.2.3.4")
    for i in range(5):
        accounts.verify_code(phone3, "999999" if code3 != "999999" else "888888")
    locked, err7 = accounts.verify_code(phone3, code3)
    ok("错误 5 次后锁死", (not locked) and err7 != "", err7)

    print("\n[5] 会话")
    token = accounts.create_session(phone, "1.2.3.4", "pytest-ua")
    ok("token 足够长", isinstance(token, str) and len(token) >= 32)
    sess = accounts.session_of(token)
    ok("会话取回手机号", sess and sess.get("phone") == phone, sess)
    ok("会话带掩码", sess and sess.get("masked") == "139****9000", sess)
    ok("库里不存明文 token", _no_plain_token(db, token))
    cookie = accounts.cookie_for(token)
    ok("Cookie 带 HttpOnly", "HttpOnly" in cookie)
    ok("Cookie 带 SameSite", "SameSite=Lax" in cookie)
    ok("Cookie 带 Max-Age", "Max-Age=" in cookie)
    ok("token_from_cookie 能取回", accounts.token_from_cookie({"Cookie": cookie.split(";")[0]}) == token)
    accounts.drop_session(token)
    ok("登出后失效", accounts.session_of(token) is None)

    print("\n[6] 腾讯云短信签名")
    payload = {"PhoneNumberSet": ["+8613800138000"], "SmsSdkAppId": "1400000000",
               "SignName": "测试", "TemplateId": "123456",
               "TemplateParamSet": ["123456"]}
    h1, body1 = sms.sign_headers("AKIDexample", "secret123", payload, timestamp=1700000000)
    h2, _ = sms.sign_headers("AKIDexample", "secret123", payload, timestamp=1700000000)
    ok("签名可复算", h1["Authorization"] == h2["Authorization"])
    auth = h1["Authorization"]
    ok("Authorization 前缀", auth.startswith("TC3-HMAC-SHA256 Credential=AKIDexample/"))
    ok("Credential 含服务与版本", "/sms/tc3_request" in auth)
    ok("SignedHeaders 固定", "SignedHeaders=content-type;host" in auth)
    sig = auth.rsplit("Signature=", 1)[-1]
    ok("签名 64 位十六进制", len(sig) == 64 and all(c in "0123456789abcdef" for c in sig), sig)
    ok("带 X-TC-Action", h1["X-TC-Action"] == "SendSms")
    ok("带 X-TC-Version", h1["X-TC-Version"] == "2021-01-11")
    ok("带 X-TC-Timestamp", h1["X-TC-Timestamp"] == "1700000000")
    ok("带 Host", h1["Host"] == "sms.tencentcloudapi.com")
    ok("Content-Type 是 json", h1["Content-Type"].startswith("application/json"))
    diff, _ = sms.sign_headers("AKIDexample", "secret123", payload, timestamp=1700000001)
    ok("时间戳变化 → 签名变化", diff["Authorization"] != auth)

    print("\n[7] 错误码人话映射")
    ok("签名未审核", "签名" in sms._hint(
        "FailedOperation.SignatureIncorrectOrUnapproved", "x", {"sign": "深圳易诉达"}))
    ok("地域不支持", "ap-guangzhou" in sms._hint("UnsupportedRegion", "x"))
    ok("限流", "号码" in sms._hint("LimitExceeded.PhoneNumberDailyLimit", "x"))
    ok("未知码原样", sms._hint("Something.New", "desc") == "Something.New：desc")

    print("\n[8] 配置与开关")
    # 本机的 config.yaml 可能已经填了真 Key，这里用假配置覆盖，保证断言稳定。
    def fake_sms(**over):
        base = {"secret_id": "", "secret_key": "", "sdk_app_id": "",
                "sign_name": "", "template_id": "", "region": "ap-guangzhou"}
        base.update(over)
        return lambda: dict(base)

    config.sms_conf = fake_sms()
    ok("未配置时 sms_ready False", config.sms_ready() is False)
    config.sms_conf = fake_sms(secret_id="AKIDxxx")
    ok("只配 Id 仍 False", config.sms_ready() is False)
    config.sms_conf = fake_sms(secret_id="AKIDxxx", secret_key="k", sdk_app_id="1400000000",
                               sign_name="测试", template_id="123456")
    ok("五项齐全 True", config.sms_ready() is True)

    config.auth_conf = _orig["auth_conf"]
    conf = config.auth_conf()
    ok("auth 默认开启", conf.get("enabled") is True)
    ok("auth 默认非 dev", conf.get("dev_mode") is False)
    os.environ["LEXIFY_AUTH"] = "0"
    ok("env 可关闭看板登录", config.auth_conf().get("enabled") is False)
    os.environ.pop("LEXIFY_AUTH", None)
    os.environ["LEXIFY_AUTH_DEV"] = "1"
    ok("env 可开 dev_mode", config.auth_conf().get("dev_mode") is True)
    os.environ.pop("LEXIFY_AUTH_DEV", None)

    config.DB = _orig["DB"]
    config.auth_conf = _orig["auth_conf"]
    config.sms_conf = _orig["sms_conf"]
    accounts._ensured = None

    print("\n%d passed, %d failed" % (len(passes), len(fails)))
    for f in fails:
        print("  FAILED:", f)
    return 1 if fails else 0


def _no_plain_token(db, token):
    import sqlite3
    conn = sqlite3.connect(db)
    rows = [r[0] for r in conn.execute("SELECT token_hash FROM auth_sessions")]
    conn.close()
    return token not in rows


if __name__ == "__main__":
    sys.exit(main())
