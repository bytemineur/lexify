# -*- coding: utf-8 -*-
"""Lexify 数据管理：一个库文件，一条命令。

    python scripts/manage.py add 新数据.db        # 去重入库 + 自动重新导出（日常就用这一条）
    python scripts/manage.py update 新数据.db     # 入库 + 导出 + 推送 GitHub
    python scripts/manage.py stats                # 看库里有什么
    python scripts/manage.py build                # 只重新导出（库没变时不用跑）
    python scripts/manage.py push                 # 只推送（数据没变会自动跳过）
    python scripts/manage.py init                 # 建空库（一般不用，add 会自动建）

数据永远只有一个真相：data/lexify.db。
网页读的 web/data/*.js 是它的一份导出快照，每次 add 后自动刷新，不用你操心。
"""
import argparse
import csv
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.environ.get("LEXIFY_DB") or os.path.join(ROOT, "data", "lexify.db")
SCHEMA = os.path.join(ROOT, "data", "schema.sql")

# 直接运行（python scripts/manage.py）时 sys.path[0] 就是 scripts/；
# 被 main.py 当模块 import 时不一定，补一下，保证 compat 在任何入口都找得到。
if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from compat import setup_console, child_env, python_exe  # noqa: E402

PY = python_exe()

COL_TENDER = os.path.join(ROOT, "collectors", "tender")
COL_BOUNTY = os.path.join(ROOT, "collectors", "court")


TABLES = ("tenders", "sources", "hearings", "notices")

# 每张表允许写入的列（多了忽略，少了留空）
COLS = {
    "sources": ["id", "name", "base_url", "enabled", "mode", "rule_path", "rule_version",
                "schedule", "health", "fail_count", "last_run_at", "last_success_at", "last_error"],
    "tenders": ["hash_key", "source_id", "source_url", "title", "notice_type", "is_result",
                "purchaser", "agency", "budget_amount", "province", "city", "industry",
                "publish_time", "deadline", "deadline_raw", "deadline_confident",
                "content_text", "is_primary", "cluster_id", "fetched_at"],
    "hearings": ["source", "source_id", "url", "court", "province", "city", "case_no",
                 "case_kind", "cause", "cause_group", "is_commercial", "plaintiff",
                 "defendant", "defendant_type", "company_count", "person_count",
                 "hearing_at", "hearing_date", "time_estimated", "courtroom",
                 "claim_amount", "amount_basis", "notice_type", "publish_date",
                 "content", "content_hash", "parse_quality", "raw_json",
                 "fetched_at", "parsed_at"],
    "notices": ["source", "source_id", "url", "court", "province", "city", "case_no",
                "debtor", "debtor_count", "subject_amount", "reward_ratio", "reward_cap",
                "reward_fixed", "reward_est", "reward_type", "publish_date",
                "valid_from", "valid_to", "contact_phone", "content", "content_hash",
                "parse_quality", "raw_json", "fetched_at", "parsed_at"],
}

# 源库里的表名 → (目标表, 类型)
TABLE_MAP = {"tenders": ("tenders", "tender"),
             "sources": ("sources", "sources"),
             "hearings": ("hearings", "hearing"),
             "notices": ("notices", "bounty")}

KIND_TO_TABLE = {"tender": "tenders", "hearing": "hearings", "bounty": "notices"}

# 文件导入时靠字段名认类型，认不出来才要求 --kind
KIND_SIGNATURE = {
    "tender": {"title", "purchaser", "budget_amount", "notice_type", "deadline", "agency"},
    "hearing": {"hearing_date", "hearing_at", "defendant", "plaintiff", "cause", "case_no"},
    "bounty": {"debtor", "reward_ratio", "subject_amount", "reward_est", "reward_cap"},
}


def guess_kind(rows):
    """按行里的字段名猜它属于哪块看板。样本取前 50 行。"""
    if not rows:
        return ""
    keys = set()
    for r in rows[:50]:
        if isinstance(r, dict):
            keys.update(r.keys())
    scores = {k: len(keys & sig) for k, sig in KIND_SIGNATURE.items()}
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return ""
    # 并列第一说明样本太杂，别硬猜
    if list(scores.values()).count(scores[best]) > 1:
        return ""
    return best


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def sha1(text):
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()


