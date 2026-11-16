# -*- coding: utf-8 -*-
"""通讯录 ↔ 法院清单 的匹配与差异报告。

两边命名习惯不同，直接按字符串比会得出「只有 580 家重合」这种假结论：
  · 通讯录（110.com）用**法院自己的名字**：四会市人民法院 / 东源县人民法院
  · 清单（court_registry）用**带地市前缀的全称**：肇庆市四会市人民法院 / 河源市东源县人民法院
  · 通讯录还夹着已撤销的老区划：广州市芳村区 / 广州市东山区 / 潮阳市

所以要按「区县名」而不是全名匹配，并把「同区县名在两套清单里出现」的
算作同一个法院（地址电话可以互认）。

    python scripts/court_match.py            # 生成报告
"""
import os
import re
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "collectors"))

from _shared import court_registry as R  # noqa: E402

DB = os.environ.get("LEXIFY_DB") or os.path.join(ROOT, "data", "lexify.db")

# 源站里有、但已经撤销的区划（2010 前后的合区）。
# 注意「市中区」「东山区」不能放进来：济南/枣庄的市中区、鹤岗的东山区都还在用。
OBSOLETE = {
    "芳村区", "潮阳市", "金园区", "升平区", "达濠区", "石炭井区",
    "南汇区", "卢湾区", "闸北区", "宣武区", "崇文区", "广州市东山区",
}


def key_of(name):
    """归一成可比较的键：去掉 省/市/自治区 前缀与「人民法院」后缀。

    「肇庆市四会市人民法院」→ 四会
    「四会市人民法院」      → 四会
    """
    n = re.sub(r"人民法院$", "", name or "")
    n = re.sub(r"(?:基层|中级|高级)?法院$", "", n)
    n = re.sub(r"(?:省|市|自治区|壮族自治区|回族自治区|维吾尔自治区)", "", n)
    n = re.sub(r"^(?:.*?(?:自治州|地区))", "", n)
    return n.strip()


def load_book(con):
    return [dict(r) for r in con.execute(
        "SELECT name, level, province, city, address, postcode, phone FROM courts")]


def main():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    book = load_book(con)
    con.close()

    by_key = {}
    for b in book:
        by_key.setdefault(key_of(b["name"]), []).append(b)

    reg = R.ALL_COURTS
    matched, unmatched_reg = [], []
    for c in reg:
        k = key_of(c["name"])
        hit = by_key.get(k)
        if hit:
            matched.append((c, hit[0]))
        else:
            unmatched_reg.append(c)

    print("法院清单 %d 家，能配上通讯录的 %d 家（%.0f%%）" % (
        len(reg), len(matched), len(matched) * 100.0 / max(1, len(reg))))
    print()

    print("按省份（清单 / 已配通讯录 / 缺电话）：")
    for prov in sorted(set(c["province"] for c in reg if c["province"])):
        cs = [c for c in reg if c["province"] == prov]
        if not cs:
            continue
        m = [x for x in matched if x[0]["province"] == prov]
        print("  %-10s %4d / %4d 缺 %3d" % (prov, len(cs), len(m), len(cs) - len(m)))
    print()

    print("清单有、通讯录没有的（前 30，按省份）：")
    by_prov = {}
    for c in unmatched_reg:
        if c["province"]:
            by_prov.setdefault(c["province"], []).append(c["name"])
    for prov in list(R.PRIORITY_PROVINCES):
        names = by_prov.get(prov, [])
        if names:
            print("  %-10s %d 家：%s" % (prov, len(names), "、".join(names[:8])))
    print()

    obsolete = [b for b in book if any(o in b["name"] for o in OBSOLETE)]
    print("通讯录里已撤销的区划（%d 条，属于历史遗留，不影响使用）：" % len(obsolete))
    for b in obsolete[:12]:
        print("   %-26s %s" % (b["name"], b["province"]))
    con2 = sqlite3.connect(DB)

    # 把匹配结果落到 courts 表：补 level 与清单一致的层级（清单更准）
    fixed = 0
    for c, b in matched:
        if c["level"] and b["level"] != c["level"]:
            con2.execute("UPDATE courts SET level=? WHERE name=?",
                         (c["level"], b["name"]))
            fixed += 1
    con2.commit()
    con2.close()
    print("\n按清单修正层级：%d 条" % fixed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
