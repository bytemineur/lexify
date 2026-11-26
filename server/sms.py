# -*- coding: utf-8 -*-
"""腾讯云短信（SMS 2021-01-11 SendSms）的纯标准库客户端。

只做一件事：把一条验证码发到一个手机号，返回 (ok, request_id, message)。
签名算法是 TC3-HMAC-SHA256，和官方 SDK 等价，但不需要装 tencentcloud-sdk-python。

文档：https://cloud.tencent.com/document/product/382/55981
"""
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

HOST = "sms.tencentcloudapi.com"
SERVICE = "sms"
VERSION = "2021-01-11"
ACTION = "SendSms"

# 腾讯云按模板变量顺序填参数，这里只发一个验证码，所以固定一个占位。
DEFAULT_REGION = "ap-guangzhou"


def _hmac_sha256(key: bytes, msg: bytes) -> bytes:
    return hmac.new(key, msg, hashlib.sha256).digest()


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sign_headers(secret_id, secret_key, payload, timestamp=None, region=DEFAULT_REGION):
    """构造 TC3-HMAC-SHA256 请求头。抽出来便于单测（不联网也能验签名）。"""
    ts = int(timestamp if timestamp is not None else time.time())
    date = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")

    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    content_type = "application/json; charset=utf-8"

    canonical_headers = "content-type:%s\nhost:%s\n" % (content_type, HOST)
    signed_headers = "content-type;host"
    canonical_request = "\n".join([
        "POST", "/", "",
        canonical_headers,
        signed_headers,
        _sha256_hex(body),
    ])

    credential_scope = "%s/%s/tc3_request" % (date, SERVICE)
    string_to_sign = "\n".join([
        "TC3-HMAC-SHA256",
        str(ts),
        credential_scope,
        _sha256_hex(canonical_request.encode("utf-8")),
    ])

    secret_date = _hmac_sha256(("TC3" + secret_key).encode("utf-8"), date.encode("utf-8"))
    secret_service = _hmac_sha256(secret_date, SERVICE.encode("utf-8"))
    secret_signing = _hmac_sha256(secret_service, b"tc3_request")
    signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"),
                         hashlib.sha256).hexdigest()

    authorization = ("TC3-HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, Signature=%s"
                     % (secret_id, credential_scope, signed_headers, signature))

    headers = {
        "Authorization": authorization,
        "Content-Type": content_type,
        "Host": HOST,
        "X-TC-Action": ACTION,
        "X-TC-Version": VERSION,
        "X-TC-Timestamp": str(ts),
        "X-TC-Region": region or DEFAULT_REGION,
    }
    return headers, body


def send_code(secret_id, secret_key, sdk_app_id, sign_name, template_id, phone, code,
              region=DEFAULT_REGION, session_context="", timeout=10):
    """发一条验证码短信。phone 传 E.164（+8613800138000）。

    返回 (ok: bool, request_id: str, message: str)。网络/签名问题都会落到 message 里。
    """
    payload = {
        "PhoneNumberSet": [phone],
        "SmsSdkAppId": str(sdk_app_id),
        "SignName": str(sign_name),
        "TemplateId": str(template_id),
        "TemplateParamSet": [str(code)],
        "SessionContext": session_context or "",
        "ExtendCode": "",
        "SenderId": "",
    }
    headers, body = sign_headers(secret_id, secret_key, payload, region=region)
    req = urllib.request.Request("https://%s/" % HOST, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        return False, "", "HTTP %s：%s" % (e.code, detail)
    except Exception as e:  # noqa: BLE001 —— 网络层什么都可能抛，统一转成人话
        return False, "", "请求失败：%s" % (e,)

    r = (data or {}).get("Response") or {}
    if r.get("Error"):
        err = r["Error"]
        return False, r.get("RequestId", ""), _hint(
            err.get("Code"), err.get("Message"), {"sign": sign_name})
    statuses = r.get("SendStatusSet") or []
    if statuses:
        st = statuses[0]
        if st.get("Code") != "Ok":
            return False, r.get("RequestId", ""), _hint(
                st.get("Code"), st.get("Message"), {"sign": sign_name})
    return True, r.get("RequestId", ""), "ok"


# 腾讯云返回的错误码，翻成页面上能看懂的话。
_ERR_HINTS = {
    "FailedOperation.SignatureIncorrectOrUnapproved":
        "短信签名还没审核通过（当前用的是「%(sign)s」），等审核通过后再试",
    "FailedOperation.TemplateIncorrectOrUnapproved":
        "短信模板还没审核通过，等模板审核通过后再试",
    "FailedOperation.PhoneNumberInBlacklist": "这个号码在腾讯云的退订黑名单里",
    "UnsupportedRegion": "腾讯云短信不支持这个 Region，用 ap-guangzhou",
    "AuthFailure.SecretIdNotFound": "SecretId 不对或已删除",
    "AuthFailure.SignatureFailure": "SecretKey 不对（签名验不过）",
    "AuthFailure.SignatureExpire": "本机时间和腾讯云差太多，校对一下系统时间",
    "LimitExceeded.PhoneNumberDailyLimit": "这个号码今天的验证码条数用完了",
    "LimitExceeded.PhoneNumberThirtySecondLimit": "同一个号码 30 秒内只能发一条",
}


def _hint(code, message, ctx=None):
    """错误码 → 人话；没有对应条目就原样返回「码：描述」。"""
    text = _ERR_HINTS.get(str(code or ""))
    if text:
        return text % (ctx or {"sign": ""})
    return "%s：%s" % (code or "Error", message or "")