def ensure_parent(path):
    """给库文件建好父目录。

    LEXIFY_DB=lexify.db 这种「裸文件名」的写法，dirname 是空串，
    os.makedirs("") 会抛 FileNotFoundError —— 三个平台都一样，只是 Windows 上更常见
    （因为 PowerShell 里习惯写当前目录文件名）。
    """
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


def writable_check(path=None):
    """写库前先探一下权限，返回 (ok, 说明)。

    「sudo 跑过一次」在服务器上太常见了：data/ 里的库和口令文件属主变成 root，
    之后 ubuntu 用户跑 manage.py 就只剩一句 readonly database。
    """
    path = path or DB
    d = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(d) or not os.access(d, os.W_OK):
        return False, "目录不可写：%s" % d
    if os.path.exists(path) and not os.access(path, os.W_OK):
        return False, "库文件不可写：%s" % path
    return True, ""


# ────────────────────────────── 库 ──────────────────────────────
def connect(create=False):
    if not os.path.exists(DB):
        if not create:
            raise SystemExit("还没有库：%s\n跑一次 python scripts/manage.py init，或直接 add 数据。" % DB)
        init_db()
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(force=False):
    if os.path.exists(DB) and force:
        import shutil
        backup = os.path.join(os.path.dirname(os.path.abspath(DB)), "backup")
        os.makedirs(backup, exist_ok=True)
        dst = os.path.join(backup, "lexify-%s.db" % datetime.now().strftime("%Y%m%d-%H%M%S"))
        shutil.copy2(DB, dst)
        print("已备份 → %s" % dst)
        os.remove(DB)
    ensure_parent(DB)
    ensure_schema()


SCHEMA_VERSION = 2


def ensure_schema(path=None, quiet=True):
    """幂等地把 schema 补齐：新库建全，老库补上后加的表（如 crawl_failures）。"""
    path = path or DB
    ensure_parent(path)
    try:
        conn = sqlite3.connect(path)
        with open(SCHEMA, encoding="utf-8") as f:
            conn.executescript(f.read())
        conn.execute("PRAGMA user_version = %d" % SCHEMA_VERSION)
        conn.commit()
    except sqlite3.OperationalError as e:
        # sqlite 只会说 "attempt to write a readonly database"，既不给路径也不给解法。
        # 这里换成能照着做的说法（raise RuntimeError，别用 SystemExit —— 它会被
        # main.py 的 except Exception 漏掉，直接把 web 进程带走）。
        low = str(e).lower()
        if "readonly" in low or "permission" in low:
            raise RuntimeError(
                "库是只读的（%s）：%s —— 目录/文件属主可能是 root，"
                "用 sudo chown -R $USER:$USER %s 修一下"
                % (path, e, os.path.dirname(os.path.abspath(path))))
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass
    if not quiet:
        print("schema 已就绪：%s（版本 %d）" % (path, SCHEMA_VERSION))


def record_failure(board, source, reason, stage="", detail="", run_id=""):
    """记一条采集失败。collected 的库可能还没建，这里自己兜底建表。"""
    try:
        conn = sqlite3.connect(DB)
        conn.execute(
            "INSERT INTO crawl_failures (started_at,run_id,board,source,stage,reason,detail) "
            "VALUES (?,?,?,?,?,?,?)",
            (now(), run_id, board, source, stage, reason[:200], (detail or "")[:500]))
        conn.commit()
        conn.close()
    except sqlite3.Error as e:
        print("  ⚠ 失败记录写不进库：%s" % e)


