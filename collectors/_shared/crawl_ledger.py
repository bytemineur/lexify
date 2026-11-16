# -*- coding: utf-8 -*-
"""采集进度账本：让「按法院遍历」可以断点续跑。

为什么需要：人民法院公告网对同一个 IP 会限流甚至封禁，一次跑不完 500+ 家
法院。没有账本的话每次重跑都从头开始，前面已经查过的法院等于白查，
一旦在 50 家处被封，就永远走不到第 51 家。

账本记录：每家法院 × 每种模式（悬赏 / 开庭）最后一次查询的时间与命中数。
调度逻辑：优先跑「从没查过的」，其次跑「查得最久远的」，同一家法院在
cool hours 内不重复查询。
"""
import json
import os
import time

LEDGER_NAME = "crawl_ledger.json"


def path_for(data_dir):
    return os.path.join(data_dir, LEDGER_NAME)


def load(path):
    if not os.path.exists(path):
        return {"courts": {}, "runs": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {"courts": {}, "runs": []}
    data.setdefault("courts", {})
    data.setdefault("runs", [])
    return data


def save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def key_of(court_name, mode):
    return "%s|%s" % (mode, court_name)


def last_ts(data, court_name, mode):
    rec = data["courts"].get(key_of(court_name, mode))
    return (rec or {}).get("ts", 0)


def pick_courts(data, courts, mode, *, limit=0, cool_hours=6.0):
    """按「最久没查过」排序挑出这一轮要跑的法院。

    courts: court_registry 的法院 dict 列表
    mode:   "悬赏" / "开庭"
    """
    now = time.time()
    cool = cool_hours * 3600
    fresh, stale = [], []
    for c in courts:
        ts = last_ts(data, c["name"], mode)
        if not ts:
            fresh.append((0, c))
        elif now - ts >= cool:
            stale.append((ts, c))
    stale.sort(key=lambda x: x[0])
    ordered = [c for _, c in fresh] + [c for _, c in stale]
    return ordered[:limit] if limit else ordered


def record(data, court_name, mode, *, found=0, kept=0, new=0, status="ok", note=""):
    data["courts"][key_of(court_name, mode)] = {
        "ts": time.time(),
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "found": found,
        "kept": kept,
        "new": new,
        "status": status,
        "note": note[:200],
    }


def start_run(data, note=""):
    run = {"start": time.strftime("%Y-%m-%d %H:%M:%S"), "note": note,
           "courts": 0, "kept": 0, "new": 0, "stop_reason": ""}
    data["runs"].append(run)
    return run


def summary(data, courts, mode):
    done = sum(1 for c in courts if last_ts(data, c["name"], mode))
    return {"total": len(courts), "done": done, "todo": len(courts) - done}
