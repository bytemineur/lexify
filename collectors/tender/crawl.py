# -*- coding: utf-8 -*-
"""招标采集命令行：不需要起 FastAPI，直接跑采集管线。

    python crawl.py                       # 所有启用的 http 站点，各 1 页
    python crawl.py --sites ggzy,cqggzy   # 只跑指定来源
    python crawl.py --pages 2             # 每个来源翻 2 页
    python crawl.py --limit 5             # 最多跑 5 个来源（试水用）
    python crawl.py --all                 # 连需要浏览器的站点一起跑（要装 playwright）

结果写进 data/tender.db，之后由 `python manage.py add` 搬进统一库。
"""
import argparse
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from app import db
from app.crawler import pipeline
from app.models import Base, Source, Tender


def run_one(sess, sid, pages, timeout):
    """单源超时。

    必须用守护线程：ThreadPoolExecutor 的线程是非守护的，站点卡死时虽然能超时返回，
    但进程退出时会被那个线程永久拖住（实测卡 5 分钟以上都不退）。
    守护线程超时后就随主进程一起结束，代价是那一条可能留下半截状态——比卡死强。
    """
    box, done = {}, threading.Event()

    def worker():
        try:
            box["res"] = pipeline.run_source(sess, sid, max_pages=pages)
        except Exception as e:                       # noqa: BLE001
            box["err"] = "%s: %s" % (type(e).__name__, str(e)[:120])
        finally:
            done.set()

    threading.Thread(target=worker, daemon=True).start()
    if not done.wait(timeout):
        return {"status": "failed", "found": 0, "new": 0, "dup": 0,
                "error": "超时 %d 秒，站点无响应" % timeout}
    if "err" in box:
        return {"status": "failed", "found": 0, "new": 0, "dup": 0, "error": box["err"]}
    return box.get("res") or {"status": "failed", "found": 0, "new": 0, "dup": 0,
                              "error": "没有返回结果"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sites", default="", help="逗号分隔的来源 id")
    ap.add_argument("--pages", type=int, default=1)
    ap.add_argument("--limit", type=int, default=0, help="最多跑几个来源，0 = 不限")
    ap.add_argument("--all", action="store_true", help="包含需要浏览器的站点")
    ap.add_argument("--timeout", type=int, default=120, help="单个来源最多等多少秒")
    args = ap.parse_args()

    os.makedirs(os.path.join(HERE, "data"), exist_ok=True)
    # 只建采集相关的表。db.init_db() 还会顺手建一套账号表（auth.db），
    # 那是已删掉的 Web 管理界面用的，这里不需要——避免多出第二个库文件。
    Base.metadata.create_all(bind=db.engine)
    sess = db.SessionLocal()
    pipeline.sync_sites(sess)

    if args.sites:
        ids = [s.strip() for s in args.sites.split(",") if s.strip()]
    else:
        rows = sess.query(Source).filter(Source.enabled == True).all()  # noqa: E712
        ids = [s.id for s in rows if args.all or (s.mode or "http") != "browser"]
    if args.limit:
        ids = ids[: args.limit]

    print("[招标] 待采 %d 个来源，每个 %d 页" % (len(ids), args.pages), flush=True)
    ok = failed = new_total = 0
    for i, sid in enumerate(ids, 1):
        t0 = time.time()
        try:
            res = run_one(sess, sid, args.pages, args.timeout)
            if res.get("status") == "success":
                ok += 1
                new_total += res.get("new", 0)
                print("  [%d/%d] %-10s 新 %-4d 重复 %-4d  %.1fs" %
                      (i, len(ids), sid, res.get("new", 0), res.get("dup", 0), time.time() - t0), flush=True)
            else:
                failed += 1
                err = (res.get("error") or "未知原因")
                print("  [%d/%d] %-10s 失败：%s" % (i, len(ids), sid, err[:60]), flush=True)
                # 机器可读标记，由 manage.py 解析后写进 crawl_failures
                print("FAIL|%s|list|%s" % (sid, err.replace("|", "/")[:200]), flush=True)
        except Exception as e:
            failed += 1
            print("  [%d/%d] %-10s 异常：%s" % (i, len(ids), sid, str(e)[:60]), flush=True)
            print("FAIL|%s|run|%s" % (sid, str(e).replace("|", "/")[:200]), flush=True)

    total = sess.query(Tender).count()
    print("\n[招标] 成功 %d，失败 %d，本次新增 %d，库内合计 %d" % (ok, failed, new_total, total))
    sess.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
