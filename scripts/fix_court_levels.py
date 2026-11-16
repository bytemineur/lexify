# -*- coding: utf-8 -*-
"""法院通讯录：按当前规则重算 level / short，不重新抓网络。

规则改了（比如补了「林区法院」「辽河油田法院」这类没有「人民法院」后缀的
命名）之后跑一遍即可，避免为改一个字段把 3000 条全爬一遍。

    python scripts/fix_court_levels.py            # 预演
    python scripts/fix_court_levels.py --apply    # 真写
"""
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "collectors"))

from courtbook import source as S  # noqa: E402

DB = os.environ.get("LEXIFY_DB") or os.path.join(ROOT, "data", "lexify.db")


def main():
    apply = "--apply" in sys.argv
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT id, name, level, short FROM courts").fetchall()
    changed = 0
    tally = {}
    for r in rows:
        lv = S.court_level(r["name"])
        sh = S.court_short(r["name"])
        tally[lv] = tally.get(lv, 0) + 1
        if lv != r["level"] or sh != r["short"]:
            changed += 1
            if changed <= 15:
                print("  %-34s %s -> %s" % (r["name"], r["level"], lv))
            if apply:
                con.execute("UPDATE courts SET level=?, short=? WHERE id=?",
                            (lv, sh, r["id"]))
    print("\n共 %d 条，需要改 %d 条" % (len(rows), changed))
    print("改后分布：", {k: v for k, v in sorted(tally.items(),
                                            key=lambda x: -(x[1] or 0))})
    if apply:
        con.commit()
        print("已写入 %s" % DB)
    else:
        print("（预演，加 --apply 才写库）")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