def do_failures(limit=30):
    conn = connect()
    rows = conn.execute(
        "SELECT started_at,board,source,stage,reason FROM crawl_failures "
        "WHERE resolved = 0 ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    if not rows:
        print("没有未处理的采集失败。")
    else:
        print("未处理的采集失败（最近 %d 条）：" % len(rows))
        for r in rows:
            print("  %s  %-8s %-12s %s" % (r["started_at"], r["board"], r["source"], (r["reason"] or "")[:60]))
    agg = conn.execute(
        "SELECT source, COUNT(*) n FROM crawl_failures WHERE resolved = 0 "
        "GROUP BY source ORDER BY n DESC LIMIT 10").fetchall()
    if agg:
        print("\n按来源汇总：")
        for r in agg:
            print("  %-14s %d 次" % (r["source"], r["n"]))
    conn.close()


# ────────────────────────────── 去重写入 ──────────────────────────────
def ensure_key(table, row):
    """补去重键。补不出来就跳过——宁可少一条，也不要脏一条。"""
    if table == "sources":
        return bool(row.get("id") and row.get("name"))
    if table == "tenders":
        if not row.get("title"):
            return False
        if not row.get("hash_key"):
            row["hash_key"] = sha1("%s|%s|%s" % (row.get("title"), row.get("purchaser"),
                                                 (row.get("publish_time") or "")[:10]))
        # 库里 source_id 是 NOT NULL，但外部 CSV / JSONL 普遍不带这个字段。
        # 不兜底的话每条都会撞 NOT NULL 约束被跳过，导入「看起来成功、实际 0 条」，
        # 而且只报一句 NOT NULL constraint failed，没人知道该怎么补。
        if not row.get("source_id"):
            row["source_id"] = "manual"
    else:
        if not row.get("source"):
            return False
        if not row.get("source_id"):
            basis = row.get("url") or "%s|%s" % (row.get("case_no"), row.get("court"))
            if not basis.strip("|"):
                return False
            row["source_id"] = sha1(basis)[:32]
    return True


def upsert(conn, table, row):
    data = {k: row.get(k) for k in COLS[table] if k in row}
    if not ensure_key(table, data):
        return "skip"

    if table == "sources":
        where, params = "id = ?", (data["id"],)
    elif table == "tenders":
        where, params = "hash_key = ?", (data["hash_key"],)
        data["updated_at"] = now()
    else:
        where, params = "source = ? AND source_id = ?", (data["source"], data["source_id"])

    exists = conn.execute("SELECT 1 FROM %s WHERE %s" % (table, where), params).fetchone()
    keys = [k for k in data if k in COLS[table] or k == "updated_at"]

    if exists:
        sets = ", ".join("%s = ?" % k for k in keys if k != "hash_key")
        vals = [data[k] for k in keys if k != "hash_key"]
        conn.execute("UPDATE %s SET %s WHERE %s" % (table, sets, where), vals + list(params))
        return "update"

    conn.execute("INSERT INTO %s (%s) VALUES (%s)" % (
        table, ", ".join(keys), ", ".join("?" for _ in keys)), [data[k] for k in keys])
    return "insert"


# ────────────────────────────── 读源 ──────────────────────────────
def read_source(path):
    """一个文件/库 → [(表名, [行...]), ...]。支持 .db / .jsonl / .json / .csv。"""
    if not os.path.exists(path):
        raise SystemExit("找不到文件：%s" % path)
    ext = os.path.splitext(path)[1].lower()

    if ext == ".db":
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        out = []
        for t in tables:
            if t in TABLE_MAP:
                out.append((TABLE_MAP[t][0], [dict(r) for r in conn.execute("SELECT * FROM %s" % t)]))
        conn.close()
        if not out:
            raise SystemExit("这个库里没有 tenders / hearings / notices 表")
        return out

    if ext in (".jsonl", ".ndjson"):
        rows = [json.loads(l) for l in open(path, encoding="utf-8-sig") if l.strip()]
    elif ext == ".json":
        data = json.load(open(path, encoding="utf-8-sig"))
        rows = data if isinstance(data, list) else data.get("rows", [])
    elif ext == ".csv":
        rows = list(csv.DictReader(open(path, encoding="utf-8-sig")))
    else:
        raise SystemExit("只支持 .db / .jsonl / .json / .csv")
    return [(None, rows)]     # 目标表由 --kind 决定


# ────────────────────────────── 流水线 ──────────────────────────────
def build(out=None):
    args = ["scripts/build_data.py"] + (["--out", out] if out else [])
    p = subprocess.run([PY] + args, cwd=ROOT, env=child_env())
    return p.returncode == 0


def push():
    p = subprocess.run([PY, "scripts/publish_data.py"], cwd=ROOT, env=child_env())
    return p.returncode == 0


def do_add(path, kind="", source="", rebuild=True):
    conn = connect(create=True)
    try:
        batches = read_source(path)
    except SystemExit as e:
        record_failure("import", os.path.basename(path), str(e), stage="import")
        raise

    total = {"insert": 0, "update": 0, "skip": 0}
    for table, rows in batches:
        if table is None:
            if not kind:
                kind = guess_kind(rows)
            if not kind:
                raise SystemExit(
                    "认不出这是什么数据（字段既不像招标，也不像开庭或悬赏）。\n"
                    "用 --kind tender|hearing|bounty 指定一下。")
            table = KIND_TO_TABLE[kind]
        if source:
            for r in rows:
                r.setdefault("source", source)
                if table == "tenders":
                    r.setdefault("source_id", source)

        run = conn.execute(
            "INSERT INTO ingest_runs (started_at,kind,mode,origin,status) VALUES (?,?,?,?,?)",
            (now(), table, "db" if path.endswith(".db") else "file", os.path.basename(path), "running"))
        run_id = run.lastrowid
        got = {"insert": 0, "update": 0, "skip": 0}
        errors = []
        for row in rows:
            try:
                r = upsert(conn, table, row)
            except sqlite3.Error as e:
                r = "skip"
                if len(errors) < 3:
                    errors.append(str(e)[:120])
            got[r] += 1
        conn.execute("UPDATE ingest_runs SET finished_at=?,total_seen=?,inserted=?,updated=?,"
                     "skipped=?,status=?,note=? WHERE id=?",
                     (now(), len(rows), got["insert"], got["update"], got["skip"],
                      "ok" if not errors else "partial", "; ".join(errors), run_id))
        conn.commit()
        for k in total:
            total[k] += got[k]
        print("  %-9s 读 %5d ｜ 新增 %5d ｜ 更新 %5d ｜ 跳过 %4d" %
              (table, len(rows), got["insert"], got["update"], got["skip"]))
        if errors:
            print("            首个错误：%s" % errors[0])

    conn.close()
    print("去重后：新增 %d，更新 %d，跳过 %d" % (total["insert"], total["update"], total["skip"]))

    # 一条都没进去 → 视为导入失败，记进台账（后台能看到）
    if total["insert"] + total["update"] == 0 and total["skip"] > 0:
        record_failure("import", os.path.basename(path),
                       "全部 %d 条都被跳过（缺唯一键或字段不符）" % total["skip"], stage="import")

    if rebuild:
        print("\n重新导出页面数据…")
        if not build():
            raise SystemExit("导出失败")


# ────────────────────────────── 采集 ──────────────────────────────
def _run_stream(label, args, cwd, env=None, on_line=None):
    """跑采集器：逐行转发输出（能实时看到进度），同时把每行交给 on_line 解析。"""
    print("\n▶ %s" % label, flush=True)
    if not os.path.isdir(cwd):
        print("  ✗ 找不到目录：%s" % cwd)
        return 127, []
    p = subprocess.Popen([PY] + args, cwd=cwd, env=child_env(env), stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                         errors="replace", bufsize=1)
    lines = []
    for line in p.stdout:
        line = line.rstrip("\n")
        lines.append(line)
        if not line.startswith("FAIL|"):        # 机器标记不打扰人看
            print("  " + line, flush=True)
        if on_line:
            on_line(line)
    p.wait()
    if p.returncode != 0:
        print("  ⚠ 返回码 %d（继续处理已入库的数据）" % p.returncode)
    return p.returncode, lines


def do_crawl(only="", limit=0, pages=1, sites="", rebuild=True):
    """采集器直接写统一库 → 重新导出。失败记进 crawl_failures。

    采集器不再产出自己的库：两个模块都通过环境变量指到 data/lexify.db，
    省掉「先写自己的库、再搬进来」这一步。
    """
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    env = dict(os.environ)
    env["LEXIFY_DB"] = DB
    env["TR_DATABASE_URL"] = "sqlite:///" + DB.replace("\\", "/")

    todo = []
    if only in ("", "tender"):
        todo.append(("招标雷达", "tender", COL_TENDER,
                     ["crawl.py", "--pages", str(pages)]
                     + (["--limit", str(limit)] if limit else [])
                     + (["--sites", sites] if sites else [])))
    if only in ("", "hearing"):
        todo.append(("开庭公告", "hearing", COL_BOUNTY,
                     ["cli.py", "hearing", "crawl"] + (["--limit", str(limit)] if limit else [])))
    if only in ("", "bounty"):
        todo.append(("执行悬赏", "bounty", COL_BOUNTY,
                     ["cli.py", "crawl"] + (["--limit", str(limit)] if limit else [])))

    if only in ("", "tender") and not _has_module("sqlalchemy"):
        raise SystemExit("招标采集需要依赖：pip install -r requirements.txt")

    failed = 0
    for label, board, cwd, args in todo:
        def on_line(line, board=board):
            nonlocal failed
            if line.startswith("FAIL|"):
                parts = line.split("|", 3)
                src = parts[1] if len(parts) > 1 else "?"
                stage = parts[2] if len(parts) > 2 else ""
                reason = parts[3] if len(parts) > 3 else ""
                record_failure(board, src, reason, stage=stage, run_id=run_id)
                failed += 1
                print("  ✗ %-10s %s" % (src, reason), flush=True)

        rc, lines = _run_stream(label, args, cwd, env, on_line)
        if rc != 0 and board != "tender":
            record_failure(board, "(整体)", "采集器返回码 %d" % rc, stage="run",
                           detail="\n".join(lines[-5:]), run_id=run_id)
            failed += 1

    if failed:
        print("\n⚠ 本轮有 %d 处采集失败，已记录（python scripts/manage.py failures 可查）" % failed, flush=True)

    if rebuild:
        print("\n▶ 重新导出页面数据")
        if not build():
            raise SystemExit("导出失败")


def _has_module(name):
    try:
        __import__(name)
        return True
    except ImportError:
        return False


def do_stats():
    conn = connect()
    print("库：%s  (%.1f MB)" % (DB, os.path.getsize(DB) / 1024 / 1024))
    for t in TABLES:
        print("  %-9s %6d" % (t, conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]))
    rows = conn.execute("SELECT started_at,kind,total_seen,inserted,updated,skipped,status "
                        "FROM ingest_runs ORDER BY id DESC LIMIT 5").fetchall()
    if rows:
        print("\n最近导入：")
        for r in rows:
            print("  %s  %-9s 读%-6d 新%-6d 改%-5d 跳%-5d %s" % tuple(r))
    conn.close()


