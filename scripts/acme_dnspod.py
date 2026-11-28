# -*- coding: utf-8 -*-
"""certbot DNS-01 钩子（DNSPod / 腾讯云 API）。

用法（certbot 会注入 CERTBOT_DOMAIN / CERTBOT_VALIDATION）：
    python3 scripts/acme_dnspod.py            建 _acme-challenge TXT
    python3 scripts/acme_dnspod.py --cleanup  删掉刚建的 TXT

凭据从 /etc/letsencrypt/dnspod.env 读（SID / SKEY，权限 600），也可以用环境变量
LEXIFY_DNSPOD_ENV 指向别的文件。为什么用 DNS-01：域名没备案时腾讯云会把 80/443 的
HTTP 请求重定向到未备案提示页，HTTP-01 校验拿不到 challenge，只能走 DNS。

几个踩过的坑都写在这里了：
- 同名记录已存在时 CreateRecord 会报 InvalidParameter.DomainRecordExist，此时要先把
  旧的 _acme-challenge TXT 清掉再建（同一个子域名不能有两份互相矛盾的校验值）；
- cleanup 只按 RecordId 删；如果 auth 阶段中途失败没留下 id，就按「值相同」兜底删；
- 公共 DNS 有缓存，轮询一会儿就够，别死等（Let's Encrypt 自己会查权威 NS）。
"""
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.request

HOST = "dnspod.tencentcloudapi.com"
SERVICE = "dnspod"
VERSION = "2021-03-23"
ENV_FILE = os.environ.get("LEXIFY_DNSPOD_ENV", "/etc/letsencrypt/dnspod.env")
STATE_DIR = os.environ.get("LEXIFY_DNSPOD_STATE", "/var/lib/acme-dnspod")


