# -*- coding: utf-8 -*-
"""全国法院遍历采集的落地入口：抓 → 解析 → 入库。

    python -m collectors.nationwide crawl --levels 高院,中院 --limit 50
    python -m collectors.nationwide crawl --province 广东省
    python -m collectors.nationwide crawl --all          # 全量 819 家

入库走现有 bounty / hearing 的 parser 与 db，保证与现库字段一致。
"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
COLL = os.path.dirname(HERE)
if COLL not in sys.path:
    sys.path.insert(0, COLL)

from _shared import (config as SC, crawl_ledger, court_crawler,  # noqa: E402
                     court_registry, http as H, rmfygg)

BOUNTY_KWS = ["悬赏", "执行悬赏", "财产线索", "赏金"]
HEARING_TYPES = ["开庭传票", "起诉状副本及开庭传票"]

DATA_DIR = os.path.join(os.path.dirname(COLL), "data")
os.makedirs(DATA_DIR, exist_ok=True)
LEDGER_PATH = crawl_ledger.path_for(DATA_DIR)


def _bounty_modules():
    """bounty 采集器的 db / parser / config 是一组同目录模块，一起准备好。"""
    bdir = os.path.join(COLL, "bounty")
    if bdir not in sys.path:
        sys.path.insert(0, bdir)
    import db as bounty_db
    import parser as bounty_parser
    import config as bounty_config
    return bounty_db, bounty_parser, bounty_config


def _open_db():
    bounty_db, _, _ = _bounty_modules()
    conn = bounty_db.connect()
    bounty_db.init_db(conn)
    return conn


def _save_bounty(conn, court, raw):
    """把一条公告网原始记录解析成悬赏记录并入库。返回 True 表示新增。"""
    bounty_db, bounty_parser, _ = _bounty_modules()
    from _shared import regions

    rec = rmfygg.to_record(raw)
    body = rec.get("content") or ""
    if not bounty_parser.looks_like_bounty(body):
        return False

    publish = (rec.get("publish_date") or "")[:10]
    parsed = bounty_parser.parse_notice(body, publish_date=publish) or {}

    row = dict(rec)
    row.update(parsed)
    row["source"] = "rmfygg"
    row["source_id"] = str(rec.get("uuid") or rec.get("source_id") or "")
    # 法院名以清单全称为准（公告网返回的常是简称）
    row["court"] = court["name"]
    row["publish_date"] = publish or row.get("publish_date")

    # 省份/城市：优先用清单，其次用项目已有的地区映射
    prov = court.get("province") or ""
    city = court.get("city") or ""
    if not prov or not city:
        try:
            info = regions.resolve(court["name"]) or {}
            prov = prov or info.get("province", "")
            city = city or info.get("city", "")
        except Exception:  # noqa: BLE001
            pass
    row["province"] = prov
    row["city"] = city

    return bounty_db.upsert(conn, row)


def _save_hearing(conn, court, raw):
    """开庭传票入库（复用 hearing 采集器的解析器）。"""
    hdir = os.path.join(COLL, "hearing")
    if hdir not in sys.path:
        sys.path.insert(0, hdir)
    import db as hearing_db
    import parser as hearing_parser

    rec = rmfygg.to_record(raw)
    body = rec.get("content") or ""
    if not hearing_parser.looks_like_hearing(body):
        return False
    publish = (rec.get("publish_date") or "")[:10]
    parsed = hearing_parser.parse_hearing(
        body, publish_date=publish, court=court["name"]) or {}

    row = dict(rec)
    row.update(parsed)
    row["source"] = "rmfygg_kt"
    row["source_id"] = str(rec.get("uuid") or rec.get("source_id") or "")
    row["court"] = court["name"]
    row["publish_date"] = publish or row.get("publish_date")
    row["province"] = court.get("province") or row.get("province") or ""
    row["city"] = court.get("city") or row.get("city") or ""

    hearing_db.init_db(conn)
    return hearing_db.upsert(conn, row)


def cmd_stats(args):
    s = court_registry.stats()
    print("全国法院清单")
    print("  总数: %d" % s["total"])
    for k, v in sorted(s["by_level"].items()):
        print("  %-4s %d" % (k, v))
    print("  覆盖省级行政区: %d" % s["provinces"])
    print()
    print("各省法院数:")
    for prov, n in sorted(s["by_province"].items(), key=lambda x: -x[1]):
        print("  %-16s %3d" % (prov, n))


def cmd_list(args):
    pool = court_registry.ALL_COURTS
    if args.levels:
        lv = set(x.strip() for x in args.levels.split(",") if x.strip())
        pool = [c for c in pool if c["level"] in lv]
    if args.province:
        pool = [c for c in pool if c["province"] == args.province]
    for c in pool[: args.limit or len(pool)]:
        print("%-32s %-12s %s" % (c["name"], c["province"] or "-", c["level"]))
    print("\n共 %d 家" % len(pool))


def _mode_pool(args, levels):
    """这一轮要覆盖的法院池（progress / crawl 共用同一份定义）。"""
    if args.priority:
        pool = court_registry.priority_courts(levels=levels)
    else:
        pool = list(court_registry.ALL_COURTS)
        if args.levels:
            pool = [c for c in pool if c["level"] in levels]
    if args.province:
        pool = [c for c in pool if c["province"] == args.province]
    return pool


def _levels_of(args):
    if not args.levels:
        return ("基层", "中院", "高院", "特殊")
    return tuple(x.strip() for x in args.levels.split(",") if x.strip())


def cmd_progress_counts(ledger, mode, priority):
    levels = ("基层", "中院", "高院", "特殊")
    if priority:
        pool = court_registry.priority_courts(levels=levels)
    else:
        pool = list(court_registry.ALL_COURTS)
    return crawl_ledger.summary(ledger, pool, mode)


def cmd_progress(args):
    ledger = crawl_ledger.load(LEDGER_PATH)
    levels = _levels_of(args)
    pool = _mode_pool(args, levels)
    print("账本：%s" % LEDGER_PATH)
    print("法院池：%d 家%s" % (len(pool), "（华南 + 江浙沪优先批次）" if args.priority else ""))
    for mode in ("悬赏", "开庭"):
        s = crawl_ledger.summary(ledger, pool, mode)
        pct = (s["done"] * 100.0 / s["total"]) if s["total"] else 0
        print("  %s：已查 %d / %d（%.1f%%），剩余 %d" % (
            mode, s["done"], s["total"], pct, s["todo"]))
    runs = ledger.get("runs") or []
    if runs:
        print("\n最近几轮：")
        for r in runs[-8:]:
            print("  %s  法院 %d  收 %d  新 %d  %s" % (
                r.get("start", ""), r.get("courts", 0), r.get("kept", 0),
                r.get("new", 0), r.get("stop_reason", "")))


def cmd_crawl(args):
    conn = _open_db()
    _, bounty_parser, _ = _bounty_modules()

    hdir = os.path.join(COLL, "hearing")
    if hdir not in sys.path:
        sys.path.insert(0, hdir)
    import parser as hearing_parser

    h = H.Http(interval=args.interval, max_retry=1)

    ledger = crawl_ledger.load(LEDGER_PATH)
    stats = {"courts": 0, "scanned": 0, "kept": 0, "new": 0, "bounty": 0, "hearing": 0}
    stop_reason = ""
    t0 = time.time()

    # 悬赏与开庭分两轮：公告类型不同，避免同一次请求里混着判定
    for mode, kws, types in (("悬赏", BOUNTY_KWS, [""]),
                             ("开庭", [""], HEARING_TYPES)):
        if args.only and args.only != mode:
            continue
        levels = _levels_of(args)
        pool = _mode_pool(args, levels)

        # 断点续跑：最久没查过的排前面，刚查过的（cool_hours 内）跳过
        ordered = crawl_ledger.pick_courts(
            ledger, pool, mode, limit=args.limit, cool_hours=args.cool_hours)
        skipped = len(pool) - len(ordered)
        print("\n" + "=" * 60)
        print("遍历模式：%s   待查 %d 家（跳过冷却中 %d 家）" % (
            mode, len(ordered), skipped))
        print("=" * 60)

        crawler = court_crawler.CourtCrawler(
            h, interval=args.interval, max_pages=args.pages,
            notice_types=types, keywords=kws,
            stop_after_zero=args.stop_after_zero, budget=args.budget)
        run = crawl_ledger.start_run(ledger, note="%s %s" % (mode, args.province or "全国"))

        def on_done(court, n_found, status, note, _mode=mode, _run=run):
            crawl_ledger.record(ledger, court["name"], _mode, found=n_found,
                                status=status, note=note)
            _run["courts"] += 1
            if status != "ok":
                _run["stop_reason"] = "%s：%s" % (status, note)
            crawl_ledger.save(LEDGER_PATH, ledger)

        seen_courts = set()
        try:
            for court, raw in crawler.iter_all(
                    courts=ordered, verbose=args.verbose, on_done=on_done):
                stats["scanned"] += 1
                if court["name"] not in seen_courts:
                    seen_courts.add(court["name"])
                    stats["courts"] += 1
                rec = rmfygg.to_record(raw)
                text = rec.get("content") or ""
                # 只收与目标类型相关的公告
                if mode == "悬赏":
                    if not bounty_parser.looks_like_bounty(text):
                        continue
                    is_new = _save_bounty(conn, court, raw)
                else:
                    if not hearing_parser.looks_like_hearing(text):
                        continue
                    is_new = _save_hearing(conn, court, raw)

                stats["kept"] += 1
                if is_new:
                    stats["new"] += 1
                if mode == "悬赏":
                    stats["bounty"] += 1
                else:
                    stats["hearing"] += 1
                run["kept"] = stats["kept"]
                run["new"] = stats["new"]
                if args.verbose and stats["kept"] % 20 == 0:
                    print("    ... 已收 %d 条（新增 %d，%.0fs）" % (
                        stats["kept"], stats["new"], time.time() - t0))
        except KeyboardInterrupt:
            print("\n手动中断，已采数据保留")
            stop_reason = "手动中断"
        except court_crawler.BlockedError as e:
            stop_reason = str(e)
            print("\n  ⚠ 本轮提前停止：%s" % e)

        if crawler.blocked and not stop_reason:
            stop_reason = "被限流"
        crawl_ledger.save(LEDGER_PATH, ledger)

    conn.close()
    print("\n" + "=" * 60)
    print("本轮结果")
    print("=" * 60)
    print("  覆盖法院: %d 家" % stats["courts"])
    print("  扫描公告: %d 条" % stats["scanned"])
    print("  命中保留: %d 条（悬赏 %d / 开庭 %d）" % (
        stats["kept"], stats["bounty"], stats["hearing"]))
    print("  新增入库: %d 条" % stats["new"])
    print("  请求数:   %d 次" % (h.count))
    print("  耗时: %.0f 秒" % (time.time() - t0))
    for mode in ("悬赏", "开庭"):
        s = cmd_progress_counts(ledger, mode, args.priority)
        print("  进度[%s]: %d/%d（剩余 %d）" % (mode, s["done"], s["total"], s["todo"]))
    if stop_reason:
        print("\n  ⚠ 本轮停止原因：%s" % stop_reason)
    if stats["kept"] == 0:
        print("\n  ⚠ 没有命中任何公告。若公告网返回全 0 / 403，通常是限流，")
        print("    等冷却后重跑同一条命令即可断点续跑（已查过的法院会跳过）。")


def main():
    ap = argparse.ArgumentParser(description="全国法院遍历采集")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("stats", help="法院清单统计")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("list", help="列出法院")
    p.add_argument("--levels", default="", help="高院,中院,基层,特殊")
    p.add_argument("--province", default="")
    p.add_argument("--limit", type=int, default=0)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("crawl", help="按法院遍历采集")
    p.add_argument("--levels", default="", help="默认全部；可填 基层 / 中院 / 高院")
    p.add_argument("--province", default="")
    p.add_argument("--priority", action="store_true",
                   help="只跑华南 + 江浙沪优先批次（635 家）")
    p.add_argument("--limit", type=int, default=0, help="本轮最多查多少家法院")
    p.add_argument("--pages", type=int, default=3, help="每个组合最多翻几页")
    p.add_argument("--interval", type=float, default=SC.RMFYGG_INTERVAL,
                   help="请求间隔秒（防限流，实测不要低于 6）")
    p.add_argument("--budget", type=int, default=SC.RMFYGG_DAILY_BUDGET,
                   help="本轮请求上限，0 = 不限")
    p.add_argument("--cool-hours", type=float, default=6.0,
                   help="同一家法院多少小时内不重复查")
    p.add_argument("--stop-after-zero", type=int, default=8,
                   help="连续多少次返回 0 就判定限流并停")
    p.add_argument("--only", default="", help="只跑 悬赏 或 开庭")
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_crawl)

    p = sub.add_parser("progress", help="查看断点续跑进度")
    p.add_argument("--levels", default="")
    p.add_argument("--province", default="")
    p.add_argument("--priority", action="store_true")
    p.set_defaults(func=cmd_progress)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
