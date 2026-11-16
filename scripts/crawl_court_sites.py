# -*- coding: utf-8 -*-
"""试跑：法院官网悬赏栏目（华南 + 江浙沪）。

    python scripts/crawl_court_sites.py              # 全部新栏目
    python scripts/crawl_court_sites.py --dry        # 只抓不写库
    python scripts/crawl_court_sites.py --only 广州   # 只跑某个法院
"""
import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "collectors"))
sys.path.insert(0, os.path.join(ROOT, "collectors", "bounty"))

import config  # noqa: E402
import db  # noqa: E402
import parser as bounty_parser  # noqa: E402
import pipeline  # noqa: E402
from _shared.http import Http  # noqa: E402


def main():
    dry = "--dry" in sys.argv
    only = ""
    if "--only" in sys.argv:
        only = sys.argv[sys.argv.index("--only") + 1]

    sites = config.REGION_BOUNTY_SITES
    if only:
        sites = [s for s in sites if only in s["court"]]
    print("栏目数：%d%s" % (len(sites), "（dry-run，不写库）" if dry else ""))

    http = Http(interval=1.2, max_retry=2)
    conn = None
    if not dry:
        conn = db.connect()
        db.init_db(conn)

    total_seen = total_kept = total_new = 0
    for site in sites:
        print("\n" + "-" * 60)
        print("[%s] %s%s" % (site["court"], site["base"],
                             site.get("entry") or site.get("prefix", "")))
        n_seen = n_kept = n_new = 0
        try:
            for raw in pipeline.court_sites.iter_site(
                    http, site, max_pages=config.COURT_MAX_PAGES, verbose=True):
                n_seen += 1
                total_seen += 1
                text = raw.get("content") or ""
                title = raw.get("title") or ""
                is_bounty = bounty_parser.looks_like_bounty(text) or \
                    ("悬赏" in title)
                if not is_bounty:
                    continue
                n_kept += 1
                total_kept += 1
                if dry:
                    print("    ★ %s  %s" % (title[:40], raw.get("publish_date")))
                    print("      %s" % text.replace("\n", " ")[:140])
                    continue
                rec = pipeline.build_record(raw)
                if db.find_same_content(conn, rec["content_hash"], rec["source_id"]):
                    continue
                if db.upsert(conn, rec):
                    n_new += 1
                    total_new += 1
                    print("    + 新增：%s（%s）" % (title[:36], rec.get("publish_date")))
        except Exception as e:  # noqa: BLE001
            print("    ! 异常：%s: %s" % (type(e).__name__, str(e)[:120]))
        print("  → 扫描 %d 条，悬赏 %d 条，新增 %d 条" % (n_seen, n_kept, n_new))
        if conn:
            conn.commit()

    if conn:
        conn.close()
    print("\n" + "=" * 60)
    print("合计：扫描 %d，悬赏 %d，新增 %d" % (total_seen, total_kept, total_new))


if __name__ == "__main__":
    main()
