# -*- coding: utf-8 -*-
"""main()：参数解析 + 启动装配。

只做「把命令行/环境变量翻译成一份运行配置，再把几颗独立组件组装起来」，
不包含任何协议/业务实现（那些在 web/auth/admin/docs/jobs/db 各模块里）。
"""
import argparse
import os
import threading

from . import jobs as jobsmod
from .config import (ADMIN_COOKIE, ADMIN_OPEN, ADMIN_TOKEN, BIND, DB, DB_FIX_HINT, PORT,
                     PY, SCHEDULE, UPDATE_AT, compat_describe, config_summary,
                     load_admin_token, setup_console)
from .db import db_is_empty, db_writable
from .web import Handler, QuietHTTPServer


def ai_line(disabled=False):
    """启动时一行 AI 状态，省得去猜入口为什么没出现。"""
    try:
        from . import ai as aimod
        s = aimod.settings()
    except Exception as e:
        return "未启用（%s）" % e
    if disabled or not s["enabled"]:
        return "未启用 → 在 config.yaml 填 ai.api_key（或设 LEXIFY_AI_API_KEY）后重启"
    return "已启用 · 模型 %s · Key 来源：%s" % (s["model"], s["keySource"])


def main():
    setup_console()
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--bind", default=BIND)
    ap.add_argument("--at", default=UPDATE_AT, help="每天几点更新 HH:MM")
    ap.add_argument("--no-schedule", action="store_true", help="不跑定时任务")
    ap.add_argument("--schedule", action="store_true", help="强制开启定时任务（配置里关了也能开）")
    ap.add_argument("--admin-only", action="store_true", help="只开后台（本地维护用）")
    ap.add_argument("--admin-token", default="", help="后台口令（同 LEXIFY_ADMIN_TOKEN）")
    ap.add_argument("--admin-open", action="store_true", help="不设防，允许任何人进后台（仅限内网）")
    ap.add_argument("--run-now", action="store_true", help="启动时先更新一次")
    ap.add_argument("--no-auto-crawl", action="store_true", help="库里没数据时不要自动采集")
    ap.add_argument("--no-ai", action="store_true", help="关闭 AI 咨询入口（同 LEXIFY_AI_DISABLED=1）")
    args = ap.parse_args()

    import server.config as cfg
    if not SCHEDULE and not args.schedule:
        args.no_schedule = True
    if args.no_ai:
        os.environ["LEXIFY_AI_DISABLED"] = "1"
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
    elif why:
        print("⚠ %s" % why)

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

    # 定时器只在没被显式关掉时启动；库能不能写交给每次任务自己判断
    # （以前这里还要求 writable，一旦启动时撞上数据库锁，定时器就再也不会启动）。
    if not args.no_schedule:
        jobsmod.start_scheduler(args.at)

    srv = QuietHTTPServer((args.bind, args.port), Handler)
    host = "127.0.0.1" if args.bind == "0.0.0.0" else args.bind
    base = "http://%s:%d" % (host, args.port)
    print("站点：  %s/" % base)
    print("后台：  %s/admin" % base)
    print("定时：  %s" % ("关闭" if not jobsmod.SCHEDULER["on"] else "每天 " + args.at))
    print("配置：  %s" % config_summary())
    print("AI 咨询：%s" % ai_line(args.no_ai))
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
