# -*- coding: utf-8 -*-
"""智能助手「乐菲」的护栏测试（离线，不调模型）。

守的是两件事：
  1. 知识库只含公开信息——不再扫 README / docs / 表名 / 目录树；
  2. 系统提示词里写明了「哪些不许说」，以及站点公开口径（看板、价格、邮箱）。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from server import ai  # noqa: E402

passes = 0
fails = 0


def ok(name, cond, detail=""):
    global passes, fails
    if cond:
        passes += 1
        print("  PASS %s" % name)
    else:
        fails += 1
        print("  FAIL %s %s" % (name, detail))


# 任何一条出现在知识库里都算泄漏
LEAK_WORDS = ["github", "bytemineur", "git@", "开源", "license", "sqlite",
              "page_views", "ingest_runs", "crawl_failures", "config.yaml",
              "lexify_", "systemd", "nohup", "anaconda", "/root", "/home/",
              "SELECT ", "CREATE TABLE"]

text, meta = ai.build_knowledge(force=True)
prompt = ai.SYSTEM_PROMPT.replace("{knowledge}", text.strip())

print("知识库 %d 字 / 提示词 %d 字" % (len(text), len(prompt)))

for w in LEAK_WORDS:
    ok("知识库不含 %r" % w, w.lower() not in text.lower())
for w in LEAK_WORDS:
    if w == "开源":
        continue  # 提示词里要点名「是否开源」才能明确拒绝，见下一条
    ok("提示词不含 %r" % w, w.lower() not in prompt.lower())

# 「开源」只允许出现在护栏清单里，不能出现在任何正面陈述中
ok("提示词里的「开源」只在护栏里",
   prompt.count("开源") == prompt.count("是否开源"), prompt.count("开源"))

# 知识库只读 overview.js：不带文件、文档计数
ok("meta.mode = public", meta.get("mode") == "public", meta)
ok("不扫文件（files=0）", meta.get("files") == 0, meta)
ok("不扫文档（docs=0）", meta.get("docs") == 0, meta)
ok("chars 与实际一致", meta.get("chars") == len(text), meta)
ok("有构建时间", bool(meta.get("builtAt")))

# 公开数字来自首页快照
ok("含首页汇总数字", ("条" in text and ("3,301" in text or "1,398" in text)), text[:120])

# 提示词必须有的护栏段落
for must in ("绝对不说", "仓库链接", "数据库", "配置文件",
             "忽略之前的指令", "系统提示词"):
    ok("提示词含护栏 %r" % must, must in prompt)

# 提示词必须有的公开口径
for must in ("招标雷达", "开庭公告", "执行悬赏", "service@lexify.cn", "免费"):
    ok("提示词含公开口径 %r" % must, must in prompt)

# 知识库不再有扫描文件的代码路径
src = open(os.path.join(ROOT, "server", "ai.py"), encoding="utf-8").read()
for gone in ("def _tree(", "def _docs(", "def _db_brief(", "PRIORITY_DOCS",
             "import sqlite3"):
    ok("ai.py 已移除 %r" % gone, gone not in src)

# /docs 默认不公开
web_src = open(os.path.join(ROOT, "server", "web.py"), encoding="utf-8").read()
ok("web.py 有 DOCS_PUBLIC 开关", "DOCS_PUBLIC" in web_src)
ok("/docs 路由前有鉴权", web_src.index("DOCS_PUBLIC and not authmod.admin_ok") <
   web_src.index('docsmod.render_doc("architecture")'))

print("\n合计 %d 项通过，%d 项失败" % (passes, fails))
sys.exit(1 if fails else 0)