def load_keys():
    sid = os.environ.get("TC_SID", "")
    skey = os.environ.get("TC_SKEY", "")
    if sid and skey:
        return sid, skey
    with open(ENV_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == "SID":
                sid = v.strip().strip('"').strip("'")
            elif k.strip() == "SKEY":
                skey = v.strip().strip('"').strip("'")
    if not sid or not skey:
        raise SystemExit("缺少 DNSPod 凭据：%s 里要有 SID / SKEY" % ENV_FILE)
    return sid, skey


def call(payload, action, sid, skey, allow_error=False):
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    ts = int(time.time())
    date = time.strftime("%Y-%m-%d", time.gmtime(ts))
    ct = "application/json; charset=utf-8"
    canonical = "\n".join([
        "POST", "/", "",
        "content-type:%s\nhost:%s\n" % (ct, HOST),
        "content-type;host",
        hashlib.sha256(body.encode()).hexdigest(),
    ])
    scope = "%s/%s/tc3_request" % (date, SERVICE)
    to_sign = "\n".join([
        "TC3-HMAC-SHA256", str(ts), scope,
        hashlib.sha256(canonical.encode()).hexdigest(),
    ])
    kd = hmac.new(("TC3" + skey).encode(), date.encode(), hashlib.sha256).digest()
    ks = hmac.new(kd, SERVICE.encode(), hashlib.sha256).digest()
    ksign = hmac.new(ks, b"tc3_request", hashlib.sha256).digest()
    sig = hmac.new(ksign, to_sign.encode(), hashlib.sha256).hexdigest()
    headers = {
        "Authorization": "TC3-HMAC-SHA256 Credential=%s/%s, SignedHeaders=content-type;host, Signature=%s"
                         % (sid, scope, sig),
        "Content-Type": ct, "Host": HOST,
        "X-TC-Action": action, "X-TC-Version": VERSION, "X-TC-Timestamp": str(ts),
    }
    req = urllib.request.Request("https://%s/" % HOST, data=body.encode(),
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        out = json.loads(resp.read().decode())
    r = out.get("Response", {})
    if "Error" in r and not allow_error:
        raise SystemExit("%s 失败：%s %s" % (action, r["Error"].get("Code"),
                                             r["Error"].get("Message")))
    return r


def zone_and_sub(domain):
    """www.lexify.cn → ("lexify.cn", "_acme-challenge.www")。"""
    parts = domain.split(".")
    zone = ".".join(parts[-2:])
    prefix = ".".join(parts[:-2])
    sub = "_acme-challenge" + ("." + prefix if prefix else "")
    return zone, sub


def state_path(domain, value):
    os.makedirs(STATE_DIR, exist_ok=True)
    return os.path.join(STATE_DIR, "%s.%s.json"
                        % (domain, hashlib.sha1(value.encode()).hexdigest()[:10]))


def list_txt(zone, sub, sid, skey):
    r = call({"Domain": zone, "Subdomain": sub, "RecordType": "TXT"},
             "DescribeRecordList", sid, skey, allow_error=True)
    if r.get("Error"):
        return []
    return [x for x in (r.get("RecordList") or []) if x.get("Name") == sub]


def wait_dns(fqdn, value, timeout=20):
    """短轮询：够 LE 刷新即可，别再死等两分钟。"""
    urls = [
        "https://dns.google/resolve?name=%s&type=TXT" % fqdn,
        "https://cloudflare-dns.com/dns-query?name=%s&type=TXT" % fqdn,
    ]
    deadline = time.time() + timeout
    while time.time() < deadline:
        for url in urls:
            try:
                req = urllib.request.Request(url, headers={"accept": "application/dns-json"})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    data = json.loads(resp.read().decode())
                for ans in data.get("Answer") or []:
                    if value in (ans.get("data") or ""):
                        return True
            except Exception:
                pass
        time.sleep(3)
    print("（顺带一提：%s 的 TXT 还没在公共 DNS 里看到，交给 LE 自己查权威 NS）" % fqdn)
    return False


def do_auth(domain, value, sid, skey):
    zone, sub = zone_and_sub(domain)
    fqdn = "%s.%s" % (sub, zone)

    rec = call({"Domain": zone, "SubDomain": sub, "RecordType": "TXT",
                "RecordLine": "默认", "Value": value, "TTL": 600},
               "CreateRecord", sid, skey, allow_error=True)

    if rec.get("Error"):
        code = rec["Error"].get("Code", "")
        if code != "InvalidParameter.DomainRecordExist":
            raise SystemExit("CreateRecord 失败：%s %s" % (code, rec["Error"].get("Message")))
        # 上一次中断留下的旧记录：先清干净再建，避免两个校验值打架
        old = list_txt(zone, sub, sid, skey)
        for x in old:
            call({"Domain": zone, "RecordId": x["RecordId"]}, "DeleteRecord", sid, skey)
        print("已清掉 %d 条旧的 %s TXT" % (len(old), fqdn))
        rec = call({"Domain": zone, "SubDomain": sub, "RecordType": "TXT",
                    "RecordLine": "默认", "Value": value, "TTL": 600},
                   "CreateRecord", sid, skey)

    rid = rec["RecordId"]
    json.dump({"id": rid, "name": fqdn, "value": value},
              open(state_path(domain, value), "w", encoding="utf-8"))
    print("已添加 TXT：%s = %s（RecordId %s）" % (fqdn, value[:12] + "…", rid))
    wait_dns(fqdn, value)


def do_cleanup(domain, value, sid, skey):
    zone, sub = zone_and_sub(domain)
    p = state_path(domain, value)
    rid = None
    if os.path.exists(p):
        rid = json.load(open(p, encoding="utf-8")).get("id")
    if rid is None:
        # auth 阶段没留 id：按值兜底删
        for x in list_txt(zone, sub, sid, skey):
            if value in (x.get("Value") or "") or value in (x.get("Value") or "").strip('"'):
                rid = x["RecordId"]
                break
    if rid is None:
        print("没有 %s 的记录可删" % domain)
        return
    call({"Domain": zone, "RecordId": rid}, "DeleteRecord", sid, skey)
    if os.path.exists(p):
        os.remove(p)
    print("已删除 %s 的 TXT（RecordId %s）" % (domain, rid))


def main(cleanup=False):
    domain = os.environ.get("CERTBOT_DOMAIN", "").strip()
    value = os.environ.get("CERTBOT_VALIDATION", "").strip()
    if not domain or not value:
        raise SystemExit("需要 CERTBOT_DOMAIN / CERTBOT_VALIDATION")
    sid, skey = load_keys()
    if cleanup:
        do_cleanup(domain, value, sid, skey)
    else:
        do_auth(domain, value, sid, skey)


if __name__ == "__main__":
    main(cleanup="--cleanup" in sys.argv)
