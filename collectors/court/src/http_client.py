# -*- coding: utf-8 -*-
"""极简 HTTP 客户端：限速 + 重试 + 统一 UA + TLS 降级。仅标准库。

部分法院站点使用了非标准 EC 曲线，OpenSSL 3.x 默认会以 BAD_ECPOINT 拒绝握手。
此时自动降级到宽松 TLS 上下文（SECLEVEL=0 + 不校验证书）重试一次。
仅用于抓取公开公告，不涉及任何凭据传输。
"""
import gzip
import io
import random
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

import config


def _ssl_context(relaxed):
    ctx = ssl.create_default_context()
    if not relaxed:
        return ctx
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    for fn in (lambda: ctx.set_ciphers("ALL:@SECLEVEL=0"),
               lambda: ctx.set_ecdh_curve("prime256v1")):
        try:
            fn()
        except Exception:
            pass
    return ctx


class Http:
    def __init__(self, interval=None, timeout=None, max_retry=None):
        self.interval = config.REQUEST_INTERVAL if interval is None else interval
        self.timeout = config.REQUEST_TIMEOUT if timeout is None else timeout
        self.max_retry = config.MAX_RETRY if max_retry is None else max_retry
        self._last = 0.0
        self.count = 0
        self.hosts_ok = set()      # 已知需要宽松 TLS 的主机
        self.blocked = {}          # 已确认被 IP 限流的主机
        self._opener_cache = {}

    # ---------- 内部 ----------
    def _opener(self, relaxed):
        if relaxed not in self._opener_cache:
            handler = urllib.request.HTTPSHandler(context=_ssl_context(relaxed))
            self._opener_cache[relaxed] = urllib.request.build_opener(handler)
        return self._opener_cache[relaxed]

    def _throttle(self):
        gap = self.interval - (time.time() - self._last)
        if gap > 0:
            time.sleep(gap + random.uniform(0, 0.25))
        self._last = time.time()
        self.count += 1

    def _decode(self, resp):
        raw = resp.read()
        enc = resp.headers.get("Content-Encoding", "")
        if "gzip" in enc:
            try:
                raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
            except OSError:
                pass
        elif "deflate" in enc:
            try:
                raw = zlib.decompress(raw, -zlib.MAX_WBITS)
            except zlib.error:
                pass
        charset = resp.headers.get_content_charset() or "utf-8"
        if charset.lower() in ("gb2312", "gbk", "gb18030"):
            charset = "gb18030"
        try:
            return raw.decode(charset, "ignore")
        except LookupError:
            return raw.decode("utf-8", "ignore")

    def _is_ssl_err(self, e):
        if isinstance(e, ssl.SSLError):
            return True
        reason = getattr(e, "reason", None)
        return isinstance(reason, ssl.SSLError)

    # ---------- 对外 ----------
    def request(self, url, data=None, headers=None, method=None):
        body = None
        hdrs = {
            "User-Agent": config.USER_AGENT,
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        }
        if headers:
            hdrs.update(headers)
        if data is not None:
            body = urllib.parse.urlencode(data).encode("utf-8")
            hdrs.setdefault("Content-Type",
                            "application/x-www-form-urlencoded; charset=UTF-8")

        host = urllib.parse.urlparse(url).netloc
        last_err = None
        for attempt in range(1, self.max_retry + 1):
            self._throttle()
            relaxed = host in self.hosts_ok
            req = urllib.request.Request(url, data=body, headers=hdrs,
                                         method=method or ("POST" if body else "GET"))
            try:
                with self._opener(relaxed).open(req, timeout=self.timeout) as resp:
                    return self._decode(resp)
            except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
                last_err = e
                # 站点用了非常规 TLS 曲线：记住该主机，改用宽松上下文重试
                if not relaxed and self._is_ssl_err(e):
                    self.hosts_ok.add(host)
                    continue
                code = getattr(e, "code", None)
                if code == 403:
                    # 冷却一次仍 403，说明是 IP 级限流，再等也没用，直接放弃，
                    # 免得每个关键词都白等 35 秒把整轮采集拖成几十分钟
                    if self.blocked.get(host):
                        raise RuntimeError(f"403 限流（{host}），本轮跳过") from e
                    self.blocked[host] = True
                    if attempt <= 2:
                        print(f"    ! 403 限流，冷却 35s 后重试 ({attempt}/2)")
                        time.sleep(35)
                        continue
                    break
                if code and 400 <= code < 500 and code != 429:
                    break
                sleep = min(2 ** attempt, 15)
                print(f"    ! 请求失败({e})，{sleep}s 后重试 {attempt}/{self.max_retry}")
                time.sleep(sleep)
        raise RuntimeError(f"请求失败: {url} -> {last_err}")

    def get(self, url, headers=None):
        return self.request(url, headers=headers)
