# -*- coding: utf-8 -*-
"""main()：参数解析 + 启动装配。

只做「把命令行/环境变量翻译成一份运行配置，再把几颗独立组件组装起来」，
不包含任何协议/业务实现（那些在 web/auth/admin/docs/jobs/db 各模块里）。
"""
import argparse
import os
import threading

from . import jobs as jobsmod
from .config import (ADMIN_COOKIE, ADMIN_OPEN, ADMIN_TOKEN, DB, DB_FIX_HINT,
                     PY, compat_describe, load_admin_token, setup_console)
from .db import db_is_empty, db_writable
from .web import Handler, QuietHTTPServer


def main():
    setup_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("LEXIFY_PORT", "8010")))
    ap.add_argument("--bind", default=os.environ.get("LEXIFY_BIND", "0.0.0.0"))
    ap.add_argument("--at", default=os.environ.get("LEXIFY_UPDATE_AT", "00:30"), help="每天几点更新 HH:MM")
    ap.add_argument("--no-schedule", action="store_true", help="不跑定时任务")
    ap.add_argument("--admin-only", action="store_true", help="只开后台（本地维护用）")
    ap.add_argument("--admin-token", default="", help="后台口令（同 LEXIFY_ADMIN_TOKEN）")
    ap.add_argument("--admin-open", action="store_true", help="不设防，允许任何人进后台（仅限内网）")
    ap.add_argument("--run-now", action="store_true", help="启动时先更新一次")
    ap.add_argument("--no-auto-crawl", action="store_true", help="库里没数据时不要自动采集")
    args = ap.parse_args()

    import server.config as cfg
    if args.admin_token:
        cfg.ADMIN_TOKEN = args.admin_token
    if args.admin_open:
        cfg.ADMIN_OPEN = True
    token, token_src = load_admin_token()

    writable, why = db_writable()
    if not writable:
        print("⚠ 数据目录不可写：%s" % why)
        print(DB_FIX_HINT % (os.path.dirname(os.path.abspath(DB)),
                             os.path.dirname(os.path.abspath(DB))))
        print("  站点照常可以浏览（页面读的是 web/data/*.js 快照）；采集/上传/重建会失败。")

    if not os.path.exists(DB):
        print("库里还没有数据，先建一个空的：%s" % DB)
        jobsmod.sh(["scripts/manage.py", "init"])
    elif writable:
        try:
            import manage
            manage.ensure_schema(quiet=True)
        except Exception as e:
            print("⚠ schema 检查失败（不影响启动）：%s" % e)

    # 首次启动：三块合起来一条数据都没有 → 后台自动跑一次采集
    if db_is_empty():
        if not writable:
            print("库是空的，但库不可写 → 自动采集已跳过，先按上面的提示修权限")
        elif args.no_auto_crawl:
            print("库是空的（--no-auto-crawl 已关闭自动采集）→ 去 /admin 点「采集全部并更新」")
        else:
            print("库是空的 → 后台自动执行第一次采集，进度看 /admin")
            jobsmod.start_job("首次采集", ["scripts/manage.py", "crawl"])
    elif not writable:
        pass
    elif args.run_now:
        threading.Thread(target=jobsmod.run_update, daemon=True).start()
    elif not args.no_schedule and jobsmod.catch_up_needed(args.at):
        print("今天 %s 的更新还没跑 → 启动即补一次" % args.at)
        threading.Thread(target=jobsmod.run_update, daemon=True).start()

    if args.admin_only:
        args.bind = "127.0.0.1"

    if not args.no_schedule and writable:
        jobsmod.start_scheduler(args.at)

    srv = QuietHTTPServer((args.bind, args.port), Handler)
    host = "127.0.0.1" if args.bind == "0.0.0.0" else args.bind
    base = "http://%s:%d" % (host, args.port)
    print("站点：  %s/" % base)
    print("后台：  %s/admin" % base)
    print("定时：  %s" % ("关闭" if args.no_schedule else "每天 " + args.at))
    print("统一库：%s" % DB)
    print("运行于：%s（不依赖 shell，cmd / PowerShell / bash 都一样）" % compat_describe())
    if cfg.ADMIN_OPEN:
        print("⚠ 后台已设为不设防（--admin-open）：任何能访问端口的人都能进，别放公网")
    elif args.bind not in ("127.0.0.1", "localhost"):
        print("后台口令：%s" % token)
        print("         来源：%s" % token_src)
        print("         本机直连可直接打开；远程请在本页输入框粘贴口令，或 URL 加 ?token=…（记 30 天）")
        print("         用 SSH 隧道更安全：ssh -L %d:127.0.0.1:%d 用户@服务器" % (args.port, args.port))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
