# -*- coding: utf-8 -*-
"""后台任务与定时更新。

- JOB / start_job / job_html：采集可能跑十几分钟，放到后台线程，/admin 用 meta refresh 轮询。
- run_update / start_scheduler / catch_up_needed：每日「采集 → 导出 → 推送」的定时编排。
"""
import html
import os
import subprocess
import threading
import time
from datetime import datetime, timedelta

from .config import PY, ROOT, child_env

LAST_UPDATE_FILE = os.path.join(ROOT, "data", "last_update.txt")

JOB = {"running": False, "label": "", "started": "", "ended": "", "ok": None, "log": []}
JOB_MAX_LINES = 400


def sh(args, timeout=1800):
    try:
        p = subprocess.run([PY] + args, cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           env=child_env())
        return p.returncode == 0, ((p.stdout or "") + (p.stderr or "")).strip()[-8000:]
    except subprocess.TimeoutExpired:
        return False, "超时（%d 秒）" % timeout
    except OSError as e:
        return False, "启动子进程失败：%s（解释器 %s）" % (e, PY)


def read_last_update():
    """上次成功更新的时间（独立存文件，导入外部数据不算「今天的定时更新」）。"""
    try:
        with open(LAST_UPDATE_FILE, encoding="utf-8") as f:
            return datetime.strptime(f.read().strip(), "%Y-%m-%d %H:%M")
    except (OSError, ValueError):
        return None


def write_last_update():
    try:
        os.makedirs(os.path.dirname(LAST_UPDATE_FILE), exist_ok=True)
        with open(LAST_UPDATE_FILE, "w", encoding="utf-8") as f:
            f.write(datetime.now().strftime("%Y-%m-%d %H:%M"))
    except OSError:
        pass


def next_run(at_hhmm, now=None):
    now = now or datetime.now()
    hh, mm = [int(x) for x in at_hhmm.split(":")]
    t = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return t if t > now else t + timedelta(days=1)


def catch_up_needed(at_hhmm):
    """今天该跑的点已过、但今天还没更新过 → 需要补一次。"""
    now = datetime.now()
    hh, mm = [int(x) for x in at_hhmm.split(":")]
    sched = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if now < sched:
        return False
    last = read_last_update()
    return last is None or last < sched


def run_update():
    # manage.py update 现在会真正「采集 → 入库 → 导出」，再推送。
    print("[%s] 开始更新" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"), flush=True)
    ok, out = sh(["scripts/manage.py", "update"])
    print(out, flush=True)
    print("[%s] 更新结束（%s）" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "ok" if ok else "失败"), flush=True)
    if ok:
        write_last_update()
    return ok


def start_scheduler(at):
    def loop():
        while True:
            nxt = next_run(at)
            print("[定时] 下次运行：%s" % nxt.strftime("%Y-%m-%d %H:%M"), flush=True)
            while datetime.now() < nxt:
                time.sleep(min(30, max(1, (nxt - datetime.now()).total_seconds())))
            run_update()

    threading.Thread(target=loop, daemon=True).start()


def start_job(label, args):
    if JOB["running"]:
        return False
    JOB.update(running=True, label=label, started=datetime.now().strftime("%H:%M:%S"),
               ended="", ok=None, log=[])

    def run():
        ok = True
        try:
            p = subprocess.Popen([PY] + args, cwd=ROOT, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                 errors="replace", bufsize=1, env=child_env())
            for line in p.stdout:
                line = line.rstrip("\n")
                if line.startswith("FAIL|"):
                    continue
                JOB["log"].append(line)
                if len(JOB["log"]) > JOB_MAX_LINES:
                    del JOB["log"][:100]
                print("  " + line, flush=True)
            p.wait()
            ok = p.returncode == 0
        except Exception as e:
            JOB["log"].append("异常：%s" % e)
            ok = False
        finally:
            JOB.update(running=False, ok=ok, ended=datetime.now().strftime("%H:%M:%S"))

    threading.Thread(target=run, daemon=True).start()
    return True


def job_html():
    if JOB["running"]:
        tail = "\n".join(JOB["log"][-18:]) or "启动中…"
        return ("<p><span class='pill'>运行中</span> %s（%s 开始）</p><pre>%s</pre>" %
                (html.escape(JOB["label"]), JOB["started"], html.escape(tail)))
    if JOB["ok"] is None:
        return '<p class="sub">还没有跑过更新。</p>'
    tail = "\n".join(JOB["log"][-18:])
    head = "✓ 完成" if JOB["ok"] else "✗ 失败"
    return ("<p><span class='pill'>%s</span> %s（%s → %s）</p><pre>%s</pre>" %
            (head, html.escape(JOB["label"]), JOB["started"], JOB["ended"], html.escape(tail)))