def main():
    setup_console()
    ap = argparse.ArgumentParser(description="Lexify 数据管理")
    ap.add_argument("cmd", choices=["init", "add", "crawl", "build", "push", "update", "stats", "failures"])
    ap.add_argument("file", nargs="?", help="add / update 时的数据文件")
    ap.add_argument("--kind", default="", choices=["", "tender", "hearing", "bounty"])
    ap.add_argument("--source", default="", help="补 source 字段（文件导入用）")
    ap.add_argument("--no-build", action="store_true", help="不重新导出")
    ap.add_argument("--force", action="store_true", help="init 时备份并重建（清空数据）")
    ap.add_argument("--only", default="", choices=["", "tender", "hearing", "bounty"],
                    help="crawl 只采某一块")
    ap.add_argument("--limit", type=int, default=0, help="crawl 最多抓多少条/几个来源")
    ap.add_argument("--pages", type=int, default=1, help="crawl 招标每个来源翻几页")
    ap.add_argument("--sites", default="", help="crawl 招标只跑指定来源（逗号分隔）")
    args = ap.parse_args()

    ok, why = writable_check()
    if not ok and args.cmd in ("init", "add", "crawl", "update"):
        print("✗ %s" % why)
        print("  这个命令要写库，先修权限（属主多半是 root，因为之前用 sudo 跑过）：")
        print("    sudo chown -R $USER:$USER %s" % os.path.dirname(os.path.abspath(DB)))
        raise SystemExit(1)

    if os.path.exists(DB):
        try:
            ensure_schema(quiet=True)
        except RuntimeError as e:
            print("✗ %s" % e)
            raise SystemExit(1)

    if args.cmd == "init":
        init_db(force=args.force)
    elif args.cmd == "crawl":
        do_crawl(args.only, args.limit, args.pages, args.sites, rebuild=not args.no_build)
    elif args.cmd == "stats":
        do_stats()
    elif args.cmd == "failures":
        do_failures()
    elif args.cmd == "build":
        sys.exit(0 if build() else 1)
    elif args.cmd == "push":
        sys.exit(0 if push() else 1)
    elif args.cmd == "add":
        if not args.file:
            raise SystemExit("用法：python scripts/manage.py add 数据文件")
        do_add(args.file, args.kind, args.source, rebuild=not args.no_build)
    elif args.cmd == "update":
        if args.file:
            do_add(args.file, args.kind, args.source, rebuild=True)
        elif not build():
            raise SystemExit("导出失败")
        print("\n推送到 GitHub…")
        push()


if __name__ == "__main__":
    main()
