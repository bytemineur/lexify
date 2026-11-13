# -*- coding: utf-8 -*-
"""把重新导出的看板数据推到 GitHub（不需要装 git）。

背景：这个仓库是纯静态站点，数据由 scripts/build_data.py 从两个同级项目的
SQLite 库里导出成 web/data/*.js。本脚本负责最后一段：判断数据是否真的变了，
变了才提交，避免每天产生一个「只有时间戳」的空提交。

用法：
    python scripts/publish_data.py                 # 只推送当前 web/data/*.js
    python scripts/publish_data.py --build         # 先跑 build_data.py 再推送
    python scripts/publish_data.py --build --dry-run

凭据（按顺序找）：
    --token=xxx  →  环境变量 GITHUB_TOKEN  →  ~/gh-token.txt

为什么比对时要抹掉 generatedAt：
    build_data.py 每次都会写入当前时间，逐字节比对必然「有变化」，
    于是每天都会多一个实质为空的历史提交。这里把该字段清空后再比。
"""
import argparse
import base64
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OWNER, REPO, BRANCH = "bytemineur", "lexify", "main"
DATA_FILES = ["overview.js", "tender.js", "hearing.js", "bounty.js"]
SUMMARY_FILE = "web/data/overview.js"

_TS = re.compile(r'"generatedAt":"[^"]*"')


def strip_ts(text):
    """去掉每次构建都会变的时间戳，只比较真实内容。"""
    return _TS.sub('"generatedAt":""', text)


def _open(req, timeout):
    import time
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise SystemExit("✗ %s %s -> %s %s" % (req.get_method(), req.full_url, e.code, detail))
        except Exception as e:           # IncompleteRead / 连接被重置 / 超时
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise SystemExit("✗ 请求失败（重试 3 次）：%s %s" % (req.full_url, last))


def api(method, path, token, body=None, timeout=120):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request("https://api.github.com" + path, data=data, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "lexify-publish")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    if data:
        req.add_header("Content-Type", "application/json")
    raw = _open(req, timeout)
    return json.loads(raw.decode("utf-8"))


def blob_text(sha, token):
    """拿远端 blob 的原始文本：用 raw 传输（比 base64 小 1/3），并带重试。"""
    req = urllib.request.Request("https://api.github.com/repos/%s/%s/git/blobs/%s" % (OWNER, REPO, sha))
    req.add_header("Accept", "application/vnd.github.raw")
    req.add_header("User-Agent", "lexify-publish")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    return _open(req, 180).decode("utf-8")


def find_token(explicit):
    if explicit:
        return explicit
    if os.environ.get("GITHUB_TOKEN"):
        return os.environ["GITHUB_TOKEN"]
    p = os.path.join(os.path.expanduser("~"), "gh-token.txt")
    if os.path.exists(p):
        return open(p, encoding="utf-8").read().strip()
    return ""


def run_build():
    script = os.path.join(ROOT, "scripts", "build_data.py")
    print("→ 重新导出数据：python scripts/build_data.py")
    p = subprocess.run([sys.executable, script], cwd=ROOT)
    if p.returncode != 0:
        raise SystemExit("✗ 数据导出失败（源库没更新或路径不对），已中止，不会推送旧数据。")


def summary_line():
    path = os.path.join(ROOT, SUMMARY_FILE)
    if not os.path.exists(path):
        return ""
    m = re.search(r'"total":(\d+)', open(path, encoding="utf-8").read())
    return ("当前共 %s 条结构化公告。" % m.group(1)) if m else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true", help="先跑 build_data.py")
    ap.add_argument("--dry-run", action="store_true", help="只看有没有变化，不提交")
    ap.add_argument("--token", default="", help="GitHub token（也可用环境变量或 ~/gh-token.txt）")
    ap.add_argument("--message", default="", help="自定义 commit message")
    args = ap.parse_args()

    if args.build:
        run_build()

    token = find_token(args.token)
    head = api("GET", "/repos/%s/%s/branches/%s" % (OWNER, REPO, BRANCH), token)
    head_sha = head["commit"]["sha"]
    head_tree = head["commit"]["commit"]["tree"]["sha"]
    tree = api("GET", "/repos/%s/%s/git/trees/%s?recursive=1" % (OWNER, REPO, head_sha), token)
    remote = {t["path"]: t["sha"] for t in tree["tree"] if t["type"] == "blob"}

    changed = []
    for name in DATA_FILES:
        rel = "web/data/" + name
        local_path = os.path.join(ROOT, "web", "data", name)
        if not os.path.exists(local_path):
            continue
        local_text = open(local_path, encoding="utf-8").read()

        remote_sha = remote.get(rel)
        same = False
        if remote_sha:
            remote_text = blob_text(remote_sha, token)
            same = strip_ts(local_text) == strip_ts(remote_text)

        print(("   = %s" if same else "   * %s") % rel)
        if not same:
            changed.append(rel)

    if not changed:
        print("\n数据没有实质变化（只有时间戳），不提交。")
        return

    if args.dry_run:
        print("\n[dry-run] 有 %d 个文件会更新，未提交。" % len(changed))
        return
    if not token:
        raise SystemExit("✗ 没有 token：设置 GITHUB_TOKEN，或写入 ~/gh-token.txt")

    entries = []
    for rel in changed:
        buf = open(os.path.join(ROOT, rel), "rb").read()
        blob = api("POST", "/repos/%s/%s/git/blobs" % (OWNER, REPO),
                   token, {"content": base64.b64encode(buf).decode(), "encoding": "base64"})
        entries.append({"path": rel, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        print("   blob ✓ " + rel)

    new_tree = api("POST", "/repos/%s/%s/git/trees" % (OWNER, REPO), token,
                   {"base_tree": head_tree, "tree": entries})

    msg = args.message or ("data: 更新看板数据 %s\n\n%s\n来自 scripts/publish_data.py" %
                           (datetime.now().strftime("%Y-%m-%d %H:%M"), summary_line()).strip())
    commit = api("POST", "/repos/%s/%s/git/commits" % (OWNER, REPO), token,
                 {"message": msg, "tree": new_tree["sha"], "parents": [head_sha]})
    api("PATCH", "/repos/%s/%s/git/refs/heads/%s" % (OWNER, REPO, BRANCH), token,
        {"sha": commit["sha"], "force": False})

    print("\n✓ 已推送 %s" % commit["sha"])
    print("https://github.com/%s/%s/commit/%s" % (OWNER, REPO, commit["sha"]))


if __name__ == "__main__":
    main()
